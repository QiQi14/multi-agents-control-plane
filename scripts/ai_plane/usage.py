"""Per-task usage accounting: what a task consumed, and how confidently we know.

Two things this deliberately does not do.

It does not price a blended token. Cached input is 96-99% of all input volume in real agent
sessions and is charged at a fraction of the input rate, so one blended number is wrong by roughly
an order of magnitude. The four classes stay separate from collection through to the report.

It does not estimate tokens from word or character counts. Input outweighs output by 300-640x in an
agent loop, because the cost is context re-read every turn rather than text anyone wrote. No word
count predicts that. Where a tool records nothing, the estimate is per-STEP, calibrated from tools
that do record, and it is labelled an estimate everywhere it appears.

Rates are user-supplied data carrying an `as_of` date and a source. None is built in: a price
recalled by a model is a guess with a short half-life, and a wrong rate that looks authoritative is
worse than no rate at all.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import scripts.ai_plane.constants as constants
from scripts.ai_plane.usage_sources import (
    Usage,
    antigravity_conversations,
    antigravity_steps,
    claude_session_context,
    claude_sessions,
    claude_usage,
    codex_session_context,
    codex_sessions,
    codex_usage,
)
from scripts.ai_plane.utils import rel

BILLING_RELATIVE_PATH = ".ai/.local/billing.json"
SCHEMA_VERSION = 1
TOKEN_CLASSES = ("input", "cached_input", "cache_write", "output")
# Rate keys a table may carry. `reasoning` is optional: providers that bill it as output omit it.
RATE_KEYS = TOKEN_CLASSES + ("reasoning",)


def billing_path(ai: Path | None = None) -> Path:
    return (ai or constants.AI) / ".local" / "billing.json"


class BillingError(ValueError):
    """A billing profile that cannot be trusted to price anything."""


def validate_billing(data: Any) -> dict[str, Any]:
    """Reject a profile that would produce a confident but meaningless number."""
    if not isinstance(data, dict):
        raise BillingError("billing profile must be an object")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise BillingError(
            f"billing schema_version must be {SCHEMA_VERSION}, got {data.get('schema_version')!r}")
    plan = data.get("plan")
    if plan not in {"subscription", "payg", "unknown"}:
        raise BillingError(f"plan must be subscription, payg, or unknown; got {plan!r}")
    rates = data.get("rates")
    if rates is None:
        return {"schema_version": SCHEMA_VERSION, "plan": plan, "rates": {}}
    if not isinstance(rates, dict):
        raise BillingError("rates must be an object keyed by model")
    for model, entry in rates.items():
        where = f"rates[{model!r}]"
        if not isinstance(entry, dict):
            raise BillingError(f"{where} must be an object")
        # as_of and source are required because a rate without them cannot be audited or aged out,
        # and an unaudited rate presented as cost is exactly the confident-but-wrong failure.
        for required in ("as_of", "source"):
            if not isinstance(entry.get(required), str) or not entry[required].strip():
                raise BillingError(f"{where} needs a non-empty {required}")
        for key, value in entry.items():
            if key in ("as_of", "source", "currency"):
                continue
            if key not in RATE_KEYS:
                raise BillingError(f"{where} has unknown rate key {key!r}")
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                raise BillingError(f"{where}.{key} must be a non-negative number")
    return data


def load_billing(ai: Path | None = None) -> dict[str, Any] | None:
    path = billing_path(ai)
    if not path.is_file():
        return None
    try:
        return validate_billing(json.loads(path.read_text(encoding="utf-8")))
    except ValueError as error:
        raise BillingError(f"{rel(path)}: {error}") from error


def price(usage: Usage, billing: dict[str, Any] | None) -> dict[str, Any]:
    """Cost for one tool's usage, or an explicit reason there is none.

    Rates are per million tokens, the unit every provider publishes, so the arithmetic reads the
    same as the price list it was copied from.
    """
    if not billing or billing.get("plan") == "subscription":
        return {"amount": None, "reason": "subscription plan: no per-token price applies"
                if billing else "no billing profile configured"}
    rates = billing.get("rates") or {}
    model = next(iter(sorted(usage.models)), None)
    entry = rates.get(model) if model else None
    if not entry:
        return {"amount": None, "reason": f"no rate configured for model {model or 'unknown'}"}
    amounts = {
        "input": usage.input_tokens,
        "cached_input": usage.cached_input_tokens,
        "cache_write": usage.cache_write_tokens,
        "output": usage.output_tokens,
    }
    total = 0.0
    priced: dict[str, float] = {}
    for key, tokens in amounts.items():
        rate = entry.get(key)
        if rate is None:
            continue
        component = tokens / 1_000_000 * rate
        priced[key] = round(component, 6)
        total += component
    if "reasoning" in entry and usage.reasoning_tokens:
        component = usage.reasoning_tokens / 1_000_000 * entry["reasoning"]
        priced["reasoning"] = round(component, 6)
        total += component
    return {
        "amount": round(total, 6),
        "currency": entry.get("currency", "USD"),
        "model": model,
        "as_of": entry.get("as_of"),
        "source": entry.get("source"),
        "components": priced,
    }


def collect(home: Path, *, cwd_filter: str | None = None) -> list[dict[str, Any]]:
    """Every readable session on this machine, with its attribution facts."""
    sessions: list[dict[str, Any]] = []
    for path in claude_sessions(home):
        usage = claude_usage(path)
        if usage is None:
            continue
        context = claude_session_context(path)
        sessions.append({"path": path, "usage": usage, **context})
    for path in codex_sessions(home):
        usage = codex_usage(path)
        if usage is None:
            continue
        context = codex_session_context(path)
        sessions.append({"path": path, "usage": usage, **context})
    for path in antigravity_conversations(home):
        steps = antigravity_steps(path)
        if steps is None:
            continue
        sessions.append({
            "path": path,
            "usage": Usage(tool="antigravity", measured=False, turns=steps,
                           basis="step count; antigravity records no tokens"),
            "session_id": path.stem, "cwd": None, "branch": None, "first": None, "last": None,
        })
    if cwd_filter:
        wanted = str(cwd_filter).replace("\\", "/").rstrip("/").lower()
        sessions = [s for s in sessions
                    if s.get("cwd") and str(s["cwd"]).replace("\\", "/").rstrip("/").lower() == wanted]
    return sessions


def calibrate(sessions: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Tokens per step, learned from tools that measure, for tools that do not.

    This is the only honest basis for estimating an unmeasurable tool: it extrapolates the thing
    that actually drives volume, which is turns against a growing context.
    """
    measured = [s["usage"] for s in sessions if s["usage"].measured and s["usage"].turns]
    if not measured:
        return None
    turns = sum(u.turns for u in measured)
    if not turns:
        return None
    total = sum(u.input_tokens + u.cached_input_tokens + u.cache_write_tokens + u.output_tokens
                for u in measured)
    return {
        "tokens_per_turn": round(total / turns, 1),
        "sample_turns": turns,
        "sample_sessions": len(measured),
        "tools": sorted({u.tool for u in measured}),
    }


def _round_significant(value: float, digits: int = 2) -> int:
    """Round hard. An estimate printed to nine figures claims a precision it does not have."""
    if value <= 0:
        return 0
    from math import floor, log10
    magnitude = floor(log10(value))
    factor = 10 ** (magnitude - digits + 1)
    return int(round(value / factor) * factor)


def estimate(usage: Usage, calibration: dict[str, Any] | None) -> dict[str, Any]:
    """A range, with its unverified assumption stated.

    The calibration is tokens per TURN, learned from tools that record turns. This tool records
    STEPS, and nothing establishes that a step and a turn are the same unit of work -- Claude writes
    roughly three records per turn, so a step is plausibly finer. Rather than launder that gap into
    a single confident number, the estimate spans an order of magnitude around the assumption and
    says why.
    """
    if not usage.turns or not calibration:
        return {"tokens": None, "reason": "no calibration available from a measuring tool"}
    centre = usage.turns * calibration["tokens_per_turn"]
    return {
        "low": _round_significant(centre / 3),
        "tokens": _round_significant(centre),
        "high": _round_significant(centre * 3),
        "basis": f"{usage.turns:,} steps x {calibration['tokens_per_turn']:,.0f} tokens/turn",
        "assumption": "one step is treated as one turn; that equivalence is NOT verified",
        "calibrated_from": calibration,
    }


def _fmt(value: int) -> str:
    return f"{value:,}"


def cmd_usage_show(args: argparse.Namespace, *, die) -> None:
    home = Path.home()
    try:
        billing = load_billing()
    except BillingError as error:
        die(str(error))
    sessions = collect(home, cwd_filter=str(constants.ROOT) if getattr(args, "here", False) else None)
    if not sessions:
        print("No readable agent sessions were found on this machine.")
        print("Nothing is reported as zero: an unmeasurable tool is reported as unknown.")
        return

    calibration = calibrate(sessions)
    by_tool: dict[str, Usage] = {}
    for session in sessions:
        usage = session["usage"]
        by_tool[usage.tool] = by_tool[usage.tool].add(usage) if usage.tool in by_tool else usage

    print(f"Sessions read: {len(sessions)}"
          + (f"  (filtered to {rel(constants.ROOT)})" if getattr(args, "here", False) else ""))
    for tool in sorted(by_tool):
        usage = by_tool[tool]
        label = "measured" if usage.measured else "ESTIMATE"
        print(f"\n{tool}  [{label}]  turns={_fmt(usage.turns)}  basis={usage.basis}")
        if usage.measured:
            print(f"  input          {_fmt(usage.input_tokens):>15}")
            print(f"  cached input   {_fmt(usage.cached_input_tokens):>15}")
            print(f"  cache write    {_fmt(usage.cache_write_tokens):>15}")
            print(f"  output         {_fmt(usage.output_tokens):>15}")
            if usage.reasoning_tokens:
                print(f"  of which reasoning {_fmt(usage.reasoning_tokens):>11}")
            if usage.models:
                print(f"  models         {', '.join(sorted(usage.models))}")
            cost = price(usage, billing)
            if cost["amount"] is None:
                print(f"  cost           unknown ({cost['reason']})")
            else:
                print(f"  cost           {cost['amount']:.4f} {cost['currency']}"
                      f"  (rate as_of {cost['as_of']}, {cost['source']})")
        else:
            guess = estimate(usage, calibration)
            if guess["tokens"] is None:
                print(f"  tokens         unknown ({guess['reason']})")
            else:
                print(f"  tokens         {_fmt(guess['low'])} - {_fmt(guess['high'])}"
                      f"  (midpoint {_fmt(guess['tokens'])})")
                print(f"  ESTIMATE       {guess['basis']}")
                print(f"  assumption     {guess['assumption']}")
        if usage.quota:
            q = usage.quota
            print(f"  quota          plan={q.get('plan')} used={q.get('used_percent')}%"
                  f" window={q.get('window_minutes')}min")
    if calibration:
        print(f"\nCalibration: {calibration['tokens_per_turn']} tokens/turn from "
              f"{calibration['sample_turns']:,} measured turns across "
              f"{calibration['sample_sessions']} session(s) ({', '.join(calibration['tools'])}).")
    if not billing:
        print(f"\nNo billing profile. Create {BILLING_RELATIVE_PATH} to price measured tokens; "
              "every rate needs an as_of date and a source.")


def add_usage_parser(sub) -> None:
    usage = sub.add_parser("usage", help="Report what agent work consumed (advisory; never routes)")
    usage_sub = usage.add_subparsers(dest="usage_command", required=True)
    show = usage_sub.add_parser("show", help="Show measured tokens, quota, and estimates by tool")
    show.add_argument("--here", action="store_true",
                      help="Only sessions whose working directory is this repository")
