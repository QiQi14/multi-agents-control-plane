"""Read what each agent tool records about its own usage.

Every source here reads a private on-disk format that no vendor documents or guarantees. None of
them is an API. So each collector validates the shape it finds and returns `None` rather than a
number it is not sure of: an unmeasurable tool must report unknown, never zero, because a zero
silently understates a total while an unknown is visible.

Nothing here opens a socket, reads a credential, or touches account state. These are files the tool
already wrote on this machine.

Measured on real sessions while designing this (see the task record):

  - Claude Code splits ONE assistant turn across several records that each repeat the same
    `output_tokens`. Summing records multiplies the count; the id groups them.
  - Cached input is 96-99% of all input volume. Pricing it at the input rate is wrong by roughly an
    order of magnitude, so the classes stay separate all the way to the report.
  - Reasoning is 22-39% of output tokens and never appears as text, so it cannot be recovered by
    reading what a model wrote.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


@dataclass
class Usage:
    """One tool's consumption. `measured` False means every number here is an estimate."""

    tool: str
    measured: bool
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    turns: int = 0
    models: set[str] = field(default_factory=set)
    quota: dict[str, Any] | None = None
    basis: str = ""

    def add(self, other: "Usage") -> "Usage":
        return Usage(
            tool=self.tool,
            measured=self.measured and other.measured,
            input_tokens=self.input_tokens + other.input_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            turns=self.turns + other.turns,
            models=self.models | other.models,
            quota=other.quota or self.quota,
            basis=other.basis or self.basis,
        )


def _read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    yield record
    except OSError:
        return


def _find(value: Any, key: str) -> Any:
    """First value for `key` anywhere in a nested record.

    The rollout format nests usage under a payload whose intermediate names have changed between
    releases, so addressing it by path would break on an upgrade that this survives.
    """
    if isinstance(value, dict):
        for name, item in value.items():
            if name == key:
                return item
            found = _find(item, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find(item, key)
            if found is not None:
                return found
    return None


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


# --- Claude Code -------------------------------------------------------------------------------

def claude_sessions(home: Path) -> list[Path]:
    root = home / ".claude" / "projects"
    return sorted(root.rglob("*.jsonl")) if root.is_dir() else []


def claude_usage(path: Path) -> Usage | None:
    """Fold one Claude Code transcript.

    Grouping by the assistant message id is load-bearing: one turn is written as several records
    (thinking, then each tool_use) and every one of them repeats that turn's usage. Counting records
    inflated a real 744-token turn to 2,232 in testing.
    """
    seen: set[str] = set()
    total = Usage(tool="claude", measured=True, basis="session transcript")
    found = False
    for record in _read_jsonl(path):
        message = record.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        identifier = message.get("id")
        usage = message.get("usage")
        if not identifier or not isinstance(usage, dict) or identifier in seen:
            continue
        seen.add(identifier)
        found = True
        total.input_tokens += _int(usage.get("input_tokens"))
        total.cached_input_tokens += _int(usage.get("cache_read_input_tokens"))
        total.cache_write_tokens += _int(usage.get("cache_creation_input_tokens"))
        total.output_tokens += _int(usage.get("output_tokens"))
        total.turns += 1
        model = message.get("model")
        if isinstance(model, str) and model and not model.startswith("<"):
            total.models.add(model)
    return total if found else None


def claude_session_context(path: Path) -> dict[str, Any]:
    """Attribution facts for one transcript: which checkout, which branch, when."""
    context: dict[str, Any] = {"session_id": None, "cwd": None, "branch": None,
                               "first": None, "last": None}
    for record in _read_jsonl(path):
        context["session_id"] = context["session_id"] or record.get("sessionId")
        context["cwd"] = context["cwd"] or record.get("cwd")
        context["branch"] = context["branch"] or record.get("gitBranch")
        stamp = record.get("timestamp")
        if isinstance(stamp, str):
            context["first"] = context["first"] or stamp
            context["last"] = stamp
    return context


# --- Codex -------------------------------------------------------------------------------------

def codex_sessions(home: Path) -> list[Path]:
    root = home / ".codex" / "sessions"
    return sorted(root.rglob("*.jsonl")) if root.is_dir() else []


def codex_usage(path: Path) -> Usage | None:
    """Fold one Codex rollout.

    The rollout carries a running `total_token_usage`, so the last one wins rather than summing the
    per-turn figures -- adding both would double count. Quota rides along in the same record.
    """
    total: dict[str, Any] | None = None
    quota: dict[str, Any] | None = None
    model: str | None = None
    turns = 0
    for record in _read_jsonl(path):
        running = _find(record, "total_token_usage")
        if isinstance(running, dict) and _int(running.get("total_tokens")):
            total = running
            turns += 1
        limits = _find(record, "rate_limits")
        if isinstance(limits, dict):
            quota = limits
        if model is None:
            candidate = _find(record, "model")
            if isinstance(candidate, str) and candidate:
                model = candidate
    if not total:
        return None
    return Usage(
        tool="codex",
        measured=True,
        input_tokens=_int(total.get("input_tokens")) - _int(total.get("cached_input_tokens")),
        cached_input_tokens=_int(total.get("cached_input_tokens")),
        cache_write_tokens=_int(total.get("cache_write_input_tokens")),
        output_tokens=_int(total.get("output_tokens")),
        reasoning_tokens=_int(total.get("reasoning_output_tokens")),
        turns=turns,
        models={model} if model else set(),
        quota=_codex_quota(quota),
        basis="rollout running total",
    )


def _codex_quota(limits: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(limits, dict):
        return None
    primary = limits.get("primary")
    if not isinstance(primary, dict):
        return None
    return {
        "plan": limits.get("plan_type"),
        "used_percent": primary.get("used_percent"),
        "window_minutes": primary.get("window_minutes"),
        "resets_at": primary.get("resets_at"),
        "reached": limits.get("rate_limit_reached_type"),
    }


def codex_session_context(path: Path) -> dict[str, Any]:
    for record in _read_jsonl(path):
        if record.get("type") != "session_meta":
            continue
        payload = record.get("payload")
        if isinstance(payload, dict):
            return {
                "session_id": payload.get("session_id") or payload.get("id"),
                "cwd": payload.get("cwd"),
                "branch": None,
                "first": payload.get("timestamp"),
                "last": payload.get("timestamp"),
            }
    return {"session_id": None, "cwd": None, "branch": None, "first": None, "last": None}


# --- Antigravity -------------------------------------------------------------------------------

def antigravity_conversations(home: Path) -> list[Path]:
    root = home / ".gemini" / "antigravity-ide" / "conversations"
    return sorted(root.glob("*.db")) if root.is_dir() else []


def antigravity_steps(path: Path) -> int | None:
    """Count agent steps in one conversation.

    Antigravity records no tokens anywhere -- seven stores were checked and none carries a
    token, usage, quota, or cost column. Steps are the only countable unit of work it exposes,
    which is why the estimate is per-step rather than per-word: token volume in an agent loop is
    driven by context re-read per turn, not by the length of anything a human wrote.
    """
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        names = {row[0] for row in
                 connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "steps" not in names:
            return None
        return int(connection.execute("SELECT COUNT(*) FROM steps").fetchone()[0])
    except (sqlite3.Error, TypeError, ValueError):
        return None
    finally:
        connection.close()
