"""Atomic, auditable application of deterministic route recommendations."""

from __future__ import annotations

import argparse
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import scripts.ai_plane.config as config_module
import scripts.ai_plane.tasks as tasks
from scripts.ai_plane.route import CONFIGURE_GUIDANCE, explain_route
from scripts.ai_plane.routing_profile import ToolProfileError, load_profile
from scripts.ai_plane.primitives import yaml_scalar
from scripts.ai_plane.utils import die, rel


REPLACE_FIELDS = (
    "preferred_tool",
    "review_tool",
    "routing_profile_tool",
    "routing_profile",
    "routing_reasoning_level",
    "routing_provenance",
    "routing_rationale",
    "routing_complexity_band",
    "assignment_availability_provenance",
    "assignment_resolution_selector",
)


def _atomic_update_scalars(
    path: Path,
    current: dict[str, Any],
    updates: dict[str, str],
    *,
    replace: bool,
) -> bool:
    """Update top-level scalar fields while leaving every untouched source byte alone."""
    source = path.read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in source else "\n"
    lines = source.splitlines(keepends=True)
    positions: dict[str, int] = {}
    for index, line in enumerate(lines):
        match = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*):", line)
        if match:
            positions[match.group(1)] = index
    changed = False
    for key, value in updates.items():
        existing = current.get(key)
        if key in positions and existing not in (None, "", config_module.PENDING_ASSIGNMENT) and not replace:
            continue
        rendered = f"{key}: {yaml_scalar(value)}"
        if key in positions:
            index = positions[key]
            ending = "\r\n" if lines[index].endswith("\r\n") else (
                "\n" if lines[index].endswith("\n") else ""
            )
            replacement = rendered + ending
            if lines[index] != replacement:
                lines[index] = replacement
                changed = True
        else:
            if lines and not lines[-1].endswith(("\n", "\r\n")):
                lines[-1] += newline
            positions[key] = len(lines)
            lines.append(rendered + newline)
            changed = True
    if not changed:
        return False
    fd, temporary_name = tempfile.mkstemp(prefix=".task.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write("".join(lines))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return True


def apply_route(
    task_id: str,
    data: dict[str, Any],
    task_file: Path,
    *,
    replace: bool = False,
    reconciliation: str | None = None,
) -> dict[str, Any]:
    if replace and not reconciliation:
        raise ValueError("--replace requires --reconciliation TEXT")
    if reconciliation and not replace:
        raise ValueError("--reconciliation requires --replace")
    evaluation = dict(data)
    if replace:
        for field in REPLACE_FIELDS:
            evaluation.pop(field, None)
    try:
        local = load_profile(
            config_module.TOOLS,
            required=True,
            declared_profiles=config_module.TOOL_PROFILES,
            taxonomy=config_module.ROUTING_TAXONOMY,
        )
    except ToolProfileError as error:
        raise ValueError(f"{error.reason}: {error.detail}. {CONFIGURE_GUIDANCE}") from error
    result = explain_route(
        task_id, evaluation, local_profile=local, respect_assignments=not replace
    )
    if result["status"] != "selected":
        details = "; ".join(
            f"{item['code']}: {item['reason']}" for item in result["conflicts"]
        )
        raise ValueError(f"route apply refused: {details}")
    selected = result["selected_candidate"]
    reviewers = result["reviewer_candidates"]
    if not reviewers:
        raise ValueError("route apply refused: no eligible reviewer candidate")
    resolution = result["resolution"]
    updates = {
        "preferred_tool": selected["tool"],
        "routing_profile_tool": selected["tool"],
        "routing_profile": selected["logical_profile"],
        "routing_reasoning_level": selected["reasoning_level"],
        "routing_provenance": "router",
        "routing_rationale": "; ".join([
            *([f"Planner input: {data['assignment_rationale']}"] if data.get("assignment_rationale") else []),
            *result["decision_reasons"],
        ]),
        "routing_complexity_band": result["complexity"]["band"],
        "routing_policy_version": str(config_module.ROUTING_TAXONOMY["version"]),
        "assignment_availability_provenance": resolution["catalog"]["provenance"],
        "assignment_resolution_selector": resolution["model"] or resolution["selector"],
    }
    if reviewers:
        updates["review_tool"] = reviewers[0]["tool"]
    if replace:
        updates.update({
            "assignment_reconciliation": reconciliation or "",
            "assignment_replaced_preferred_tool": str(data.get("preferred_tool", "")),
            "assignment_replaced_review_tool": str(data.get("review_tool", "")),
            "assignment_replaced_profile": str(data.get("routing_profile", "")),
            "assignment_replaced_reasoning_level": str(
                data.get("routing_reasoning_level", "")
            ),
        })
    changed = _atomic_update_scalars(task_file, data, updates, replace=replace)
    return {"changed": changed, "updates": updates, "route": result}


def cmd_route_apply(args: argparse.Namespace) -> None:
    task_dir, data = tasks.find_task(args.task_id)
    try:
        result = apply_route(
            args.task_id,
            data,
            task_dir / "task.yaml",
            replace=args.replace,
            reconciliation=args.reconciliation,
        )
    except (ValueError, OSError) as error:
        die(str(error))
    state = "updated" if result["changed"] else "already current"
    print(f"Route assignment {state}: {rel(task_dir / 'task.yaml')}")
