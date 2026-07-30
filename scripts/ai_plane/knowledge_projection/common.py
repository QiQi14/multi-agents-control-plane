"""Deterministic helpers shared by the production knowledge projection."""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Any


PROJECTION_SCHEMA_VERSION = 1


def sanitize(value: Any) -> Any:
    """Return a JSON-safe value without inventing replacements for unknown data."""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, dict):
        return {str(key): sanitize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    return value


def canonical_json(value: Any, *, pretty: bool = False) -> str:
    """Encode stable UTF-8 JSON with recursively sorted mapping keys."""
    if pretty:
        return json.dumps(
            sanitize(value), ensure_ascii=False, indent=2, sort_keys=True,
            separators=(",", ": "),
        ) + "\n"
    return json.dumps(
        sanitize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )


def fingerprint(value: Any) -> str:
    """Hash the canonical semantic payload."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def repository_revision(root: Path) -> dict[str, Any]:
    """Read stable revision metadata without changing repository state."""
    commands = {
        "commit": ["git", "rev-parse", "HEAD"],
        "refreshed_at": ["git", "show", "-s", "--format=%cI", "HEAD"],
    }
    result: dict[str, Any] = {
        "commit": None,
        "refreshed_at": None,
        "refresh_time_provenance": "unavailable",
    }
    for field, argv in commands.items():
        try:
            completed = subprocess.run(
                argv, cwd=root, capture_output=True, text=True, check=False,
            )
        except OSError:
            continue
        if completed.returncode == 0 and completed.stdout.strip():
            result[field] = completed.stdout.strip()
    if result["refreshed_at"]:
        result["refresh_time_provenance"] = "git-commit-time"
    return result


def boundary(
    state: str,
    *,
    fingerprint_value: str | None,
    indexed_roots: list[str],
    include_rules: list[str],
    exclude_rules: list[str],
    omitted_count: int = 0,
    errors: list[str] | None = None,
    warnings: list[str] | None = None,
    rebuild_guidance: str,
) -> dict[str, Any]:
    """Build the common truth-system boundary envelope."""
    return {
        "state": state,
        "fingerprint": fingerprint_value,
        "indexed_roots": sorted(indexed_roots),
        "include_rules": sorted(include_rules),
        "exclude_rules": sorted(exclude_rules),
        "omitted_count": omitted_count,
        "errors": sorted(errors or []),
        "warnings": sorted(warnings or []),
        "rebuild_guidance": rebuild_guidance,
    }


def aggregate_state(states: list[str]) -> str:
    """Fold peer truth-system states without hiding degraded inputs."""
    if "error" in states:
        return "error"
    if "stale" in states:
        return "stale"
    if any(state in {"partial", "unavailable"} for state in states):
        return "partial"
    return "fresh"
