"""Project this machine's agent usage into the reader, without leaking the machine.

`ai usage` reads session records out of the user's HOME directory: absolute working-directory
paths, session ids, branch names, and -- once a billing profile exists -- real money. The reader is
the opposite kind of artifact. `ai docs build` renders a deterministic projection of the
repository, and `ai docs export` exists to be handed to someone with no checkout, including a
client.

So usage never enters ``CP_DATA``. It is written as a separate, opt-in asset that `docs build`
leaves in a ``not_built`` state and only `ai usage build` fills in. What it fills in is aggregate:
per tool, never per session, so no path, id, or branch reaches the page even if someone copies the
whole site directory somewhere else.

The honesty contract from the CLI survives the trip. Measured and estimated totals stay separate
rather than summing into one number, the four token classes stay separate because cached input is
96-99% of volume at a fraction of the rate, and an unpriced tool says why it is unpriced instead of
rendering zero.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.ai_plane.usage import calibrate, estimate, price
from scripts.ai_plane.usage_sources import Usage

SCHEMA_VERSION = 1
ASSET_NAME = "usage-data.js"
GLOBAL_NAME = "window.CP_USAGE"

BUILD_COMMAND = "python scripts/ai_cli.py usage build"
NOT_BUILT_GUIDANCE = (
    "Usage is not part of the deterministic site build: it is per-machine data read from your "
    f"home directory. Run `{BUILD_COMMAND}` to collect it locally."
)


def not_built_payload() -> dict[str, Any]:
    """What `docs build` writes, so the asset always exists and the panel never invents a zero."""
    return {
        "schema_version": SCHEMA_VERSION,
        "state": "not_built",
        "generated_at": None,
        "guidance": NOT_BUILT_GUIDANCE,
        "command": BUILD_COMMAND,
        "sessions_read": 0,
        "tools": [],
        "calibration": None,
        "billing": {"configured": False, "plan": None},
        "tasks": [],
        "coverage": {"tasks_total": 0, "tasks_attributed": 0,
                     "sessions_claimed": 0, "sessions_ambiguous": 0},
    }


def unavailable_payload(reason: str) -> dict[str, Any]:
    """Collection was attempted and failed. Say so; do not fall back to `not_built`."""
    payload = not_built_payload()
    payload.update({
        "state": "unavailable",
        "guidance": f"Usage could not be collected: {reason}",
    })
    return payload


def _tool_entry(usage: Usage, billing: dict[str, Any] | None,
                calibration: dict[str, Any] | None) -> dict[str, Any]:
    """One tool's row. Measured and estimated tools carry different keys on purpose.

    A tool that records nothing gets `tokens: None` and an `estimate`; nothing downstream can add
    the two together by accident, which is the whole point of keeping them apart.
    """
    entry: dict[str, Any] = {
        "tool": usage.tool,
        "measured": usage.measured,
        "turns": usage.turns,
        "basis": usage.basis,
        "models": sorted(usage.models),
        "quota": usage.quota,
        "tokens": None,
        "cost": None,
        "estimate": None,
    }
    if usage.measured:
        entry["tokens"] = {
            "input": usage.input_tokens,
            "cached_input": usage.cached_input_tokens,
            "cache_write": usage.cache_write_tokens,
            "output": usage.output_tokens,
            "reasoning": usage.reasoning_tokens,
            "total": (usage.input_tokens + usage.cached_input_tokens
                      + usage.cache_write_tokens + usage.output_tokens),
        }
        entry["cost"] = price(usage, billing)
    else:
        entry["estimate"] = estimate(usage, calibration)
    return entry


def _task_entry(task_id: str, usage: Usage, billing: dict[str, Any] | None) -> dict[str, Any]:
    """One attributed task. Keyed by task id alone -- no session id reaches the page."""
    return {
        "task_id": task_id,
        "tool": usage.tool,
        "measured": usage.measured,
        "turns": usage.turns,
        "tokens": {
            "input": usage.input_tokens,
            "cached_input": usage.cached_input_tokens,
            "cache_write": usage.cache_write_tokens,
            "output": usage.output_tokens,
            "total": (usage.input_tokens + usage.cached_input_tokens
                      + usage.cache_write_tokens + usage.output_tokens),
        } if usage.measured else None,
        "cost": price(usage, billing) if usage.measured else None,
    }


def build_payload(sessions: list[dict[str, Any]], billing: dict[str, Any] | None,
                  *, generated_at: str, by_task: dict[str, Usage] | None = None,
                  coverage: dict[str, Any] | None = None) -> dict[str, Any]:
    """Aggregate collected sessions into the reader payload.

    ``generated_at`` is passed in rather than read from the clock so the projection stays a pure
    function of its inputs and a test can pin the whole payload.
    """
    if not sessions:
        payload = not_built_payload()
        payload.update({
            "state": "unavailable",
            "generated_at": generated_at,
            "guidance": ("No readable agent sessions were found on this machine. An unmeasurable "
                         "tool is reported as unknown, never as zero."),
        })
        return payload

    calibration = calibrate(sessions)
    by_tool: dict[str, Usage] = {}
    for session in sessions:
        usage = session["usage"]
        by_tool[usage.tool] = by_tool[usage.tool].add(usage) if usage.tool in by_tool else usage

    return {
        "schema_version": SCHEMA_VERSION,
        "state": "measured",
        "generated_at": generated_at,
        "guidance": "",
        "command": BUILD_COMMAND,
        "sessions_read": len(sessions),
        "tools": [_tool_entry(by_tool[tool], billing, calibration) for tool in sorted(by_tool)],
        "calibration": calibration,
        "billing": {
            "configured": bool(billing),
            "plan": (billing or {}).get("plan"),
        },
        # Per-task attribution is separate from the per-tool totals and never a subset of them:
        # a task appears only if a receipt recorded the session that produced it. `coverage` is
        # what stops an empty list reading as "these tasks cost nothing".
        "tasks": [_task_entry(task, by_task[task], billing) for task in sorted(by_task or {})],
        "coverage": coverage or {"tasks_total": 0, "tasks_attributed": 0,
                                 "sessions_claimed": 0, "sessions_ambiguous": 0},
    }


def render_asset(payload: dict[str, Any]) -> str:
    """The runtime asset, shaped like the reader's other generated globals (`data.js`)."""
    import json

    body = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    return ("/* GENERATED by `ai usage build` from local agent session records. */\n"
            "/* Per-machine and NOT part of the deterministic site build; do not commit. */\n"
            f"{GLOBAL_NAME} = {body};\n")


def write_asset(assets_dir: Path, payload: dict[str, Any]) -> Path:
    assets_dir.mkdir(parents=True, exist_ok=True)
    path = assets_dir / ASSET_NAME
    path.write_bytes(render_asset(payload).encode("utf-8"))
    return path
