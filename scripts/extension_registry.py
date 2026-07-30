#!/usr/bin/env python3
"""Extension capability registry for the Agent Control Plane (maw_core, task_181).

Implements extension_api_design.md: the reusable core owns coordination protocols and
never language, stack, vendor, editor, build-tool, or project-specific behavior. Four
capability types compose here:

- ``integration``  declarative agent-adapter contribution (format, tokens, render descriptor)
- ``pack``         declarative content (rules, workflows, skills, templates, scope vocabulary)
- ``gate``         executable evidence gate (verification planning + argv commands + evidence)
- ``command``      optional deterministic CLI subcommand (advisory index/projection)

Registration comes only from explicit project configuration. Filesystem presence, import-time
side effects, package entrypoint scanning, and ambient global installs never enable an
extension. Composition is deterministic and every invalid version, duplicate, cycle, conflict,
undeclared scope/command, or out-of-authority read/write root fails closed with a named reason.

This module imports no extension and no stack policy. It is stdlib only.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import os
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

# Core verification scope vocabulary. These belong to the core, not to any extension, and are
# never a substituted default for a missing extension roster; a gate may add to this set.
CORE_SCOPES: tuple[str, ...] = (
    "control-plane",
    "affected",
    "affected-plus-neighbors",
    "workspace",
)

SUPPORTED_API_VERSIONS: frozenset[int] = frozenset({1})
CAPABILITY_TYPES: frozenset[str] = frozenset({"integration", "pack", "command", "gate"})
EXECUTABLE_CAPABILITIES: frozenset[str] = frozenset({"gate", "command"})
# The verify-gate protocol a loaded gate module must expose before ai_cli routes to it. cmd_verify
# is the minimum; cargo/cargo-cache are gate-optional and are not required of a generic gate.
GATE_PROTOCOL: tuple[str, ...] = ("cmd_verify",)

_ID_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_DEFAULT_ROOTS: tuple[str, ...] = (".ai/extensions",)

# Typed extension configuration (task_190b). `config_schema` maps each declared, non-empty key to
# exactly one of these value-type tokens; declared defaults AND project `extensions.config` values
# are validated against the declared token. This slice adds no nested object/list-element schema,
# coercion, aliases, or implicit types (design.md "Typed Extension Configuration").
_CONFIG_TYPE_TOKENS: tuple[str, ...] = ("string", "integer", "number", "boolean", "list")
# A command consumes its declaring extension's effective configuration through single-pass
# `{config:<key>}` placeholders in its declared `args` (the only place configuration is consumed).
# Expanded values are never rescanned; placeholders are unsupported anywhere but `args`.
_CONFIG_PLACEHOLDER_RE = re.compile(r"\{config:([^{}]+)\}")

# Pack content composition (task_190c). A pack's `contributes.content` declares typed content that is
# COMPOSED into the effective control-plane surface, not merely copied: rules/workflows/skills carry
# registry frontmatter (id + type) and participate in the generated registry/catalogs indexed by id;
# templates are materialized raw and may consume a pack's cross-pack effective defaults through
# single-pass `{default:<key>}` placeholders (design.md "Content Contributions" / "Defaults
# Contributions"). Every content kind is materialized through the hash manifest (enable adds it,
# disable prunes it), and the whole composition is resolved fail-closed before any generation
# transaction opens.
_CONTENT_KINDS: tuple[str, ...] = ("rules", "workflows", "skills", "templates")
# rules/workflows/skills content is identified by its frontmatter `id` and indexed into the registry;
# its declared `kind` must agree with the document's frontmatter `type` (design.md "Content Contributions"
# — "a `type` consistent with `kind`"). templates carry no frontmatter and are not indexed.
_CONTENT_KIND_TYPE: dict[str, str] = {"rules": "rule", "workflows": "workflow", "skills": "skill"}
# A pack-contributed template consumes the cross-pack composed defaults through single-pass
# `{default:<key>}` placeholders resolved at composition time; unresolved output is never rescanned.
_DEFAULT_PLACEHOLDER_RE = re.compile(r"\{default:([^{}]+)\}")
# The relation operations a pack may declare over another contribution (design.md "Relation
# Operations"). `before`/`after` continue to express precedence; `relations` express the merge.
_RELATION_OPS: tuple[str, ...] = ("replace", "prepend", "append", "wrap")
_RELATION_KINDS: tuple[str, ...] = ("content", "defaults")

# The vendor-free core neutral render descriptor (design.md "Neutral descriptor and lifecycle"):
# one minimal AGENTS.md, stack-neutral control-plane command tokens only (no cargo/stack command),
# no support tree / dispatch / catalog / gate. Its prose names only `.ai/`, the task contract, and
# the configured tool — no codex/claude/antigravity/gemini/cargo/rust identifier. It is data (not a
# branch), selected when a tool declares a null/omitted render_source; an explicit source never
# falls back to it. It lives here (stdlib-only) so config can import it without an ai_plane cycle.
_NEUTRAL_MARKER_TEMPLATE = (
    "{generated_warning}\n\n"
    "# Agent Control Plane\n\n"
    "This is the generated adapter for `{tool}`.\n\n"
    "Canonical source: `.ai/`. Read the task contract under `.ai/tasks/` before acting, and treat "
    "`.ai/` as the only source of truth.\n"
)
_NEUTRAL_COMMANDS: dict[str, str] = {
    "INIT": "ai init", "SYNC": "ai sync", "AUDIT_FRAMEWORK": "ai audit-framework",
    "FEATURE": "ai feature", "RESEARCH": "ai research", "PLAN": "ai plan",
    "TASKS": "ai tasks", "TASK": "ai task", "DISPATCH": "ai dispatch",
    "REVIEW": "ai review", "QA": "ai qa", "MERGE": "ai merge",
    "LEARN": "ai learn", "ARCHIVE": "ai archive", "VERIFY": "ai verify", "BLUEPRINT": "ai blueprint",
}


def neutral_descriptor() -> dict[str, Any]:
    """Return the vendor-free core neutral render descriptor (fresh copy)."""
    return {
        "format": "neutral",
        "agent_document": None,
        "catalogs": {},
        "argument_placeholder": "<task_id>",
        "generated_file_extension": ".md",
        "invoke_separator": " ",
        "detect_marker": "AGENTS.md",
        "commands": dict(_NEUTRAL_COMMANDS),
        "render": {"artifacts": [
            {"kind": "marker_document", "path": "AGENTS.md", "template_inline": _NEUTRAL_MARKER_TEMPLATE},
        ]},
    }

_MANIFEST_FIELDS: frozenset[str] = frozenset({
    "id", "version", "api_version", "types", "root", "entrypoints", "contributes",
    "config_schema", "defaults", "scopes", "commands", "dependencies", "conflicts",
    "priority", "before", "after", "read_roots", "write_roots", "executables", "platforms",
    "integration", "relations",
})


class RegistryError(ValueError):
    """Fail-closed registry error. ``reason`` is a stable machine-readable slug."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(f"{reason}: {message}")
        self.reason = reason
        self.message = message


def _require(condition: bool, reason: str, message: str) -> None:
    if not condition:
        raise RegistryError(reason, message)


def _contained_relative(value: str) -> bool:
    """True only for a repository-relative, contained path. Evaluated under BOTH the POSIX and the
    Windows path conventions so a manifest cannot smuggle a target that escapes on whichever OS
    happens to validate it: any absolute path, any drive component (``C:escape``), any root component
    (``\\escape`` / ``/escape``), or any ``..`` parent traversal fails closed. On Windows
    ``C:escape`` and ``\\escape`` are NOT ``is_absolute()`` yet resolve outside the repository when
    joined to the repo root, so drive/root are checked explicitly."""
    if not isinstance(value, str) or not value:
        return False
    for pure in (PurePosixPath(value), PureWindowsPath(value)):
        if pure.is_absolute() or pure.drive or pure.root or ".." in pure.parts:
            return False
    return True


def _config_value_matches(value: Any, token: str) -> bool:
    """Strict predicate for one declared config type token (design.md "Typed Extension
    Configuration"). ``bool`` is excluded from ``integer``/``number`` because in Python ``bool`` is
    an ``int`` subclass and a boolean supplied where a number is declared must fail closed."""
    if token == "string":
        return isinstance(value, str)
    if token == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if token == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if token == "boolean":
        return isinstance(value, bool)
    if token == "list":
        return isinstance(value, list)
    return False


def _render_config_scalar(value: Any) -> str:
    """Deterministically render one effective-config value for `{config:<key>}` expansion into an
    argv token: strings verbatim, booleans as ``true``/``false``, integers/numbers in their JSON
    scalar spelling, and lists in compact JSON array spelling. ``bool`` is handled before ``int``
    because it is an ``int`` subclass."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return json.dumps(value)
    if isinstance(value, list):
        return json.dumps(value, separators=(",", ":"))
    raise RegistryError("invalid-extension-config-value",
                        f"config value {value!r} of type {type(value).__name__} cannot be rendered")


def render_default_placeholders(text: str, composed_defaults: dict[str, Any]) -> str:
    """Resolve single-pass ``{default:<key>}`` placeholders in a pack-contributed template using the
    cross-pack composed defaults (task_190c "Defaults Contributions" consumption path). Every value is
    rendered with the same deterministic scalar/JSON spelling as ``{config:<key>}``; a referenced key
    with no composed value fails closed with ``missing-default-value``; expanded output is never
    rescanned for further placeholders."""
    def _replace(match: "re.Match[str]") -> str:
        key = match.group(1)
        if key not in composed_defaults:
            raise RegistryError("missing-default-value",
                                f"template references default key {key!r} with no composed value")
        return _render_config_scalar(composed_defaults[key])
    return _DEFAULT_PLACEHOLDER_RE.sub(_replace, text)


def _validate_command_authority(name: str, spec: dict[str, Any], manifest: dict[str, Any],
                                source: str) -> None:
    """Enforce a command's declared input/output/write-authority contract (design.md "Inputs,
    outputs, and declared authority"). Shared by manifest-time validation AND the dispatch-time
    re-check so a post-resolution mutation cannot smuggle an out-of-authority target past dispatch:

    - every command ``write_roots`` entry is contained and within the manifest's ``write_roots``;
    - an ``inputs`` entry is either a ``stdin:`` stream or a contained path within ``read_roots``;
    - an ``outputs`` entry is either a ``stdout:``/``stderr:`` stream or a contained path within one
      of the command's own ``write_roots`` entries.
    """
    manifest_write_roots = [Path(w) for w in manifest.get("write_roots", [])]
    read_roots = [Path(r) for r in manifest.get("read_roots", [])]
    command_write_roots = [Path(w) for w in spec.get("write_roots", [])]

    def _within(candidate: Path, roots: list[Path]) -> bool:
        return any(candidate == r or r in candidate.parents for r in roots)

    for wr in spec.get("write_roots", []):
        _require(isinstance(wr, str) and _contained_relative(wr), "out-of-authority-write-root",
                 f"{source} command {name!r} write_root {wr!r} must be repository-relative and contained")
        _require(_within(Path(wr), manifest_write_roots), "out-of-authority-write-root",
                 f"{source} command {name!r} write_root {wr!r} is not within the manifest's declared "
                 f"write_roots {manifest.get('write_roots', [])}")
    for inp in spec.get("inputs", []):
        if isinstance(inp, str) and inp.startswith("stdin:"):
            continue
        _require(isinstance(inp, str) and _contained_relative(inp), "out-of-authority-read-root",
                 f"{source} command {name!r} input {inp!r} must be a 'stdin:' stream or a "
                 f"repository-relative contained path")
        _require(_within(Path(inp), read_roots), "out-of-authority-read-root",
                 f"{source} command {name!r} input {inp!r} is not within the manifest's declared "
                 f"read_roots {manifest.get('read_roots', [])}")
    for out in spec.get("outputs", []):
        if isinstance(out, str) and (out.startswith("stdout:") or out.startswith("stderr:")):
            continue
        _require(isinstance(out, str) and _contained_relative(out), "out-of-authority-write-root",
                 f"{source} command {name!r} output {out!r} must be a 'stdout:'/'stderr:' stream or a "
                 f"repository-relative contained path")
        _require(_within(Path(out), command_write_roots), "out-of-authority-write-root",
                 f"{source} command {name!r} output {out!r} is not within the command's declared "
                 f"write_roots {spec.get('write_roots', [])}")


# The render.artifacts kinds and the fields each permits (task_190a design.md "Artifact common
# law"). Static shape is validated here; template existence, placeholder resolution, and output
# collisions are render-time (pre-mutation plan) concerns.
_INTEGRATION_REQUIRED_FIELDS: frozenset[str] = frozenset({
    "format", "agent_document", "catalogs", "argument_placeholder", "generated_file_extension",
    "invoke_separator", "detect_marker", "commands", "render",
})
_INTEGRATION_FIELDS = _INTEGRATION_REQUIRED_FIELDS | frozenset({"command_catalog_source"})
_ARTIFACT_KINDS: dict[str, frozenset[str]] = {
    "marker_document": frozenset({"kind", "path", "template"}),
    "support_document": frozenset({"kind", "path", "template"}),
    "command_document": frozenset({"kind", "path", "template", "invocation"}),
    "settings_document": frozenset({"kind", "path", "document"}),
    "rules_tree": frozenset({"kind", "source_root", "destination", "strip_frontmatter",
                             "legacy_aliases", "rule_frontmatter"}),
    "skills_tree": frozenset({"kind", "source_root", "destination", "transform", "index",
                              "blueprint_source", "blueprint_destination"}),
}
_CATALOG_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _validate_artifact_fields(artifact: dict[str, Any], index: int, source: str,
                              commands: dict[str, Any]) -> None:
    """Validate one render artifact's required fields, value types, and nested objects per
    design.md's per-kind schema (invocation, legacy_aliases, index, blueprint pairing). All
    declared paths must be contained relative paths; on-disk existence/authority is checked by
    validate_integration_paths before mutation."""
    kind = artifact["kind"]
    where = f"{source} artifact[{index}] ({kind})"

    def check(condition: bool, message: str) -> None:
        _require(condition, "invalid-render-descriptor", f"{where} {message}")

    def contained_str(value: Any, field: str) -> None:
        check(isinstance(value, str) and _contained_relative(value), f"{field} must be a contained relative path")

    if kind in ("marker_document", "support_document", "command_document"):
        contained_str(artifact.get("path"), "path")
        contained_str(artifact.get("template"), "template")
    if kind == "command_document":
        inv = artifact.get("invocation")
        check(isinstance(inv, dict) and set(inv) == {"token", "arguments"},
              "invocation must declare exactly token, arguments")
        check(isinstance(inv["token"], str) and inv["token"] in commands,
              "invocation.token must be a declared command token")
        check(isinstance(inv["arguments"], list) and all(isinstance(a, str) for a in inv["arguments"]),
              "invocation.arguments must be a list of strings")
    if kind == "settings_document":
        contained_str(artifact.get("path"), "path")
        check(isinstance(artifact.get("document"), dict), "document must be an object")
    if kind == "rules_tree":
        contained_str(artifact.get("source_root"), "source_root")
        contained_str(artifact.get("destination"), "destination")
        check(isinstance(artifact.get("strip_frontmatter", False), bool), "strip_frontmatter must be a boolean")
        aliases = artifact.get("legacy_aliases", [])
        check(isinstance(aliases, list), "legacy_aliases must be a list")
        for alias in aliases:
            check(isinstance(alias, dict) and set(alias) == {"path", "title", "replacement", "reason"}
                  and all(isinstance(alias[k], str) and alias[k] for k in alias),
                  "each legacy alias must declare non-empty path, title, replacement, reason")
            check("/" not in alias["path"] and _contained_relative(alias["path"]),
                  "legacy alias path must be a bare contained filename (no path separators or '..')")
    if kind == "skills_tree":
        contained_str(artifact.get("source_root"), "source_root")
        contained_str(artifact.get("destination"), "destination")
        check(artifact.get("transform") == "generated_tree", "transform must be 'generated_tree'")
        idx = artifact.get("index")
        check(isinstance(idx, dict) and set(idx) == {"path", "template", "entry_glob",
              "entry_template", "blueprint_entry_template"},
              "index must declare exactly path, template, entry_glob, entry_template, blueprint_entry_template")
        contained_str(idx["path"], "index.path")
        contained_str(idx["template"], "index.template")
        check(idx["entry_glob"] == "*/SKILL.md", "index.entry_glob must be exactly '*/SKILL.md'")
        check(isinstance(idx["entry_template"], str)
              and "{skill_name}" in idx["entry_template"] and "{skill_source}" in idx["entry_template"],
              "index.entry_template must contain {skill_name} and {skill_source}")
        bet = idx["blueprint_entry_template"]
        check(bet is None or (isinstance(bet, str) and "{blueprint_source}" in bet),
              "index.blueprint_entry_template must be null or contain {blueprint_source}")
        source_bp, dest_bp = artifact.get("blueprint_source"), artifact.get("blueprint_destination")
        check((source_bp is None) == (dest_bp is None),
              "blueprint_source and blueprint_destination must both be null or both set")
        for bp in (source_bp, dest_bp):
            if bp is not None:
                contained_str(bp, "blueprint path")


def _validate_integration_descriptor(data: Any, *, source: str) -> None:
    """Static (manifest-load) validation of an ``integration`` render descriptor per design.md's
    normative schema. Fails closed with `invalid-render-descriptor` (or `unknown-artifact-kind`).
    Template existence, placeholder resolution, and output collisions are validated at render time
    (the pre-mutation plan), not here."""
    _require(isinstance(data, dict), "invalid-render-descriptor",
             f"{source} integration must be an object")
    missing = sorted(_INTEGRATION_REQUIRED_FIELDS - set(data))
    unknown = sorted(set(data) - _INTEGRATION_FIELDS)
    _require(not missing, "invalid-render-descriptor",
             f"{source} integration is missing required field(s): {', '.join(missing)}")
    _require(not unknown, "invalid-render-descriptor",
             f"{source} integration declares unknown field(s): {', '.join(unknown)}")

    _require(isinstance(data["format"], str) and bool(data["format"]), "invalid-render-descriptor",
             f"{source} integration.format must be a non-empty string")
    agent = data["agent_document"]
    _require(agent is None or (isinstance(agent, str) and _contained_relative(agent)),
             "invalid-render-descriptor", f"{source} integration.agent_document must be null or a contained path")
    for scalar in ("argument_placeholder", "invoke_separator"):
        _require(isinstance(data[scalar], str), "invalid-render-descriptor",
                 f"{source} integration.{scalar} must be a string")
    ext = data["generated_file_extension"]
    _require(isinstance(ext, str) and ext.startswith("."), "invalid-render-descriptor",
             f"{source} integration.generated_file_extension must be a dot-prefixed string")
    _require(isinstance(data["detect_marker"], str) and _contained_relative(data["detect_marker"]),
             "invalid-render-descriptor", f"{source} integration.detect_marker must be a contained path")
    command_catalog = data.get("command_catalog_source")
    _require(command_catalog is None
             or (isinstance(command_catalog, str) and _contained_relative(command_catalog)),
             "invalid-render-descriptor",
             f"{source} integration.command_catalog_source must be a contained path when declared")

    catalogs = data["catalogs"]
    _require(isinstance(catalogs, dict), "invalid-render-descriptor",
             f"{source} integration.catalogs must be an object")
    for name, spec in catalogs.items():
        _require(isinstance(name, str) and bool(_CATALOG_NAME_RE.fullmatch(name)),
                 "invalid-render-descriptor", f"{source} catalog name {name!r} must match {_CATALOG_NAME_RE.pattern}")
        _require(isinstance(spec, dict) and set(spec) == {"title", "source_root", "include", "exclude"},
                 "invalid-render-descriptor",
                 f"{source} catalog {name!r} must declare exactly title, source_root, include, exclude")
        _require(isinstance(spec["title"], str) and bool(spec["title"]), "invalid-render-descriptor",
                 f"{source} catalog {name!r} title must be a non-empty string")
        _require(isinstance(spec["source_root"], str) and _contained_relative(spec["source_root"]),
                 "invalid-render-descriptor", f"{source} catalog {name!r} source_root must be a contained path")
        for list_field in ("include", "exclude"):
            value = spec[list_field]
            if value is None and list_field == "include":
                continue
            _require(isinstance(value, list) and all(isinstance(v, str) and v for v in value),
                     "invalid-render-descriptor",
                     f"{source} catalog {name!r} {list_field} must be a list of non-empty strings"
                     + (" or null" if list_field == "include" else ""))
            _require(all("/" not in v and _contained_relative(v) for v in value), "invalid-render-descriptor",
                     f"{source} catalog {name!r} {list_field} entries must be bare contained filenames")
            _require(value == sorted(value), "invalid-render-descriptor",
                     f"{source} catalog {name!r} {list_field} must be sorted")
            _require(len(set(value)) == len(value), "invalid-render-descriptor",
                     f"{source} catalog {name!r} {list_field} must be duplicate-free")

    commands = data["commands"]
    _require(isinstance(commands, dict) and bool(commands), "invalid-render-descriptor",
             f"{source} integration.commands must be a non-empty object")
    for token, command in commands.items():
        _require(isinstance(token, str) and bool(re.fullmatch(r"[A-Z][A-Z0-9_]*", token)),
                 "invalid-render-descriptor", f"{source} integration.commands token {token!r} is invalid")
        _require(isinstance(command, str) and bool(command), "invalid-render-descriptor",
                 f"{source} integration.commands.{token} must be a non-empty string")


    render = data["render"]
    _require(isinstance(render, dict) and set(render) == {"artifacts"}, "invalid-render-descriptor",
             f"{source} integration.render must declare exactly 'artifacts'")
    artifacts = render["artifacts"]
    _require(isinstance(artifacts, list) and bool(artifacts), "invalid-render-descriptor",
             f"{source} integration.render.artifacts must be a non-empty list")
    marker_paths: list[str] = []
    for index, artifact in enumerate(artifacts):
        _require(isinstance(artifact, dict) and isinstance(artifact.get("kind"), str),
                 "invalid-render-descriptor", f"{source} artifact[{index}] must be an object with a string kind")
        kind = artifact["kind"]
        _require(kind in _ARTIFACT_KINDS, "unknown-artifact-kind",
                 f"{source} artifact[{index}] has unknown kind {kind!r}")
        unknown_af = sorted(set(artifact) - _ARTIFACT_KINDS[kind])
        _require(not unknown_af, "invalid-render-descriptor",
                 f"{source} artifact[{index}] ({kind}) declares unknown field(s): {', '.join(unknown_af)}")
        _validate_artifact_fields(artifact, index, source, commands)
        if kind == "marker_document":
            marker_paths.append(artifact["path"])
    _require(marker_paths == [data["detect_marker"]], "invalid-render-descriptor",
             f"{source} render must declare exactly one marker_document whose path equals "
             f"detect_marker {data['detect_marker']!r} (got {marker_paths})")


# ---------------------------------------------------------------------------
# Manifest validation
# ---------------------------------------------------------------------------


def validate_manifest(data: Any, *, source: str, folder_id: str | None = None) -> dict[str, Any]:
    """Validate one extension manifest and return it normalized. Fails closed with a named
    reason on any structural, versioning, capability, or authority violation."""
    _require(isinstance(data, dict), "manifest-not-object", f"{source} manifest must be a JSON object")

    unknown = sorted(set(data) - _MANIFEST_FIELDS)
    _require(not unknown, "unknown-field", f"{source} declares unknown field(s): {', '.join(unknown)}")

    ext_id = data.get("id")
    _require(isinstance(ext_id, str) and bool(_ID_RE.fullmatch(ext_id)),
             "invalid-id", f"{source} id must match {_ID_RE.pattern}")
    if folder_id is not None:
        _require(ext_id == folder_id, "id-folder-mismatch",
                 f"{source} id {ext_id!r} does not match its folder {folder_id!r}")

    version = data.get("version")
    _require(isinstance(version, str) and bool(_VERSION_RE.fullmatch(version)),
             "invalid-version", f"{source} version must be MAJOR.MINOR.PATCH")

    api_version = data.get("api_version")
    _require(api_version in SUPPORTED_API_VERSIONS, "unsupported-api-version",
             f"{source} api_version {api_version!r} not in supported {sorted(SUPPORTED_API_VERSIONS)}")

    types = data.get("types")
    _require(isinstance(types, list) and bool(types), "invalid-capability-type",
             f"{source} types must be a non-empty list")
    for cap in types:
        _require(cap in CAPABILITY_TYPES, "invalid-capability-type",
                 f"{source} declares unknown capability type {cap!r}")
    _require(len(set(types)) == len(types), "invalid-capability-type",
             f"{source} lists a duplicate capability type")
    type_set = set(types)

    # Manifest fields are gated by capability type so a declarative capability cannot spill an
    # executable/scope/content surface it did not declare (fail-closed capability confinement).
    def _field_requires(field: str, allowed: set[str]) -> None:
        if data.get(field) and not (type_set & allowed):
            _require(False, "capability-field-mismatch",
                     f"{source} declares '{field}' but none of its capability types {sorted(type_set)} "
                     f"may contribute it (allowed: {sorted(allowed)})")

    _field_requires("commands", {"gate", "command"})
    _field_requires("scopes", {"pack", "gate"})
    _field_requires("contributes", {"pack", "integration"})
    _field_requires("config_schema", {"pack", "gate", "command"})
    _field_requires("defaults", {"pack", "gate", "command"})
    _field_requires("integration", {"integration"})
    _field_requires("relations", {"pack"})
    if data.get("integration") is not None:
        _validate_integration_descriptor(data["integration"], source=source)

    root = data.get("root", str(Path(".ai/extensions") / (ext_id or "")))
    _require(_contained_relative(root), "out-of-authority-root",
             f"{source} root {root!r} must be a repository-relative contained path")

    # A `gate` REQUIRES a loaded Python entrypoint (its cmd_verify protocol is validated at load).
    # A `command` runs a declared external executable and does NOT need a Python entrypoint, so its
    # entrypoint is OPTIONAL (declaring a mandatory-but-never-loaded entrypoint was a contradiction).
    # Any entrypoint that IS declared must resolve inside the declared root and match a listed type.
    entrypoints = data.get("entrypoints", {})
    _require(isinstance(entrypoints, dict), "invalid-entrypoints",
             f"{source} entrypoints must be an object")
    if "gate" in type_set:
        ep = entrypoints.get("gate")
        _require(isinstance(ep, str) and bool(ep), "missing-entrypoint",
                 f"{source} capability 'gate' requires an entrypoint")
    for cap, ep in entrypoints.items():
        _require(cap in EXECUTABLE_CAPABILITIES,
                 "invalid-entrypoints", f"{source} declares an entrypoint for non-executable {cap!r}")
        _require(cap in type_set, "invalid-entrypoints",
                 f"{source} declares an entrypoint for {cap!r}, which is not in its capability types {sorted(type_set)}")
        _require(isinstance(ep, str) and bool(ep), "invalid-entrypoints",
                 f"{source} entrypoint for {cap!r} must be a non-empty string")
        _require(_contained_relative(ep) and (Path(root) / ep).parts[:len(Path(root).parts)] == Path(root).parts,
                 "entrypoint-out-of-root",
                 f"{source} entrypoint {ep!r} for {cap!r} must resolve inside root {root!r}")

    for list_field in ("scopes", "dependencies", "conflicts", "before", "after",
                       "read_roots", "write_roots", "executables", "platforms"):
        value = data.get(list_field, [])
        _require(isinstance(value, list) and all(isinstance(v, str) for v in value),
                 "invalid-field", f"{source} {list_field} must be a list of strings")

    for scope in data.get("scopes", []):
        _require(bool(scope), "invalid-scope", f"{source} declares an empty scope value")

    for root_field in ("read_roots", "write_roots"):
        for entry in data.get(root_field, []):
            _require(_contained_relative(entry), "out-of-authority-write-root"
                     if root_field == "write_roots" else "out-of-authority-read-root",
                     f"{source} {root_field} entry {entry!r} must be repository-relative and contained")

    commands = data.get("commands", {})
    _require(isinstance(commands, dict), "invalid-commands", f"{source} commands must be an object")
    declared_executables = set(data.get("executables", []))
    for name, spec in commands.items():
        _require(isinstance(name, str) and bool(name), "invalid-commands",
                 f"{source} command name must be a non-empty string")
        _require(isinstance(spec, dict), "invalid-commands", f"{source} command {name!r} must be an object")
        unknown_spec = sorted(set(spec) - {"executable", "args", "inputs", "outputs", "write_roots", "timeout_seconds"})
        _require(not unknown_spec, "invalid-commands",
                 f"{source} command {name!r} declares unknown field(s): {', '.join(unknown_spec)}")
        # Every command must declare the external executable it runs, and it must be in the
        # manifest's declared executables set (no undeclared runtime executable).
        exe = spec.get("executable")
        _require(isinstance(exe, str) and bool(exe), "undeclared-command",
                 f"{source} command {name!r} must declare a non-empty 'executable'")
        _require(exe in declared_executables, "undeclared-command",
                 f"{source} command {name!r} uses executable {exe!r} not in declared executables")
        # Nested list fields must be actual lists of strings (a bare string is a fail-closed error,
        # never silently iterated character-by-character).
        for nested in ("args", "inputs", "outputs", "write_roots"):
            value = spec.get(nested, [])
            _require(isinstance(value, list) and all(isinstance(v, str) and v for v in value),
                     "invalid-commands",
                     f"{source} command {name!r} field {nested!r} must be a list of non-empty strings")
        # A command's declared input/output/write authority (write_roots within the manifest's own
        # write_roots, inputs within read_roots, file outputs within the command's write_roots) is
        # validated here AND re-checked at dispatch from the resolved manifest through the same
        # helper, so a command cannot claim — or later mutate in — authority the manifest never
        # declared.
        _validate_command_authority(name, spec, data, source)
        timeout = spec.get("timeout_seconds")
        if timeout is not None:
            _require(isinstance(timeout, (int, float)) and not isinstance(timeout, bool) and timeout > 0,
                     "invalid-commands", f"{source} command {name!r} timeout_seconds must be a positive number")

    # Declarative content contributions (design.md "Content Contributions"). `files` copies raw bytes
    # to a generated destination; `content` ADDITIONALLY composes typed rules/workflows/skills into the
    # generated registry/catalogs (indexed by frontmatter id) and materializes templates that may
    # consume cross-pack defaults. Both `from` values are relative to the extension root (on-disk
    # containment checked at discovery) and both `to` values are repository-relative generated
    # destinations sync writes (manifest-tracked, so enable adds and disable prunes). No executable
    # content — contributions are pure data.
    contributes = data.get("contributes", {})
    _require(isinstance(contributes, dict), "invalid-contributes", f"{source} contributes must be an object")
    _require(not (set(contributes) - {"files", "content"}), "invalid-contributes",
             f"{source} contributes may only declare 'files' and 'content'")
    manifest_write_roots = [Path(w) for w in data.get("write_roots", [])]

    def _destination_within_write_roots(to_value: str, what: str) -> None:
        # A generated destination must fall within the manifest's OWN declared write authority — a pack
        # cannot write outside the roots it declared it would write.
        to_path = Path(to_value)
        within = any(to_path == wr or wr in to_path.parents for wr in manifest_write_roots)
        _require(within, "out-of-authority-write-root",
                 f"{source} {what} destination {to_value!r} is not within the manifest's "
                 f"declared write_roots {data.get('write_roots', [])}")

    files = contributes.get("files", [])
    _require(isinstance(files, list), "invalid-contributes", f"{source} contributes.files must be a list")
    for entry in files:
        _require(isinstance(entry, dict) and set(entry) == {"from", "to"}, "invalid-contributes",
                 f"{source} contributes.files entry must be an object with exactly 'from' and 'to'")
        _require(isinstance(entry["from"], str) and _contained_relative(entry["from"]),
                 "invalid-contributes", f"{source} contributes.files 'from' must be a contained relative path")
        _require(isinstance(entry["to"], str) and _contained_relative(entry["to"]),
                 "invalid-contributes", f"{source} contributes.files 'to' must be a contained relative path")
        _destination_within_write_roots(entry["to"], "contribution")

    # Content is COMPOSED (design.md "Content Contributions"), not merely copied, and only a `pack` may
    # contribute it. The public entry is exactly {kind, from} — rules/workflows/skills are identified by
    # their frontmatter `id` (read at composition time) and indexed into the registry; templates are
    # materialized under the pack's declared write authority. There is no manifest-declared destination.
    content = contributes.get("content", [])
    _require(isinstance(content, list), "invalid-contributes", f"{source} contributes.content must be a list")
    _require(not content or "pack" in type_set, "capability-field-mismatch",
             f"{source} declares contributes.content but none of its capability types {sorted(type_set)} "
             f"is 'pack'")
    for entry in content:
        _require(isinstance(entry, dict) and set(entry) == {"kind", "from"}, "invalid-contributes",
                 f"{source} contributes.content entry must be an object with exactly 'kind' and 'from'")
        _require(entry["kind"] in _CONTENT_KINDS, "invalid-contributes",
                 f"{source} contributes.content kind {entry['kind']!r} must be one of {list(_CONTENT_KINDS)}")
        _require(isinstance(entry["from"], str) and _contained_relative(entry["from"]),
                 "invalid-contributes", f"{source} contributes.content 'from' must be a contained relative path")
        # Composed content is materialized under the pack's declared write authority (rules/workflows/
        # skills are ALSO indexed into the registry by frontmatter id), so any content requires a write root.
        _require(bool(manifest_write_roots), "out-of-authority-write-root",
                 f"{source} content {entry['from']!r} requires a declared write_root to materialize into")

    # Relation operations (design.md "Relation Operations"): each declares how this pack's contribution
    # composes onto a target another contribution owns. Only a `pack` may declare them (gated above via
    # _field_requires). Structure is validated here; target existence, precedence, and ambiguity are
    # resolved fail-closed in resolve() where the full enabled composition is known.
    relations = data.get("relations", [])
    _require(isinstance(relations, list), "invalid-relation", f"{source} relations must be a list")
    for entry in relations:
        _require(isinstance(entry, dict) and set(entry) == {"op", "kind", "target"}, "invalid-relation",
                 f"{source} relation entry must be an object with exactly 'op', 'kind', and 'target'")
        _require(entry["op"] in _RELATION_OPS, "invalid-relation",
                 f"{source} relation op {entry['op']!r} must be one of {list(_RELATION_OPS)}")
        _require(entry["kind"] in _RELATION_KINDS, "invalid-relation",
                 f"{source} relation kind {entry['kind']!r} must be one of {list(_RELATION_KINDS)}")
        _require(isinstance(entry["target"], str) and bool(entry["target"]), "invalid-relation",
                 f"{source} relation target must be a non-empty string")

    # config_schema declares each configuration key's VALUE TYPE; defaults are validated against it.
    # config_schema maps each non-empty key to exactly one supported type token; an unknown token or
    # non-string schema value fails closed (design.md "Manifest schema").
    config_schema = data.get("config_schema", {})
    _require(isinstance(config_schema, dict), "invalid-config-schema",
             f"{source} config_schema must be an object")
    for key, token in config_schema.items():
        _require(isinstance(key, str) and bool(key), "invalid-config-schema",
                 f"{source} config_schema declares an empty or non-string key")
        _require(isinstance(token, str) and token in _CONFIG_TYPE_TOKENS, "invalid-config-schema",
                 f"{source} config_schema key {key!r} type {token!r} must be one of "
                 f"{list(_CONFIG_TYPE_TOKENS)}")
    # Every default key must be declared in config_schema, and every default value must satisfy its
    # declared type (a boolean field given a string is rejected).
    defaults = data.get("defaults", {})
    _require(isinstance(defaults, dict), "invalid-defaults", f"{source} defaults must be an object")
    for key, value in defaults.items():
        _require(key in config_schema, "invalid-defaults",
                 f"{source} defaults key {key!r} is not declared in config_schema")
        _require(_config_value_matches(value, config_schema[key]), "invalid-defaults",
                 f"{source} default {key!r}={value!r} is not a {config_schema[key]}")
    # A command consumes configuration only through `{config:<key>}` placeholders in its declared
    # args. Every referenced key must be DECLARED in config_schema (design.md "Required consumption
    # path"). A default is NOT required — a key may be supplied solely through project
    # `extensions.config` — so whether every referenced key resolves to an effective value is checked
    # in resolve(), after effective configuration is composed.
    for name, spec in commands.items():
        for arg in spec.get("args", []):
            for key in _CONFIG_PLACEHOLDER_RE.findall(arg):
                _require(key in config_schema, "invalid-commands",
                         f"{source} command {name!r} arg references undeclared config key {key!r}")

    priority = data.get("priority", 100)
    _require(isinstance(priority, int) and not isinstance(priority, bool),
             "invalid-priority", f"{source} priority must be an integer")

    platforms = data.get("platforms", [])
    for plat in platforms:
        _require(plat in ("nt", "posix", "any"), "invalid-platform",
                 f"{source} platform {plat!r} must be one of nt, posix, any")

    normalized = dict(data)
    normalized["root"] = root
    normalized["entrypoints"] = entrypoints
    normalized.setdefault("scopes", [])
    normalized.setdefault("dependencies", [])
    normalized.setdefault("conflicts", [])
    normalized.setdefault("before", [])
    normalized.setdefault("after", [])
    normalized.setdefault("priority", priority)
    normalized.setdefault("commands", {})
    normalized.setdefault("contributes", {})
    normalized.setdefault("relations", [])
    return normalized


# ---------------------------------------------------------------------------
# Discovery and configuration
# ---------------------------------------------------------------------------


def read_extensions_config(config: dict[str, Any]) -> dict[str, Any]:
    """Extract and validate the required ``extensions`` block. A missing block fails closed —
    the zero-extension form is an EXPLICIT ``extensions: {enabled: []}`` — and a present-but-
    malformed block fails closed. No roster is ever invented to cover a missing or invalid
    declaration."""
    block = config.get("extensions")
    _require(block is not None, "missing-extensions-config",
             "config.extensions is required; declare an explicit roster (enabled: [] for a "
             "zero-extension project). No default roster is ever substituted.")
    _require(isinstance(block, dict), "invalid-extensions-config",
             "config.extensions must be a mapping")
    unknown = sorted(set(block) - {"roots", "enabled", "config"})
    _require(not unknown, "invalid-extensions-config",
             f"config.extensions declares unknown field(s): {', '.join(unknown)}")

    roots = block.get("roots", list(_DEFAULT_ROOTS))
    _require(isinstance(roots, list) and all(isinstance(r, str) for r in roots) and bool(roots),
             "invalid-extensions-config", "config.extensions.roots must be a non-empty list of strings")
    for r in roots:
        _require(_contained_relative(r), "invalid-extensions-config",
                 f"config.extensions.roots entry {r!r} must be repository-relative and contained")

    _require("enabled" in block, "missing-extensions-config",
             "config.extensions.enabled is required; declare an explicit roster (enabled: [] for a "
             "zero-extension project). A missing enabled roster is never defaulted to empty.")
    enabled = block["enabled"]
    _require(isinstance(enabled, list) and all(isinstance(e, str) for e in enabled),
             "invalid-extensions-config", "config.extensions.enabled must be a list of strings")
    _require(len(set(enabled)) == len(enabled), "duplicate-enabled-id",
             "config.extensions.enabled contains a duplicate id")

    # Optional per-extension configuration overrides (design.md "Project values and effective
    # values"). Structure is validated here; unknown/disabled ids, undeclared keys, and mistyped
    # values are validated against the resolved catalog in `resolve` (they need the manifests).
    config_values = block.get("config", {})
    _require(isinstance(config_values, dict), "invalid-extensions-config",
             "config.extensions.config must be a mapping of extension id to a value mapping")
    for cfg_id, values in config_values.items():
        _require(isinstance(cfg_id, str) and bool(cfg_id), "invalid-extensions-config",
                 "config.extensions.config keys must be non-empty extension ids")
        _require(isinstance(values, dict), "invalid-extensions-config",
                 f"config.extensions.config.{cfg_id} must be a value mapping")
    return {"roots": roots, "enabled": enabled, "config": config_values}


def validate_entrypoint_paths(manifest: dict[str, Any], repo_root: Path, source: str) -> None:
    """Resolve the declared code root and each executable entrypoint on disk and confirm they
    stay inside the repository and inside the declared root, with no symlink/`..` escape.

    Security model (owner-approved, task_181 re-review): manifests are pure data and only
    gate/command entrypoints execute, and those entrypoints are TRUSTED first-party code enabled
    solely by explicit owner config — this is path containment and tamper-evidence, not an
    in-process sandbox around trusted code. read_roots/write_roots are declared authority for
    audit, not a runtime jail. `Path.resolve()` collapses symlinks, so comparing the resolved
    target against the resolved root catches symlink and `..` escapes."""
    repo_resolved = repo_root.resolve()
    root_dir = (repo_root / manifest["root"]).resolve()
    _require(root_dir == repo_resolved or str(root_dir).startswith(str(repo_resolved) + os.sep),
             "out-of-authority-root", f"{source} root {manifest['root']!r} resolves outside the repository")
    for cap, ep in manifest.get("entrypoints", {}).items():
        target = (repo_root / manifest["root"] / ep).resolve()
        _require(str(target).startswith(str(root_dir) + os.sep) or target == root_dir,
                 "entrypoint-out-of-root",
                 f"{source} entrypoint {ep!r} for {cap!r} resolves outside its declared root {manifest['root']!r}")
        _require(target.is_file(), "missing-entrypoint-file",
                 f"{source} entrypoint {ep!r} for {cap!r} does not resolve to a file on disk")
    # Contribution AND content source files must resolve to a real file inside the declared root (no
    # symlink/`..` escape). Both are read at composition time, so both are disk-checked here.
    contributes = manifest.get("contributes", {})
    for entry in [*contributes.get("files", []), *contributes.get("content", [])]:
        src = (repo_root / manifest["root"] / entry["from"]).resolve()
        _require(str(src).startswith(str(root_dir) + os.sep) or src == root_dir,
                 "contribution-out-of-root",
                 f"{source} contribution source {entry['from']!r} resolves outside its declared root {manifest['root']!r}")
        _require(src.is_file(), "missing-contribution-file",
                 f"{source} contribution source {entry['from']!r} does not resolve to a file on disk")


def validate_integration_paths(manifest: dict[str, Any], repo_root: Path, source: str) -> None:
    """For an `integration` manifest, prove BEFORE any mutation that every render OUTPUT path is
    within the manifest's declared write_roots, every declared `.ai` INPUT source is within its
    read_roots, and every referenced template resolves to a real file inside the extension root
    (no `..`/symlink escape). Fail-closed with a named reason. `.ai` source EXISTENCE is not checked
    here — it is a per-repo render-time concern — but templates ship WITH the extension, so they
    must exist at discovery."""
    integration = manifest.get("integration")
    if integration is None:
        return
    root = manifest["root"]
    root_dir = (repo_root / root).resolve()
    write_roots = [Path(w) for w in manifest.get("write_roots", [])]
    read_roots = [Path(r) for r in manifest.get("read_roots", [])]

    def _within(value: str, roots: list[Path]) -> bool:
        candidate = Path(value)
        return any(candidate == r or r in candidate.parents for r in roots)

    def out(path_value: str, what: str) -> None:
        _require(_contained_relative(path_value) and _within(path_value, write_roots),
                 "out-of-authority-write-root",
                 f"{source} {what} {path_value!r} is not within declared write_roots {manifest.get('write_roots', [])}")

    def read(path_value: str, what: str) -> None:
        _require(_contained_relative(path_value) and _within(path_value, read_roots),
                 "out-of-authority-read-root",
                 f"{source} {what} {path_value!r} is not within declared read_roots {manifest.get('read_roots', [])}")

    def template(ref: str, what: str) -> None:
        target = (repo_root / root / ref).resolve()
        _require(str(target).startswith(str(root_dir) + os.sep), "template-out-of-root",
                 f"{source} {what} template {ref!r} resolves outside its extension root {root!r}")
        _require(target.is_file(), "missing-template",
                 f"{source} {what} template {ref!r} does not resolve to a file on disk")

    if integration.get("agent_document"):
        read(integration["agent_document"], "agent_document")
    if integration.get("command_catalog_source"):
        read(integration["command_catalog_source"], "command_catalog_source")
    for name, spec in integration.get("catalogs", {}).items():
        read(spec["source_root"], f"catalog {name!r} source_root")
    for artifact in integration["render"]["artifacts"]:
        kind = artifact["kind"]
        if kind in ("marker_document", "support_document", "command_document"):
            out(artifact["path"], f"{kind} output")
            template(artifact["template"], kind)
        elif kind == "settings_document":
            out(artifact["path"], "settings_document output")
        elif kind == "rules_tree":
            out(artifact["destination"], "rules_tree destination")
            read(artifact["source_root"], "rules_tree source_root")
        elif kind == "skills_tree":
            out(artifact["destination"], "skills_tree destination")
            read(artifact["source_root"], "skills_tree source_root")
            out(artifact["index"]["path"], "skills index output")
            template(artifact["index"]["template"], "skills index")
            if artifact.get("blueprint_source"):
                read(artifact["blueprint_source"], "blueprint_source")
                out(artifact["blueprint_destination"], "blueprint_destination")


def discover_manifests(repo_root: Path, roots: list[str]) -> dict[str, dict[str, Any]]:
    """Discover and validate every manifest under the configured roots. Discovery never
    enables anything; it only builds the catalog the explicit enabled list selects from."""
    catalog: dict[str, dict[str, Any]] = {}
    seen_paths: dict[str, str] = {}

    for root in roots:
        root_dir = repo_root / root
        if not root_dir.is_dir():
            continue
        for manifest_path in sorted(root_dir.glob("*/extension.json")):
            folder_id = manifest_path.parent.name
            source = manifest_path.relative_to(repo_root).as_posix()
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise RegistryError("unreadable-manifest", f"{source}: {error}") from error
            manifest = validate_manifest(data, source=source, folder_id=folder_id)
            validate_entrypoint_paths(manifest, repo_root, source)
            validate_integration_paths(manifest, repo_root, source)
            ext_id = manifest["id"]
            _require(ext_id not in catalog, "duplicate-id",
                     f"extension id {ext_id!r} declared in both {seen_paths.get(ext_id)} and {source}")
            catalog[ext_id] = manifest
            seen_paths[ext_id] = source
    return catalog


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def _topological_order(enabled: list[dict[str, Any]]) -> list[str]:
    """Deterministic order: (priority asc, id asc) as the stable base, refined by explicit
    before/after edges via Kahn's algorithm. A cycle fails closed."""
    ids = {ext["id"] for ext in enabled}
    by_id = {ext["id"]: ext for ext in enabled}
    edges: dict[str, set[str]] = {i: set() for i in ids}  # edge u->v means u before v
    indegree: dict[str, int] = {i: 0 for i in ids}

    def add_edge(u: str, v: str) -> None:
        if v not in edges[u]:
            edges[u].add(v)
            indegree[v] += 1

    for ext in enabled:
        i = ext["id"]
        for later in ext.get("before", []):
            add_edge(i, later)
        for earlier in ext.get("after", []):
            add_edge(earlier, i)
        # A dependency is also an ordering edge (the dependency composes before its dependent),
        # so a dependency cycle surfaces here as a composition cycle rather than resolving quietly.
        for dep in ext.get("dependencies", []):
            add_edge(dep, i)

    def rank(i: str) -> tuple[int, str]:
        return (int(by_id[i].get("priority", 100)), i)

    ready = sorted((i for i in ids if indegree[i] == 0), key=rank)
    order: list[str] = []
    while ready:
        node = ready.pop(0)
        order.append(node)
        for nxt in sorted(edges[node], key=rank):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                ready.append(nxt)
        ready.sort(key=rank)

    _require(len(order) == len(ids), "composition-cycle",
             f"extension ordering has a cycle among {sorted(ids - set(order))}")
    return order


def _compose_default_value(base: Any, modifier: Any, op: str, key: str) -> Any:
    """Compose one pack-defaults value onto an existing one under a relation op. `replace` supersedes;
    `prepend`/`append`/`wrap` compose two lists or two strings; any other type pairing fails closed."""
    if op == "replace":
        return modifier
    if isinstance(base, list) and isinstance(modifier, list):
        if op == "prepend":
            return [*modifier, *base]
        if op == "append":
            return [*base, *modifier]
        if op == "wrap":
            return [*modifier, *base, *modifier]
    if isinstance(base, str) and isinstance(modifier, str):
        if op == "prepend":
            return modifier + base
        if op == "append":
            return base + modifier
        if op == "wrap":
            return modifier + base + modifier
    raise RegistryError("invalid-relation",
                        f"relation op {op!r} on defaults key {key!r} requires two lists or two strings, "
                        f"not {type(base).__name__} and {type(modifier).__name__}")


def _precedence_pairs(enabled: list[dict[str, Any]]) -> set[tuple[str, str]]:
    """Return ``{(a, b)}`` meaning ``a`` EXPLICITLY precedes ``b`` through a declared before/after or
    dependency edge (transitively). The ``(priority, id)`` tie-breaker is deliberately EXCLUDED: it
    yields a deterministic total ORDER, but it is not a declared precedence and must never silently
    resolve a conflict or a relation race (design.md "Determinism and Fail-Closed Law")."""
    ids = [ext["id"] for ext in enabled]
    edges: dict[str, set[str]] = {i: set() for i in ids}  # u -> v : u precedes v
    for ext in enabled:
        i = ext["id"]
        for later in ext.get("before", []):
            edges.setdefault(i, set()).add(later)
        for earlier in ext.get("after", []):
            edges.setdefault(earlier, set()).add(i)
        for dep in ext.get("dependencies", []):
            edges.setdefault(dep, set()).add(i)
    pairs: set[tuple[str, str]] = set()
    for start in ids:
        stack = list(edges.get(start, ()))
        seen: set[str] = set()
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            pairs.add((start, node))
            stack.extend(edges.get(node, ()))
    return pairs


def _ordered_by_precedence(pids: list[str], precedes: set[tuple[str, str]]) -> list[str] | None:
    """Return ``pids`` in a TOTAL explicit-precedence order, or ``None`` if any pair is unordered — in
    which case the composition would depend on the tie-breaker, not a declared precedence, and the
    caller must fail closed (``ambiguous-relation``)."""
    remaining = list(pids)
    order: list[str] = []
    while remaining:
        heads = [p for p in remaining if not any((q, p) in precedes for q in remaining if q != p)]
        if len(heads) != 1:
            return None
        order.append(heads[0])
        remaining.remove(heads[0])
    return order


def _unique_precedence_max(pids: list[str], precedes: set[tuple[str, str]]) -> str | None:
    """Return the single pid that every OTHER pid explicitly precedes, else ``None`` (no unique max)."""
    maxes = [p for p in pids if all((q, p) in precedes for q in pids if q != p)]
    return maxes[0] if len(maxes) == 1 else None


def _compose_defaults(order: list[str], by_id: dict[str, dict[str, Any]],
                      precedes: set[tuple[str, str]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Compose every enabled pack's ``defaults`` into one cross-pack namespace (design.md "Defaults
    Contributions"). Differing values for a key are resolved by an explicit ``before``/``after``
    precedence (the higher-precedence value wins, overridden origins recorded) or by a declared
    relation; genuinely unordered differing values fail closed with ``pack-defaults-conflict``. Values
    live in the manifest, so this composes directly and returns (composed, report)."""
    packs = [pid for pid in order if "pack" in by_id[pid]["types"]]
    relation_ops: dict[str, dict[str, str]] = {}
    for pid in packs:
        for rel_entry in by_id[pid].get("relations", []):
            if rel_entry["kind"] == "defaults":
                target_ops = relation_ops.setdefault(rel_entry["target"], {})
                _require(pid not in target_ops, "ambiguous-relation",
                         f"pack {pid!r} declares more than one defaults relation targeting "
                         f"{rel_entry['target']!r}; a single pack's operations for one target are ambiguous")
                target_ops[pid] = rel_entry["op"]
    contributors: dict[str, list[str]] = {}
    values: dict[tuple[str, str], Any] = {}
    for pid in packs:
        for key, value in by_id[pid].get("defaults", {}).items():
            contributors.setdefault(key, []).append(pid)
            values[(key, pid)] = value

    composed: dict[str, Any] = {}
    report: list[dict[str, Any]] = []
    for key in sorted(set(contributors) | set(relation_ops)):
        contribs = contributors.get(key, [])
        ops = relation_ops.get(key, {})
        for rel_pid in ops:
            _require(rel_pid in contribs, "unresolved-relation-target",
                     f"pack {rel_pid!r} declares a defaults relation on key {key!r} it does not contribute")
        _require(contribs, "unresolved-relation-target",
                 f"a defaults relation targets key {key!r} that no pack contributes")
        bases = [p for p in contribs if p not in ops]
        modifiers = [p for p in contribs if p in ops]
        _require(bases, "unresolved-relation-target",
                 f"defaults key {key!r} has only relation modifiers and no base value to modify")
        # A replace modifier discards the base value, so — like content — differing bases are permitted
        # when a replace supersedes them; otherwise an explicit before/after precedence must pick one.
        has_replace = any(ops[p] == "replace" for p in modifiers)
        overridden: list[str] = []
        if len(bases) == 1:
            base_pid = bases[0]
        elif all(values[(key, p)] == values[(key, bases[0])] for p in bases):
            base_pid = bases[0]  # identical values are idempotent, not a conflict
        elif has_replace:
            base_pid = bases[0]  # differing bases are all superseded by the replace
            overridden = [p for p in bases if p != base_pid]
        else:
            base_pid = _unique_precedence_max(bases, precedes)
            _require(base_pid is not None, "pack-defaults-conflict",
                     f"defaults key {key!r} has differing values from packs {sorted(bases)} with no "
                     f"before/after precedence or relation to resolve which wins")
            overridden = [p for p in bases if p != base_pid]
        value = values[(key, base_pid)]
        effective_origin = base_pid
        ordered_mods = modifiers if len(modifiers) <= 1 else _ordered_by_precedence(modifiers, precedes)
        _require(ordered_mods is not None, "ambiguous-relation",
                 f"defaults key {key!r} has relation modifiers {sorted(modifiers)} with no total "
                 f"before/after precedence, so their composition is non-deterministic")
        # The precedence chain records EVERY effective contributor in resolved order — the bases in
        # resolution order (which reflects before/after; the last is the winner) followed by the ordered
        # relation modifiers — so a superseded base and a before/after win are both auditable (AC3).
        precedence: list[dict[str, str]] = [{"origin": b, "op": "base"} for b in bases]
        for mod_pid in ordered_mods:
            op = ops[mod_pid]
            value = _compose_default_value(value, values[(key, mod_pid)], op, key)
            precedence.append({"origin": mod_pid, "op": op})
            if op == "replace":
                effective_origin = mod_pid
                overridden = [p for p in bases if p != base_pid] + [base_pid]
        composed[key] = value
        report.append({"key": key, "value": value, "origin": effective_origin,
                       "precedence": precedence, "overridden": sorted(set(overridden))})
    return composed, report


CORE_ORIGIN = "core"


def compose_content(content_records: list[dict[str, Any]], core_records: list[dict[str, Any]],
                    content_relations: list[tuple[str, str, str]],
                    precedes: set[tuple[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """PURE id-based content composition (no I/O — the maw_core boundary forbids reading frontmatter
    here, so the ai_plane sync/report layer reads each document's frontmatter and supplies records).

    ``content_records``: ``[{origin, kind, id, type, from}]`` for every enabled rules/workflows/skills
    pack content document (templates carry no frontmatter and are excluded). ``core_records``:
    ``[{id, type, path}]`` for the canonical/CORE registry documents, each a base contributor with the
    reserved origin ``core`` so a pack ``replace`` can supersede a core id (design.md "Content
    Contributions"). ``content_relations``: ``[(pack_id, target_id, op)]``. ``precedes``: explicit-
    precedence pairs.

    Content is identified by its frontmatter ``id``. Multiple documents sharing an ``id`` require a
    declared relation to compose; modifiers apply to the one base in a total explicit-precedence order.
    A core document may only be SUPERSEDED by ``replace`` (it has no write authority for a partial
    merge). Fails closed: ``invalid-content`` (kind/type mismatch or mixed kinds), ``content-id-conflict``
    (unrelated duplicate ids, including vs a core doc, with no resolving replace), ``unresolved-relation-
    target``, ``ambiguous-relation`` (unordered modifiers OR a single pack's duplicate operations on one
    target). Returns (content_plan, content_report, superseded_core_ids); only ids a pack contributes or
    a relation targets are composed — pure-core ids stay with the canonical scan."""
    for rec in content_records:
        expected = _CONTENT_KIND_TYPE[rec["kind"]]
        _require(rec["type"] == expected, "invalid-content",
                 f"pack {rec['origin']!r} content {rec['from']!r} declares kind {rec['kind']!r} but its "
                 f"frontmatter type is {rec['type']!r} (a {rec['kind']!r} document must be type {expected!r})")

    core_by_id = {rec["id"]: rec for rec in core_records}
    by_id: dict[str, list[dict[str, Any]]] = {}
    for rec in content_records:
        by_id.setdefault(rec["id"], []).append(rec)
    relation_ops: dict[str, dict[str, str]] = {}
    for pack_id, target_id, op in content_relations:
        target_ops = relation_ops.setdefault(target_id, {})
        _require(pack_id not in target_ops, "ambiguous-relation",
                 f"pack {pack_id!r} declares more than one content relation targeting {target_id!r}; "
                 f"a single pack's operations for one target are ambiguous")
        target_ops[pack_id] = op

    content_plan: list[dict[str, Any]] = []
    content_report: list[dict[str, Any]] = []
    superseded_core_ids: list[str] = []
    for cid in sorted(set(by_id) | set(relation_ops)):
        recs = by_id.get(cid, [])
        ops = relation_ops.get(cid, {})
        pack_origins = {r["origin"] for r in recs}
        for rel_pid in ops:
            _require(rel_pid in pack_origins, "unresolved-relation-target",
                     f"pack {rel_pid!r} declares a content relation on id {cid!r} it does not contribute")
        _require(recs, "unresolved-relation-target",
                 f"a content relation targets id {cid!r} that no pack contributes")
        _require(len({r["kind"] for r in recs}) == 1, "invalid-content",
                 f"content id {cid!r} is contributed as multiple kinds {sorted({r['kind'] for r in recs})}")
        kind = recs[0]["kind"]
        rec_by_origin: dict[str, dict[str, Any]] = {r["origin"]: r for r in recs}
        core_rec = core_by_id.get(cid)
        if core_rec is not None:
            rec_by_origin[CORE_ORIGIN] = {"origin": CORE_ORIGIN, "from": core_rec["path"]}
        contributors = [r["origin"] for r in recs] + ([CORE_ORIGIN] if core_rec is not None else [])
        bases = [o for o in contributors if o not in ops]  # a core document never declares a relation
        modifiers = [o for o in contributors if o in ops]
        _require(bases, "unresolved-relation-target",
                 f"content id {cid!r} has only relation modifiers and no base document to modify")
        ordered_mods = modifiers if len(modifiers) <= 1 else _ordered_by_precedence(modifiers, precedes)
        _require(ordered_mods is not None, "ambiguous-relation",
                 f"content id {cid!r} has relation modifiers {sorted(modifiers)} with no total "
                 f"before/after precedence, so their composition is non-deterministic")
        # A `replace` discards ALL accumulated content (every base and every earlier modifier), so its
        # position makes prior ordering irrelevant. Multiple bases are therefore permitted IFF the chain
        # contains a replace that supersedes them; prepend/append/wrap cannot resolve multiple bases
        # (their unordered combination would be non-deterministic).
        replace_positions = [i for i, m in enumerate(ordered_mods) if ops[m] == "replace"]
        _require(len(bases) == 1 or replace_positions, "content-id-conflict",
                 f"content id {cid!r} is contributed by unrelated sources {sorted(bases)} (a second pack or "
                 f"a core document) with no resolving replace relation")
        # A core document has no pack write authority, so it may only be fully SUPERSEDED by a replace.
        if CORE_ORIGIN in bases:
            _require(bool(replace_positions), "invalid-relation",
                     f"content id {cid!r} collides with a core document; a pack may only 'replace' it, not "
                     f"prepend/append/wrap or coexist as an unrelated base")
            superseded_core_ids.append(cid)
        if replace_positions:
            # The last replace is the effective base; everything before it (all bases and earlier
            # modifiers) is superseded, and post-replace modifiers apply to the replacement.
            start = ordered_mods[replace_positions[-1]]
            tail = ordered_mods[replace_positions[-1] + 1:]
            layers = [{"origin": start, "from": rec_by_origin[start]["from"], "op": "base"}]
            for mod in tail:
                layers.append({"origin": mod, "from": rec_by_origin[mod]["from"], "op": ops[mod]})
            effective_origin = start
        else:
            base = bases[0]
            layers = [{"origin": base, "from": rec_by_origin[base]["from"], "op": "base"}]
            for mod in ordered_mods:
                layers.append({"origin": mod, "from": rec_by_origin[mod]["from"], "op": ops[mod]})
            effective_origin = base
        content_plan.append({"id": cid, "kind": kind, "effective_origin": effective_origin, "layers": layers})
        # Full resolved chain: EVERY base (superseded origins visible after a replace) then the ordered
        # relation modifiers, so every effective content origin AND its precedence is auditable (AC3).
        precedence = [{"origin": b, "op": "base"} for b in bases] + [{"origin": m, "op": ops[m]} for m in ordered_mods]
        content_report.append({"kind": kind, "id": cid, "origin": effective_origin, "precedence": precedence})
    return content_plan, content_report, superseded_core_ids


def resolve(config: dict[str, Any], repo_root: Path, *, platform_name: str | None = None) -> dict[str, Any]:
    """Resolve the enabled extensions into a deterministic composition. Raises RegistryError
    with a named reason on any fail-closed condition. Returns a report with ordering, the
    effective scope vocabulary, effective commands, per-type contributions, and gate ids."""
    ext_config = read_extensions_config(config)
    catalog = discover_manifests(repo_root, ext_config["roots"])
    enabled_ids = ext_config["enabled"]

    enabled: list[dict[str, Any]] = []
    for ext_id in enabled_ids:
        manifest = catalog.get(ext_id)
        _require(manifest is not None, "unknown-enabled-id",
                 f"config enables {ext_id!r} but no manifest declares it under the configured roots")
        enabled.append(manifest)

    enabled_set = {ext["id"] for ext in enabled}
    for ext in enabled:
        for dep in ext.get("dependencies", []):
            _require(dep in enabled_set, "missing-dependency",
                     f"extension {ext['id']!r} depends on {dep!r}, which is not enabled")
        for conflict in ext.get("conflicts", []):
            _require(conflict not in enabled_set, "conflict",
                     f"extension {ext['id']!r} conflicts with enabled extension {conflict!r}")
        # An ordering constraint that names an id not in the enabled roster cannot be honored;
        # rather than silently drop it, fail closed with a named reason.
        for field in ("before", "after"):
            for target in ext.get(field, []):
                _require(target in enabled_set, "unresolved-constraint",
                         f"extension {ext['id']!r} {field} references {target!r}, which is not enabled")

    if platform_name is not None:
        for ext in enabled:
            platforms = ext.get("platforms", [])
            if platforms and "any" not in platforms:
                _require(platform_name in platforms, "unsupported-platform",
                         f"extension {ext['id']!r} does not support platform {platform_name!r}")

    order = _topological_order(enabled)
    by_id = {ext["id"]: ext for ext in enabled}

    scopes: list[str] = list(CORE_SCOPES)
    for ext_id in order:
        for scope in by_id[ext_id].get("scopes", []):
            if scope not in scopes:
                scopes.append(scope)

    commands: dict[str, str] = {}
    for ext_id in order:
        for name in by_id[ext_id].get("commands", {}):
            _require(name not in commands, "duplicate-command",
                     f"command {name!r} declared by more than one enabled extension")
            commands[name] = ext_id

    # Effective configuration = each enabled extension's manifest defaults overlaid by validated
    # project `extensions.config` values, keyed by extension id. Manifest defaults are never mutated
    # (each extension starts from a fresh copy). Project overrides are validated against the resolved
    # catalog here — where the manifests are available — and fail closed before composition returns.
    effective_config: dict[str, dict[str, Any]] = {
        ext_id: dict(by_id[ext_id].get("defaults", {})) for ext_id in order
    }
    for cfg_id, values in ext_config.get("config", {}).items():
        _require(cfg_id in enabled_set, "invalid-extensions-config",
                 f"extensions.config names {cfg_id!r}, which is not an enabled extension")
        schema = by_id[cfg_id].get("config_schema", {})
        for key, value in values.items():
            _require(key in schema, "invalid-extension-config-value",
                     f"extensions.config.{cfg_id} sets key {key!r}, which {cfg_id!r} does not declare "
                     f"in config_schema")
            _require(_config_value_matches(value, schema[key]), "invalid-extension-config-value",
                     f"extensions.config.{cfg_id}.{key} value {value!r} is not a {schema[key]}")
            effective_config[cfg_id][key] = value

    # Every `{config:<key>}` a command references must resolve to an EFFECTIVE value (from a manifest
    # default or a project override). A declared key with neither is a fail-closed configuration gap
    # — reported once here, after composition, so an override-only key that IS supplied resolves and a
    # truly-missing one fails deterministically before any command dispatch.
    for ext_id in order:
        effective = effective_config[ext_id]
        for name, spec in by_id[ext_id].get("commands", {}).items():
            for arg in spec.get("args", []):
                for key in _CONFIG_PLACEHOLDER_RE.findall(arg):
                    _require(key in effective, "missing-config-value",
                             f"command {name!r} of extension {ext_id!r} references config key {key!r}, "
                             f"which has no effective value (no default and no project override)")

    contributions: dict[str, list[dict[str, Any]]] = {t: [] for t in sorted(CAPABILITY_TYPES)}
    gate_ids: list[str] = []
    integration_descriptors: dict[str, dict[str, Any]] = {}
    for ext_id in order:
        ext = by_id[ext_id]
        for cap in ext["types"]:
            contributions[cap].append({"id": ext_id, "manifest": ext})
        if "gate" in ext["types"]:
            gate_ids.append(ext_id)
        if "integration" in ext["types"] and ext.get("integration") is not None:
            integration_descriptors[ext_id] = ext["integration"]

    # Pack content/default/relation composition (task_190c). DEFAULTS values live in the manifest, so
    # they are composed here (fail-closed on an unordered differing-value conflict or ambiguous relation
    # before returning). CONTENT is identified by its frontmatter `id`, which the maw_core boundary
    # forbids reading here; resolve() therefore emits the content DECLARATIONS, the content relations,
    # and the explicit-precedence pairs, and the ai_plane sync/report layer reads frontmatter and calls
    # `compose_content` to finish the id-based composition before any generation transaction.
    precedes = _precedence_pairs(enabled)
    composed_defaults, defaults_report = _compose_defaults(order, by_id, precedes)
    content_declarations: list[dict[str, Any]] = []
    content_relations: list[list[str]] = []
    for ext_id in order:
        ext = by_id[ext_id]
        if "pack" not in ext["types"]:
            continue
        for entry in ext.get("contributes", {}).get("content", []):
            content_declarations.append({"origin": ext_id, "kind": entry["kind"],
                                         "from": entry["from"], "root": ext["root"]})
        for rel_entry in ext.get("relations", []):
            if rel_entry["kind"] == "content":
                content_relations.append([ext_id, rel_entry["target"], rel_entry["op"]])

    return {
        "order": order,
        "extensions": by_id,
        "roots": ext_config["roots"],
        "scopes": scopes,
        "commands": commands,
        "config": effective_config,
        "contributions": contributions,
        "gate_ids": gate_ids,
        "integration_descriptors": integration_descriptors,
        "composed_defaults": composed_defaults,
        "defaults_report": defaults_report,
        "content_declarations": content_declarations,
        "content_relations": content_relations,
        "precedence_pairs": sorted(precedes),
    }


def _validate_gate_interface(gate_id: str, func: Any) -> None:
    """Fail closed with ``invalid-gate-interface`` BEFORE routing unless ``func`` matches exactly the
    normative public shape ``cmd_verify(args, *, root, ai)`` (design.md "Gate Interface"): one
    required positional-or-keyword parameter NAMED ``args`` plus the two required keyword-only
    parameters ``root`` and ``ai``. Annotations are irrelevant. A wrong positional name, wrong
    keyword names, wrong arity, ``*args``/``**kwargs``, positional-only, extra parameters, or a
    default that makes a required parameter optional all fail closed here rather than as a later
    ``TypeError`` at routing."""
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError) as error:
        raise RegistryError("invalid-gate-interface",
                            f"gate {gate_id!r} cmd_verify signature is not introspectable: {error}") from error
    params = list(signature.parameters.values())
    positional = [p for p in params if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD]
    keyword_only = [p for p in params if p.kind is inspect.Parameter.KEYWORD_ONLY]
    has_variadic = any(p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
                       for p in params)
    has_positional_only = any(p.kind is inspect.Parameter.POSITIONAL_ONLY for p in params)
    empty = inspect.Parameter.empty
    conforms = (
        not has_variadic
        and not has_positional_only
        and len(positional) == 1 and positional[0].name == "args" and positional[0].default is empty
        and len(keyword_only) == 2
        and {p.name for p in keyword_only} == {"root", "ai"}
        and all(p.default is empty for p in keyword_only)
    )
    _require(conforms, "invalid-gate-interface",
             f"gate {gate_id!r} cmd_verify must accept exactly (args, *, root, ai): a required "
             f"positional parameter named 'args' plus required keyword-only 'root' and 'ai', with no "
             f"extra, variadic, positional-only, or optional parameters (got {signature})")


def load_gate_module(resolved: dict[str, Any], gate_id: str, repo_root: Path):
    """Load a registered gate's entrypoint module by explicit registry resolution.

    This is the only place a gate's code is imported, and only for an id the project explicitly
    enabled — there is never an import-time side effect or ambient discovery. The entrypoint is
    re-validated for resolved-path containment before loading (defense in depth). The security
    model is honest about in-process Python: gate entrypoints are TRUSTED first-party code
    enabled solely by explicit owner config, not sandboxed untrusted plugins; the registry keeps
    manifests as pure data, path-contains the entrypoint, and fails closed on every declarative
    violation, but it does not (and in-process cannot) jail a trusted entrypoint's runtime
    authority. read_roots/write_roots are declared authority for audit, not a runtime jail."""
    _require(gate_id in resolved["gate_ids"], "unknown-gate",
             f"gate {gate_id!r} is not a registered, enabled gate")
    manifest = resolved["extensions"][gate_id]
    validate_entrypoint_paths(manifest, repo_root, f"gate {gate_id!r}")
    entrypoint = manifest["entrypoints"]["gate"]
    module_path = (repo_root / manifest["root"] / entrypoint).resolve()
    scripts_dir = str((repo_root / "scripts").resolve())
    import sys
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location(f"maw_gate_{gate_id}", module_path)
    _require(spec is not None and spec.loader is not None, "unloadable-gate",
             f"gate {gate_id!r} entrypoint {module_path} cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Validate the gate protocol BEFORE it is routed to, so a structurally valid but non-conforming
    # gate fails with a named reason instead of a later AttributeError/TypeError. Presence AND the
    # cmd_verify callable SIGNATURE are both checked here (a wrong-arity/keyword gate is rejected
    # before ai_cli.cmd_verify routes to it).
    for required in GATE_PROTOCOL:
        member = getattr(module, required, None)
        _require(callable(member), "invalid-gate-interface",
                 f"gate {gate_id!r} entrypoint does not expose the required gate protocol "
                 f"member {required!r}(args, *, root, ai)")
        if required == "cmd_verify":
            _validate_gate_interface(gate_id, member)
    return module


def _expand_config_placeholders(arg: str, effective: dict[str, Any], command_name: str) -> str:
    """Single-pass expansion of `{config:<key>}` placeholders in one declared arg using the
    command's declaring extension's effective configuration. Every referenced key is guaranteed
    declared and defaulted at manifest time, so it resolves here; expanded output is never rescanned
    for further placeholders."""
    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in effective:
            raise RegistryError("missing-config-value",
                                f"command {command_name!r} references config key {key!r} with no "
                                f"effective value")
        return _render_config_scalar(effective[key])
    return _CONFIG_PLACEHOLDER_RE.sub(_replace, arg)


def command_argv(resolved: dict[str, Any], command_name: str, extra_args: list[str]) -> tuple[list[str], dict[str, Any]]:
    """Build the argv for a registered command capability: its declared executable + its declared
    args (with `{config:<key>}` placeholders expanded from effective configuration). Fails closed on
    an unknown command, on any caller-supplied argv, and on a dispatch-time authority violation.
    Returns (argv, spec).

    The command's manifest ``args`` array is its complete invocation convention (design.md "Caller
    arguments"): caller ``extra_args`` are rejected with ``unexpected-command-arguments`` rather than
    appended, so a command cannot gain an undeclared argument through the `ai ext run` boundary. The
    declared input/output/write authority is re-checked from the RESOLVED manifest here (not only at
    manifest load), so a post-resolution mutation cannot smuggle an out-of-authority target past
    dispatch."""
    _require(command_name in resolved["commands"], "unknown-command",
             f"command {command_name!r} is not registered by any enabled extension")
    _require(not extra_args, "unexpected-command-arguments",
             f"command {command_name!r} accepts no caller arguments; its declared args are the "
             f"complete invocation convention (got {list(extra_args)})")
    ext_id = resolved["commands"][command_name]
    manifest = resolved["extensions"][ext_id]
    spec = manifest["commands"][command_name]
    _validate_command_authority(command_name, spec, manifest, "dispatch")
    effective = resolved.get("config", {}).get(ext_id, {})
    expanded = [_expand_config_placeholders(arg, effective, command_name) for arg in spec.get("args", [])]
    argv = [spec["executable"], *expanded]
    return argv, spec


def run_command_capability(resolved: dict[str, Any], command_name: str, repo_root: Path,
                           extra_args: list[str] | None = None) -> int:
    """Execute a registered command capability through the generic process substrate
    (scripts/verify_core.run_argv): an argv array with the shell disabled, honoring the declared
    timeout. Deterministic, argv-only; no shell string is ever constructed. Returns the exit
    status. Timeout vs. launch failure are distinguished by the substrate's named errors."""
    import scripts.verify_core as verify_core

    argv, spec = command_argv(resolved, command_name, list(extra_args or []))
    try:
        return verify_core.run_argv(argv, repo_root, timeout=spec.get("timeout_seconds"))
    except verify_core.VerifyError as error:
        reason = "command-timed-out" if str(error).startswith("command-timed-out") else "command-launch-failed"
        raise RegistryError(reason, f"command {command_name!r}: {error}") from error


def resolver_report(resolved: dict[str, Any], *,
                    content_report: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """A human-auditable explanation of the effective composition and precedence: the origin of every
    effective scope, command, file contribution, composed default, and declared relation, plus the
    resolution order. Content is identified by its frontmatter ``id``, which the maw_core boundary
    forbids reading here, so the ai_plane ``ai ext list`` path composes the id-based ``content_report``
    (via ``compose_content``) and passes it in; called without it, the content section is empty."""
    scope_origin: dict[str, str] = {scope: "core" for scope in CORE_SCOPES}
    for ext_id in resolved["order"]:
        for scope in resolved["extensions"][ext_id].get("scopes", []):
            scope_origin.setdefault(scope, ext_id)
    contributions: list[dict[str, str]] = []
    for cap in ("pack", "integration"):
        for entry in resolved["contributions"][cap]:
            manifest = entry["manifest"]
            for f in manifest.get("contributes", {}).get("files", []):
                contributions.append({"destination": f["to"], "origin": manifest["id"], "capability": cap})
    # Normative defaults report shape {key, value, origin, precedence} (design.md "Defaults
    # Contributions"); the overridden origins are retained as an auditable extra field.
    defaults = [{"key": d["key"], "value": d["value"], "origin": d["origin"],
                 "precedence": d["precedence"], "overridden": d.get("overridden", [])}
                for d in resolved.get("defaults_report", [])]
    # The relations section reports every effective relation with its RESOLVED order — the ordered
    # modifier chain (non-base precedence entries) per target and the effective (winning) origin —
    # rather than raw declaration-array order (design.md AC3; R3-190C-2).
    relations: list[dict[str, Any]] = []
    for entry in (content_report or []):
        chain = [p for p in entry.get("precedence", []) if p["op"] != "base"]
        if chain:
            relations.append({"kind": "content", "target": entry["id"],
                              "effective_origin": entry["origin"], "resolved": chain})
    for entry in defaults:
        chain = [p for p in entry["precedence"] if p["op"] != "base"]
        if chain:
            relations.append({"kind": "defaults", "target": entry["key"],
                              "effective_origin": entry["origin"], "resolved": chain})
    return {
        "resolution_order": resolved["order"],
        "scopes": [{"scope": s, "origin": scope_origin.get(s, "core")} for s in resolved["scopes"]],
        "commands": [{"command": c, "origin": o} for c, o in sorted(resolved["commands"].items())],
        "contributions": sorted(contributions, key=lambda c: c["destination"]),
        # Origin AND precedence of every effective composed content, default, and relation (task_190c).
        "content": content_report if content_report is not None else [],
        "defaults": defaults,
        "relations": relations,
        "gates": resolved["gate_ids"],
        "capabilities": {
            cap: [entry["id"] for entry in entries]
            for cap, entries in resolved["contributions"].items()
        },
    }
