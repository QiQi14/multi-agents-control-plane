"""Per-task usage accounting.

The properties pinned here are the ones that make a cost figure trustworthy rather than merely
present: a turn is counted once, an unmeasurable tool is unknown and not zero, cached tokens are
priced as cached, and no rate is accepted without provenance.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import scripts.ai_plane.usage as usage_module
import scripts.ai_plane.usage_sources as sources
from scripts.ai_plane.usage_sources import Usage


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        ("\n".join(json.dumps(r) for r in records) + "\n").encode("utf-8"))


def claude_turn(message_id: str, tokens: dict, blocks: list[dict]) -> list[dict]:
    """One assistant turn as Claude Code writes it: one record per content block.

    Every record repeats the whole turn's usage, which is the trap this fixture exists for.
    """
    return [
        {"type": "assistant", "sessionId": "s1", "cwd": "/repo", "gitBranch": "main",
         "timestamp": f"2026-07-31T10:0{index}:00Z",
         "message": {"role": "assistant", "id": message_id, "model": "claude-opus-5",
                     "usage": tokens, "content": [block]}}
        for index, block in enumerate(blocks)
    ]


class ClaudeCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.transcript = self.home / ".claude" / "projects" / "proj" / "s1.jsonl"

    def test_a_turn_split_across_records_is_counted_once(self) -> None:
        # The real defect this pins: three records, each repeating output_tokens=744, is one
        # 744-token turn. Summing records reported 2,232 -- a 3x overstatement of every cost.
        tokens = {"input_tokens": 10, "cache_read_input_tokens": 5000,
                  "cache_creation_input_tokens": 100, "output_tokens": 744}
        write_jsonl(self.transcript, claude_turn("msg_1", tokens, [
            {"type": "thinking", "thinking": "..."},
            {"type": "tool_use", "input": {"a": 1}},
            {"type": "tool_use", "input": {"b": 2}},
        ]))
        result = sources.claude_usage(self.transcript)
        self.assertIsNotNone(result)
        self.assertEqual(744, result.output_tokens)
        self.assertEqual(5000, result.cached_input_tokens)
        self.assertEqual(1, result.turns)

    def test_two_distinct_turns_do_accumulate(self) -> None:
        base = {"input_tokens": 1, "cache_read_input_tokens": 10,
                "cache_creation_input_tokens": 0, "output_tokens": 100}
        records = claude_turn("msg_1", base, [{"type": "text", "text": "one"}])
        records += claude_turn("msg_2", base, [{"type": "text", "text": "two"}])
        write_jsonl(self.transcript, records)
        result = sources.claude_usage(self.transcript)
        self.assertEqual(200, result.output_tokens)
        self.assertEqual(2, result.turns)

    def test_a_transcript_without_usage_is_unknown_not_zero(self) -> None:
        write_jsonl(self.transcript, [{"type": "user", "message": {"role": "user"}}])
        self.assertIsNone(sources.claude_usage(self.transcript))

    def test_attribution_facts_are_read(self) -> None:
        write_jsonl(self.transcript, claude_turn(
            "msg_1", {"output_tokens": 5}, [{"type": "text", "text": "x"}]))
        context = sources.claude_session_context(self.transcript)
        self.assertEqual("/repo", context["cwd"])
        self.assertEqual("main", context["branch"])
        self.assertEqual("s1", context["session_id"])


class CodexCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.rollout = self.home / ".codex" / "sessions" / "2026" / "r.jsonl"

    def test_running_total_wins_over_summing_turns(self) -> None:
        # total_token_usage is cumulative; adding each turn's copy would double count.
        write_jsonl(self.rollout, [
            {"payload": {"info": {"total_token_usage": {
                "input_tokens": 100, "cached_input_tokens": 60,
                "output_tokens": 10, "reasoning_output_tokens": 3, "total_tokens": 110}}}},
            {"payload": {"info": {"total_token_usage": {
                "input_tokens": 500, "cached_input_tokens": 400,
                "output_tokens": 40, "reasoning_output_tokens": 12, "total_tokens": 540}}}},
        ])
        result = sources.codex_usage(self.rollout)
        self.assertEqual(400, result.cached_input_tokens)
        self.assertEqual(100, result.input_tokens)   # 500 total minus 400 cached
        self.assertEqual(40, result.output_tokens)
        self.assertEqual(12, result.reasoning_tokens)

    def test_quota_is_reported_when_present(self) -> None:
        write_jsonl(self.rollout, [{"payload": {"info": {
            "total_token_usage": {"input_tokens": 1, "cached_input_tokens": 0,
                                  "output_tokens": 1, "total_tokens": 2},
            "rate_limits": {"plan_type": "pro", "primary": {
                "used_percent": 52.0, "window_minutes": 10080, "resets_at": 1784650966}}}}}])
        result = sources.codex_usage(self.rollout)
        self.assertEqual("pro", result.quota["plan"])
        self.assertEqual(52.0, result.quota["used_percent"])

    def test_absent_quota_is_none_not_a_zero_reading(self) -> None:
        write_jsonl(self.rollout, [{"payload": {"info": {"total_token_usage": {
            "input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1,
            "total_tokens": 2}}}}])
        self.assertIsNone(sources.codex_usage(self.rollout).quota)

    def test_a_quota_block_without_a_primary_window_is_not_a_reading(self) -> None:
        # A release that ships `rate_limits` without `primary` must not render as
        # "used=None%" -- an absent window is unknown, and unknown is not a number.
        write_jsonl(self.rollout, [{"payload": {"info": {
            "total_token_usage": {"input_tokens": 1, "cached_input_tokens": 0,
                                  "output_tokens": 1, "total_tokens": 2},
            "rate_limits": {"plan_type": "pro", "primary": None}}}}])
        self.assertIsNone(sources.codex_usage(self.rollout).quota)


class AntigravityCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "conv.db"

    def test_steps_are_counted_when_the_table_exists(self) -> None:
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE steps (idx INTEGER)")
        con.executemany("INSERT INTO steps VALUES (?)", [(i,) for i in range(7)])
        con.commit(); con.close()
        self.assertEqual(7, sources.antigravity_steps(self.db))

    def test_a_store_without_steps_is_unknown(self) -> None:
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE other (x INTEGER)")
        con.commit(); con.close()
        self.assertIsNone(sources.antigravity_steps(self.db))


class BillingValidationTests(unittest.TestCase):
    def profile(self, **overrides):
        rate = {"as_of": "2026-07-31", "source": "vendor price list",
                "input": 3.0, "cached_input": 0.3, "cache_write": 3.75, "output": 15.0}
        rate.update(overrides.pop("rate", {}))
        data = {"schema_version": 1, "plan": "payg", "rates": {"claude-opus-5": rate}}
        data.update(overrides)
        return data

    def test_a_rate_without_as_of_is_rejected(self) -> None:
        bad = self.profile()
        del bad["rates"]["claude-opus-5"]["as_of"]
        with self.assertRaises(usage_module.BillingError) as ctx:
            usage_module.validate_billing(bad)
        self.assertIn("as_of", str(ctx.exception))

    def test_a_rate_without_a_source_is_rejected(self) -> None:
        bad = self.profile()
        bad["rates"]["claude-opus-5"]["source"] = "  "
        with self.assertRaises(usage_module.BillingError):
            usage_module.validate_billing(bad)

    def test_an_unknown_rate_key_is_rejected(self) -> None:
        bad = self.profile()
        bad["rates"]["claude-opus-5"]["blended"] = 5.0
        with self.assertRaises(usage_module.BillingError):
            usage_module.validate_billing(bad)

    def test_a_valid_profile_passes(self) -> None:
        self.assertEqual(1, usage_module.validate_billing(self.profile())["schema_version"])


class PricingTests(unittest.TestCase):
    def billing(self):
        return {"schema_version": 1, "plan": "payg", "rates": {"claude-opus-5": {
            "as_of": "2026-07-31", "source": "vendor price list", "currency": "USD",
            "input": 3.0, "cached_input": 0.3, "cache_write": 3.75, "output": 15.0}}}

    def usage(self):
        # Shaped like real agent work: cached input dominates by ~99%.
        return Usage(tool="claude", measured=True, input_tokens=1_000_000,
                     cached_input_tokens=99_000_000, cache_write_tokens=1_000_000,
                     output_tokens=1_000_000, models={"claude-opus-5"})

    def test_cached_tokens_are_not_priced_at_the_input_rate(self) -> None:
        cost = usage_module.price(self.usage(), self.billing())
        # 1*3 + 99*0.3 + 1*3.75 + 1*15 = 51.45. Blending at the input rate would give ~309.
        self.assertAlmostEqual(51.45, cost["amount"], places=4)
        self.assertLess(cost["amount"], 100, "cached input must not be charged as fresh input")

    def test_cost_carries_its_provenance(self) -> None:
        cost = usage_module.price(self.usage(), self.billing())
        self.assertEqual("2026-07-31", cost["as_of"])
        self.assertEqual("vendor price list", cost["source"])

    def test_no_profile_yields_no_amount_and_a_reason(self) -> None:
        cost = usage_module.price(self.usage(), None)
        self.assertIsNone(cost["amount"])
        self.assertTrue(cost["reason"])

    def test_subscription_plan_has_no_per_token_price(self) -> None:
        cost = usage_module.price(self.usage(), {"schema_version": 1, "plan": "subscription"})
        self.assertIsNone(cost["amount"])
        self.assertIn("subscription", cost["reason"])

    def test_an_unpriced_model_is_unknown_rather_than_free(self) -> None:
        usage = self.usage()
        usage.models = {"some-model-with-no-rate"}
        cost = usage_module.price(usage, self.billing())
        self.assertIsNone(cost["amount"])


class EstimateTests(unittest.TestCase):
    def calibration(self):
        return {"tokens_per_magnitude": 0.02, "spread_low": 0.01, "spread_high": 0.04,
                "sample_sessions": 2, "sample_magnitude": 5_000, "tools": ["claude"],
                "fit": "median of per-session ratios"}

    def test_estimate_is_a_range_that_states_its_assumption(self) -> None:
        guess = usage_module.estimate(
            Usage(tool="antigravity", measured=False, turns=10, magnitude=100_000),
            self.calibration())
        self.assertLess(guess["low"], guess["tokens"])
        self.assertGreater(guess["high"], guess["tokens"])
        self.assertIn("NOT verified", guess["assumption"])
        self.assertIn("claude", guess["assumption"], "the cross-tool fit must be named")

    def test_estimate_avoids_false_precision(self) -> None:
        guess = usage_module.estimate(
            Usage(tool="antigravity", measured=False, turns=3677, magnitude=7_067_200_299),
            self.calibration())
        self.assertEqual(guess["tokens"],
                         usage_module._round_significant(7_067_200_299 * 0.02))
        self.assertNotEqual(guess["tokens"], int(7_067_200_299 * 0.02))

    def test_without_calibration_there_is_no_number(self) -> None:
        guess = usage_module.estimate(
            Usage(tool="antigravity", measured=False, turns=10, magnitude=100), None)
        self.assertIsNone(guess["tokens"])

    def test_an_unreadable_conversation_is_unknown_not_zero(self) -> None:
        """magnitude 0 means the store could not be read, which is not the same as no work."""
        guess = usage_module.estimate(
            Usage(tool="antigravity", measured=False, turns=10, magnitude=0), self.calibration())
        self.assertIsNone(guess["tokens"])
        self.assertIn("could not be read", guess["reason"])

    def test_a_longer_conversation_estimates_higher_at_equal_step_count(self) -> None:
        """The whole point of the reshape: step count alone cannot separate these two."""
        calibration = self.calibration()
        short = usage_module.estimate(
            Usage(tool="antigravity", measured=False, turns=50, magnitude=1_000_000), calibration)
        long = usage_module.estimate(
            Usage(tool="antigravity", measured=False, turns=50, magnitude=90_000_000), calibration)
        self.assertGreater(long["tokens"], short["tokens"])

    def test_calibration_uses_only_measured_sessions_with_magnitude(self) -> None:
        sessions = [
            {"usage": Usage(tool="claude", measured=True, turns=2, output_tokens=1000,
                            magnitude=50_000)},
            {"usage": Usage(tool="antigravity", measured=False, turns=999, magnitude=9_000_000)},
        ]
        calibration = usage_module.calibrate(sessions)
        self.assertEqual(1, calibration["sample_sessions"])
        self.assertEqual(["claude"], calibration["tools"])

    def test_a_single_sample_widens_the_band_instead_of_claiming_a_point(self) -> None:
        """One measured session gives no observable spread. low == high would read as precision."""
        one = {"usage": Usage(tool="claude", measured=True, turns=1, output_tokens=1000,
                              magnitude=100_000)}
        calibration = usage_module.calibrate([one])
        self.assertEqual(calibration["spread_low"], calibration["spread_high"])
        guess = usage_module.estimate(
            Usage(tool="antigravity", measured=False, turns=5, magnitude=1_000_000), calibration)
        self.assertLess(guess["low"], guess["high"], "a degenerate spread must still yield a range")
        self.assertEqual("assumed", guess["band"])
        self.assertIn("too small to observe a spread", guess["assumption"])

    def test_calibration_median_resists_one_huge_session(self) -> None:
        """A summed fit is dragged by the largest session; the median is not.

        Three ordinary sessions share a ratio; one session a thousand times larger carries a very
        different one. Total-over-total would land near the outlier's ratio.
        """
        def measured(tokens, magnitude):
            return {"usage": Usage(tool="claude", measured=True, turns=1,
                                   output_tokens=tokens, magnitude=magnitude)}
        sessions = [measured(100, 10_000), measured(100, 10_000), measured(100, 10_000),
                    measured(50_000_000, 10_000_000)]
        calibration = usage_module.calibrate(sessions)
        summed = sum(s["usage"].output_tokens for s in sessions) / sum(
            s["usage"].magnitude for s in sessions)
        self.assertAlmostEqual(0.01, calibration["tokens_per_magnitude"], places=6)
        self.assertGreater(summed, calibration["tokens_per_magnitude"] * 2,
                           "a summed fit should differ sharply, or this fixture proves nothing")


class AdvisoryBoundaryTests(unittest.TestCase):
    """The routing law forbids the plane touching network, auth, account, or payment state."""

    NETWORK_MODULES = {
        "socket", "ssl", "http", "urllib", "requests", "httpx", "ftplib",
        "smtplib", "telnetlib", "asyncio", "xmlrpc", "webbrowser",
    }

    def imported_roots(self, path: Path) -> set[str]:
        # AST, not substring: the modules describe their own boundary in prose, and matching prose
        # made this guard fail on the sentence promising it never opens a socket.
        import ast
        roots: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                roots.add(node.module.split(".")[0])
        return roots

    def test_usage_modules_import_nothing_that_reaches_the_network(self) -> None:
        for name in ("usage.py", "usage_sources.py"):
            path = Path(__file__).resolve().parents[1] / "ai_plane" / name
            with self.subTest(module=name):
                offenders = self.imported_roots(path) & self.NETWORK_MODULES
                self.assertEqual(set(), offenders, f"{name} imports {sorted(offenders)}")

    def test_the_guard_would_catch_a_real_network_import(self) -> None:
        # A guard that cannot fail is not a guard.
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.py"
            probe.write_text("import urllib.request\n", encoding="utf-8")
            self.assertIn("urllib", self.imported_roots(probe) & self.NETWORK_MODULES)

    def test_no_credential_file_is_read(self) -> None:
        for name in ("usage.py", "usage_sources.py"):
            text = (Path(__file__).resolve().parents[1] / "ai_plane" / name).read_text(
                encoding="utf-8")
            with self.subTest(module=name):
                self.assertNotIn("auth.json", text)


if __name__ == "__main__":
    unittest.main()
