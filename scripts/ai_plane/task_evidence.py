"""Versioned immutable task receipts, evidence sets, and closeout folds.

Schema-versioned ``*.yaml`` files use the JSON-compatible subset of YAML 1.2. This keeps the
control plane standard-library-only while providing strict nested data. Historical free-form YAML
is never reinterpreted as typed context; it remains byte-preserved behind an incomplete adapter.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
LEGACY_LABEL = "legacy-untyped-context"
BASELINE_REL = ".ai/project/task-artifact-legacy-baseline.json"
TASK_STATES = ("queue", "active", "done", "archive")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class TaskEvidenceError(ValueError):
    """A versioned task artifact is malformed or inconsistent."""


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise TaskEvidenceError(
            f"{path.as_posix()}: schema-versioned YAML must use the strict JSON-compatible YAML subset: {error}"
        ) from error
    if not isinstance(value, dict):
        raise TaskEvidenceError(f"{path.as_posix()}: document root must be a mapping")
    return value


def parse_yaml_text(text: str, source: str = "<memory>") -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise TaskEvidenceError(f"{source}: invalid JSON-compatible YAML: {error}") from error
    if not isinstance(value, dict):
        raise TaskEvidenceError(f"{source}: document root must be a mapping")
    return value


def _keys(data: dict[str, Any], required: set[str], allowed: set[str], where: str) -> list[str]:
    errors = [f"{where}: missing required field {key!r}" for key in sorted(required - set(data))]
    errors += [f"{where}: unknown field {key!r}" for key in sorted(set(data) - allowed)]
    return errors


def _map(value: Any, where: str, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{where}: must be a mapping")
        return {}
    return value


def _items(value: Any, where: str, errors: list[str]) -> list[Any]:
    if not isinstance(value, list):
        errors.append(f"{where}: must be a list")
        return []
    return value


def _text(value: Any, where: str, errors: list[str], *, identifier: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{where}: must be a non-empty string")
        return ""
    if identifier and not ID_RE.fullmatch(value):
        errors.append(f"{where}: must match {ID_RE.pattern}")
    return value


def _enum(value: Any, allowed: set[str], where: str, errors: list[str]) -> str:
    text = _text(value, where, errors)
    if text and text not in allowed:
        errors.append(f"{where}: {text!r} is not one of {', '.join(sorted(allowed))}")
    return text


def _schema(data: dict[str, Any], where: str, errors: list[str]) -> None:
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"{where}: schema_version must be integer {SCHEMA_VERSION}")


def validate_receipt(data: dict[str, Any], where: str = "receipt") -> list[str]:
    required = {
        "schema_version", "receipt_id", "task_id", "role", "sequence", "actor", "revision",
        "environment", "decision", "gates", "evidence_refs", "context_items",
    }
    errors = _keys(data, required, required | {"notes", "knowledge_to_capture", "review_independence"}, where)
    _schema(data, where, errors)
    receipt_id = _text(data.get("receipt_id"), f"{where}.receipt_id", errors, identifier=True)
    _text(data.get("task_id"), f"{where}.task_id", errors, identifier=True)
    role = _enum(data.get("role"), {"executor", "qa"}, f"{where}.role", errors)

    sequence = _map(data.get("sequence"), f"{where}.sequence", errors)
    sequence_required = {"attempt" if role == "executor" else "round"}
    errors += _keys(sequence, sequence_required, {"attempt", "round"}, f"{where}.sequence")
    for key, value in sequence.items():
        if key in {"attempt", "round"} and (not isinstance(value, int) or isinstance(value, bool) or value < 1):
            errors.append(f"{where}.sequence.{key}: must be a positive integer")

    actor = _map(data.get("actor"), f"{where}.actor", errors)
    actor_fields = {"name", "family", "tool", "model", "reasoning"}
    errors += _keys(actor, actor_fields, actor_fields, f"{where}.actor")
    for key in actor_fields:
        _text(actor.get(key), f"{where}.actor.{key}", errors)

    revision = _map(data.get("revision"), f"{where}.revision", errors)
    revision_fields = {"base_commit", "head_commit", "diff"}
    errors += _keys(revision, revision_fields, revision_fields | {"diff_fingerprint"}, f"{where}.revision")
    for key in revision_fields:
        _text(revision.get(key), f"{where}.revision.{key}", errors)
    fingerprint = revision.get("diff_fingerprint")
    if fingerprint is not None and (not isinstance(fingerprint, str) or not SHA256_RE.fullmatch(fingerprint)):
        errors.append(f"{where}.revision.diff_fingerprint: must be a lowercase SHA-256")

    environment = _map(data.get("environment"), f"{where}.environment", errors)
    env_fields = {"os", "arch", "device"}
    errors += _keys(environment, env_fields, env_fields | {"toolchain"}, f"{where}.environment")
    for key in env_fields:
        _text(environment.get(key), f"{where}.environment.{key}", errors)

    decision = _map(data.get("decision"), f"{where}.decision", errors)
    errors += _keys(decision, {"status", "outcome"}, {"status", "outcome"}, f"{where}.decision")
    statuses = {"ready", "blocked", "revision_required"} if role == "executor" else {"accept", "revise", "reject"}
    _enum(decision.get("status"), statuses, f"{where}.decision.status", errors)
    _text(decision.get("outcome"), f"{where}.decision.outcome", errors)

    refs = _items(data.get("evidence_refs"), f"{where}.evidence_refs", errors)
    for index, ref in enumerate(refs):
        _text(ref, f"{where}.evidence_refs[{index}]", errors, identifier=True)

    gates = _items(data.get("gates"), f"{where}.gates", errors)
    for index, value in enumerate(gates):
        gate_where = f"{where}.gates[{index}]"
        gate = _map(value, gate_where, errors)
        fields = {"gate_id", "command", "result", "evidence_refs"}
        errors += _keys(gate, fields, fields | {"notes"}, gate_where)
        _text(gate.get("gate_id"), f"{gate_where}.gate_id", errors, identifier=True)
        _text(gate.get("command"), f"{gate_where}.command", errors)
        _enum(gate.get("result"), {"pass", "fail", "skipped"}, f"{gate_where}.result", errors)
        gate_refs = _items(gate.get("evidence_refs"), f"{gate_where}.evidence_refs", errors)
        for ref_index, ref in enumerate(gate_refs):
            _text(ref, f"{gate_where}.evidence_refs[{ref_index}]", errors, identifier=True)

    context = _items(data.get("context_items"), f"{where}.context_items", errors)
    seen: set[str] = set()
    for index, value in enumerate(context):
        item_where = f"{where}.context_items[{index}]"
        item = _map(value, item_where, errors)
        required_item = {
            "context_item_id", "type", "blocking", "severity", "summary", "state",
            "source_receipt_id",
        }
        allowed_item = required_item | {
            "source_receipt_id", "locations", "evidence_refs", "resolution", "owner",
            "target_task", "related_context_item_id",
        }
        errors += _keys(item, required_item, allowed_item, item_where)
        item_id = _text(item.get("context_item_id"), f"{item_where}.context_item_id", errors, identifier=True)
        if item_id in seen:
            errors.append(f"{item_where}.context_item_id: duplicate {item_id!r}")
        seen.add(item_id)
        _enum(item.get("type"), {"finding", "risk", "limitation", "observation", "follow-up", "decision"}, f"{item_where}.type", errors)
        if not isinstance(item.get("blocking"), bool):
            errors.append(f"{item_where}.blocking: must be boolean")
        _enum(item.get("severity"), {"critical", "high", "medium", "low", "info"}, f"{item_where}.severity", errors)
        _text(item.get("summary"), f"{item_where}.summary", errors)
        _enum(item.get("state"), {"open", "resolved", "accepted-risk", "deferred", "transferred", "superseded"}, f"{item_where}.state", errors)
        source_receipt_id = _text(
            item.get("source_receipt_id"), f"{item_where}.source_receipt_id", errors, identifier=True
        )
        if source_receipt_id and source_receipt_id != receipt_id:
            errors.append(f"{item_where}.source_receipt_id: must equal this receipt_id")
        for list_key in ("locations", "evidence_refs"):
            if list_key in item:
                values = _items(item[list_key], f"{item_where}.{list_key}", errors)
                for value_index, entry in enumerate(values):
                    _text(entry, f"{item_where}.{list_key}[{value_index}]", errors)
    return errors


def validate_evidence_set(data: dict[str, Any], where: str = "evidence") -> list[str]:
    required = {"schema_version", "evidence_set_id", "task_id", "items"}
    errors = _keys(data, required, required | {"notes"}, where)
    _schema(data, where, errors)
    _text(data.get("evidence_set_id"), f"{where}.evidence_set_id", errors, identifier=True)
    _text(data.get("task_id"), f"{where}.task_id", errors, identifier=True)
    items = _items(data.get("items"), f"{where}.items", errors)
    seen: set[str] = set()
    for index, value in enumerate(items):
        item_where = f"{where}.items[{index}]"
        item = _map(value, item_where, errors)
        fields = {"evidence_id", "kind", "role", "storage", "availability", "producer", "claim", "acceptance_links", "accessibility_text"}
        errors += _keys(item, fields, fields | {"artifact", "inspection", "coverage", "notes"}, item_where)
        evidence_id = _text(item.get("evidence_id"), f"{item_where}.evidence_id", errors, identifier=True)
        if evidence_id in seen:
            errors.append(f"{item_where}.evidence_id: duplicate {evidence_id!r}")
        seen.add(evidence_id)
        _enum(item.get("kind"), {"generated-result", "expected-reference", "golden", "comparison-diff"}, f"{item_where}.kind", errors)
        _enum(item.get("role"), {"acceptance", "supporting", "diagnostic"}, f"{item_where}.role", errors)
        storage = _enum(item.get("storage"), {"committed", "regenerable", "external"}, f"{item_where}.storage", errors)
        _enum(item.get("availability"), {"available", "unavailable", "expired"}, f"{item_where}.availability", errors)
        producer = _map(item.get("producer"), f"{item_where}.producer", errors)
        errors += _keys(producer, {"command", "environment"}, {"command", "environment"}, f"{item_where}.producer")
        command = _text(producer.get("command"), f"{item_where}.producer.command", errors)
        producer_env = _map(producer.get("environment"), f"{item_where}.producer.environment", errors)
        errors += _keys(producer_env, {"os", "arch", "device"}, {"os", "arch", "device", "toolchain"}, f"{item_where}.producer.environment")
        for key in ("os", "arch", "device"):
            _text(producer_env.get(key), f"{item_where}.producer.environment.{key}", errors)
        claim = _text(item.get("claim"), f"{item_where}.claim", errors)
        links = _items(item.get("acceptance_links"), f"{item_where}.acceptance_links", errors)
        for link_index, link in enumerate(links):
            _text(link, f"{item_where}.acceptance_links[{link_index}]", errors)
        if not claim and not links:
            errors.append(f"{item_where}: informative evidence needs a claim or acceptance linkage")
        _text(item.get("accessibility_text"), f"{item_where}.accessibility_text", errors)
        artifact = item.get("artifact")
        artifact_map: dict[str, Any] = {}
        if artifact is not None:
            artifact_map = _map(artifact, f"{item_where}.artifact", errors)
            allowed = {"path", "media_type", "sha256", "width", "height", "variant", "theme", "locale", "route"}
            errors += _keys(artifact_map, set(), allowed, f"{item_where}.artifact")
            for text_key in ("path", "media_type", "variant", "theme", "locale", "route"):
                if text_key in artifact_map:
                    _text(artifact_map[text_key], f"{item_where}.artifact.{text_key}", errors)
            digest = artifact_map.get("sha256")
            if digest is not None and (not isinstance(digest, str) or not SHA256_RE.fullmatch(digest)):
                errors.append(f"{item_where}.artifact.sha256: must be a lowercase SHA-256")
            for dimension in ("width", "height"):
                value = artifact_map.get(dimension)
                if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 1):
                    errors.append(f"{item_where}.artifact.{dimension}: must be a positive integer")
        if storage == "committed":
            if not artifact_map:
                errors.append(f"{item_where}.artifact: committed evidence requires artifact identity")
            else:
                _text(artifact_map.get("path"), f"{item_where}.artifact.path", errors)
                _text(artifact_map.get("media_type"), f"{item_where}.artifact.media_type", errors)
        if storage == "regenerable" and not command:
            errors.append(f"{item_where}: regenerable evidence requires an exact producer command")
    return errors

def _task_dirs(root: Path) -> list[Path]:
    task_root = root / ".ai" / "tasks"
    return [path.parent for state in TASK_STATES for path in sorted((task_root / state).rglob("task.yaml"))]


def find_task_dir(root: Path, task_id: str) -> Path | None:
    for task_dir in _task_dirs(root):
        if task_dir.name == task_id:
            return task_dir
        text = (task_dir / "task.yaml").read_text(encoding="utf-8")
        match = re.search(r'^id:\s*["\']?([^"\'\s]+)', text, re.MULTILINE)
        if match and match.group(1) == task_id:
            return task_dir
    return None


def _versioned_receipts(task_dir: Path) -> tuple[list[tuple[Path, dict[str, Any]]], list[str]]:
    receipts: list[tuple[Path, dict[str, Any]]] = []
    errors: list[str] = []
    for path in sorted(task_dir.glob("receipt*.yaml")):
        try:
            data = load_yaml(path)
        except TaskEvidenceError:
            continue
        if data.get("schema_version") == SCHEMA_VERSION:
            errors += validate_receipt(data, path.as_posix())
            receipts.append((path, data))
    return receipts, errors


def validate_closeout(data: dict[str, Any], task_dir: Path, root: Path) -> list[str]:
    where = (task_dir / "task-closeout.yaml").as_posix()
    required = {"schema_version", "closeout_id", "task_id", "accepted_receipt_id", "receipt_events", "context_dispositions", "closed_by", "closed_at"}
    errors = _keys(data, required, required | {"notes"}, where)
    _schema(data, where, errors)
    _text(data.get("closeout_id"), f"{where}.closeout_id", errors, identifier=True)
    task_id = _text(data.get("task_id"), f"{where}.task_id", errors, identifier=True)
    _text(data.get("closed_by"), f"{where}.closed_by", errors)
    _text(data.get("closed_at"), f"{where}.closed_at", errors)
    receipts, receipt_errors = _versioned_receipts(task_dir)
    errors += receipt_errors
    receipt_by_id = {receipt["receipt_id"]: receipt for _path, receipt in receipts if receipt.get("receipt_id")}
    events = _items(data.get("receipt_events"), f"{where}.receipt_events", errors)
    for event_index, event_id in enumerate(events):
        _text(event_id, f"{where}.receipt_events[{event_index}]", errors, identifier=True)
    if len(events) != len(set(events)):
        errors.append(f"{where}.receipt_events: duplicate receipt ID")
    if set(events) != set(receipt_by_id):
        errors.append(f"{where}.receipt_events: must contain every immutable versioned receipt exactly once")
    last_sequence = {"executor": 0, "qa": 0}
    seen_sequence: set[tuple[str, int]] = set()
    for event_id in events:
        receipt = receipt_by_id.get(event_id)
        if not receipt:
            continue
        role = receipt.get("role")
        sequence_key = "attempt" if role == "executor" else "round"
        sequence = receipt.get("sequence", {}).get(sequence_key)
        if role not in last_sequence or not isinstance(sequence, int):
            continue
        identity = (role, sequence)
        if identity in seen_sequence:
            errors.append(f"{where}.receipt_events: duplicate {role} {sequence_key} {sequence}")
        seen_sequence.add(identity)
        if sequence <= last_sequence[role]:
            errors.append(f"{where}.receipt_events: {role} {sequence_key}s must be in increasing causal order")
        last_sequence[role] = sequence
    accepted_id = data.get("accepted_receipt_id")
    accepted = receipt_by_id.get(accepted_id)
    if not accepted or accepted.get("role") != "qa" or accepted.get("decision", {}).get("status") != "accept":
        errors.append(f"{where}.accepted_receipt_id: must reference an accepting QA receipt")
    if events and events[-1] != accepted_id:
        errors.append(f"{where}.receipt_events: accepting QA receipt must be the final causal event")

    context: dict[str, tuple[str, dict[str, Any]]] = {}
    for _path, receipt in receipts:
        raw_context = receipt.get("context_items")
        if not isinstance(raw_context, list):
            continue
        for item in raw_context:
            if not isinstance(item, dict):
                continue
            item_id = item.get("context_item_id")
            if item_id in context:
                errors.append(f"{where}: duplicate context item ID {item_id!r} across receipts")
            else:
                context[item_id] = (receipt.get("receipt_id", ""), item)
    dispositions = _items(data.get("context_dispositions"), f"{where}.context_dispositions", errors)
    seen: set[str] = set()
    for index, value in enumerate(dispositions):
        item_where = f"{where}.context_dispositions[{index}]"
        item = _map(value, item_where, errors)
        required_item = {"context_item_id", "source_receipt_id", "disposition", "rationale"}
        allowed_item = required_item | {"owner", "target_task", "target_context_item_id", "superseded_by"}
        errors += _keys(item, required_item, allowed_item, item_where)
        item_id = _text(item.get("context_item_id"), f"{item_where}.context_item_id", errors, identifier=True)
        if item_id in seen:
            errors.append(f"{item_where}.context_item_id: duplicate disposition")
        seen.add(item_id)
        source = item.get("source_receipt_id")
        if item_id not in context or context.get(item_id, (None,))[0] != source:
            errors.append(f"{item_where}: source receipt does not own context item {item_id!r}")
        disposition = _enum(item.get("disposition"), {"resolved", "accepted-risk", "deferred", "transferred", "superseded"}, f"{item_where}.disposition", errors)
        _text(item.get("rationale"), f"{item_where}.rationale", errors)
        if disposition in {"accepted-risk", "deferred", "transferred"}:
            _text(item.get("owner"), f"{item_where}.owner", errors)
        if disposition == "superseded":
            _text(item.get("superseded_by"), f"{item_where}.superseded_by", errors, identifier=True)
        if disposition == "transferred":
            target_task = _text(item.get("target_task"), f"{item_where}.target_task", errors, identifier=True)
            target_item = _text(item.get("target_context_item_id"), f"{item_where}.target_context_item_id", errors, identifier=True)
            target_dir = find_task_dir(root, target_task) if target_task else None
            if target_dir is None:
                errors.append(f"{item_where}.target_task: no real task {target_task!r}")
            else:
                target_receipts, target_errors = _versioned_receipts(target_dir)
                errors += target_errors
                reciprocal = f"{task_id}/{item_id}"
                matches = [
                    target_context
                    for _target_path, target_receipt in target_receipts
                    for target_context in (
                        target_receipt.get("context_items")
                        if isinstance(target_receipt.get("context_items"), list) else []
                    )
                    if isinstance(target_context, dict)
                    and target_context.get("context_item_id") == target_item
                    and target_context.get("related_context_item_id") == reciprocal
                ]
                if not matches:
                    errors.append(f"{item_where}: transfer target lacks reciprocal context relation {reciprocal!r}")
    missing = sorted(set(context) - seen)
    extra = sorted(seen - set(context))
    if missing:
        errors.append(f"{where}: undispositioned context items: {', '.join(missing)}")
    if extra:
        errors.append(f"{where}: dispositions reference unknown context items: {', '.join(extra)}")
    return errors


def receipt_template(task_id: str, role: str, actor_tool: str, base_commit: str) -> dict[str, Any]:
    sequence = {"attempt": 1} if role == "executor" else {"round": 1}
    status = "ready" if role == "executor" else "revise"
    return {
        "schema_version": 1,
        "receipt_id": f"{task_id}-{role}-1",
        "task_id": task_id,
        "role": role,
        "sequence": sequence,
        "actor": {"name": "fill-me", "family": "fill-me", "tool": actor_tool, "model": "fill-me", "reasoning": "fill-me"},
        "revision": {"base_commit": base_commit, "head_commit": "fill-me", "diff": "fill-me"},
        "environment": {"os": "fill-me", "arch": "fill-me", "device": "fill-me"},
        "decision": {"status": status, "outcome": "fill-me"},
        "gates": [],
        "evidence_refs": [],
        "context_items": [],
        "notes": [],
    }
