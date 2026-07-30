"""Project Intelligence projection sourced only through ai-impact's export contract."""
from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from scripts.ai_plane.knowledge_projection.common import boundary, fingerprint, sanitize


_AGENT_PROOF_QUERIES = (
    (
        "vectorExplore",
        "vector-vector",
        "impl-header[impl Mul < Vector3 > for Vector3]::mul",
    ),
    (
        "scalarExplore",
        "scalar-vector",
        "impl-header[impl Mul < f32 > for Vector3]::mul",
    ),
)
_EPHEMERAL_DATABASE_ARGUMENT = "<temporary-ai-impact-index>"


def _build_input_fingerprint(root: Path) -> str:
    """Fingerprint every governed input that can change the ai-impact executable."""
    tool_root = root / "tools" / "ai-impact"
    candidates = [
        tool_root / "Cargo.toml",
        tool_root / "Cargo.lock",
        tool_root / "build.rs",
        root / "rust-toolchain",
        root / "rust-toolchain.toml",
    ]
    for directory in (tool_root / "src", tool_root / ".cargo", root / ".cargo"):
        if directory.is_dir():
            candidates.extend(path for path in directory.rglob("*") if path.is_file())
    existing = {path for path in candidates if path.is_file()}
    digest = hashlib.sha256()
    for path in sorted(existing, key=lambda item: item.relative_to(root).as_posix()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _ai_impact_command(
    root: Path,
    database: Path,
    command: str,
    *operands: str,
) -> list[str] | None:
    """Run ai-impact from governed source instead of an ambient ignored binary."""
    tool_manifest = root / "tools" / "ai-impact" / "Cargo.toml"
    project_manifest = root / "project" / "Cargo.toml"
    if not tool_manifest.is_file() or not project_manifest.is_file():
        return None
    target_dir = (
        root
        / ".ai"
        / ".local"
        / "ai-impact-build"
        / _build_input_fingerprint(root)
    )
    return [
        "cargo",
        "run",
        "--quiet",
        "--locked",
        "--manifest-path",
        tool_manifest.relative_to(root).as_posix(),
        "--target-dir",
        target_dir.relative_to(root).as_posix(),
        "--bin",
        "ai-impact",
        "--",
        command,
        "--manifest",
        project_manifest.relative_to(root).as_posix(),
        "--database",
        str(database),
        "--content-audit",
        *operands,
    ]


def _export_command(root: Path, database: Path) -> list[str] | None:
    return _ai_impact_command(root, database, "export")


def _recorded_command(command: list[str]) -> list[str]:
    """Normalize only the random temporary database path in captured command evidence."""
    recorded = list(command)
    database_index = recorded.index("--database") + 1
    recorded[database_index] = _EPHEMERAL_DATABASE_ARGUMENT
    return recorded


def _not_requested_agent_bundle() -> dict[str, Any]:
    return {
        "state": "not-requested",
        "exact": False,
        "nonfabricated": True,
        "fabricated": False,
        "items": [],
        "guidance": (
            "Injected export data has no live temporary ai-impact index; "
            "no semantic query result was invented."
        ),
    }


def _semantic_owner_query(
    exported: dict[str, Any],
    identity_name: str,
) -> str | None:
    """Derive one selectable semantic name from the governed export fields."""
    nodes = exported.get("semantic_nodes", [])
    if not isinstance(nodes, list):
        return None
    candidates = [
        "::".join(
            part
            for part in (
                str(node.get("rust_crate_name") or ""),
                str(node.get("module_path") or ""),
                str(node.get("identity_name") or ""),
            )
            if part
        )
        for node in nodes
        if isinstance(node, dict)
        and str(node.get("rust_crate_name") or "") == "core"
        and str(node.get("module_path") or "") == "math"
        and str(node.get("qualified_name") or "") == "Vector3::mul"
        and str(node.get("identity_name") or "") == identity_name
    ]
    return candidates[0] if len(candidates) == 1 and candidates[0] else None


def _captured_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value if isinstance(value, str) else ""


def _unavailable_proof_item(
    name: str,
    proof_kind: str,
    *,
    reason: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "proof": proof_kind,
        "command": None,
        "query": None,
        "exit": None,
        "stdout": "",
        "stderr": "",
        "state": "unavailable",
        "exact": False,
        "nonfabricated": True,
        "provenance": {
            "source": "ai-impact export contract v1 semantic_nodes",
            "capture": "not-produced",
            "reason": reason,
        },
    }


def _capture_agent_result_bundles(
    root: Path,
    database: Path,
    exported: dict[str, Any],
    *,
    run: Callable[..., subprocess.CompletedProcess[str]],
) -> dict[str, Any]:
    """Capture exact proof-query output before the export database is deleted."""
    items: list[dict[str, Any]] = []
    for name, proof_kind, identity_name in _AGENT_PROOF_QUERIES:
        query = _semantic_owner_query(exported, identity_name)
        if query is None:
            items.append(_unavailable_proof_item(
                name,
                proof_kind,
                reason=(
                    "the export did not contain exactly one matching "
                    f"{proof_kind} Vector3::mul semantic owner"
                ),
            ))
            continue
        command = _ai_impact_command(root, database, "explore", query)
        if command is None:
            items.append(_unavailable_proof_item(
                name,
                proof_kind,
                reason="the governed ai-impact or project Cargo manifest became unavailable",
            ))
            continue
        try:
            completed = run(
                command,
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except OSError as error:
            items.append({
                "name": name,
                "proof": proof_kind,
                "command": _recorded_command(command),
                "query": query,
                "exit": None,
                "stdout": "",
                "stderr": str(error),
                "state": "error",
                "exact": False,
                "nonfabricated": True,
                "provenance": {
                    "source": "governed tools/ai-impact source via Cargo --locked",
                    "capture": "subprocess launch failure captured verbatim",
                    "database": "same ephemeral index used by the export",
                    "command_database_argument": "normalized in evidence only",
                },
            })
            continue
        succeeded = completed.returncode == 0
        items.append({
            "name": name,
            "proof": proof_kind,
            "command": _recorded_command(command),
            "query": query,
            "exit": completed.returncode,
            "stdout": _captured_text(completed.stdout),
            "stderr": _captured_text(completed.stderr),
            "state": "ready" if succeeded else "error",
            "exact": succeeded,
            "nonfabricated": True,
            "provenance": {
                "source": "governed tools/ai-impact source via Cargo --locked",
                "capture": "verbatim subprocess stdout and stderr",
                "database": "same ephemeral index used by the export",
                "command_database_argument": "normalized in evidence only",
            },
        })

    ready = (
        len(items) == len(_AGENT_PROOF_QUERIES)
        and all(item["state"] == "ready" and item["exact"] for item in items)
    )
    state = (
        "ready"
        if ready
        else "error"
        if any(item["state"] == "error" for item in items)
        else "unavailable"
    )
    return {
        "state": state,
        "exact": ready,
        "nonfabricated": all(item["nonfabricated"] for item in items),
        "fabricated": False,
        "items": items,
        "guidance": (
            "Exact ai-impact explore --content-audit stdout captured for both "
            "accepted Vector3::mul semantic owners."
            if ready
            else "Agent proof output is unavailable or failed; no query output was invented."
        ),
    }


def _load_export(
    root: Path,
    *,
    export_data: dict[str, Any] | None,
    run: Callable[..., subprocess.CompletedProcess[str]],
) -> tuple[dict[str, Any] | None, str | None, dict[str, Any]]:
    if export_data is not None:
        return sanitize(export_data), None, _not_requested_agent_bundle()
    with tempfile.TemporaryDirectory(prefix="maw-reader-impact-") as temp:
        database = Path(temp) / "index.sqlite"
        command = _export_command(root, database)
        if command is None:
            # Distinguish "this project never configured the capability" from "it is configured
            # and broken". Naming exporter files a repository was never expected to have reads as
            # a broken install to anyone opening the reader for the first time.
            exporter = root / "tools" / "ai-impact" / "Cargo.toml"
            if not exporter.is_file():
                return (
                    None,
                    "Project Intelligence is not configured for this repository. It is an "
                    "optional capability that indexes source symbols; the rest of the reader "
                    "does not depend on it.",
                    _not_requested_agent_bundle(),
                )
            missing = [
                rel for rel, path in (
                    ("project/Cargo.toml", root / "project" / "Cargo.toml"),
                ) if not path.is_file()
            ]
            return (
                None,
                "Project Intelligence source unavailable: "
                f"missing {', '.join(missing)}" if missing else
                "Project Intelligence source unavailable: the exporter could not be resolved",
                _not_requested_agent_bundle(),
            )
        try:
            completed = run(
                command,
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except OSError as error:
            return (
                None,
                "could not launch Cargo for the governed ai-impact export "
                f"({error}); install or enable Cargo and rerun the docs build",
                _not_requested_agent_bundle(),
            )
        if completed.returncode != 0:
            detail = (
                _captured_text(completed.stderr)
                or _captured_text(completed.stdout)
                or "no diagnostic output"
            ).strip()
            return (
                None,
                f"governed ai-impact Cargo build/export failed: {detail}",
                _not_requested_agent_bundle(),
            )
        try:
            payload = json.loads(_captured_text(completed.stdout))
        except json.JSONDecodeError as error:
            return (
                None,
                f"ai-impact export returned invalid JSON: {error}",
                _not_requested_agent_bundle(),
            )
        if not isinstance(payload, dict):
            return (
                None,
                "ai-impact export returned a non-object JSON payload",
                _not_requested_agent_bundle(),
            )
        exported = sanitize(payload)
        agent_bundles = _capture_agent_result_bundles(
            root,
            database,
            exported,
            run=run,
        )
        return exported, None, agent_bundles


def _group(key: str, label: str, derivation: str) -> dict[str, str]:
    return {
        "key": key,
        "label": label,
        "derivation": derivation,
        "provenance": "task-195e-accepted-presentation-group-contract",
        "authority": "layout-only",
    }


def _visible(
    identity: str,
    kind: str,
    label: str,
    group: dict[str, str],
    **context: Any,
) -> dict[str, Any]:
    return {
        "identity": identity,
        "kind": kind,
        "label": label,
        "presentation_group": group,
        "source_context": sanitize(context),
    }


def _build_views(export: dict[str, Any]) -> dict[str, Any]:
    packages = sorted(export.get("packages", []), key=lambda item: str(item.get("package_id", "")))
    modules = sorted(
        export.get("modules", []),
        key=lambda item: (str(item.get("rust_crate_name", "")), str(item.get("module_path", "")), str(item.get("path", ""))),
    )
    hierarchy = sorted(
        export.get("semantic_hierarchy", []),
        key=lambda item: (str(item.get("rust_crate_name", "")), str(item.get("module_path", "")), str(item.get("path", ""))),
    )
    nodes = sorted(export.get("semantic_nodes", []), key=lambda item: str(item.get("id", "")))
    relations = sorted(
        export.get("relations", []),
        key=lambda item: (str(item.get("source_id", "")), str(item.get("target_id", "")), str(item.get("kind", ""))),
    )
    package_by_crate = {
        str(package.get("rust_semantic_target_name")): str(package.get("package_id"))
        for package in packages
    }
    node_by_id = {str(node.get("id")): node for node in nodes}

    workspace_nodes = [
        _visible("workspace", "workspace", "Workspace", _group("workspace", "Workspace", "workspace-root"))
    ]
    for package in packages:
        package_id = str(package.get("package_id"))
        workspace_nodes.append(_visible(
            f"crate:{package_id}", "crate", str(package.get("cargo_display_name", package_id)),
            _group(package_id, str(package.get("cargo_display_name", package_id)), "crate-package-id"),
            package_id=package_id,
            rust_semantic_target_name=package.get("rust_semantic_target_name"),
        ))

    crate_views: list[dict[str, Any]] = []
    for package in packages:
        package_id = str(package.get("package_id"))
        crate_name = str(package.get("rust_semantic_target_name"))
        visible = [_visible(
            f"crate:{package_id}", "crate", str(package.get("cargo_display_name", package_id)),
            _group(package_id, str(package.get("cargo_display_name", package_id)), "crate-root-package-id"),
            package_id=package_id,
        )]
        module_names = sorted({
            str(module.get("module_path"))
            for module in modules if str(module.get("rust_crate_name")) == crate_name
        })
        for module_name in module_names:
            visible.append(_visible(
                f"module:{crate_name}:{module_name}", "module", module_name,
                _group(module_name, module_name, "qualified-module-name"),
                package_id=package_id, rust_crate_name=crate_name,
            ))
        crate_views.append({"package_id": package_id, "visible_nodes": visible})

    module_views: list[dict[str, Any]] = []
    for crate_name, module_name in sorted({
        (str(item.get("rust_crate_name")), str(item.get("module_path"))) for item in hierarchy
    }):
        visible = [_visible(
            f"module:{crate_name}:{module_name}", "module", module_name,
            _group(module_name, module_name, "module-root-qualified-module-name"),
            rust_crate_name=crate_name,
        )]
        for row in hierarchy:
            if str(row.get("rust_crate_name")) != crate_name or str(row.get("module_path")) != module_name:
                continue
            path = str(row.get("path"))
            parent = Path(path).parent.as_posix()
            visible.append(_visible(
                f"file:{path}", "file", path,
                _group(parent, parent or ".", "repository-relative-parent-directory"),
                rust_crate_name=crate_name, module_path=module_name,
            ))
        module_views.append({
            "rust_crate_name": crate_name,
            "module_path": module_name,
            "visible_nodes": visible,
        })

    file_views: list[dict[str, Any]] = []
    for row in hierarchy:
        path = str(row.get("path"))
        crate_name = str(row.get("rust_crate_name"))
        package_id = package_by_crate.get(crate_name, crate_name)
        visible = [_visible(
            f"file:{path}", "file", path,
            _group(package_id, package_id, "file-root-owning-package-id"),
            rust_crate_name=crate_name, module_path=row.get("module_path"),
        )]
        own_ids = {str(item) for item in row.get("semantic_node_ids", [])}
        for node_id in sorted(own_ids):
            node = node_by_id.get(node_id, {})
            kind = str(node.get("kind", "unknown"))
            visible.append(_visible(
                node_id, "symbol", str(node.get("qualified_name", node_id)),
                _group(kind, kind, "in-file-semantic-kind"),
                relation="in-file", path=path, semantic_kind=kind,
            ))
        cross_ids = {
            str(relation.get("target_id"))
            for relation in relations
            if str(relation.get("source_id")) in own_ids
            and str(relation.get("target_id")) in node_by_id
            and str(node_by_id[str(relation.get("target_id"))].get("path")) != path
        }
        for node_id in sorted(cross_ids):
            node = node_by_id[node_id]
            owner = package_by_crate.get(str(node.get("rust_crate_name")), str(node.get("rust_crate_name")))
            visible.append(_visible(
                node_id, "cross-file-symbol", str(node.get("qualified_name", node_id)),
                _group(owner, owner, "cross-file-owning-package-id"),
                relation="cross-file-or-blast-radius", path=node.get("path"),
            ))
        file_views.append({"path": path, "visible_nodes": visible})
    return {
        "workspace": {"visible_nodes": workspace_nodes},
        "crates": crate_views,
        "modules": module_views,
        "files": file_views,
        "presentation_group_semantics": "layout-only; excluded from semantic identity, ownership, purpose, and edges",
    }


def build_project_intelligence(
    root: Path,
    *,
    export_data: dict[str, Any] | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    """Load, validate, and enrich the accepted deterministic ai-impact export."""
    exported, error, agent_result_bundles = _load_export(
        root, export_data=export_data, run=run,
    )
    if exported is None:
        return {
            "boundary": boundary(
                "unavailable",
                fingerprint_value=None,
                indexed_roots=["project/"],
                include_rules=["ai-impact export contract v1"],
                exclude_rules=["SQLite internals", "pending references as semantic edges"],
                omitted_count=1,
                errors=[error or "unknown export failure"],
                rebuild_guidance=(
                    "Rerun `python scripts/ai_cli.py docs build`; it compiles the governed "
                    "tools/ai-impact source with Cargo --locked before exporting."
                ),
            ),
            "contract_version": None,
            "packages": [],
            "files": [],
            "modules": [],
            "semantic_nodes": [],
            "semantic_hierarchy": [],
            "relations": [],
            "pending_boundaries": [],
            "omissions": {"project_export_unavailable": 1},
            "views": {"workspace": {"visible_nodes": []}, "crates": [], "modules": [], "files": []},
            "agent_result_bundles": {
                "state": "unavailable",
                "exact": False,
                "nonfabricated": True,
                "fabricated": False,
                "items": [],
                "guidance": "No semantic query result was invented while the export was unavailable.",
            },
        }
    validation_errors: list[str] = []
    if exported.get("contract_version") != 1:
        validation_errors.append("unsupported ai-impact export contract_version")
    required_lists = (
        "packages", "modules", "files", "semantic_nodes", "semantic_hierarchy",
        "relations", "pending_boundaries",
    )
    for field in required_lists:
        if not isinstance(exported.get(field), list):
            validation_errors.append(f"ai-impact export field {field} must be a list")
            exported[field] = []
    packages = exported["packages"]
    nodes = exported["semantic_nodes"]
    relations = exported["relations"]
    node_ids = {str(node.get("id")) for node in nodes if isinstance(node, dict)}
    resolved_relations = [
        item for item in relations
        if str(item.get("source_id")) in node_ids and str(item.get("target_id")) in node_ids
    ]
    if len(resolved_relations) != len(relations):
        validation_errors.append("ai-impact export contains a relation without two resolved semantic nodes")
    exported["relations"] = sorted(
        resolved_relations,
        key=lambda item: (
            str(item.get("source_id")), str(item.get("target_id")),
            str(item.get("kind")), str(item.get("provenance")),
        ),
    )
    nodes_by_id = {str(node.get("id")): node for node in nodes}
    for package in packages:
        crate_name = str(package.get("rust_semantic_target_name"))
        package_ids = {
            node_id for node_id, node in nodes_by_id.items()
            if str(node.get("rust_crate_name")) == crate_name
        }
        relation_count = sum(
            1 for item in exported["relations"]
            if str(item.get("source_id")) in package_ids or str(item.get("target_id")) in package_ids
        )
        package["graph_counts"] = {
            "semantic_nodes": len(package_ids),
            "resolved_relations": relation_count,
        }
    semantic = {
        "contract_version": exported.get("contract_version"),
        "source_fingerprint": exported.get("source_fingerprint"),
        "packages": sorted(packages, key=lambda item: str(item.get("package_id"))),
        "files": sorted(exported["files"], key=lambda item: str(item.get("path"))),
        "modules": sorted(
            exported["modules"],
            key=lambda item: (str(item.get("rust_crate_name")), str(item.get("module_path")), str(item.get("path"))),
        ),
        "semantic_nodes": sorted(nodes, key=lambda item: str(item.get("id"))),
        "semantic_hierarchy": sorted(
            exported["semantic_hierarchy"],
            key=lambda item: (str(item.get("rust_crate_name")), str(item.get("module_path")), str(item.get("path"))),
        ),
        "relations": exported["relations"],
        "pending_boundaries": sorted(
            exported["pending_boundaries"],
            key=lambda item: (str(item.get("owner_path")), int(item.get("start_row", 0)), int(item.get("start_column", 0))),
        ),
        "omissions": sanitize(exported.get("omissions", {})),
        "views": _build_views(exported),
        "agent_result_bundles": sanitize(agent_result_bundles),
    }
    state = "error" if validation_errors else "fresh"
    return {
        "boundary": boundary(
            state,
            fingerprint_value=fingerprint(semantic),
            indexed_roots=["project/Cargo.toml", "project/crates/"],
            include_rules=["ai-impact deterministic export contract v1", "resolved semantic relations"],
            exclude_rules=["SQLite internals", "pending references as edges", "presentation groups as semantics"],
            omitted_count=len(validation_errors),
            errors=validation_errors,
            rebuild_guidance=(
                "Rerun `python scripts/ai_cli.py docs build`; it compiles the governed "
                "tools/ai-impact source with Cargo --locked before exporting."
            ),
        ),
        **semantic,
    }
