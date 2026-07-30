from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

import scripts.ai_plane.adapter_render as adapter_render
import scripts.ai_plane.config as config_module
import scripts.ai_plane.constants as constants
import scripts.extension_registry as extension_registry
from scripts.ai_plane.config import ensure_dirs
from scripts.ai_plane.frontmatter import parse_frontmatter
from scripts.ai_plane.manifest import generation_session, write_generated_bytes
from scripts.ai_plane.registry import canonical_content_records, generate_registry
from scripts.ai_plane.task_evidence_legacy import repository_task_artifact_violations
from scripts.ai_plane.utils import die, rel


def resolve_command_tokens(content: str, tool: str, adapter: dict[str, Any]) -> str:
    """Resolve neutral canonical command tokens for one configured adapter's command map."""
    commands = adapter["commands"]

    def replace(match: re.Match[str]) -> str:
        token = match.group(1)
        if token not in commands:
            die(
                f"Unknown neutral command token {match.group(0)} for tool '{tool}' in "
                ".ai/config.yaml adapter.commands"
            )
        return commands[token]

    rendered = re.sub(r"__AI_COMMAND_([A-Z][A-Z0-9_]*)__", replace, content)
    unresolved = re.search(r"__AI_COMMAND_[A-Z0-9_]*__", rendered)
    if unresolved:
        die(f"Unresolved neutral command token {unresolved.group(0)} for tool '{tool}'")
    return rendered


def _render_context(tool: str) -> dict[str, str]:
    return {
        "tool": tool,
        "tool_role": config_module.TOOL_ROLES.get(tool, ""),
        "default_isolation": config_module.TOOL_DEFAULTS.get(tool, ""),
    }


def plan_tool_adapter(tool: str, adapter: dict[str, Any]) -> list[tuple[str, bytes]]:
    """Render one tool's adapter into repository-relative ``(path, bytes)`` entries through the
    generic renderer (no per-vendor branch), driven entirely by the resolved render descriptor."""
    descriptor = adapter["descriptor"]
    ext_root = Path(adapter["ext_root"]) if adapter.get("ext_root") else None
    try:
        return adapter_render.plan_artifacts(descriptor, _render_context(tool), constants.ROOT, ext_root)
    except adapter_render.RenderError as error:
        die(f"cannot render adapter for tool '{tool}': {error}")


def render_adapter(tool: str, adapter: dict[str, Any]) -> None:
    """Render and write one tool's adapter through the generic renderer."""
    for path, data in plan_tool_adapter(tool, adapter):
        write_generated_bytes(constants.ROOT / path, data)


def resolve_or_die() -> dict[str, Any]:
    """Resolve the full extension composition ONCE, fail-closed via die(). A MISSING extensions block
    fails closed exactly like an invalid one (R4-Q181-1) — only an explicit ``extensions.enabled: []``
    means zero extensions, so a missing block is never silently treated as empty."""
    try:
        parsed = config_module.parse_config_yaml(constants.AI / "config.yaml")
    except config_module.ConfigError as error:
        die(f"cannot resolve extensions; config unreadable: {error}")
    try:
        return extension_registry.resolve(parsed, constants.ROOT, platform_name=os.name)
    except extension_registry.RegistryError as error:
        die(f"extension roster is missing or invalid; refusing to sync: {error}")


def resolve_extension_contributions(resolved: dict[str, Any]) -> list[tuple[Path, bytes]]:
    """Resolve the enabled pack/integration ``contributes.files`` into (destination, bytes) BEFORE any
    generation transaction opens, fail-closed with a named reason and NO filesystem/manifest change on
    a duplicate destination, so the output depends only on the declared composition, not the sync
    count."""
    planned: list[tuple[Path, bytes]] = []
    owner: dict[str, str] = {}
    for capability in ("pack", "integration"):
        for entry in resolved["contributions"][capability]:
            manifest = entry["manifest"]
            ext_root = constants.ROOT / manifest["root"]
            for contribution in manifest.get("contributes", {}).get("files", []):
                to = contribution["to"]
                if to in owner:
                    die(
                        f"duplicate-contribution-destination: {to!r} is contributed by both "
                        f"{owner[to]!r} and {manifest['id']!r}; composition would be non-deterministic"
                    )
                owner[to] = manifest["id"]
                planned.append(((constants.ROOT / to), (ext_root / contribution["from"]).read_bytes()))
    return planned


def _read_content_records(resolved: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read each enabled rules/workflows/skills content document's frontmatter (which the maw_core
    boundary forbids extension_registry from doing) and return (content_records, template_declarations).
    content_records: ``[{origin, kind, id, type, from, root}]``. Fails closed via die() on a
    missing/undecodable document or one missing frontmatter ``id``/``type`` (invalid-content)."""
    records: list[dict[str, Any]] = []
    templates: list[dict[str, Any]] = []
    for decl in resolved["content_declarations"]:
        if decl["kind"] == "templates":
            templates.append(decl)
            continue
        src = constants.ROOT / decl["root"] / decl["from"]
        try:
            text = src.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            die(f"pack content unreadable ({decl['origin']} {decl['from']}); refusing to sync: {error}")
        meta, _body = parse_frontmatter(text)
        doc_id = meta.get("id") if isinstance(meta, dict) else None
        doc_type = meta.get("type") if isinstance(meta, dict) else None
        if not (isinstance(doc_id, str) and doc_id and isinstance(doc_type, str) and doc_type):
            die(f"invalid-content: pack {decl['origin']} content {decl['from']} is missing frontmatter "
                f"'id' or 'type'; refusing to sync")
        records.append({"origin": decl["origin"], "kind": decl["kind"], "id": doc_id,
                        "type": doc_type, "from": decl["from"], "root": decl["root"]})
    return records, templates


def compose_pack_content(resolved: dict[str, Any]) -> tuple[list[tuple[Path, bytes]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Compose enabled-pack CONTENT (task_190c) into (materialized files, registry documents,
    content_report, superseded_core_ids), fully validated BEFORE any generation transaction. Reads each
    document's frontmatter and delegates the id-based composition + fail-closed relation semantics to the
    pure ``extension_registry.compose_content`` — including the CANONICAL registry documents as base
    contributors so a pack ``replace`` may supersede a core id. rules/workflows/skills are materialized
    and indexed by their frontmatter id; templates are materialized with ``{default:<key>}`` resolved."""
    records, templates = _read_content_records(resolved)
    core_records = canonical_content_records()
    precedes = {tuple(pair) for pair in resolved["precedence_pairs"]}
    relations = [tuple(rel_entry) for rel_entry in resolved["content_relations"]]
    try:
        content_plan, content_report, superseded = extension_registry.compose_content(
            records, core_records, relations, precedes)
    except extension_registry.RegistryError as error:
        die(f"pack content composition failed; refusing to sync: {error}")

    # A core base layer's `from` is repository-relative (e.g. .ai/rules/x.md), read from the repo root.
    root_by_origin = {rec["origin"]: rec["root"] for rec in records}
    root_by_origin[extension_registry.CORE_ORIGIN] = "."
    ext = resolved["extensions"]
    content_files: list[tuple[Path, bytes]] = []
    registry_docs: list[dict[str, Any]] = []
    try:
        for entry in content_plan:
            composed: bytes | None = None
            for layer in entry["layers"]:
                src = (constants.ROOT / root_by_origin[layer["origin"]] / layer["from"]).read_bytes()
                op = layer["op"]
                if op in ("base", "replace"):
                    composed = src
                elif op == "prepend":
                    composed = src + (composed or b"")
                elif op == "append":
                    composed = (composed or b"") + src
                elif op == "wrap":
                    composed = src + (composed or b"") + src
            # Materialize the composed document under the EFFECTIVE origin's write authority (a replace
            # relation makes the replacing pack the effective origin) AND index it by frontmatter id.
            effective = entry["effective_origin"]
            eff_from = next(layer["from"] for layer in entry["layers"] if layer["origin"] == effective)
            dest_rel = (Path(ext[effective]["write_roots"][0]) / eff_from).as_posix()
            content_files.append((constants.ROOT / dest_rel, composed))
            registry_docs.append({"rel_path": dest_rel, "text": composed.decode("utf-8"), "origin": effective})

        composed_defaults = resolved["composed_defaults"]
        for decl in templates:
            src = constants.ROOT / decl["root"] / decl["from"]
            rendered = extension_registry.render_default_placeholders(
                src.read_text(encoding="utf-8"), composed_defaults)
            dest_rel = (Path(ext[decl["origin"]]["write_roots"][0]) / decl["from"]).as_posix()
            content_files.append((constants.ROOT / dest_rel, rendered.encode("utf-8")))
    except (OSError, UnicodeDecodeError, extension_registry.RegistryError) as error:
        die(f"pack content composition failed; refusing to sync: {error}")
    return content_files, registry_docs, content_report, superseded


def cmd_sync(_args: argparse.Namespace) -> None:
    """Regenerate every adapter and contribution through the generic renderer. The COMPLETE plan is
    rendered to unique (repository-relative path, bytes) entries and collision-checked BEFORE any
    filesystem mutation or manifest transaction (design.md "Pre-mutation render plan"), so any
    render/roster/collision failure leaves generated files and the manifest byte-identical."""
    sync_tool = config_module.COMMAND_TOOL_DEFAULTS["implementation_tool"]
    command = config_module.configured_command(sync_tool, "SYNC")

    # 1-7: build the whole plan before touching the filesystem.
    plan: list[tuple[str, bytes]] = []
    owners: dict[str, str] = {}

    def claim(path_value: str, owner: str) -> None:
        if path_value in owners:
            die(
                f"duplicate-render-output: {path_value!r} is produced by both {owners[path_value]!r} "
                f"and {owner!r}; composition would be non-deterministic"
            )
        owners[path_value] = owner

    for tool in config_module.TOOLS:
        for path_value, data in plan_tool_adapter(tool, config_module.ADAPTERS[tool]):
            claim(path_value, f"tool:{tool}")
            plan.append((path_value, data))

    # Resolve the full extension composition ONCE (fail-closed before any mutation), then plan every
    # file contribution, materialized template, and the composed registry from it.
    resolved = resolve_or_die()
    for dest, content in resolve_extension_contributions(resolved):
        path_value = rel(dest)
        claim(path_value, "extension-contribution")
        plan.append((path_value, content))

    # Pack CONTENT composition (task_190c): rules/workflows/skills are indexed into the registry by
    # their frontmatter id; templates are materialized with {default:} resolved. All validated before
    # any mutation like every other plan entry.
    content_files, registry_docs, _content_report, superseded = compose_pack_content(resolved)
    for dest, content in content_files:
        path_value = rel(dest)
        claim(path_value, "pack-content")
        plan.append((path_value, content))

    try:
        registry_payload = generate_registry(extension_documents=registry_docs, superseded_ids=set(superseded))
    except extension_registry.RegistryError as error:
        die(f"pack content registry composition failed; refusing to sync: {error}")
    registry_bytes = (json.dumps(registry_payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    claim(".ai/_registry.json", "registry")
    plan.append((".ai/_registry.json", registry_bytes))

    contribution_count = sum(1 for _, owner in owners.items()
                             if owner in ("extension-contribution", "pack-content"))

    # 8-9: only now mutate — write the already-complete plan inside the manifest transaction.
    ensure_dirs()
    with generation_session(command, replace_sync_entries=True):
        for path_value, data in plan:
            write_generated_bytes(constants.ROOT / path_value, data)

    generated = ", ".join(
        rel(constants.ROOT / adapter["output_dir"] / adapter["detect_marker"])
        for adapter in config_module.ADAPTERS.values()
    )
    suffix = f"; {contribution_count} extension contribution file(s)" if contribution_count else ""
    print(f"Generated adapters: {generated}{suffix}")


def cmd_audit_framework(_args: argparse.Namespace) -> None:
    """Audit every task artifact in the repository against the evidence schema.

    This used to also require a historical migration document to exist and died when it did not,
    which made an unrelated document load-bearing for an audit that never read it.
    """
    artifact_violations = repository_task_artifact_violations(constants.ROOT)
    if artifact_violations:
        die("Task evidence audit failed: " + "; ".join(artifact_violations))
    print("Task evidence audit: all schema-versioned task artifacts are valid.")
