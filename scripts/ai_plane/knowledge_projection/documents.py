"""Corpus-separated document library and graph projection."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from scripts.ai_plane.knowledge_projection.common import boundary, fingerprint, sanitize
from scripts.ai_plane.registry import parse_frontmatter, resolve_registry_source_path


def _load_body(ai_root: Path, document: dict[str, Any]) -> tuple[str, str | None]:
    source = resolve_registry_source_path(ai_root, str(document.get("path", "")))
    if not source.is_file():
        return "", f"missing document source: {document.get('path', '')}"
    try:
        _metadata, body = parse_frontmatter(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        return "", f"unreadable document source {document.get('path', '')}: {error}"
    return body, None


def build_documents(
    ai_root: Path,
    registry_data: dict[str, Any],
    *,
    edges: list[dict[str, Any]],
    source_bodies: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Compose complete bodies and corpus-scoped graph payloads."""
    documents = [
        sanitize(document)
        for document in registry_data.get("documents", [])
        if isinstance(document, dict) and isinstance(document.get("id"), str)
    ]
    documents.sort(key=lambda item: (str(item.get("corpus", "")), str(item["id"])))
    bodies = source_bodies or {}
    source_errors: list[str] = []
    entries: list[dict[str, Any]] = []
    for document in documents:
        doc_id = str(document["id"])
        body = bodies.get(doc_id)
        if body is None:
            body, error = _load_body(ai_root, document)
            if error:
                source_errors.append(error)
        entries.append({
            "namespace_id": f"{document.get('corpus', 'control-plane')}::{doc_id}",
            "id": doc_id,
            "corpus": str(document.get("corpus", "control-plane")),
            "metadata": document,
            "body": body,
            "legacy_boundary": {
                "legacy": bool(document.get("legacy", False)),
                "warning": document.get("warning"),
            },
        })

    entry_by_id = {entry["id"]: entry for entry in entries}
    normalized_edges: list[dict[str, Any]] = []
    external_references: list[dict[str, Any]] = []
    for raw in edges:
        if not isinstance(raw, dict):
            continue
        source = str(raw.get("source", ""))
        target = str(raw.get("target", ""))
        source_entry = entry_by_id.get(source)
        target_entry = entry_by_id.get(target)
        item = sanitize(raw)
        if source_entry:
            item["source_namespace_id"] = source_entry["namespace_id"]
        if target_entry:
            item["target_namespace_id"] = target_entry["namespace_id"]
        if target_entry is None:
            external_references.append(item)
        else:
            normalized_edges.append(item)
    normalized_edges.sort(
        key=lambda item: (
            str(item.get("source_corpus", "")), str(item.get("source", "")),
            str(item.get("target_corpus", "")), str(item.get("target", "")),
            str(item.get("type", "")), str(item.get("provenance", "")),
        )
    )
    external_references.sort(
        key=lambda item: (
            str(item.get("source", "")), str(item.get("target", "")),
            str(item.get("type", "")),
        )
    )

    backlinks: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in normalized_edges:
        backlinks[str(edge["target"])].append({
            "source": edge["source"],
            "source_namespace_id": edge["source_namespace_id"],
            "type": edge.get("type"),
            "provenance": edge.get("provenance"),
        })
    for entry in entries:
        entry["backlinks"] = sorted(
            backlinks.get(entry["id"], []),
            key=lambda item: (str(item["source"]), str(item.get("type", ""))),
        )

    corpora: dict[str, Any] = {}
    corpus_ids = sorted({entry["corpus"] for entry in entries})
    for corpus in corpus_ids:
        library = [entry for entry in entries if entry["corpus"] == corpus]
        graph_edges = [
            edge for edge in normalized_edges
            if edge.get("source_corpus") == corpus
            and edge.get("target_corpus") == corpus
            and not edge.get("bridge")
        ]
        corpora[corpus] = {
            "namespace": corpus,
            "library": library,
            "graph": {
                "nodes": [
                    {
                        "namespace_id": entry["namespace_id"],
                        "id": entry["id"],
                        "title": entry["metadata"].get("title", entry["id"]),
                        "type": entry["metadata"].get("type"),
                        "domain": entry["metadata"].get("domain"),
                    }
                    for entry in library
                ],
                "edges": graph_edges,
            },
            "filters": {
                field: sorted({
                    str(entry["metadata"].get(field))
                    for entry in library if entry["metadata"].get(field) is not None
                })
                for field in ("type", "domain", "status", "authority", "visibility")
            },
        }

    bridges = [
        dict(edge, enabled_by_default=False)
        for edge in normalized_edges if edge.get("bridge")
    ]
    legacy_count = sum(1 for entry in entries if entry["legacy_boundary"]["legacy"])
    registry_errors = [str(item) for item in registry_data.get("errors", [])]
    warnings = [str(item) for item in registry_data.get("warnings", [])] + source_errors
    state = "error" if registry_errors else ("partial" if warnings or legacy_count else "fresh")
    semantic = {
        "schema_version": registry_data.get("schema_version"),
        "corpora": corpora,
        "bridges": bridges,
        "bridge_policy": "typed-authored-bridges-disabled-by-default",
        "external_references": external_references,
        "unresolved_references": sorted(
            str(item) for item in registry_data.get("unresolved_references", [])
        ),
    }
    return {
        "boundary": boundary(
            state,
            fingerprint_value=fingerprint(semantic),
            indexed_roots=[".ai/", "project/docs/"],
            include_rules=["registered control-plane Markdown", "governed or baselined product Markdown"],
            exclude_rules=["unregistered files", "unresolved targets as graph nodes"],
            omitted_count=len(source_errors),
            errors=registry_errors,
            warnings=warnings,
            rebuild_guidance="Run `python scripts/ai_cli.py docs build` after resolving docs lint errors.",
        ),
        **semantic,
    }
