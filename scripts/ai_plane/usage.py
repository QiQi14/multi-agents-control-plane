"""Per-task usage accounting: what a task consumed, and how confidently we know.

Two things this deliberately does not do.

It does not price a blended token. Cached input is 96-99% of all input volume in real agent
sessions and is charged at a fraction of the input rate, so one blended number is wrong by roughly
an order of magnitude. The four classes stay separate from collection through to the report.

It does not estimate tokens from word or character counts. Input outweighs output by 300-640x in an
agent loop, because the cost is context re-read every turn rather than text anyone wrote. No word
count predicts that. Where a tool records nothing, the estimate is driven by how far its own
conversation GREW -- the summed running prefix, which is the area under the transcript rather than
its length -- calibrated against tools that do record, and labelled an estimate wherever it appears.

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
    antigravity_reread_magnitude,
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
                           basis="conversation growth; antigravity records no tokens",
                           magnitude=antigravity_reread_magnitude(path) or 0),
            "session_id": path.stem, "cwd": None, "branch": None, "first": None, "last": None,
        })
    if cwd_filter:
        wanted = str(cwd_filter).replace("\\", "/").rstrip("/").lower()
        sessions = [s for s in sessions
                    if s.get("cwd") and str(s["cwd"]).replace("\\", "/").rstrip("/").lower() == wanted]
    return sessions


def calibrate(sessions: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Tokens per unit of prefix-re-read magnitude, learned from tools that measure.

    The quantity being extrapolated matters more than the constant. An agent loop re-reads its
    accumulated context every step, so consumption tracks the AREA under the transcript, not its
    length and not the step count. Measured across 106 local Claude sessions carrying 8.74e9 input
    tokens: a flat tokens-per-turn multiplier put 49% of sessions within a factor of two (median
    error 103%), while this magnitude-based fit put 98% within a factor of two (median error 59%).

    The constant is the MEDIAN of each session's own ratio, not total-over-total. A summed fit is
    dominated by a handful of very large sessions and then misprices every ordinary one -- which is
    the same bias, moved rather than removed.
    """
    ratios: list[float] = []
    tools: set[str] = set()
    magnitude = 0
    for entry in sessions:
        usage = entry["usage"]
        if not usage.measured or not usage.magnitude:
            continue
        tokens = (usage.input_tokens + usage.cached_input_tokens
                  + usage.cache_write_tokens + usage.output_tokens)
        if tokens <= 0:
            continue
        ratios.append(tokens / usage.magnitude)
        tools.add(usage.tool)
        magnitude += usage.magnitude
    if not ratios:
        return None
    ratios.sort()
    middle = len(ratios) // 2
    median = ratios[middle] if len(ratios) % 2 else (ratios[middle - 1] + ratios[middle]) / 2
    # Spread is reported so a reader can judge the estimate rather than take it: the interquartile
    # band is how far ordinary sessions sit from the constant being applied to them.
    low = ratios[len(ratios) // 4]
    high = ratios[(len(ratios) * 3) // 4]
    return {
        "tokens_per_magnitude": median,
        "spread_low": low,
        "spread_high": high,
        "sample_sessions": len(ratios),
        "sample_magnitude": magnitude,
        "tools": sorted(tools),
        "fit": "median of per-session ratios; a summed fit is dominated by the largest sessions",
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
    """A range, with its unverified assumptions stated.

    The magnitude is this tool's own recorded conversation growth, so session length is no longer
    laundered through an average. Two assumptions remain, and both are named rather than absorbed:
    the constant was fitted on a DIFFERENT tool's sessions, and this tool's stored payload may
    include render or permission data that is not context, which inflates magnitude. The band comes
    from the observed spread of the fit, not from a decorative multiplier.
    """
    if not calibration:
        return {"tokens": None, "reason": "no calibration available from a measuring tool"}
    if not usage.magnitude:
        return {"tokens": None,
                "reason": "this tool's conversation store could not be read for content size"}
    rate = calibration["tokens_per_magnitude"]
    low_rate, high_rate = calibration["spread_low"], calibration["spread_high"]
    narrow = high_rate <= low_rate
    if narrow:
        # Too few measured sessions to observe a spread. Reporting low == high would claim a
        # precision the sample cannot support, so widen explicitly and say the band is assumed
        # rather than measured.
        low_rate, high_rate = rate / 3, rate * 3
    others = [t for t in calibration.get("tools", []) if t != usage.tool]
    assumption = ("the rate was fitted on " + (", ".join(others) or "another tool")
                  + " sessions and applied to this tool; that equivalence is NOT verified")
    if narrow:
        assumption += ("; the sample was too small to observe a spread, so the band is an assumed "
                       "third-to-triple rather than a measured one")
    return {
        "low": _round_significant(usage.magnitude * low_rate),
        "tokens": _round_significant(usage.magnitude * rate),
        "high": _round_significant(usage.magnitude * high_rate),
        "basis": f"{usage.magnitude:,} bytes of prefix re-read x {rate:.4f} tokens/byte",
        "band": "assumed" if narrow else "observed spread of the fit",
        "assumption": assumption,
        "calibrated_from": calibration,
    }


TASK_STATES = ("queue", "active", "done", "archive")
PLACEHOLDER = "fill-me"


def receipt_session_map(ai: Path | None = None) -> tuple[dict[str, str], dict[str, Any]]:
    """Map each recorded agent session to the task whose receipt claims it.

    Returns ``(session_id -> task_id, coverage)``. Only an identity a receipt actually recorded is
    used. Nothing is inferred from branch, which collapses to the default branch under patch
    isolation, nor from wall-clock overlap, which cannot separate two agents working concurrently
    in one checkout: a wrong attribution is worse than none, because it reads as a fact.

    A session claimed by more than one task is dropped rather than split or double-counted, and
    counted so the ambiguity stays visible instead of quietly shrinking the total.
    """
    root = ai or constants.AI
    owners: dict[str, set[str]] = {}
    tasks_seen: set[str] = set()
    tasks_with_identity: set[str] = set()
    for state in TASK_STATES:
        state_dir = root / "tasks" / state
        if not state_dir.is_dir():
            continue
        for task_dir in sorted(p for p in state_dir.iterdir() if p.is_dir()):
            tasks_seen.add(task_dir.name)
            for receipt in sorted(task_dir.glob("receipt*.yaml")):
                try:
                    data = json.loads(receipt.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    # A legacy free-form receipt is not JSON. It carries no identity by
                    # definition, so it is skipped rather than treated as a failure.
                    continue
                actor = data.get("actor")
                session = actor.get("session_id") if isinstance(actor, dict) else None
                if not isinstance(session, str) or not session or session.startswith(PLACEHOLDER):
                    continue
                owners.setdefault(session, set()).add(task_dir.name)
                tasks_with_identity.add(task_dir.name)

    ambiguous = {s for s, t in owners.items() if len(t) > 1}
    mapping = {s: next(iter(t)) for s, t in owners.items() if len(t) == 1}
    coverage = {
        "tasks_total": len(tasks_seen),
        "tasks_attributed": len(tasks_with_identity),
        "sessions_claimed": len(mapping),
        "sessions_ambiguous": len(ambiguous),
    }
    return mapping, coverage


def attribute(sessions: list[dict[str, Any]], mapping: dict[str, str]) -> dict[str, Usage]:
    """Fold each collected session into the task that claimed it, by recorded identity alone."""
    by_task: dict[str, Usage] = {}
    for session in sessions:
        task = mapping.get(str(session.get("session_id") or ""))
        if not task:
            continue
        usage = session["usage"]
        by_task[task] = by_task[task].add(usage) if task in by_task else usage
    return by_task


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
        print(f"\nCalibration: {calibration['tokens_per_magnitude']:.4f} tokens per byte of prefix "
              f"re-read (middle half spans {calibration['spread_low']:.4f}-"
              f"{calibration['spread_high']:.4f}), fitted as the median of "
              f"{calibration['sample_sessions']} measured session(s) "
              f"({', '.join(calibration['tools'])}).")
    if not billing:
        print(f"\nNo billing profile. Create {BILLING_RELATIVE_PATH} to price measured tokens; "
              "every rate needs an as_of date and a source.")


def cmd_usage_build(args: argparse.Namespace, *, die) -> None:
    """Write the reader's usage asset from local session records.

    Deliberately separate from `ai docs build`. That build is a deterministic projection of the
    repository and its output is meant to be shareable; this reads the operator's home directory.
    Keeping it a distinct, opt-in command is what stops per-machine data reaching an exported
    report.
    """
    from datetime import datetime, timezone

    from scripts.ai_plane import usage_reader

    try:
        billing = load_billing()
    except BillingError as error:
        die(str(error))
    home = Path.home()
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        sessions = collect(home, cwd_filter=str(constants.ROOT) if args.here else None)
    except OSError as error:
        payload = usage_reader.unavailable_payload(str(error))
    else:
        mapping, coverage = receipt_session_map()
        payload = usage_reader.build_payload(
            sessions, billing, generated_at=stamp,
            by_task=attribute(sessions, mapping), coverage=coverage)

    assets = constants.AI / "_site" / "assets"
    if not assets.parent.is_dir():
        die("No site to write into. Run `python scripts/ai_cli.py docs build` first.")
    path = usage_reader.write_asset(assets, payload)
    tools = ", ".join(entry["tool"] for entry in payload["tools"]) or "none"
    print(f"Wrote {rel(path)}  state={payload['state']}  "
          f"sessions={payload['sessions_read']}  tools={tools}")
    print("This asset is per-machine and gitignored. It is not part of `ai docs export`.")


def cmd_usage(args: argparse.Namespace, *, die) -> None:
    """Route the usage subcommands here rather than in the CLI facade.

    `ai_cli.py` is capped by an architecture ratchet and is meant to stay a thin facade, so a
    command owns its own subcommand routing the way `ext` and `skills` already do.
    """
    if getattr(args, "usage_command", "show") == "build":
        cmd_usage_build(args, die=die)
    else:
        cmd_usage_show(args, die=die)


def add_usage_parser(sub) -> None:
    usage = sub.add_parser("usage", help="Report what agent work consumed (advisory; never routes)")
    usage_sub = usage.add_subparsers(dest="usage_command", required=True)
    show = usage_sub.add_parser("show", help="Show measured tokens, quota, and estimates by tool")
    show.add_argument("--here", action="store_true",
                      help="Only sessions whose working directory is this repository")
    build = usage_sub.add_parser(
        "build", help="Write the reader's usage panel data from local session records")
    build.add_argument("--here", action="store_true",
                       help="Only sessions whose working directory is this repository")
