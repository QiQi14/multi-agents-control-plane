"""Versioned deterministic reader data model for governed repository knowledge."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.ai_plane.knowledge_projection.common import (
    PROJECTION_SCHEMA_VERSION,
    aggregate_state,
    canonical_json,
    fingerprint,
    repository_revision,
)
from scripts.ai_plane.knowledge_projection.documents import build_documents
from scripts.ai_plane.knowledge_projection.project import build_project_intelligence
from scripts.ai_plane.knowledge_projection.reader_accepted import (
    write_accepted_reader_assets,
)
from scripts.ai_plane.knowledge_projection.task_presentation import (
    contains_source_locator,
    presentation_contract_violations,
)
from scripts.ai_plane.knowledge_projection.tasks import build_tasks


def build_knowledge_projection(
    root: Path,
    *,
    registry_data: dict[str, Any],
    document_edges: list[dict[str, Any]],
    document_bodies: dict[str, str] | None = None,
    project_export: dict[str, Any] | None = None,
    revision: dict[str, Any] | None = None,
    source_states: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Compose three peer truth systems under one deterministic boundary."""
    repo_root = root.resolve()
    revision_data = revision or repository_revision(repo_root)
    project = build_project_intelligence(repo_root, export_data=project_export)
    documents = build_documents(
        repo_root / ".ai",
        registry_data,
        edges=document_edges,
        source_bodies=document_bodies,
    )
    tasks = build_tasks(repo_root)
    truth_systems = {
        "project_intelligence": project,
        "documents": documents,
        "tasks_features": tasks,
    }
    allowed_states = {"fresh", "stale", "partial", "error", "unavailable"}
    for truth_system, state in (source_states or {}).items():
        if truth_system not in truth_systems:
            raise ValueError(f"unknown truth-system state override: {truth_system}")
        if state not in allowed_states:
            raise ValueError(f"unknown truth-system boundary state: {state}")
        truth_systems[truth_system]["boundary"]["state"] = state
        truth_systems[truth_system]["boundary"]["state_provenance"] = "explicit-source-probe"
    model = {
        "schema_version": PROJECTION_SCHEMA_VERSION,
        "source": {
            **revision_data,
            "fingerprint": "",
            "state": aggregate_state([
                str(project["boundary"]["state"]),
                str(documents["boundary"]["state"]),
                str(tasks["boundary"]["state"]),
            ]),
            "truth_systems": [
                "project-intelligence",
                "documents/control-plane",
                "documents/product",
                "tasks-features",
            ],
            "authority_boundary": (
                "Peer truth systems remain distinct; only explicit typed bridges are projected."
            ),
        },
        "truth_systems": truth_systems,
    }
    model["source"]["fingerprint"] = fingerprint(model)
    return model


def write_knowledge_assets(model: dict[str, Any], site_dir: Path) -> tuple[Path, Path]:
    """Write byte-stable local JSON and JS assets for file:// consumption."""
    site_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = site_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    json_text = canonical_json(model, pretty=True)
    json_path = site_dir / "reader-data.json"
    js_path = assets_dir / "reader-data.js"
    json_path.write_text(json_text, encoding="utf-8", newline="\n")
    js_path.write_text(
        "window.__READER_DATA__ = " + canonical_json(model) + ";\n",
        encoding="utf-8",
        newline="\n",
    )
    repository_root = (
        site_dir.parent.parent
        if site_dir.name == "_site" and site_dir.parent.name == ".ai"
        else None
    )
    write_accepted_reader_assets(model, assets_dir, repository_root=repository_root)
    return json_path, js_path


__all__ = [
    "PROJECTION_SCHEMA_VERSION",
    "build_knowledge_projection",
    "contains_source_locator",
    "presentation_contract_violations",
    "write_knowledge_assets",
]
