#!/usr/bin/env python3
"""Generic, vendor-free adapter renderer (task_190a).

The reusable core owns rendering MECHANICS; explicitly enabled `integration` capabilities own
tool/vendor policy and content as DATA (see the normative
`.ai/tasks/done/task_190a_integration_driven_adapter_renderer/design.md`). This module contains no
vendor or stack identifier and never branches on a vendor `format`; it interprets an integration
render descriptor into raw `(repository-relative path, bytes)` entries.

Every failure fails closed with a named `RenderError.reason`. Expansion is single-pass: an expanded
value (catalog, agent document, invocation) is never rescanned, so literal braces it contains are
preserved verbatim.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

import scripts.ai_plane.constants as constants
from scripts.ai_plane.frontmatter import parse_frontmatter, render_catalog
from scripts.ai_plane.manifest import platform_text_bytes
from scripts.ai_plane.utils import generated_markdown, read_md_dir, rel

_CATALOG_NAME_RE = re.compile(r"[a-z][a-z0-9_]*")
_SCALAR_TOKENS = frozenset({
    "generated_warning", "tool", "tool_role", "agent_document",
    "default_isolation", "argument_placeholder", "generated_file_extension",
})

# The vendor-free core neutral render descriptor is data owned by scripts.extension_registry
# (config imports the registry, not this module — keeping the ai_plane import graph acyclic). This
# renderer only needs to interpret it; its one marker artifact ships its template inline as data.


class RenderError(ValueError):
    """Fail-closed render error. ``reason`` is a stable machine-readable slug."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(f"{reason}: {message}")
        self.reason = reason
        self.message = message


def _require(condition: bool, reason: str, message: str) -> None:
    if not condition:
        raise RenderError(reason, message)


# ---------------------------------------------------------------------------
# Single-pass template expansion
# ---------------------------------------------------------------------------


def expand(template: str, resolve: Callable[[str], str]) -> str:
    """Expand ``{token}`` placeholders in one pass. ``{{``/``}}`` encode literal braces. An
    unclosed ``{``, a stray ``}``, or a placeholder containing ``{`` fails with
    `unresolved-render-placeholder`; an unknown token is whatever ``resolve`` raises. Resolved
    values are appended verbatim and never rescanned (single pass)."""
    out: list[str] = []
    i, n = 0, len(template)
    while i < n:
        ch = template[i]
        if ch == "{":
            if i + 1 < n and template[i + 1] == "{":
                out.append("{")
                i += 2
                continue
            close = template.find("}", i + 1)
            _require(close != -1, "unresolved-render-placeholder",
                     f"unclosed '{{' at offset {i}")
            name = template[i + 1:close]
            _require("{" not in name, "unresolved-render-placeholder",
                     f"malformed placeholder {{{name}}}")
            out.append(resolve(name))
            i = close + 1
            continue
        if ch == "}":
            if i + 1 < n and template[i + 1] == "}":
                out.append("}")
                i += 2
                continue
            _require(False, "unresolved-render-placeholder", f"stray '}}' at offset {i}")
        out.append(ch)
        i += 1
    return "".join(out)


# ---------------------------------------------------------------------------
# Catalog and agent-document expansion
# ---------------------------------------------------------------------------


def _catalog_text(spec: dict[str, Any], repo_root: Path) -> str:
    source = repo_root / spec["source_root"]
    include = spec.get("include")
    exclude = spec.get("exclude") or []
    only = set(include) if include is not None else None
    text = render_catalog(spec["title"], source, only=only)
    if exclude:
        keep = [ln for ln in text.splitlines(keepends=True)
                if not any(f"`{rel(source / name)}`" in ln for name in exclude)]
        text = "".join(keep)
    return text


def _command_catalog_text(descriptor: dict[str, Any], repo_root: Path) -> str:
    source = descriptor.get("command_catalog_source")
    _require(isinstance(source, str), "unresolved-render-placeholder",
             "{command_catalog} requires integration.command_catalog_source")
    path = repo_root / source
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RenderError("invalid-command-catalog", f"{source}: {error}") from error

    _require(isinstance(payload, dict)
             and set(payload) == {"schema_version", "title", "commands"},
             "invalid-command-catalog",
             f"{source} must declare exactly schema_version, title, commands")
    _require(payload["schema_version"] == 1, "invalid-command-catalog",
             f"{source} schema_version must be 1")
    _require(_single_line(payload["title"]), "invalid-command-catalog",
             f"{source} title must be a non-empty single-line string")
    rows = payload["commands"]
    _require(isinstance(rows, list) and rows, "invalid-command-catalog",
             f"{source} commands must be a non-empty list")

    commands = descriptor.get("commands", {})
    seen: set[str] = set()
    rendered = [f"## {_escape_markdown(payload['title'])}", ""]
    for index, row in enumerate(rows):
        where = f"{source} commands[{index}]"
        _require(isinstance(row, dict)
                 and set(row) == {"token", "label", "summary", "invocations"},
                 "invalid-command-catalog",
                 f"{where} must declare exactly token, label, summary, invocations")
        token = row["token"]
        _require(isinstance(token, str) and bool(re.fullmatch(r"[A-Z][A-Z0-9_]*", token)),
                 "invalid-command-catalog", f"{where}.token is invalid")
        _require(token not in seen, "invalid-command-catalog",
                 f"{source} declares duplicate command token {token!r}")
        seen.add(token)
        _require(token in commands, "invalid-command-catalog",
                 f"{source} token {token!r} is absent from integration.commands")
        for field in ("label", "summary"):
            _require(_single_line(row[field]), "invalid-command-catalog",
                     f"{where}.{field} must be a non-empty single-line string")
        invocations = row["invocations"]
        _require(isinstance(invocations, list) and invocations
                 and all(_single_line(value) and "`" not in value for value in invocations),
                 "invalid-command-catalog",
                 f"{where}.invocations must be non-empty single-line strings without backticks")
        prefix = commands[token]
        _require(_single_line(prefix) and "`" not in prefix, "invalid-command-catalog",
                 f"integration.commands.{token} cannot be rendered safely")
        rendered.append(
            f"- **{_escape_markdown(row['label'])}** — {_escape_markdown(row['summary'])}"
        )
        rendered.extend(f"  - `{prefix} {suffix}`" for suffix in invocations)
    return "\n".join(rendered)


def _single_line(value: Any) -> bool:
    return (isinstance(value, str) and bool(value.strip()) and value == value.strip()
            and "\n" not in value and "\r" not in value
            and all(ord(character) >= 32 for character in value))


def _escape_markdown(value: str) -> str:
    escaped = value.replace("\\", "\\\\")
    for character in ("*", "_", "[", "]", "<", ">"):
        escaped = escaped.replace(character, f"\\{character}")
    return escaped


def agent_document_body(descriptor: dict[str, Any], repo_root: Path) -> str:
    """The frontmatter-stripped body of the integration's declared agent document, reproducing the
    current `parse_frontmatter(read_text(...))[1]` behavior (read + whole-file strip, then strip
    canonical frontmatter)."""
    ref = descriptor.get("agent_document")
    if ref is None:
        return ""
    path = repo_root / ref
    _require(path.is_file(), "missing-template", f"agent_document {ref!r} does not resolve to a file")
    _, body = parse_frontmatter(path.read_text(encoding="utf-8").strip())
    return body


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


def make_resolver(descriptor: dict[str, Any], context: dict[str, str], repo_root: Path,
                  *, invocation: str | None = None) -> Callable[[str], str]:
    """Build the single-pass placeholder resolver for one artifact. ``invocation`` is provided only
    for a command document; ``{invocation}`` elsewhere fails closed."""
    catalogs = descriptor.get("catalogs", {})
    commands = descriptor.get("commands", {})
    separator = descriptor.get("invoke_separator", " ")

    scalars = {
        "generated_warning": constants.WARNING,
        "tool": context["tool"],
        "tool_role": context["tool_role"],
        "default_isolation": context["default_isolation"],
        "argument_placeholder": descriptor.get("argument_placeholder", ""),
        "generated_file_extension": descriptor.get("generated_file_extension", ""),
    }

    def resolve(name: str) -> str:
        if name in scalars:
            return scalars[name]
        if name == "agent_document":
            return agent_document_body(descriptor, repo_root)
        if name == "invocation":
            _require(invocation is not None, "unresolved-render-placeholder",
                     "{invocation} is only valid inside a command document")
            return invocation
        if name == "command_catalog":
            return _command_catalog_text(descriptor, repo_root)
        if name.startswith("catalog:"):
            key = name[len("catalog:"):]
            _require(key in catalogs, "unresolved-render-placeholder",
                     f"unknown catalog {key!r}")
            return _catalog_text(catalogs[key], repo_root)
        if name.startswith("invoke:"):
            token = name[len("invoke:"):]
            _require(token in commands, "unresolved-render-placeholder",
                     f"invoke references unknown command token {token!r}")
            return commands[token]
        _require(False, "unresolved-render-placeholder", f"unknown placeholder {{{name}}}")

    return resolve


def _invocation_string(descriptor: dict[str, Any], spec: dict[str, Any], context: dict[str, str],
                       repo_root: Path) -> str:
    """Join `[commands[token], *expanded_arguments]` with the integration's invoke_separator."""
    commands = descriptor.get("commands", {})
    token = spec["token"]
    _require(token in commands, "unresolved-render-placeholder",
             f"command invocation references unknown token {token!r}")
    arg_resolve = make_resolver(descriptor, context, repo_root)
    arguments = [expand(arg, arg_resolve) for arg in spec.get("arguments", [])]
    return descriptor.get("invoke_separator", " ").join([commands[token], *arguments])


# ---------------------------------------------------------------------------
# Template loading
# ---------------------------------------------------------------------------


def load_template(ext_root: Path, ref: str) -> str:
    """Read a contained template file as UTF-8, normalizing CRLF/CR to LF (platform_text_bytes
    re-applies the platform ending at write time), fail-closed if missing."""
    path = ext_root / ref
    _require(path.is_file(), "missing-template", f"template {ref!r} not found under {rel(ext_root)}")
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    return text.replace("\r\n", "\n").replace("\r", "\n")


# ---------------------------------------------------------------------------
# Document artifact kinds (marker/support/command/settings)
# ---------------------------------------------------------------------------


def _render_document(descriptor: dict[str, Any], artifact: dict[str, Any], context: dict[str, str],
                     repo_root: Path, ext_root: Path | None, *, invocation: str | None = None) -> bytes:
    if "template_inline" in artifact:  # the core neutral descriptor ships its template as data
        template = artifact["template_inline"].replace("\r\n", "\n").replace("\r", "\n")
    else:
        template = load_template(ext_root, artifact["template"])
    resolve = make_resolver(descriptor, context, repo_root, invocation=invocation)
    return platform_text_bytes(expand(template, resolve))


def _render_settings(descriptor: dict[str, Any], artifact: dict[str, Any], context: dict[str, str],
                     repo_root: Path) -> bytes:
    """Expand scalar placeholders in string leaves of the JSON-compatible document, preserving
    insertion order and the current `json.dumps(..., indent=2)` + platform_text_bytes shape."""
    resolve = make_resolver(descriptor, context, repo_root)

    def walk(node: Any) -> Any:
        if isinstance(node, str):
            return expand(node, resolve)
        if isinstance(node, dict):
            return {key: walk(value) for key, value in node.items()}
        if isinstance(node, list):
            return [walk(value) for value in node]
        return node

    document = walk(artifact["document"])
    return platform_text_bytes(json.dumps(document, indent=2))


# ---------------------------------------------------------------------------
# Artifact plan dispatcher
# ---------------------------------------------------------------------------

_DOCUMENT_KINDS = frozenset({"marker_document", "support_document"})


def validate_descriptor_sources(descriptor: dict[str, Any], repo_root: Path) -> None:
    """Prove every declared canonical `.ai` source exists with the correct type BEFORE rendering.
    Static schema (extension_registry) already proved containment/authority at discovery; `.ai`
    content is per-repo, so its EXISTENCE is checked here — in the sync pre-mutation plan — so a
    missing agent/catalog/rules/skills/blueprint source fails closed (`missing-source`) before any
    write, leaving the tree byte-identical."""
    def need_file(ref: str, what: str) -> None:
        _require((repo_root / ref).is_file(), "missing-source", f"{what} source {ref!r} is not an existing file")

    def need_dir(ref: str, what: str) -> None:
        _require((repo_root / ref).is_dir(), "missing-source", f"{what} source {ref!r} is not an existing directory")

    if descriptor.get("agent_document"):
        need_file(descriptor["agent_document"], "agent_document")
    if descriptor.get("command_catalog_source"):
        need_file(descriptor["command_catalog_source"], "command_catalog_source")
    for name, spec in descriptor.get("catalogs", {}).items():
        need_dir(spec["source_root"], f"catalog {name!r}")
    for artifact in descriptor["render"]["artifacts"]:
        if artifact["kind"] == "rules_tree":
            need_dir(artifact["source_root"], "rules_tree")
        elif artifact["kind"] == "skills_tree":
            need_dir(artifact["source_root"], "skills_tree")
            if artifact.get("blueprint_source"):
                need_dir(artifact["blueprint_source"], "blueprint_source")


def plan_artifacts(descriptor: dict[str, Any], context: dict[str, str], repo_root: Path,
                   ext_root: Path) -> list[tuple[str, bytes]]:
    """Render every artifact in the descriptor into repository-relative ``(path, bytes)`` entries.
    A marker artifact's path must equal the descriptor's ``detect_marker``. Tree kinds
    (rules_tree/skills_tree) expand to many entries. This does not touch the filesystem beyond
    reading declared template/source inputs; collision detection across the whole plan is the
    caller's (pre-mutation) responsibility."""
    validate_descriptor_sources(descriptor, repo_root)
    detect_marker = descriptor.get("detect_marker")
    entries: list[tuple[str, bytes]] = []
    for artifact in descriptor["render"]["artifacts"]:
        kind = artifact["kind"]
        if kind in _DOCUMENT_KINDS:
            if kind == "marker_document":
                _require(artifact["path"] == detect_marker, "invalid-render-descriptor",
                         f"marker_document path {artifact['path']!r} must equal detect_marker {detect_marker!r}")
            entries.append((artifact["path"], _render_document(descriptor, artifact, context, repo_root, ext_root)))
        elif kind == "command_document":
            invocation = _invocation_string(descriptor, artifact["invocation"], context, repo_root)
            entries.append((artifact["path"],
                            _render_document(descriptor, artifact, context, repo_root, ext_root, invocation=invocation)))
        elif kind == "settings_document":
            entries.append((artifact["path"], _render_settings(descriptor, artifact, context, repo_root)))
        elif kind == "rules_tree":
            entries.extend(_render_rules_tree(descriptor, artifact, context, repo_root))
        elif kind == "skills_tree":
            entries.extend(_render_skills_tree(descriptor, artifact, context, repo_root, ext_root))
        else:  # pragma: no cover - static validation rejects unknown kinds first
            _require(False, "unknown-artifact-kind", f"unknown artifact kind {kind!r}")
    return entries


# ---------------------------------------------------------------------------
# Tree artifact kinds (rules_tree / skills_tree) — the generated_tree transform
# ---------------------------------------------------------------------------

_COMMAND_TOKEN_RE = re.compile(r"__AI_COMMAND_([A-Z][A-Z0-9_]*)__")


def _generated_marker(suffix: str) -> str | None:
    return {
        ".py": "# GENERATED FROM .ai/. DO NOT EDIT DIRECTLY. Run `ai sync`.\n",
        ".md": f"{constants.WARNING}\n\n",
        ".html": f"{constants.WARNING}\n",
        ".css": f"/* {constants.WARNING} */\n",
        ".yaml": f"# {constants.WARNING}\n",
    }.get(suffix.lower())


def resolve_command_tokens(content: str, commands: dict[str, str]) -> str:
    """Resolve `__AI_COMMAND_TOKEN__` markers from the integration command map, failing closed on
    an unknown token (mirrors the current sync-time resolver, keyed on integration data)."""
    def replace(match: re.Match[str]) -> str:
        token = match.group(1)
        _require(token in commands, "unresolved-render-placeholder",
                 f"unknown neutral command token {match.group(0)!r}")
        return commands[token]

    rendered = _COMMAND_TOKEN_RE.sub(replace, content)
    leftover = re.search(r"__AI_COMMAND_[A-Z0-9_]*__", rendered)
    _require(leftover is None, "unresolved-render-placeholder",
             f"unresolved neutral command token {leftover.group(0)!r}" if leftover else "")
    return rendered


def render_generated_tree_file(source: Path, commands: dict[str, str]) -> bytes:
    """The generic generated_tree transform (design.md), byte-equivalent to the current
    render_generated_tree_file: raw bytes for non-UTF-8; CRLF/CR normalized; Markdown frontmatter
    stripped; suffix marker injected when absent from the first three lines (preserving a Python
    shebang); command tokens resolved; original raw bytes only when no transform applies."""
    raw = source.read_bytes()
    marker = _generated_marker(source.suffix)
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw
    normalized = decoded.replace("\r\n", "\n").replace("\r", "\n")
    if source.suffix.lower() == ".md":
        _, normalized = parse_frontmatter(normalized)
    rendered = normalized
    if marker and "GENERATED FROM .ai/" not in "\n".join(normalized.splitlines()[:3]):
        if source.suffix.lower() == ".py" and normalized.startswith("#!"):
            first, _, rest = normalized.partition("\n")
            rendered = first + "\n" + marker + rest
        else:
            rendered = marker + normalized
    rendered = resolve_command_tokens(rendered, commands)
    if rendered == normalized and marker is None:
        return raw
    return platform_text_bytes(rendered)


def _copy_generated_tree(source_dir: Path, dest_prefix: str, commands: dict[str, str]) -> list[tuple[str, bytes]]:
    entries: list[tuple[str, bytes]] = []
    if not source_dir.exists():
        return entries
    for src in sorted(source_dir.rglob("*")):
        if not src.is_file() or src.suffix == ".pyc" or "__pycache__" in src.parts:
            continue
        relative = src.relative_to(source_dir).as_posix()
        entries.append((f"{dest_prefix}/{relative}", render_generated_tree_file(src, commands)))
    return entries


def _rule_summary(body: str, name: str) -> str:
    """A short human-readable summary of a rule: its heading joined to its first prose line."""
    title = ""
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("<!--"):
            continue
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            continue
        summary = stripped.lstrip("0123456789. ").strip()
        if summary:
            return f"{title}. {summary}" if title else summary
    return title or name.removesuffix(".md").replace("-", " ")


def _yaml_quoted(value: str) -> str:
    """Quote a scalar for YAML frontmatter.

    A rule summary routinely contains a colon, which would silently break the client's parse of
    the whole frontmatter block, so the value is always emitted quoted and escaped.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _rule_activation_frontmatter(spec: dict[str, Any] | None, meta: dict[str, Any], name: str,
                                 body: str = "") -> str:
    """Emit the integration's NATIVE rule-activation frontmatter, when it declares one.

    Some clients decide whether a rule is in context from frontmatter on the rule file itself. If
    the plane strips its own registry frontmatter and writes nothing back, those rules arrive with
    no declared activation and the client is free to ignore them -- which reads to a user as "the
    agent ignored all the rules". The canonical rule may declare a generic `activation` (and
    `globs`); the SPELLING of that concept belongs to the integration manifest, so the core stays
    context-agnostic.
    """
    if not spec:
        return ""
    activation = str(meta.get("activation") or spec.get("default_activation", "")).strip()
    mapped = spec.get("activation_map", {}).get(activation, activation)
    if not mapped:
        return ""
    description = str(meta.get("description") or "").strip().replace("\n", " ")
    if not description:
        description = _rule_summary(body, name)
    description = _yaml_quoted(description)
    globs = meta.get("globs") or spec.get("default_globs") or ""
    if isinstance(globs, list):
        globs = ", ".join(str(item) for item in globs)
    rendered = (
        str(spec["template"])
        .replace("{activation}", mapped)
        .replace("{description}", description)
        .replace("{globs}", str(globs))
    )
    return rendered if rendered.endswith("\n") else rendered + "\n"


def _render_rules_tree(descriptor: dict[str, Any], artifact: dict[str, Any], context: dict[str, str],
                       repo_root: Path) -> list[tuple[str, bytes]]:
    commands = descriptor.get("commands", {})
    source_root = repo_root / artifact["source_root"]
    dest = artifact["destination"]
    strip = artifact.get("strip_frontmatter", False)
    frontmatter_spec = artifact.get("rule_frontmatter")
    entries: list[tuple[str, bytes]] = []
    for name, content in read_md_dir(source_root):
        meta, stripped_body = parse_frontmatter(content)
        body = stripped_body if strip else content
        header = _rule_activation_frontmatter(frontmatter_spec, meta, name, body)
        text = f"{header}{constants.WARNING}\n\n{body}\n"
        entries.append((f"{dest}/{name}", platform_text_bytes(resolve_command_tokens(text, commands))))
    for alias in artifact.get("legacy_aliases", []):
        body = "\n".join([
            f"Legacy adapter alias for `{alias['replacement']}`.",
            "",
            f"Reason: {alias['reason']}",
            "",
            "Do not use this file as canonical source. Update `.ai/` and run `ai sync`.",
            "",
            f"Canonical replacement: `{alias['replacement']}`",
        ])
        # A deprecation stub exists so an agent that follows a stale path finds the pointer. It must
        # never spend always-on context, so it declares the manual activation explicitly rather than
        # arriving with no activation at all.
        alias_meta = {
            "activation": "manual",
            "description": f"Deprecated. Superseded by {alias['replacement']}.",
        }
        header = _rule_activation_frontmatter(frontmatter_spec, alias_meta, alias["path"], body)
        text = header + generated_markdown(alias["title"], body)
        entries.append((f"{dest}/{alias['path']}", platform_text_bytes(resolve_command_tokens(text, commands))))
    return entries


def _render_skills_tree(descriptor: dict[str, Any], artifact: dict[str, Any], context: dict[str, str],
                        repo_root: Path, ext_root: Path) -> list[tuple[str, bytes]]:
    commands = descriptor.get("commands", {})
    source_root = repo_root / artifact["source_root"]
    dest = artifact["destination"]
    index_spec = artifact["index"]
    entries: list[tuple[str, bytes]] = []
    entry_lines: list[str] = []
    for skill in sorted(source_root.glob(index_spec["entry_glob"])):
        skill_name = skill.parent.name
        entries.extend(_copy_generated_tree(skill.parent, f"{dest}/{skill_name}", commands))
        entry_lines.append(index_spec["entry_template"]
                           .replace("{skill_name}", skill_name)
                           .replace("{skill_source}", rel(skill)))
    blueprint_source = artifact.get("blueprint_source")
    blueprint_dest = artifact.get("blueprint_destination")
    if blueprint_source is not None and (repo_root / blueprint_source).exists():
        blueprint_entry = index_spec.get("blueprint_entry_template")
        if blueprint_entry is not None:
            entry_lines.append(blueprint_entry.replace("{blueprint_source}", rel(repo_root / blueprint_source / "README.md")))
    header = expand(load_template(ext_root, index_spec["template"]),
                    make_resolver(descriptor, context, repo_root)).rstrip("\n")
    index_text = header + "\n\n" + "\n".join(entry_lines)
    entries.append((index_spec["path"], platform_text_bytes(resolve_command_tokens(index_text, commands))))
    if blueprint_source is not None and blueprint_dest is not None:
        entries.extend(_copy_generated_tree(repo_root / blueprint_source, blueprint_dest, commands))
    return entries
