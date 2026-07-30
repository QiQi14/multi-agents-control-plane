"""Compatibility projection for the accepted Task 193 reader runtime.

The production knowledge projection is the only input.  This module does not
walk the repository, reopen task artifacts, or infer semantic relationships.
It reshapes the governed model into the two globals consumed by the accepted
reader assets:

``window.CP_DATA`` and ``window.CONTROL_PLANE_PROJECT``.

Governed task source, Project file metadata, and symbol positions are retained
when present. Project source-code snapshots remain explicitly unavailable
because ai-impact export contract v1 intentionally excludes source bytes.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
from pathlib import Path, PurePosixPath
import re
import shutil
from typing import Any

from scripts.ai_plane.knowledge_projection.common import canonical_json, sanitize
from scripts.ai_plane.knowledge_projection.task_presentation import (
    TaskPresentationError,
    build_task_presentation,
    contains_source_locator,
    verified_media_alias_records,
)


class AcceptedReaderAdapterError(ValueError):
    """Raised when a source model cannot truthfully satisfy the reader contract."""


_DOC_GROUPS: dict[str, tuple[str, str]] = {
    "agent": ("Roles & Workflows", "How the agents divide the work"),
    "workflow": ("Roles & Workflows", "How the agents divide the work"),
    "rule": ("Rules & Governance", "Binding law the plane enforces"),
    "project-doc": ("Project & Architecture", "What this repository is and how it is built"),
    "architecture": ("Project & Architecture", "What this repository is and how it is built"),
    "overview": ("Project & Architecture", "What this repository is and how it is built"),
    "user-doc": ("Project & Architecture", "How the product is understood and used"),
    "decision": ("Decisions & History", "Why the project is shaped this way"),
    "memory": ("Knowledge & Memory", "What earlier work taught us"),
    "migration": ("Knowledge & Memory", "What earlier work taught us"),
    "skill": ("Skills & Craft", "Reusable practice packs"),
    "spec": ("Specs & Templates", "Review specifications and templates"),
    "technical-spec": ("Specs & Templates", "Technical specifications for implementation"),
    "software-spec": ("Specs & Templates", "Product and software specifications"),
}

_DOC_READER_GOALS = {
    "agent": "Read this to know what a role is accountable for.",
    "workflow": "Read this to run a phase end to end.",
    "rule": "Read this before you write code that the plane will gate.",
    "project-doc": "Read this to understand the system as it is today.",
    "architecture": "Read this to understand how the product is structured.",
    "overview": "Read this for the product-level orientation.",
    "decision": "Read this to learn why a choice was made, and when.",
    "memory": "Read this to avoid repeating a solved problem.",
    "migration": "Read this for the record of a completed migration.",
    "skill": "Read this when you need the craft standard for a stack.",
    "spec": "Read this as a governed specification.",
    "technical-spec": "Read this for the implementation contract.",
    "software-spec": "Read this for the product behavior contract.",
    "user-doc": "Read this to understand or operate the product.",
}

_TITLE_PREFIX = {
    "rule": "Rule: ",
    "workflow": "Workflow: ",
    "agent": "Agent Role: ",
    "skill": "Skill: ",
}

_LIFECYCLE = {
    "active": ("active", "Active", 0),
    "queue": ("queued", "Queued", 1),
    "queued": ("queued", "Queued", 1),
    "done": ("done", "Done", 2),
    "archive": ("archived", "Archived", 3),
    "archived": ("archived", "Archived", 3),
}

_SPEC_KEYS = {
    "input_contract", "output_contract", "acceptance_tests", "target_files",
    "provisional_target_files", "forbidden_files", "known_risks", "commands",
}
_RELATION_KEYS = {
    "depends_on", "blocked_by", "decomposed_into", "parallel_safe_with",
    "parallelizable_with", "informed_by", "coordinate_with", "slice_ref",
    "decision_ref", "parent", "parent_task",
}
_CONTEXT_KEYS = {
    "risk", "preferred_tool", "review_tool", "secondary_tool",
    "isolation_strategy", "verification_scope", "priority",
}

_PATHISH = re.compile(r"^[A-Za-z0-9_.*{}!?+\-\[\]]+(?:/[A-Za-z0-9_.*{}!?+\-\[\]]+)+/?$")
_ATX = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def _as_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple)):
        result: list[str] = []
        for item in value:
            result.extend(_as_list(item))
        return result
    if isinstance(value, dict):
        return [
            f"{key}: {_as_text(item)}"
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        ]
    text = str(value).strip()
    return [text] if text else []


def _as_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, (list, tuple)):
        return "\n".join(f"- {item}" for item in _as_list(value))
    if isinstance(value, dict):
        return "\n".join(
            f"- **{key}:** {_as_text(item)}"
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        )
    return str(value).strip()


def _display_title(title: str, doc_type: str) -> str:
    prefix = _TITLE_PREFIX.get(doc_type)
    if prefix and title.startswith(prefix):
        return title[len(prefix):].strip()
    return title


def _summary(markdown: str) -> str:
    """Return a deterministic, presentation-only first prose paragraph."""
    lines: list[str] = []
    in_fence = False
    for raw in markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = raw.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not stripped:
            if lines:
                break
            continue
        if _ATX.match(stripped):
            if lines:
                break
            continue
        stripped = re.sub(r"^>\s?", "", stripped)
        stripped = re.sub(r"^(?:[-*+]|\d+\.)\s+", "", stripped)
        if stripped:
            lines.append(stripped)
        if len(" ".join(lines)) > 280:
            break
    text = " ".join(lines)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\*\*([^*]*)\*\*", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return text.strip()


def _outline(markdown: str) -> list[dict[str, Any]]:
    headings: list[dict[str, Any]] = []
    seen: Counter[str] = Counter()
    in_fence = False
    for raw in markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = raw.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _ATX.match(stripped)
        if not match:
            continue
        text = re.sub(r"`([^`]*)`", r"\1", match.group(2)).strip()
        base = re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")
        base = base or "section"
        seen[base] += 1
        anchor = base if seen[base] == 1 else f"{base}-{seen[base] - 1}"
        headings.append({"level": len(match.group(1)), "text": text, "anchor": anchor})
    return headings


def _documents_payload(system: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    graph_by_corpus: dict[str, Any] = {}
    graph_edges: list[dict[str, Any]] = []
    corpora = system.get("corpora", {})
    if not isinstance(corpora, dict):
        raise AcceptedReaderAdapterError("documents.corpora must be a mapping")

    for corpus_name, corpus in sorted(corpora.items(), key=lambda pair: str(pair[0])):
        if not isinstance(corpus, dict):
            continue
        library = corpus.get("library", [])
        if not isinstance(library, list):
            continue
        corpus_ids: list[str] = []
        for entry in library:
            if not isinstance(entry, dict):
                continue
            metadata = entry.get("metadata", {})
            if not isinstance(metadata, dict):
                metadata = {}
            doc_id = str(entry.get("id", ""))
            if not doc_id:
                continue
            corpus_ids.append(doc_id)
            doc_type = str(metadata.get("type") or "other")
            title = str(metadata.get("title") or metadata.get("name") or doc_id)
            group, group_hint = _DOC_GROUPS.get(doc_type, ("Other", "Other governed material"))
            body = str(entry.get("body") or "")
            warning = entry.get("legacy_boundary", {}).get("warning") if isinstance(
                entry.get("legacy_boundary"), dict
            ) else None
            docs.append({
                "id": doc_id,
                "namespaceId": entry.get("namespace_id"),
                "corpus": str(entry.get("corpus") or corpus_name),
                "title": title,
                "displayTitle": _display_title(title, doc_type),
                "type": doc_type,
                "domain": str(metadata.get("domain") or ""),
                "status": str(metadata.get("status") or ""),
                "owner": str(metadata.get("owner") or ""),
                "updated": str(metadata.get("updated") or metadata.get("last_reviewed") or ""),
                "version": str(metadata.get("version") or ""),
                "authority": metadata.get("authority"),
                "visibility": metadata.get("visibility"),
                "path": str(metadata.get("path") or ""),
                "group": group,
                "groupHint": group_hint,
                "readerGoal": _DOC_READER_GOALS.get(doc_type, "Read this as governed repository context."),
                "summary": _summary(body),
                "summaryProvenance": "layout-only:first-prose-paragraph-from-projected-body",
                "outline": _outline(body),
                "relations": sanitize(metadata.get("relations", [])),
                "backlinks": sanitize(entry.get("backlinks", [])),
                "bytes": len(body.encode("utf-8")),
                "sourceIssues": [str(warning)] if warning else [],
                "body": body,
                "sourceBoundary": {
                    "frontmatter_included": False,
                    "reason": "production projection retains parsed metadata and Markdown body separately",
                },
                "legacyBoundary": sanitize(entry.get("legacy_boundary", {})),
                "metadata": sanitize(metadata),
            })

        corpus_graph = corpus.get("graph", {})
        edges = corpus_graph.get("edges", []) if isinstance(corpus_graph, dict) else []
        normalized: list[dict[str, Any]] = []
        for edge in edges if isinstance(edges, list) else []:
            if not isinstance(edge, dict):
                continue
            normalized_edge = {
                "source": str(edge.get("source", "")),
                "target": str(edge.get("target", "")),
                "type": str(edge.get("type") or "related"),
                "provenance": str(edge.get("provenance") or "unknown"),
                "sourceCorpus": edge.get("source_corpus", corpus_name),
                "targetCorpus": edge.get("target_corpus", corpus_name),
            }
            normalized.append(normalized_edge)
            graph_edges.append(normalized_edge)
        graph_by_corpus[str(corpus_name)] = {
            "namespace": corpus.get("namespace", corpus_name),
            "documentIds": sorted(corpus_ids),
            "edges": sorted(
                normalized,
                key=lambda item: (
                    item["source"], item["target"], item["type"], item["provenance"]
                ),
            ),
            "filters": sanitize(corpus.get("filters", {})),
        }

    docs.sort(key=lambda item: (
        item["group"], item["type"], item["displayTitle"].lower(), item["id"]
    ))
    degree: Counter[str] = Counter()
    graph_edges.sort(key=lambda item: (
        item["source"], item["target"], item["type"], item["provenance"]
    ))
    for edge in graph_edges:
        degree[edge["source"]] += 1
        degree[edge["target"]] += 1
    graph = {
        "nodes": [
            {
                "id": doc["id"],
                "namespaceId": doc["namespaceId"],
                "corpus": doc["corpus"],
                "title": doc["displayTitle"],
                "type": doc["type"],
                "group": doc["group"],
                "status": doc["status"],
                "degree": degree[doc["id"]],
            }
            for doc in docs
        ],
        "edges": graph_edges,
        "byCorpus": graph_by_corpus,
        "bridges": sanitize(system.get("bridges", [])),
        "bridgePolicy": system.get("bridge_policy"),
        "bridgesEnabledByDefault": False,
        "externalReferences": sanitize(system.get("external_references", [])),
        "unresolvedReferences": sanitize(system.get("unresolved_references", [])),
    }
    return docs, graph


def _area_for(path: str, task_slug: str) -> tuple[str, str] | None:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized.startswith(".ai/tasks/"):
        if f"/{task_slug}/" in f"/{normalized}/":
            return "own-task-folder", "This task's own folder"
        return "other-task-folders", "Other task folders and history"
    fixed = (
        (".ai/rules/", ("control-plane-rules", "Control-plane rules")),
        (".ai/workflows/", ("control-plane-workflows", "Control-plane workflows")),
        (".ai/agents/", ("control-plane-agents", "Agent role definitions")),
        (".ai/project/", ("control-plane-docs", "Control-plane project docs")),
        (".ai/memory/", ("control-plane-memory", "Typed memory")),
        (".ai/skills/", ("control-plane-skills", "Skill packs")),
        (".ai/_site/", ("generated-site", "Generated documentation site")),
        ("scripts/", ("plane-tooling", "Control-plane tooling (Python)")),
        ("project/docs/", ("product-docs", "Product documentation")),
        ("project/schemas/", ("product-schemas", "Product schemas")),
        ("project/generated/", ("product-generated", "Generated product output")),
        ("project/apps/", ("product-apps", "Product apps")),
    )
    for prefix, result in fixed:
        if normalized.startswith(prefix):
            return result
    if normalized.startswith("project/crates/"):
        parts = normalized.split("/")
        crate = parts[2].replace("**", "*") if len(parts) > 2 else "*"
        if not crate or "*" in crate:
            return "crates-all", "All Rust crates"
        return f"crate-{crate}", f"Rust crate: {crate}"
    if normalized.startswith("project/"):
        return "product-source", "Product source"
    if normalized.startswith(".ai/"):
        return "control-plane-other", "Control plane (other)"
    return None


def _scope_areas(entries: list[str], task_slug: str) -> tuple[list[dict[str, Any]], list[str]]:
    areas: dict[str, dict[str, Any]] = {}
    limits: list[str] = []
    for raw in entries:
        text = raw.strip().strip("`'\"").rstrip(".")
        if not text:
            continue
        if _PATHISH.match(text) or contains_source_locator(text):
            area = _area_for(text, task_slug)
            if area:
                key, label = area
                bucket = areas.setdefault(key, {
                    "key": key,
                    "label": label,
                    "count": 0,
                    "examples": [],
                    "authority": "presentation-only",
                })
                bucket["count"] += 1
                if len(bucket["examples"]) < 4 and text not in bucket["examples"]:
                    bucket["examples"].append(text)
                continue
            # Unknown technical locators remain source-only. They must never
            # be mislabeled as human semantic limits.
            if _PATHISH.match(text) or contains_source_locator(text):
                continue
        if text not in limits:
            limits.append(text)
    return sorted(areas.values(), key=lambda item: (-item["count"], item["label"])), limits


def _event_data(event: dict[str, Any]) -> dict[str, Any]:
    data = event.get("data")
    if isinstance(data, dict):
        return data
    boundary = event.get("boundary")
    return boundary if isinstance(boundary, dict) else {}


def _receipt_summary(event: dict[str, Any], role: str) -> dict[str, Any]:
    data = _event_data(event)
    actor_data = data.get("actor") if isinstance(data.get("actor"), dict) else {}
    revision = data.get("revision") if isinstance(data.get("revision"), dict) else {}
    decision = data.get("decision") if isinstance(data.get("decision"), dict) else {}
    gates = data.get("gates") if isinstance(data.get("gates"), list) else []
    actor_name = actor_data.get("name") or actor_data.get("family") or ""
    summary = {
        "file": PurePosixPath(str(event.get("path") or "")).name,
        "actor": str(actor_name),
        "tool": str(actor_data.get("tool") or data.get("tool") or ""),
        "baseCommit": str(revision.get("base_commit") or data.get("base_commit") or ""),
        "receiptId": event.get("receipt_id"),
        "legacy": bool(event.get("legacy")),
        "sourceEvent": sanitize(event),
    }
    if role == "executor":
        gate_result = "; ".join(
            str(gate.get("result"))
            for gate in gates if isinstance(gate, dict) and gate.get("result")
        )
        summary.update({
            "status": str(decision.get("status") or data.get("status") or ""),
            "testResult": str(decision.get("outcome") or gate_result or data.get("test_result") or ""),
        })
    else:
        summary.update({
            "decision": str(decision.get("outcome") or decision.get("status") or data.get("decision") or ""),
            "decisionStatus": str(decision.get("status") or data.get("status") or ""),
            "scopeCheck": str(data.get("scope_check") or ""),
            "contractCheck": str(data.get("contract_check") or ""),
            "testsVerified": str(data.get("tests_verified") or ""),
        })
    return summary


def _delivery(task: dict[str, Any]) -> dict[str, Any]:
    events = [item for item in task.get("receipt_events", []) if isinstance(item, dict)]
    executor_events = [
        item for item in events
        if item.get("role") == "executor" or item.get("legacy_role_hint") == "executor"
    ]
    qa_events = [
        item for item in events
        if item.get("role") == "qa" or item.get("legacy_role_hint") == "qa"
    ]
    stage_data = task.get("delivery_stage", {})
    if not isinstance(stage_data, dict):
        stage_data = {}
    if stage_data.get("reviewed_artifact_present") or qa_events:
        stage, label = "reviewed", "Reviewed"
        if stage_data.get("accepted_review"):
            label = "Reviewed · accepted"
    elif stage_data.get("executed_artifact_present") or executor_events:
        stage, label = "executed", "Executed · awaiting review"
    else:
        stage, label = "planned", "Planned · awaiting execution"
    integrity: list[str] = []
    if qa_events and not executor_events:
        integrity.append(
            "QA receipt exists without an executor receipt; the projected evidence boundary is incomplete."
        )
    if task.get("legacy_boundary", {}).get("incomplete"):
        integrity.append("Legacy receipt fields remain incomplete and are preserved separately.")
    return {
        "stage": stage,
        "label": label,
        "executorReceiptCount": len(executor_events),
        "qaReceiptCount": len(qa_events),
        "executor": _receipt_summary(executor_events[-1], "executor") if executor_events else None,
        "qa": _receipt_summary(qa_events[-1], "qa") if qa_events else None,
        "executorReceipts": [str(item.get("path") or "") for item in executor_events],
        "qaReceipts": [str(item.get("path") or "") for item in qa_events],
        "integrityNotes": integrity,
        "productionStage": sanitize(stage_data),
    }


def _feature_key(task: dict[str, Any]) -> tuple[str, str, str]:
    link = task.get("feature_link", {})
    if not isinstance(link, dict):
        link = {}
    feature_id = link.get("feature_id")
    label = str(link.get("display_label") or "")
    if isinstance(feature_id, str) and feature_id:
        return feature_id, label or feature_id, "explicit"
    if label:
        return f"legacy-label:{label}", label, "legacy-display-label-only"
    return "_unlabelled", "(no feature recorded)", "unlabelled"


def _reader_href(path_value: Any) -> str | None:
    """Return a local href relative to ``.ai/_site/index.html``."""
    path = str(path_value or "").replace("\\", "/")
    if not path or re.match(r"^[a-z]+://", path, re.I) or re.match(r"^[A-Za-z]:/", path):
        return None
    if path.startswith(".ai/"):
        return "../" + path[len(".ai/"):]
    return "../../" + path.lstrip("./")


def _artifact_with_reader_href(
    artifact: Any,
    resolutions: dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(artifact, dict):
        return None
    result = sanitize(artifact)
    recorded_path = str(result.get("path") or "")
    resolution = resolutions.get(recorded_path, {})
    resolution = resolution if isinstance(resolution, dict) else {}
    resolved_path = (
        str(resolution.get("resolved_path"))
        if resolution.get("state") == "verified" and resolution.get("resolved_path")
        else None
    )
    href = _reader_href(resolved_path)
    if href:
        result["readerHref"] = href
        result["resolvedPath"] = resolved_path
    result["readerResolution"] = {
        **sanitize(resolution),
        "state": str(resolution.get("state") or "unavailable"),
        "recordedPath": recorded_path,
        "resolvedPath": resolved_path,
        "guidance": str(
            resolution.get("guidance")
            or "No current repository file was verified for this recorded artifact path."
        ),
    }
    return result


def _task_evidence_alias(source: dict[str, Any]) -> dict[str, Any]:
    evidence_set = source.get("evidence_set")
    evidence_set = evidence_set if isinstance(evidence_set, dict) else {}
    items = [item for item in evidence_set.get("items", []) if isinstance(item, dict)]
    resolutions = source.get("evidence_artifact_resolutions", {})
    resolutions = (
        resolutions if isinstance(resolutions, dict) else {}
    )
    events = [item for item in source.get("receipt_events", []) if isinstance(item, dict)]
    projected_items: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    media: list[dict[str, Any]] = []
    visuals: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    commands: list[dict[str, Any]] = []
    environments: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    availability: Counter[str] = Counter()
    for item in items:
        projected = sanitize(item)
        artifact = _artifact_with_reader_href(item.get("artifact"), resolutions)
        if artifact is not None:
            projected["artifact"] = artifact
        projected_items.append(projected)
        evidence_id = item.get("evidence_id")
        kind = str(item.get("kind") or "unclassified")
        availability[str(item.get("availability") or "unrecorded")] += 1
        producer = item.get("producer") if isinstance(item.get("producer"), dict) else {}
        if producer:
            provenance.append({
                "evidenceId": evidence_id, "kind": kind, "producer": sanitize(producer),
            })
            if producer.get("command"):
                commands.append({
                    "command": producer.get("command"), "source": "evidence-producer",
                    "evidenceId": evidence_id, "kind": kind,
                })
            if isinstance(producer.get("environment"), dict):
                environments.append({
                    "environment": sanitize(producer["environment"]),
                    "source": "evidence-producer", "evidenceId": evidence_id,
                })
        if isinstance(item.get("coverage"), dict):
            coverage.append({"evidenceId": evidence_id, "coverage": sanitize(item["coverage"])})
        if artifact is not None:
            artifact_record = {
                "evidenceId": evidence_id,
                "kind": kind,
                "role": item.get("role"),
                "availability": item.get("availability"),
                "storage": item.get("storage"),
                "claim": item.get("claim"),
                "accessibilityText": item.get("accessibility_text"),
                **artifact,
            }
            artifacts.append(artifact_record)
            media_type = str(artifact.get("media_type") or "")
            if media_type.startswith(("image/", "audio/", "video/")):
                media.append(artifact_record)
            if media_type.startswith("image/"):
                visuals.append(artifact_record)

    for event in events:
        data = _event_data(event)
        receipt_id = event.get("receipt_id")
        if isinstance(data.get("environment"), dict):
            environments.append({
                "environment": sanitize(data["environment"]), "source": "receipt",
                "receiptId": receipt_id,
                "role": event.get("role") or event.get("legacy_role_hint"),
            })
        gates = data.get("gates") if isinstance(data.get("gates"), list) else []
        for gate in gates:
            if isinstance(gate, dict) and gate.get("command"):
                commands.append({
                    "command": gate.get("command"), "result": gate.get("result"),
                    "source": "receipt-gate", "receiptId": receipt_id,
                    "role": event.get("role") or event.get("legacy_role_hint"),
                })

    return {
        "evidenceSetId": evidence_set.get("evidence_set_id"),
        "items": projected_items,
        "generatedResults": [item for item in projected_items if item.get("kind") == "generated-result"],
        "expectedReferences": [item for item in projected_items if item.get("kind") == "expected-reference"],
        "goldens": [item for item in projected_items if item.get("kind") == "golden"],
        "comparisonDiffs": [item for item in projected_items if item.get("kind") == "comparison-diff"],
        "artifacts": artifacts,
        "media": media,
        "visuals": visuals,
        "coverage": coverage,
        "commands": commands,
        "environment": environments,
        "provenance": provenance,
        "availability": dict(sorted(availability.items())),
        "inventory": sanitize(source.get("evidence_inventory", {})),
        "receipts": sanitize(events),
        "notes": sanitize(evidence_set.get("notes", [])),
        "classificationRule": (
            "Evidence kinds remain disjoint; expected-reference is never a generated-result."
        ),
    }


def _task_review_alias(source: dict[str, Any]) -> dict[str, Any]:
    events = [item for item in source.get("receipt_events", []) if isinstance(item, dict)]
    context_items = [item for item in source.get("context_items", []) if isinstance(item, dict)]
    closeout = source.get("closeout") if isinstance(source.get("closeout"), dict) else {}
    contract = source.get("contract") if isinstance(source.get("contract"), dict) else {}
    qa_rounds = [
        item for item in events
        if item.get("role") == "qa" or item.get("legacy_role_hint") == "qa"
    ]
    notes: list[dict[str, Any]] = []
    for event in events:
        data = _event_data(event)
        receipt_notes = data.get("notes") if isinstance(data.get("notes"), list) else []
        notes.extend({"receiptId": event.get("receipt_id"), "note": sanitize(note)} for note in receipt_notes)

    def context_of(*types: str) -> list[dict[str, Any]]:
        allowed = set(types)
        return [item for item in context_items if str(item.get("type") or "") in allowed]

    known_types = {"finding", "risk", "limitation", "note", "follow-up", "follow_up", "followup"}
    return {
        "rounds": sanitize(qa_rounds),
        "findings": sanitize(context_of("finding")),
        "risks": sanitize(context_of("risk")),
        "limitations": sanitize(context_of("limitation")),
        "notes": notes + sanitize(context_of("note")),
        "followUps": sanitize(context_of("follow-up", "follow_up", "followup")),
        "otherContext": sanitize([
            item for item in context_items if str(item.get("type") or "") not in known_types
        ]),
        "contextItems": sanitize(context_items),
        "dispositions": sanitize(closeout.get("context_dispositions", [])),
        "acceptedReceiptId": closeout.get("accepted_receipt_id"),
        "closeout": sanitize(closeout) if closeout else None,
        "receipts": sanitize(events),
        "contractRisks": sanitize(contract.get("known_risks")),
    }


def _resolved(refs: list[str], task_ids: set[str]) -> list[dict[str, Any]]:
    return [{"ref": ref, "id": ref if ref in task_ids else None} for ref in refs]


def _tasks_payload(system: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    source_tasks = [item for item in system.get("tasks", []) if isinstance(item, dict)]
    task_ids = {str(item.get("task_id")) for item in source_tasks}
    parent_by_task: dict[str, str] = {}
    for item in source_tasks:
        contract = item.get("contract", {})
        if not isinstance(contract, dict):
            continue
        parent = contract.get("parent_task") or contract.get("parent")
        if isinstance(parent, str) and parent in task_ids:
            parent_by_task[str(item.get("task_id"))] = parent

    child_slices: defaultdict[str, list[str]] = defaultdict(list)
    for child, parent in parent_by_task.items():
        child_slices[parent].append(child)

    tasks: list[dict[str, Any]] = []
    feature_buckets: dict[str, dict[str, Any]] = {}
    for source in source_tasks:
        contract = source.get("contract", {})
        if not isinstance(contract, dict):
            contract = {}
        task_id = str(source.get("task_id") or "")
        source_path = str(source.get("source_path") or "")
        task_folder = PurePosixPath(source_path).parent
        slug = task_folder.name or task_id
        source_lifecycle = str(source.get("lifecycle") or "")
        lifecycle, lifecycle_label, lifecycle_order = _LIFECYCLE.get(
            source_lifecycle, ("unknown", "Unfiled", 4)
        )
        lineage = [str(item) for item in source.get("lifecycle_lineage", [])]
        if lineage and lineage[-1] == slug:
            lineage = lineage[:-1]
        try:
            presentation = build_task_presentation(source, contract, slug)
        except TaskPresentationError as error:
            raise AcceptedReaderAdapterError(
                f"task {task_id}: {error}"
            ) from error
        feature_key = str(presentation["featureKey"] or "_unlabelled")
        feature_label = str(presentation["featureLabel"] or "(no feature recorded)")
        identity_state = str(presentation["featureState"])
        bucket = feature_buckets.setdefault(feature_key, {
            "key": feature_key,
            "label": feature_label,
            "labels": [],
            "variantCount": 0,
            "count": 0,
            "lifecycle": Counter(),
            "identityState": identity_state,
        })
        if feature_label not in bucket["labels"]:
            bucket["labels"].append(feature_label)
        bucket["variantCount"] = len(bucket["labels"])
        bucket["count"] += 1
        bucket["lifecycle"][lifecycle] += 1

        target_entries = _as_list(contract.get("target_files"))
        provisional_entries = _as_list(contract.get("provisional_target_files"))
        target_provisional = not target_entries and bool(provisional_entries)
        if target_provisional:
            target_entries = provisional_entries
        forbidden_entries = _as_list(contract.get("forbidden_files"))
        areas_touched, allow_limits = _scope_areas(target_entries, slug)
        areas_off_limits, deny_limits = _scope_areas(forbidden_entries, slug)
        limits = deny_limits + [item for item in allow_limits if item not in deny_limits]

        depends = _as_list(contract.get("depends_on")) or [
            str(item) for item in source.get("dependencies", [])
        ]
        blocked = _as_list(contract.get("blocked_by"))
        decomposed = _as_list(contract.get("decomposed_into"))
        parallel = (
            _as_list(contract.get("parallel_safe_with"))
            + _as_list(contract.get("parallelizable_with"))
        )
        informed = (
            _as_list(contract.get("informed_by"))
            + _as_list(contract.get("coordinate_with"))
        )
        slices = sorted({
            item["id"] for item in _resolved(decomposed, task_ids) if item["id"]
        } | set(child_slices.get(task_id, [])))

        context = {
            key: _as_text(contract.get(key))
            for key in sorted(_CONTEXT_KEYS) if contract.get(key) not in (None, "")
        }
        routing = {
            str(key): sanitize(value)
            for key, value in sorted(contract.items(), key=lambda pair: str(pair[0]))
            if str(key).startswith("routing_") and value not in (None, "")
        }
        known = (
            _SPEC_KEYS | _RELATION_KEYS | _CONTEXT_KEYS
            | {key for key in contract if str(key).startswith("routing_")}
            | {"id", "title", "feature", "feature_id", "status"}
        )
        extras = {
            str(key): _as_text(value)
            for key, value in sorted(contract.items(), key=lambda pair: str(pair[0]))
            if key not in known and value not in (None, "")
        }
        events = [item for item in source.get("receipt_events", []) if isinstance(item, dict)]
        evidence_alias = _task_evidence_alias(source)
        review_alias = _task_review_alias(source)
        raw_contract = str(source.get("raw_contract") or "")
        raw_available = source.get("raw_contract") is not None
        canonical_events: list[dict[str, Any]] = []
        seen_event_keys: set[tuple[str, str]] = set()
        for event_index, event in enumerate(events):
            receipt_id = event.get("receipt_id")
            recorded_path = event.get("path")
            if receipt_id:
                event_key = ("receipt-id", str(receipt_id))
            elif recorded_path:
                event_key = ("legacy-path", str(recorded_path))
            else:
                event_key = ("ordinal", str(event_index))
            if event_key in seen_event_keys:
                continue
            seen_event_keys.add(event_key)
            canonical_events.append(sanitize(event))
        source_projection = {
            "contract": {
                "path": source_path,
                "sha256": source.get("source_sha256"),
                "bytes": source.get("source_bytes"),
                "raw": raw_contract,
                "rawAvailable": raw_available,
            },
            "receipts": canonical_events,
            "evidenceSet": sanitize(source.get("evidence_set")),
            "closeout": sanitize(source.get("closeout")),
            "legacyBoundary": sanitize(source.get("legacy_boundary", {})),
        }
        sources_alias = {
            "contract": {
                "path": source_path,
                "raw": raw_contract,
                "sha256": source.get("source_sha256"),
                "bytes": source.get("source_bytes"),
                "parsed": sanitize(contract),
                "rawAvailable": raw_available,
            },
            "receipts": [
                {
                    "path": event.get("path"),
                    "receiptId": event.get("receipt_id"),
                    "role": event.get("role") or event.get("legacy_role_hint"),
                    "legacy": bool(event.get("legacy")),
                }
                for event in events
            ],
            "evidenceSetId": evidence_alias["evidenceSetId"],
            "closeout": sanitize(source.get("closeout")),
            "sourceProjectionPath": source_path,
        }
        sidecars = sorted({
            PurePosixPath(str(event.get("path"))).name
            for event in events if event.get("path")
        })
        tasks.append({
            "id": task_id,
            "slug": slug,
            "title": presentation["title"],
            "titleState": presentation["titleState"],
            "path": task_folder.as_posix(),
            "contractPath": source_path,
            "lifecycle": lifecycle,
            "lifecycleLabel": lifecycle_label,
            "lifecycleOrder": lifecycle_order,
            "shelf": "/".join(lineage),
            "featureLabel": presentation["featureLabel"],
            "featureKey": presentation["featureKey"],
            "featureIdentityState": identity_state,
            "statusNote": _as_text(contract.get("status")),
            "delivery": _delivery(source),
            "presentation": presentation,
            "context": context,
            "routing": routing,
            "spec": {
                "input": _as_text(contract.get("input_contract")),
                "output": _as_text(contract.get("output_contract")),
                "acceptance": _as_list(contract.get("acceptance_tests")),
                "targetFiles": target_entries,
                "targetProvisional": target_provisional,
                "forbiddenFiles": forbidden_entries,
                "areasTouched": areas_touched,
                "areasOffLimits": areas_off_limits,
                "limits": limits,
                "commands": _as_list(contract.get("commands")),
                "risks": _as_text(contract.get("known_risks")),
            },
            "rel": {
                "dependsOn": depends,
                "blockedBy": blocked,
                "decomposedInto": decomposed,
                "parallelWith": parallel,
                "informedBy": informed,
                "sliceRef": _as_text(contract.get("slice_ref")),
                "relatedDocs": sorted(str(item) for item in source.get("document_links", [])),
                "mentions": [],
                "resolved": {
                    "dependsOn": _resolved(depends, task_ids),
                    "blockedBy": _resolved(blocked, task_ids),
                    "decomposedInto": _resolved(decomposed, task_ids),
                    "parallelWith": _resolved(parallel, task_ids),
                    "informedBy": _resolved(informed, task_ids),
                },
                "parent": parent_by_task.get(task_id),
                "blocks": sorted(str(item) for item in source.get("reverse_dependencies", [])),
                "slices": slices,
            },
            "extras": extras,
            "sidecars": sidecars,
            "subfolders": [],
            "raw": raw_contract,
            "rawBoundary": {
                "state": "available" if raw_available else "unavailable",
                "sha256": source.get("source_sha256"),
                "bytes": source.get("source_bytes"),
                "provenance": "governed task source projection",
            },
            "contract": sanitize(contract),
            "evidence": evidence_alias,
            "review": review_alias,
            "reviewAndFollowups": review_alias,
            "sources": sources_alias,
            "receipts": sanitize(events),
            "contextRecords": sanitize(source.get("context_items", [])),
            "deliveryStage": sanitize(source.get("delivery_stage", {})),
            "receipt_events": sanitize(source.get("receipt_events", [])),
            "evidence_set": sanitize(source.get("evidence_set")),
            "evidence_inventory": sanitize(source.get("evidence_inventory", {})),
            "context_items": sanitize(source.get("context_items", [])),
            "closeout": sanitize(source.get("closeout")),
            "legacy_boundary": sanitize(source.get("legacy_boundary", {})),
            "sourceProjection": source_projection,
        })

    tasks.sort(key=lambda item: (
        item["lifecycleOrder"], item["path"], item["id"]
    ))
    features = []
    for bucket in feature_buckets.values():
        bucket = dict(bucket)
        bucket["lifecycle"] = dict(sorted(bucket["lifecycle"].items()))
        features.append(bucket)
    features.sort(key=lambda item: (-item["count"], item["label"].lower(), item["key"]))

    area_counts: dict[str, dict[str, Any]] = {}
    for task in tasks:
        for area in task["spec"]["areasTouched"]:
            bucket = area_counts.setdefault(area["key"], {
                "key": area["key"],
                "label": area["label"],
                "taskCount": 0,
                "authority": "presentation-only",
            })
            bucket["taskCount"] += 1
    areas = sorted(area_counts.values(), key=lambda item: (-item["taskCount"], item["label"]))
    return tasks, features, areas


def _contextual_name(crate_name: str, module_path: str, local_name: str) -> str:
    if not local_name:
        return "::".join(part for part in (crate_name, module_path) if part)
    if crate_name and (local_name == crate_name or local_name.startswith(crate_name + "::")):
        return local_name
    parts: list[str] = []
    for part in (crate_name, module_path, local_name):
        if not part:
            continue
        if parts and (part == parts[-1] or part.startswith(parts[-1] + "::")):
            if part.startswith(parts[-1] + "::"):
                parts[-1] = part
            continue
        parts.append(part)
    return "::".join(parts)


def _project_payload(model: dict[str, Any], system: dict[str, Any]) -> dict[str, Any]:
    boundary = system.get("boundary", {})
    if not isinstance(boundary, dict):
        raise AcceptedReaderAdapterError("project_intelligence.boundary must be a mapping")
    packages = [item for item in system.get("packages", []) if isinstance(item, dict)]
    modules = [item for item in system.get("modules", []) if isinstance(item, dict)]
    source_files = [item for item in system.get("files", []) if isinstance(item, dict)]
    source_nodes = [item for item in system.get("semantic_nodes", []) if isinstance(item, dict)]
    hierarchy = [item for item in system.get("semantic_hierarchy", []) if isinstance(item, dict)]
    source_edges = [item for item in system.get("relations", []) if isinstance(item, dict)]
    pending = [item for item in system.get("pending_boundaries", []) if isinstance(item, dict)]

    if boundary.get("state") == "fresh" and (
        not packages or not source_files or not hierarchy or not source_nodes
    ):
        raise AcceptedReaderAdapterError(
            "fresh Project Intelligence must contain packages, files, semantic hierarchy, and nodes"
        )

    node_source = {str(node.get("id")): node for node in source_nodes}
    incoming: Counter[str] = Counter()
    outgoing: Counter[str] = Counter()
    for edge in source_edges:
        outgoing[str(edge.get("source_id"))] += 1
        incoming[str(edge.get("target_id"))] += 1
    pending_by_node: Counter[str] = Counter(
        str(item.get("source_node_id"))
        for item in pending if item.get("source_node_id")
    )
    pending_by_file: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in pending:
        pending_by_file[str(item.get("owner_path") or "")].append(item)

    nodes: list[dict[str, Any]] = []
    for source in source_nodes:
        node_id = str(source.get("id") or "")
        crate_name = str(source.get("rust_crate_name") or "")
        module_path = str(source.get("module_path") or "")
        identity_name = str(source.get("identity_name") or source.get("qualified_name") or "")
        qualified_name = str(source.get("qualified_name") or identity_name)
        nodes.append({
            "id": node_id,
            "path": str(source.get("path") or ""),
            "kind": str(source.get("kind") or "unknown"),
            "identity": _contextual_name(crate_name, module_path, identity_name),
            "public": _contextual_name(crate_name, module_path, qualified_name),
            "identityName": identity_name,
            "qualifiedName": qualified_name,
            "row": source.get("start_row"),
            "column": source.get("start_column"),
            "endRow": source.get("end_row"),
            "endColumn": source.get("end_column"),
            "sourcePositionAvailable": all(
                source.get(key) is not None
                for key in ("start_row", "start_column", "end_row", "end_column")
            ),
            "crate": crate_name or "(workspace)",
            "module": module_path,
            "incoming": incoming[node_id],
            "outgoing": outgoing[node_id],
            "pending": pending_by_node[node_id],
            "source": sanitize(source),
        })
    nodes.sort(key=lambda item: item["id"])
    node_by_id = {node["id"]: node for node in nodes}

    edges = [
        {
            "source": str(item.get("source_id") or ""),
            "target": str(item.get("target_id") or ""),
            "kind": str(item.get("kind") or "related"),
            "provenance": str(item.get("provenance") or "unknown"),
            "confidence": item.get("confidence"),
        }
        for item in source_edges
        if str(item.get("source_id")) in node_by_id
        and str(item.get("target_id")) in node_by_id
    ]
    edges.sort(key=lambda item: (
        item["source"], item["target"], item["kind"], item["provenance"]
    ))

    module_by_path = {
        str(item.get("path")): item for item in modules if item.get("path")
    }
    file_by_path = {
        str(item.get("path")): item for item in source_files if item.get("path")
    }
    file_in: Counter[str] = Counter()
    file_out: Counter[str] = Counter()
    for edge in edges:
        source_path = node_by_id[edge["source"]]["path"]
        target_path = node_by_id[edge["target"]]["path"]
        file_out[source_path] += 1
        file_in[target_path] += 1

    files: list[dict[str, Any]] = []
    for row in hierarchy:
        path = str(row.get("path") or "")
        ids = [str(item) for item in row.get("semantic_node_ids", [])]
        module = module_by_path.get(path, {})
        file_meta = file_by_path.get(path, {})
        file_size = file_meta.get("size_bytes")
        file_sha256 = file_meta.get("sha256")
        file_metadata_available = bool(file_meta)
        file_pending = sorted(
            pending_by_file[path],
            key=lambda item: (
                int(item.get("start_row") or 0),
                int(item.get("start_column") or 0),
                str(item.get("spelling") or ""),
            ),
        )
        files.append({
            "path": path,
            "name": PurePosixPath(path).name,
            "crate": str(file_meta.get("rust_crate_name") or row.get("rust_crate_name") or "(workspace)"),
            "module": str(file_meta.get("module_path") or row.get("module_path") or ""),
            "size": file_size if isinstance(file_size, int) else 0,
            "sizeAvailable": isinstance(file_size, int),
            "sha256": str(file_sha256 or ""),
            "sha256Available": bool(file_sha256),
            "nodes": len(ids),
            "pending": len(file_pending),
            "incoming": file_in[path],
            "outgoing": file_out[path],
            "source": "",
            "sourceTruncated": False,
            "sourceAvailable": False,
            "sourceBoundary": {
                "state": "unavailable",
                "reason": "ai-impact export contract v1 retains file metadata but excludes source bytes",
            },
            "fileMetadataBoundary": {
                "state": "available" if file_metadata_available else "unavailable",
                "provenance": "ai-impact export contract v1 files[]",
            },
            "pendingSample": sanitize(file_pending),
            "purpose": sanitize(module.get("purpose")),
            "semanticNodeIds": ids,
            "sourceFile": sanitize(file_meta),
            "sourceHierarchy": sanitize(row),
        })
    files.sort(key=lambda item: item["path"])

    package_by_crate = {
        str(item.get("rust_semantic_target_name") or ""): item for item in packages
    }
    crate_names = sorted(
        set(package_by_crate)
        | {node["crate"] for node in nodes}
        | {file["crate"] for file in files}
    )
    clusters: list[dict[str, Any]] = []
    for crate_name in crate_names:
        package = package_by_crate.get(crate_name, {})
        crate_nodes = {node["id"] for node in nodes if node["crate"] == crate_name}
        crate_files = [file for file in files if file["crate"] == crate_name]
        clusters.append({
            "id": crate_name,
            "label": str(package.get("cargo_display_name") or crate_name),
            "packageId": package.get("package_id"),
            "rustCrateName": crate_name,
            "files": len(crate_files),
            "nodes": len(crate_nodes),
            "pending": sum(file["pending"] for file in crate_files),
            "resolvedEdges": sum(
                1 for edge in edges
                if edge["source"] in crate_nodes or edge["target"] in crate_nodes
            ),
            "purpose": sanitize(package.get("purpose")),
            "relatedProductDocumentIds": sanitize(
                package.get("related_product_document_ids", [])
            ),
            "sourcePackage": sanitize(package),
        })
    clusters.sort(key=lambda item: (-item["nodes"], item["label"], item["id"]))

    preferred_proof = sorted(
        [
            node for node in nodes
            if node["path"].replace("\\", "/").endswith("crates/core/src/math.rs")
            and node["public"].endswith("Vector3::mul")
            and "impl-header[impl Mul" in node["identity"]
        ],
        key=lambda node: (
            node["row"] if isinstance(node["row"], int) else 2**31,
            node["column"] if isinstance(node["column"], int) else 2**31,
            node["identity"], node["id"],
        ),
    )
    if len(preferred_proof) >= 2:
        proof_public = preferred_proof[0]["public"]
        proof_candidates = [
            node["id"] for node in preferred_proof if node["public"] == proof_public
        ]
    else:
        public_groups: defaultdict[str, list[str]] = defaultdict(list)
        for node in nodes:
            public_groups[node["public"]].append(node["id"])
        ambiguous = sorted(
            ((public_name, sorted(ids)) for public_name, ids in public_groups.items() if len(ids) > 1),
            key=lambda item: (item[0], item[1]),
        )
        proof_public, proof_candidates = ambiguous[0] if ambiguous else ("", [])

    source = model.get("source", {})
    if not isinstance(source, dict):
        source = {}
    commit = source.get("commit")
    commit_text = str(commit or "")
    include_rules = [str(item) for item in boundary.get("include_rules", [])]
    exclude_rules = [str(item) for item in boundary.get("exclude_rules", [])]
    state = str(boundary.get("state") or "unavailable")
    state_labels = {
        "fresh": "Semantic index current",
        "stale": "Semantic index stale",
        "partial": "Semantic index partial",
        "error": "Semantic index error",
        "unavailable": "Semantic index unavailable",
    }
    diagnostics = [
        str(item)
        for item in boundary.get("errors", []) + boundary.get("warnings", [])
    ]
    bundle = system.get("agent_result_bundles", {})
    if not isinstance(bundle, dict):
        bundle = {}
    unavailable_query = {
        "query": None,
        "command": None,
        "exitCode": None,
        "stdout": "",
        "stderr": "",
        "state": str(bundle.get("state") or "not-requested"),
        "exact": False,
        "nonfabricated": bool(bundle.get("nonfabricated", True)),
        "provenance": None,
    }
    named_bundle_items = {
        str(item.get("name")): item
        for item in bundle.get("items", [])
        if isinstance(item, dict) and item.get("name")
    }

    def accepted_query(name: str) -> dict[str, Any]:
        item = named_bundle_items.get(name)
        if item is None:
            return dict(unavailable_query)
        return {
            "query": item.get("query"),
            "command": sanitize(item.get("command")),
            "exitCode": item.get("exit"),
            "stdout": item.get("stdout") if isinstance(item.get("stdout"), str) else "",
            "stderr": item.get("stderr") if isinstance(item.get("stderr"), str) else "",
            "state": str(item.get("state") or bundle.get("state") or "unavailable"),
            "exact": item.get("exact") is True,
            "nonfabricated": item.get("nonfabricated") is True,
            "provenance": sanitize(item.get("provenance")),
        }
    counts = {
        "files": len(files),
        "nodes": len(nodes),
        "edges": len(edges),
        "pending": len(pending),
        "clusters": len(clusters),
    }
    return {
        "meta": {
            "sourceCommit": commit,
            "sourceShortCommit": commit_text[:12],
            "sourceBranch": source.get("branch"),
            "generatedAt": source.get("refreshed_at"),
            "freshness": state,
            "trackedChanges": [],
            "schemaVersion": system.get("contract_version"),
            "nodeIdentityVersion": None,
            "resolutionVersion": None,
            "binarySha256": None,
            "indexSha256": boundary.get("fingerprint"),
            "indexedRoot": ", ".join(str(item) for item in boundary.get("indexed_roots", [])),
            "includeRule": "; ".join(include_rules),
            "excludeRule": "; ".join(exclude_rules),
            "sourceFingerprint": system.get("source_fingerprint"),
            "projectionFingerprint": source.get("fingerprint"),
        },
        "status": {
            "state": "current" if state == "fresh" else state,
            "label": state_labels.get(state, f"Semantic index {state}"),
            "detail": diagnostics[0] if diagnostics else (
                f"Project Intelligence boundary reports {state}."
            ),
            "supported": include_rules,
            "limitations": exclude_rules,
        },
        "counts": counts,
        "clusters": clusters,
        "files": files,
        "nodes": nodes,
        "edges": edges,
        "pendingReasons": [
            {"reason": reason, "count": count}
            for reason, count in sorted(Counter(
                str(item.get("reason") or "unknown") for item in pending
            ).items(), key=lambda item: (-item[1], item[0]))
        ],
        "defaultSelection": nodes[0]["id"] if nodes else "",
        "proofSelection": {
            "public": proof_public,
            "candidates": proof_candidates,
            "derivation": "accepted Vector3::mul pair when present; otherwise first exact duplicate",
        },
        "agentContext": {
            "contract": str(bundle.get("guidance") or ""),
            "state": str(bundle.get("state") or "not-requested"),
            "items": sanitize(bundle.get("items", [])),
            "initialSync": None,
            "queries": {
                "vectorExplore": accepted_query("vectorExplore"),
                "scalarExplore": accepted_query("scalarExplore"),
            },
            "ranking": (
                "No query result ranking is embedded by the static export; "
                "run an exact ai-impact query."
            ),
            "limits": {
                "resolvedRelations": "all relations present in ai-impact export contract v1",
                "pendingSamplePerFile": "all projected pending boundaries",
                "source": "source bytes unavailable; governed file size/hash metadata retained",
                "omitted": sanitize(system.get("omissions", {})),
            },
        },
        "boundary": sanitize(boundary),
        "packages": sanitize(packages),
        "modules": sanitize(modules),
        "sourceFiles": sanitize(source_files),
        "semanticHierarchy": sanitize(hierarchy),
        "pendingBoundaries": sanitize(pending),
        "omissions": sanitize(system.get("omissions", {})),
        "views": sanitize(system.get("views", {})),
        "agentResultBundles": sanitize(bundle),
    }


def build_accepted_reader_payloads(model: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(CP_DATA, CONTROL_PLANE_PROJECT)`` for the accepted reader."""
    truth = model.get("truth_systems")
    if not isinstance(truth, dict):
        raise AcceptedReaderAdapterError("knowledge projection has no truth_systems mapping")
    required = ("documents", "tasks_features", "project_intelligence")
    missing = [name for name in required if not isinstance(truth.get(name), dict)]
    if missing:
        raise AcceptedReaderAdapterError(
            "knowledge projection is missing truth systems: " + ", ".join(missing)
        )
    documents_system = truth["documents"]
    tasks_system = truth["tasks_features"]
    project_system = truth["project_intelligence"]
    docs, graph = _documents_payload(documents_system)
    tasks, features, areas = _tasks_payload(tasks_system)
    lifecycle_counts = Counter(task["lifecycle"] for task in tasks)
    delivery_counts = Counter(task["delivery"]["stage"] for task in tasks)
    for key in ("active", "queued", "done", "archived", "unknown"):
        lifecycle_counts.setdefault(key, 0)
    for key in ("planned", "executed", "reviewed"):
        delivery_counts.setdefault(key, 0)
    counts = {
        "docs": len(docs),
        "docTypes": dict(sorted(Counter(doc["type"] for doc in docs).items())),
        "docGroups": dict(sorted(Counter(doc["group"] for doc in docs).items())),
        "tasks": len(tasks),
        "lifecycle": dict(sorted(lifecycle_counts.items())),
        "delivery": dict(sorted(delivery_counts.items())),
        "features": len(features),
        "featureLabels": len({
            task["featureLabel"] for task in tasks if task["featureLabel"]
        }),
        "graphNodes": len(graph["nodes"]),
        "graphEdges": len(graph["edges"]),
        "areas": len(areas),
    }
    source = model.get("source", {})
    if not isinstance(source, dict):
        source = {}
    cp_data = {
        "meta": {
            "generatedFrom": "production governed knowledge projection",
            "sourceCommit": source.get("commit"),
            "registrySchema": documents_system.get("schema_version"),
            "registryGenerator": None,
            "prototype": "accepted Task 193 runtime contract / production adapter",
            "note": "Build-time projection; the page makes no network request.",
            "projectionSchemaVersion": model.get("schema_version"),
            "projectionFingerprint": source.get("fingerprint"),
            "sourceState": source.get("state"),
        },
        "docs": docs,
        "tasks": tasks,
        "graph": graph,
        "features": features,
        "areas": areas,
        "counts": counts,
        "boundaries": {
            "source": sanitize(source),
            "documents": sanitize(documents_system.get("boundary", {})),
            "tasksFeatures": sanitize(tasks_system.get("boundary", {})),
        },
        "corpora": sanitize(graph["byCorpus"]),
    }
    project_data = _project_payload(model, project_system)
    return cp_data, project_data


def render_accepted_reader_javascript(model: dict[str, Any]) -> tuple[str, str]:
    """Encode deterministic Task 193-compatible JavaScript globals."""
    cp_data, project_data = build_accepted_reader_payloads(model)
    data_js = (
        "/* GENERATED from the governed knowledge projection; do not hand-edit. */\n"
        f"window.CP_DATA = {canonical_json(cp_data)};\n"
    )
    project_js = (
        "/* GENERATED from the governed Project Intelligence projection; do not hand-edit. */\n"
        f"window.CONTROL_PLANE_PROJECT = {canonical_json(project_data)};\n"
    )
    return data_js, project_js


def write_accepted_reader_assets(
    model: dict[str, Any],
    assets_dir: Path,
    *,
    repository_root: Path | None = None,
) -> tuple[Path, Path]:
    """Write deterministic ``data.js`` and ``project-data.js`` runtime assets."""
    data_js, project_js = render_accepted_reader_javascript(model)
    assets_dir.mkdir(parents=True, exist_ok=True)
    data_path = assets_dir / "data.js"
    project_path = assets_dir / "project-data.js"
    data_path.write_text(data_js, encoding="utf-8", newline="\n")
    project_path.write_text(project_js, encoding="utf-8", newline="\n")
    write_task_media_aliases(
        model,
        assets_dir,
        repository_root=repository_root,
    )
    return data_path, project_path


def write_task_media_aliases(
    model: dict[str, Any],
    assets_dir: Path,
    *,
    repository_root: Path | None = None,
) -> list[Path]:
    """Copy verified task media behind deterministic noncanonical aliases."""
    alias_dir = assets_dir / "task-media"
    if alias_dir.exists():
        shutil.rmtree(alias_dir)
    alias_dir.mkdir(parents=True, exist_ok=True)

    if repository_root is None:
        if assets_dir.name == "assets" and assets_dir.parent.name == "_site":
            ai_dir = assets_dir.parent.parent
            if ai_dir.name == ".ai":
                repository_root = ai_dir.parent
    if repository_root is None:
        return []

    root = repository_root.resolve()
    written: list[Path] = []
    for alias, source_relative, expected_sha256 in verified_media_alias_records(model):
        source = (root / PurePosixPath(source_relative)).resolve()
        try:
            source.relative_to(root)
        except ValueError:
            continue
        if not source.is_file():
            continue
        source_bytes = source.read_bytes()
        if expected_sha256 and hashlib.sha256(source_bytes).hexdigest() != expected_sha256:
            continue
        destination = assets_dir.parent / PurePosixPath(alias)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source_bytes)
        written.append(destination)
    return sorted(written)


__all__ = [
    "AcceptedReaderAdapterError",
    "build_accepted_reader_payloads",
    "render_accepted_reader_javascript",
    "write_accepted_reader_assets",
    "write_task_media_aliases",
]
