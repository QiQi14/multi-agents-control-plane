"""Git-anchored legacy task evidence baseline and repository audit mechanics."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from scripts.ai_plane.task_evidence import (
    BASELINE_REL,
    LEGACY_LABEL,
    SCHEMA_VERSION,
    TaskEvidenceError,
    _task_dirs,
    _versioned_receipts,
    load_yaml,
    parse_yaml_text,
    validate_closeout,
    validate_evidence_set,
    validate_receipt,
)

def _artifact_key(path: Path) -> str:
    parts = path.parts
    try:
        tasks_index = parts.index("tasks")
    except ValueError:
        return f"{path.parent.name}/{path.name}"
    return "/".join(parts[tasks_index + 2:])


def git_blob_reader(root: Path, base_commit: str) -> Callable[[str], bytes]:
    """Return exact committed bytes; line-ending policy comes from Git, never the checkout."""
    def read_blob(path_value: str) -> bytes:
        result = subprocess.run(
            ["git", "cat-file", "blob", f"{base_commit}:{path_value}"],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise TaskEvidenceError(f"cannot read {path_value!r} at {base_commit}: {detail}")
        return result.stdout
    return read_blob


def build_legacy_baseline(
    root: Path,
    base_commit: str,
    *,
    blob_reader: Callable[[str], bytes] | None = None,
) -> dict[str, Any]:
    contracts: list[dict[str, str]] = []
    receipts: list[dict[str, str]] = []
    for task_dir in _task_dirs(root):
        task = task_dir / "task.yaml"
        contracts.append({
            "artifact_key": _artifact_key(task),
            "observed_path": task.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(
                blob_reader(task.relative_to(root).as_posix()) if blob_reader else task.read_bytes()
            ).hexdigest(),
            "label": LEGACY_LABEL,
        })

    for receipt in sorted((root / ".ai" / "tasks").rglob("receipt*.yaml")):
        receipts.append({
            "artifact_key": _artifact_key(receipt),
            "observed_path": receipt.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(
                blob_reader(receipt.relative_to(root).as_posix()) if blob_reader else receipt.read_bytes()
            ).hexdigest(),
            "label": LEGACY_LABEL,
        })
    return {
        "schema_version": 1,
        "kind": "task-artifact-legacy-baseline",
        "base_commit": base_commit,
        "historical_review_fact": {"task_contracts": 222, "receipt_files": 414},
        "captured_counts": {"task_contracts": len(contracts), "receipt_files": len(receipts)},
        "task_contracts": sorted(contracts, key=lambda item: item["artifact_key"]),
        "receipts": sorted(receipts, key=lambda item: item["artifact_key"]),
    }


def load_legacy_baseline(root: Path) -> dict[str, Any] | None:
    path = root / BASELINE_REL
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise TaskEvidenceError(f"{BASELINE_REL}: invalid baseline: {error}") from error
    if data.get("schema_version") != 1 or data.get("kind") != "task-artifact-legacy-baseline":
        raise TaskEvidenceError(f"{BASELINE_REL}: unsupported baseline schema")
    return data


def _current_receipts_by_key(root: Path) -> dict[str, Path]:
    return {_artifact_key(path): path for path in sorted((root / ".ai" / "tasks").rglob("receipt*.yaml"))}


def _matches_git_digest(path: Path, expected: str) -> bool:
    current = path.read_bytes()
    variants = {
        current,
        current.replace(b"\r\n", b"\n"),
        current.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"),
    }
    return expected in {hashlib.sha256(value).hexdigest() for value in variants}


def repository_task_artifact_violations(root: Path) -> list[str]:
    try:
        baseline = load_legacy_baseline(root)
    except TaskEvidenceError as error:
        return [str(error)]
    if baseline is None:
        return []
    violations: list[str] = []
    current = _current_receipts_by_key(root)
    legacy = {item["artifact_key"]: item for item in baseline.get("receipts", [])}
    for key, item in legacy.items():
        path = current.get(key)
        if path is None:
            violations.append(f"legacy receipt removed: {key}")
        elif not _matches_git_digest(path, item.get("sha256", "")):
            violations.append(f"legacy receipt changed: {key}")
    for key, path in current.items():
        if key in legacy:
            continue
        raw = path.read_text(encoding="utf-8")
        try:
            data = load_yaml(path)
        except TaskEvidenceError as error:
            if "schema_version" in raw:
                violations.append(str(error))
            else:
                violations.append(f"new untyped receipt is forbidden: {path.relative_to(root).as_posix()}")
            continue
        if data.get("schema_version") != SCHEMA_VERSION:
            violations.append(f"new untyped receipt is forbidden: {path.relative_to(root).as_posix()}")
        else:
            violations += validate_receipt(data, path.relative_to(root).as_posix())
    for task_dir in _task_dirs(root):
        contract_text = (task_dir / "task.yaml").read_text(encoding="utf-8")
        task_match = re.search(r'^id:\s*["\']?([^"\'\s]+)', contract_text, re.MULTILINE)
        task_id = task_match.group(1) if task_match else task_dir.name
        evidence_ids: set[str] = set()
        evidence = task_dir / "evidence.yaml"
        if evidence.exists():
            try:
                evidence_data = load_yaml(evidence)
                violations += validate_evidence_set(evidence_data, evidence.relative_to(root).as_posix())
                if evidence_data.get("task_id") != task_id:
                    violations.append(f"{evidence.relative_to(root).as_posix()}: task_id does not match {task_id!r}")
                raw_items = evidence_data.get("items")
                evidence_ids = {
                    item.get("evidence_id")
                    for item in raw_items if isinstance(item, dict) and isinstance(item.get("evidence_id"), str)
                } if isinstance(raw_items, list) else set()
            except TaskEvidenceError as error:
                violations.append(str(error))
        versioned_receipts, receipt_errors = _versioned_receipts(task_dir)
        violations += receipt_errors
        for receipt_path, receipt_data in versioned_receipts:
            receipt_rel = receipt_path.relative_to(root).as_posix()
            if receipt_data.get("task_id") != task_id:
                violations.append(f"{receipt_rel}: task_id does not match {task_id!r}")
            referenced: set[str] = set()

            def add_references(value: Any) -> None:
                if isinstance(value, list):
                    referenced.update(item for item in value if isinstance(item, str))

            add_references(receipt_data.get("evidence_refs"))
            gates = receipt_data.get("gates")
            if isinstance(gates, list):
                for gate in gates:
                    if isinstance(gate, dict):
                        add_references(gate.get("evidence_refs"))
            context_items = receipt_data.get("context_items")
            if isinstance(context_items, list):
                for context_item in context_items:
                    if isinstance(context_item, dict):
                        add_references(context_item.get("evidence_refs"))
            for missing_ref in sorted(referenced - evidence_ids):
                violations.append(f"{receipt_rel}: unknown evidence reference {missing_ref!r}")
        closeout = task_dir / "task-closeout.yaml"
        if closeout.exists():
            try:
                closeout_data = load_yaml(closeout)
                if closeout_data.get("task_id") != task_id:
                    violations.append(f"{closeout.relative_to(root).as_posix()}: task_id does not match {task_id!r}")
                violations += validate_closeout(closeout_data, task_dir, root)
            except TaskEvidenceError as error:
                violations.append(str(error))
    return violations


def changed_task_artifact_violations(root: Path, change_paths: list[str], base: str, git_fn: Callable[[Path, list[str]], str]) -> list[str]:
    violations: list[str] = []
    try:
        base_baseline_text = git_fn(root, ["show", f"{base}:{BASELINE_REL}"])
        base_baseline = json.loads(base_baseline_text)
    except Exception:
        base_baseline_text = None
        base_baseline = None
    if base_baseline_text is not None and BASELINE_REL in change_paths:
        current_baseline = root / BASELINE_REL
        if not current_baseline.is_file() or current_baseline.read_text(encoding="utf-8") != base_baseline_text:
            violations.append("immutable legacy baseline was modified or deleted")
    relevant = [
        path for path in change_paths
        if path.startswith(".ai/tasks/") and (
            (Path(path).name.startswith("receipt") and Path(path).suffix == ".yaml")
            or Path(path).name in {"evidence.yaml", "task-closeout.yaml"}
        )
    ]
    if not relevant:
        return sorted(set(violations))
    violations.extend(repository_task_artifact_violations(root))
    for rel_path in relevant:
        try:
            base_text = git_fn(root, ["show", f"{base}:{rel_path}"])
            base_data = parse_yaml_text(base_text, f"{base}:{rel_path}")
        except Exception:
            continue
        if base_data.get("schema_version") != SCHEMA_VERSION:
            continue
        receipt_id = base_data.get("receipt_id")
        if not receipt_id:
            violations.append(f"immutable versioned artifact removed or changed: {rel_path}")
            continue
        matches: list[Path] = []
        for task_dir in _task_dirs(root):
            for current in task_dir.glob("receipt*.yaml"):
                try:
                    current_data = load_yaml(current)
                except TaskEvidenceError:
                    continue
                if current_data.get("receipt_id") == receipt_id:
                    matches.append(current)
        if len(matches) != 1 or matches[0].read_text(encoding="utf-8") != base_text:
            violations.append(f"immutable receipt event {receipt_id!r} was modified or deleted")
    return sorted(set(violations))


def read_task_evidence(task_dir: Path, root: Path) -> dict[str, Any]:
    """Return renderer input without fabricating typed fields for historical receipts."""
    baseline = load_legacy_baseline(root) or {"receipts": []}
    legacy_keys = {item["artifact_key"] for item in baseline.get("receipts", [])}
    receipts: list[dict[str, Any]] = []
    for path in sorted(task_dir.glob("receipt*.yaml")):
        if _artifact_key(path) in legacy_keys:
            receipts.append({
                "path": path.relative_to(root).as_posix(),
                "legacy": True,
                "label": LEGACY_LABEL,
                "data": {"status": "incomplete", "raw_available": True},
            })
        else:
            receipts.append({"path": path.relative_to(root).as_posix(), "legacy": False, "data": load_yaml(path)})
    evidence_path = task_dir / "evidence.yaml"
    closeout_path = task_dir / "task-closeout.yaml"
    return {
        "task_id": task_dir.name,
        "receipts": receipts,
        "evidence": load_yaml(evidence_path) if evidence_path.exists() else None,
        "closeout": load_yaml(closeout_path) if closeout_path.exists() else None,
        "legacy_incomplete": any(item["legacy"] for item in receipts),
    }
