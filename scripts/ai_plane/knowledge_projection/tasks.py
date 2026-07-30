"""Task, receipt, evidence, context, and closeout projection."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath
from typing import Any

from scripts.ai_plane.knowledge_projection.common import boundary, fingerprint, sanitize
from scripts.ai_plane.primitives import parse_simple_yaml
from scripts.ai_plane.task_evidence import TaskEvidenceError
from scripts.ai_plane.task_evidence_legacy import read_task_evidence


LIFECYCLES = ("active", "queue", "done", "archive")


def _explicit_document_links(contract: dict[str, Any]) -> list[str]:
    links: set[str] = set()
    for field in ("document_ids", "related_documents", "documents"):
        value = contract.get(field)
        if isinstance(value, list):
            links.update(str(item) for item in value if isinstance(item, str) and item)
    return sorted(links)


def _role_hint(path_value: str) -> str | None:
    name = Path(path_value).name
    if name.startswith("receipt.executor"):
        return "executor"
    if name.startswith("receipt.qa"):
        return "qa"
    return None


def _events(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for receipt in evidence.get("receipts", []):
        if receipt.get("legacy"):
            events.append({
                "receipt_id": None,
                "path": receipt.get("path"),
                "role": None,
                "legacy_role_hint": _role_hint(str(receipt.get("path", ""))),
                "legacy": True,
                "boundary": sanitize(receipt.get("data")),
            })
            continue
        data = sanitize(receipt.get("data", {}))
        events.append({
            "receipt_id": data.get("receipt_id"),
            "path": receipt.get("path"),
            "role": data.get("role"),
            "sequence": data.get("sequence"),
            "decision": data.get("decision"),
            "legacy": False,
            "data": data,
        })
    closeout = evidence.get("closeout")
    order = closeout.get("receipt_events", []) if isinstance(closeout, dict) else []
    rank = {str(receipt_id): index for index, receipt_id in enumerate(order)}
    events.sort(
        key=lambda item: (
            rank.get(str(item.get("receipt_id")), len(rank)),
            str(item.get("path", "")),
        )
    )
    return events


def _delivery(events: list[dict[str, Any]], closeout: dict[str, Any] | None) -> dict[str, Any]:
    executed = any(
        event.get("role") == "executor" or event.get("legacy_role_hint") == "executor"
        for event in events
    )
    reviewed = any(
        event.get("role") == "qa" or event.get("legacy_role_hint") == "qa"
        for event in events
    )
    accepted_review = any(
        event.get("role") == "qa"
        and isinstance(event.get("decision"), dict)
        and event["decision"].get("status") == "accept"
        for event in events
    )
    return {
        "planned": True,
        "executed_artifact_present": executed,
        "reviewed_artifact_present": reviewed,
        "accepted_review": accepted_review,
        "closed": isinstance(closeout, dict),
        "accepted_receipt_id": closeout.get("accepted_receipt_id") if isinstance(closeout, dict) else None,
    }


def _evidence_inventory(evidence_set: dict[str, Any] | None) -> dict[str, Any]:
    items = evidence_set.get("items", []) if isinstance(evidence_set, dict) else []
    typed = [item for item in items if isinstance(item, dict)]
    return {
        "generated_results": [item.get("evidence_id") for item in typed if item.get("kind") == "generated-result"],
        "expected_references": [item.get("evidence_id") for item in typed if item.get("kind") == "expected-reference"],
        "goldens": [item.get("evidence_id") for item in typed if item.get("kind") == "golden"],
        "comparison_diffs": [item.get("evidence_id") for item in typed if item.get("kind") == "comparison-diff"],
        "unavailable_regenerable": [
            {
                "evidence_id": item.get("evidence_id"),
                "availability": item.get("availability"),
                "producer_command": (
                    item.get("producer", {}).get("command")
                    if isinstance(item.get("producer"), dict) else None
                ),
            }
            for item in typed
            if item.get("storage") == "regenerable" and item.get("availability") != "available"
        ],
        "classification_rule": "Evidence kinds remain disjoint; expected-reference is never a generated-result.",
    }


def _evidence_artifact_resolutions(
    evidence_set: dict[str, Any] | None,
    *,
    task_dir: Path,
    root: Path,
) -> dict[str, dict[str, Any]]:
    """Resolve recorded artifact paths without rewriting their provenance.

    Historical receipts retain the lifecycle path that was current when they
    were written. A task can later move from queue to done, so reader links
    must be resolved against both the recorded root-relative path and the
    task's current governed folder. Only existing files contained by the
    repository are exported as reader-resolvable.
    """
    items = evidence_set.get("items", []) if isinstance(evidence_set, dict) else []
    root_resolved = root.resolve()
    task_resolved = task_dir.resolve()
    resolutions: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("artifact"), dict):
            continue
        recorded = str(item["artifact"].get("path") or "")
        normalized = recorded.replace("\\", "/")
        if (
            not normalized
            or re.match(r"^[a-z]+://", normalized, re.I)
            or re.match(r"^[A-Za-z]:/", normalized)
            or PurePosixPath(normalized).is_absolute()
        ):
            continue
        parts = PurePosixPath(normalized).parts
        expected_sha256 = str(item["artifact"].get("sha256") or "").lower()
        sha256_recorded = bool(re.fullmatch(r"[0-9a-f]{64}", expected_sha256))
        mismatch: dict[str, Any] | None = None
        candidates = [root_resolved.joinpath(*parts)]
        if not normalized.startswith(".ai/"):
            candidates.append(task_resolved.joinpath(*parts))
        if (
            len(parts) > 4
            and parts[0] == ".ai"
            and parts[1] == "tasks"
            and parts[2] in LIFECYCLES
        ):
            referenced_task = parts[3]
            suffix = parts[4:]
            candidates.extend(
                root_resolved / ".ai" / "tasks" / lifecycle / referenced_task / Path(*suffix)
                for lifecycle in LIFECYCLES
            )
        task_indexes = [index for index, part in enumerate(parts) if part == task_dir.name]
        if task_indexes and task_indexes[-1] + 1 < len(parts):
            candidates.append(task_resolved.joinpath(*parts[task_indexes[-1] + 1:]))
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
                resolved.relative_to(root_resolved)
            except (OSError, ValueError):
                continue
            if resolved.is_file():
                relative = resolved.relative_to(root_resolved).as_posix()
                try:
                    actual_sha256 = hashlib.sha256(resolved.read_bytes()).hexdigest()
                except OSError:
                    continue
                if sha256_recorded and actual_sha256 != expected_sha256:
                    mismatch = {
                        "state": "hash-mismatch",
                        "resolved_path": None,
                        "candidate_path": relative,
                        "recorded_sha256": expected_sha256,
                        "actual_sha256": actual_sha256,
                        "sha256_verified": False,
                        "guidance": (
                            "A current file exists at the lifecycle-resolved path, but its bytes "
                            "do not match the evidence record."
                        ),
                    }
                    continue
                resolutions[recorded] = {
                    "state": "verified",
                    "resolved_path": relative,
                    "recorded_sha256": expected_sha256 if sha256_recorded else None,
                    "actual_sha256": actual_sha256,
                    "sha256_verified": sha256_recorded,
                    "guidance": (
                        "Current repository file exists and matches the recorded SHA-256."
                        if sha256_recorded
                        else "Current repository file exists; no valid recorded SHA-256 was available."
                    ),
                }
                break
        if recorded not in resolutions:
            resolutions[recorded] = mismatch or {
                "state": "unavailable",
                "resolved_path": None,
                "recorded_sha256": expected_sha256 if sha256_recorded else None,
                "sha256_verified": False,
                "guidance": "No current repository file was verified for this recorded artifact path.",
            }
    return dict(sorted(resolutions.items()))


def build_tasks(root: Path) -> dict[str, Any]:
    """Recursively project all task lifecycles without normalizing legacy truth."""
    tasks_root = root / ".ai" / "tasks"
    errors: list[str] = []
    tasks: list[dict[str, Any]] = []
    for lifecycle in LIFECYCLES:
        lifecycle_root = tasks_root / lifecycle
        if not lifecycle_root.exists():
            continue
        for task_path in sorted(lifecycle_root.rglob("task.yaml")):
            task_dir = task_path.parent
            relative = task_path.relative_to(root).as_posix()
            try:
                raw_contract_bytes = task_path.read_bytes()
                raw_contract = raw_contract_bytes.decode("utf-8-sig")
                contract = sanitize(parse_simple_yaml(task_path))
                source_sha256 = hashlib.sha256(raw_contract_bytes).hexdigest()
            except (OSError, UnicodeDecodeError, ValueError) as error:
                errors.append(f"{relative}: {error}")
                continue
            task_id = str(contract.get("id") or task_dir.name)
            try:
                evidence = sanitize(read_task_evidence(task_dir, root))
            except TaskEvidenceError as error:
                errors.append(str(error))
                evidence = {
                    "task_id": task_id,
                    "receipts": [],
                    "evidence": None,
                    "closeout": None,
                    "legacy_incomplete": True,
                }
            events = _events(evidence)
            context_items = [
                item
                for event in events if not event.get("legacy")
                for item in event.get("data", {}).get("context_items", [])
                if isinstance(item, dict)
            ]
            feature_id = contract.get("feature_id")
            feature_label = contract.get("feature")
            tasks.append({
                "task_id": task_id,
                "source_path": relative,
                "source_sha256": source_sha256,
                "source_bytes": len(raw_contract_bytes),
                "raw_contract": raw_contract,
                "lifecycle": lifecycle,
                "lifecycle_lineage": task_path.relative_to(lifecycle_root).parts[:-1],
                "contract": contract,
                "feature_link": {
                    "feature_id": feature_id if isinstance(feature_id, str) else None,
                    "display_label": feature_label if isinstance(feature_label, str) else None,
                    "identity_state": (
                        "explicit" if isinstance(feature_id, str)
                        else "legacy-display-label-only"
                    ),
                },
                "document_links": _explicit_document_links(contract),
                "dependencies": sorted(
                    str(item) for item in contract.get("depends_on", []) if isinstance(item, str)
                ),
                "delivery_stage": _delivery(
                    events, evidence.get("closeout") if isinstance(evidence.get("closeout"), dict) else None
                ),
                "receipt_events": events,
                "evidence_set": evidence.get("evidence"),
                "evidence_inventory": _evidence_inventory(
                    evidence.get("evidence") if isinstance(evidence.get("evidence"), dict) else None
                ),
                "evidence_artifact_resolutions": _evidence_artifact_resolutions(
                    evidence.get("evidence") if isinstance(evidence.get("evidence"), dict) else None,
                    task_dir=task_dir,
                    root=root,
                ),
                "context_items": context_items,
                "closeout": evidence.get("closeout"),
                "legacy_boundary": {
                    "incomplete": bool(evidence.get("legacy_incomplete")),
                    "label": (
                        "legacy receipt available; typed fields incomplete"
                        if evidence.get("legacy_incomplete") else None
                    ),
                },
            })
    tasks.sort(key=lambda item: (item["lifecycle"], item["source_path"], item["task_id"]))
    reverse: dict[str, list[str]] = {}
    for task in tasks:
        for dependency in task["dependencies"]:
            reverse.setdefault(dependency, []).append(task["task_id"])
    for task in tasks:
        task["reverse_dependencies"] = sorted(reverse.get(task["task_id"], []))

    explicit_features: dict[str, dict[str, Any]] = {}
    legacy_feature_labels: dict[str, list[str]] = {}
    for task in tasks:
        link = task["feature_link"]
        if link["feature_id"]:
            feature = explicit_features.setdefault(
                link["feature_id"],
                {"feature_id": link["feature_id"], "display_label": link["display_label"], "task_ids": []},
            )
            feature["task_ids"].append(task["task_id"])
        elif link["display_label"]:
            legacy_feature_labels.setdefault(link["display_label"], []).append(task["task_id"])
    features = {
        "explicit": [
            dict(item, task_ids=sorted(item["task_ids"]))
            for _key, item in sorted(explicit_features.items())
        ],
        "legacy_display_labels": [
            {
                "feature_id": None,
                "display_label": label,
                "identity_state": "not-a-stable-identity",
                "task_ids": sorted(task_ids),
            }
            for label, task_ids in sorted(legacy_feature_labels.items())
        ],
    }
    legacy_count = sum(1 for task in tasks if task["legacy_boundary"]["incomplete"])
    semantic = {"tasks": tasks, "features": features}
    return {
        "boundary": boundary(
            "error" if errors else ("partial" if legacy_count else "fresh"),
            fingerprint_value=fingerprint(semantic),
            indexed_roots=[f".ai/tasks/{lifecycle}/" for lifecycle in LIFECYCLES],
            include_rules=["recursive task.yaml", "all receipt events", "typed evidence/context/closeout"],
            exclude_rules=["receipt prose inference", "feature-label normalization", "expected references as results"],
            omitted_count=len(errors),
            errors=errors,
            warnings=[f"{legacy_count} tasks contain incomplete legacy receipt boundaries"] if legacy_count else [],
            rebuild_guidance="Resolve malformed task artifacts, then rerun `python scripts/ai_cli.py docs build`.",
        ),
        **semantic,
    }
