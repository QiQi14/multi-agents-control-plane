"""Guards for the reader's usage panel.

The two failure modes worth a test here are not arithmetic. They are (1) per-machine identifiers
reaching an asset that can be copied out of the checkout, and (2) the panel quietly presenting an
unmeasurable tool as zero, or adding an estimate to a measurement. Both look fine in a screenshot.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.ai_plane import usage as usage_module
from scripts.ai_plane import usage_reader
from scripts.ai_plane.usage_sources import Usage

PRODUCTION = (Path(__file__).resolve().parents[1] / "ai_plane" / "knowledge_projection"
              / "reader_assets" / "production")

MACHINE_SECRETS = {
    "cwd": "/home/someone/private-client-repo",
    "session_id": "0ac84bee-79ac-406e-9103-78b289321f9e",
    "branch": "feature/unreleased-acquisition",
}


def _session(usage: Usage) -> dict:
    """A collected session, carrying exactly the attribution facts `collect()` attaches."""
    return {"path": Path("/home/someone/.claude/projects/x.jsonl"), "usage": usage,
            "first": "2026-08-01T00:00:00Z", "last": "2026-08-01T01:00:00Z", **MACHINE_SECRETS}


def _measured(tool: str = "claude") -> Usage:
    # `magnitude` is the prefix-re-read size of this session; a measured tool supplies both it and
    # its token counts, which is the pairing the estimator's constant is fitted from.
    return Usage(tool=tool, measured=True, input_tokens=1_000, cached_input_tokens=900_000,
                 cache_write_tokens=5_000, output_tokens=20_000, reasoning_tokens=6_000,
                 turns=40, models={"claude-opus-5"}, basis="session transcript",
                 magnitude=40_000_000)


def _unmeasured(tool: str = "antigravity") -> Usage:
    return Usage(tool=tool, measured=False, turns=100, magnitude=9_000_000,
                 basis="conversation growth; antigravity records no tokens")


class UsagePayloadLeakTest(unittest.TestCase):
    def test_no_machine_identifier_reaches_the_asset(self) -> None:
        """The decisive guard: the site directory is copyable, so the asset must be anonymous.

        `collect()` attaches cwd, session id, and branch to every session. If the payload ever
        starts carrying the session list instead of per-tool aggregates, this fails.
        """
        payload = usage_reader.build_payload(
            [_session(_measured()), _session(_unmeasured())], None, generated_at="2026-08-01T00:00:00+00:00")
        rendered = usage_reader.render_asset(payload)
        for name, secret in MACHINE_SECRETS.items():
            self.assertNotIn(secret, rendered, f"{name} leaked into the reader asset")
        self.assertNotIn(".jsonl", rendered, "a session file path leaked into the reader asset")

    def test_the_asset_is_a_script_assignment_not_json(self) -> None:
        """It is loaded by a <script src> tag because the reader is opened over file://,
        where fetching a sibling JSON file is blocked."""
        rendered = usage_reader.render_asset(usage_reader.not_built_payload())
        self.assertTrue(rendered.startswith("/*"), "asset should lead with its generated banner")
        self.assertIn("window.CP_USAGE = {", rendered)
        self.assertTrue(rendered.rstrip().endswith(";"), "assignment must terminate")
        body = rendered[rendered.index("{"):rendered.rstrip().rindex("}") + 1]
        json.loads(body)


class UsagePayloadHonestyTest(unittest.TestCase):
    def test_not_built_reports_no_tools_rather_than_zero_tokens(self) -> None:
        payload = usage_reader.not_built_payload()
        self.assertEqual("not_built", payload["state"])
        self.assertEqual([], payload["tools"])
        self.assertIn("usage build", payload["command"])

    def test_no_sessions_is_unavailable_not_measured(self) -> None:
        payload = usage_reader.build_payload([], None, generated_at="2026-08-01T00:00:00+00:00")
        self.assertEqual("unavailable", payload["state"])
        self.assertEqual([], payload["tools"])
        self.assertIn("never as zero", payload["guidance"])

    def test_a_measured_tool_keeps_the_four_classes_separate(self) -> None:
        """Cached input runs 96-99% of volume at a fraction of the rate, so a blended total is
        wrong by roughly an order of magnitude. The classes must survive into the payload."""
        payload = usage_reader.build_payload(
            [_session(_measured())], None, generated_at="2026-08-01T00:00:00+00:00")
        tokens = payload["tools"][0]["tokens"]
        for cls in ("input", "cached_input", "cache_write", "output"):
            self.assertIn(cls, tokens)
        self.assertEqual(900_000, tokens["cached_input"])
        self.assertNotEqual(tokens["input"], tokens["cached_input"])

    def test_an_unmeasured_tool_carries_no_token_total_to_add(self) -> None:
        """`tokens` stays None so nothing downstream can sum an estimate into a measurement."""
        payload = usage_reader.build_payload(
            [_session(_measured()), _session(_unmeasured())], None,
            generated_at="2026-08-01T00:00:00+00:00")
        entry = next(t for t in payload["tools"] if not t["measured"])
        self.assertIsNone(entry["tokens"])
        self.assertIsNotNone(entry["estimate"])

    def test_an_estimate_states_its_unverified_assumption(self) -> None:
        payload = usage_reader.build_payload(
            [_session(_measured()), _session(_unmeasured())], None,
            generated_at="2026-08-01T00:00:00+00:00")
        estimate = next(t for t in payload["tools"] if not t["measured"])["estimate"]
        self.assertIn("NOT verified", estimate["assumption"])
        self.assertLess(estimate["low"], estimate["high"], "an estimate is a range, not a point")

    def test_an_unpriced_tool_says_why_instead_of_costing_zero(self) -> None:
        payload = usage_reader.build_payload(
            [_session(_measured())], None, generated_at="2026-08-01T00:00:00+00:00")
        cost = payload["tools"][0]["cost"]
        self.assertIsNone(cost["amount"])
        self.assertTrue(cost["reason"], "an unpriced tool must carry a reason")

    def test_a_subscription_plan_is_not_priced_per_token(self) -> None:
        billing = {"schema_version": 1, "plan": "subscription", "rates": {}}
        payload = usage_reader.build_payload(
            [_session(_measured())], billing, generated_at="2026-08-01T00:00:00+00:00")
        self.assertIsNone(payload["tools"][0]["cost"]["amount"])
        self.assertTrue(payload["billing"]["configured"])


class AttributionTest(unittest.TestCase):
    """Joining cost to a task. A wrong join is worse than no join: it reads as a fact."""

    def _plane(self, receipts: dict[str, dict]) -> Path:
        root = Path(tempfile.mkdtemp(prefix="usage-attr-"))
        self.addCleanup(shutil.rmtree, root, True)
        for task_id, actor in receipts.items():
            folder = root / "tasks" / "done" / task_id
            folder.mkdir(parents=True)
            (folder / "receipt.executor.yaml").write_text(
                json.dumps({"schema_version": 1, "task_id": task_id, "actor": actor}),
                encoding="utf-8")
        return root

    def test_a_recorded_session_maps_to_its_task(self) -> None:
        plane = self._plane({"task_1_alpha": {"name": "x", "session_id": "abc-1"}})
        mapping, coverage = usage_module.receipt_session_map(plane)
        self.assertEqual({"abc-1": "task_1_alpha"}, mapping)
        self.assertEqual(1, coverage["tasks_attributed"])
        self.assertEqual(1, coverage["tasks_total"])

    def test_the_scaffold_placeholder_is_not_an_identity(self) -> None:
        """A generated receipt ships `session_id: fill-me`; joining on it would attribute every
        unfilled task to one imaginary session."""
        plane = self._plane({"task_1_alpha": {"name": "x", "session_id": "fill-me"}})
        mapping, coverage = usage_module.receipt_session_map(plane)
        self.assertEqual({}, mapping)
        self.assertEqual(0, coverage["tasks_attributed"])

    def test_a_session_claimed_by_two_tasks_is_dropped_not_split(self) -> None:
        plane = self._plane({
            "task_1_alpha": {"name": "x", "session_id": "shared-1"},
            "task_2_beta": {"name": "y", "session_id": "shared-1"},
        })
        mapping, coverage = usage_module.receipt_session_map(plane)
        self.assertEqual({}, mapping, "an ambiguous session must not be attributed to either task")
        self.assertEqual(1, coverage["sessions_ambiguous"])

    def test_a_receipt_without_an_identity_contributes_nothing(self) -> None:
        plane = self._plane({"task_1_alpha": {"name": "x"}})
        mapping, coverage = usage_module.receipt_session_map(plane)
        self.assertEqual({}, mapping)
        self.assertEqual(1, coverage["tasks_total"], "the task still counts toward coverage")
        self.assertEqual(0, coverage["tasks_attributed"])

    def test_attribution_joins_only_on_recorded_identity(self) -> None:
        """Never on branch, which is the default branch under patch isolation, nor on timing."""
        sessions = [
            {"usage": _measured("claude"), "session_id": "abc-1",
             "branch": "main", "first": "2026-08-01T00:00:00Z"},
            {"usage": _measured("codex"), "session_id": "no-claim",
             "branch": "main", "first": "2026-08-01T00:00:00Z"},
        ]
        by_task = usage_module.attribute(sessions, {"abc-1": "task_1_alpha"})
        self.assertEqual(["task_1_alpha"], list(by_task))
        self.assertEqual("claude", by_task["task_1_alpha"].tool)

    def test_an_unattributed_catalog_reports_coverage_not_zero_cost(self) -> None:
        payload = usage_reader.build_payload(
            [_session(_measured())], None, generated_at="2026-08-01T00:00:00+00:00",
            by_task={}, coverage={"tasks_total": 237, "tasks_attributed": 0,
                                  "sessions_claimed": 0, "sessions_ambiguous": 0})
        self.assertEqual([], payload["tasks"])
        self.assertEqual(237, payload["coverage"]["tasks_total"])

    def test_a_task_row_carries_no_session_identity(self) -> None:
        payload = usage_reader.build_payload(
            [_session(_measured())], None, generated_at="2026-08-01T00:00:00+00:00",
            by_task={"task_1_alpha": _measured()},
            coverage={"tasks_total": 1, "tasks_attributed": 1,
                      "sessions_claimed": 1, "sessions_ambiguous": 0})
        rendered = usage_reader.render_asset(payload)
        self.assertIn("task_1_alpha", rendered)
        for secret in MACHINE_SECRETS.values():
            self.assertNotIn(secret, rendered)


class UsageReaderWiringTest(unittest.TestCase):
    """Structural contracts over the shipped reader source, which no Python test would otherwise
    reach. `accepted/` stays hash-pinned; only `production/` may carry this."""

    def test_the_shell_loads_the_usage_asset(self) -> None:
        html = (PRODUCTION / "index.html").read_text(encoding="utf-8")
        self.assertIn('<script src="assets/usage-data.js"></script>', html)

    def test_the_app_renders_a_usage_section_from_the_global(self) -> None:
        app = (PRODUCTION / "app.js").read_text(encoding="utf-8")
        self.assertIn("window.CP_USAGE", app)
        # Assert the CALL, not the declaration: `usageSection()` alone also matches
        # `function usageSection() {`, so deleting the call site would leave the guard green.
        self.assertIn("usageSection() +", app, "the home screen must call the usage section")
        home = app[app.index("function screenHome"):app.index("function usageData")]
        self.assertIn("usageSection()", home, "the call must live inside screenHome")

    def test_the_app_handles_a_missing_or_unbuilt_asset(self) -> None:
        """A reader opened before `usage build` has run must not throw or show a zero."""
        app = (PRODUCTION / "app.js").read_text(encoding="utf-8")
        self.assertIn("state !== 'measured'", app)
        self.assertIn("usage-empty", app)

    def test_app_js_declares_no_duplicate_function_names(self) -> None:
        """The whole file, not just the usage block.

        `app.js` is one IIFE, so two `function x()` declarations do not collide loudly -- the later
        one silently wins everywhere, including inside functions defined earlier. Adding a
        `tokens(n)` number formatter next to the existing `tokens(q)` search tokenizer is exactly
        how the Documents and Tasks screens broke while every unit test stayed green: search
        received a formatted string and `.filter()` threw during render.
        """
        app = (PRODUCTION / "app.js").read_text(encoding="utf-8")
        names = re.findall(r"^\s*function ([A-Za-z_$][\w$]*)\s*\(", app, re.M)
        duplicates = sorted({n for n in names if names.count(n) > 1})
        self.assertEqual([], duplicates,
                         f"redefined in app.js, the later declaration silently wins: {duplicates}")

    def test_the_search_tokenizer_is_not_shadowed(self) -> None:
        """Name the specific casualty, so a rename cannot quietly reintroduce it."""
        app = (PRODUCTION / "app.js").read_text(encoding="utf-8")
        self.assertEqual(1, len(re.findall(r"^\s*function tokens\s*\(", app, re.M)),
                         "tokens() is the search tokenizer; it must have exactly one declaration")

    def test_the_attribution_table_is_rendered_and_states_coverage(self) -> None:
        app = (PRODUCTION / "app.js").read_text(encoding="utf-8")
        self.assertIn("usageAttribution(u) +", app, "the usage section must render attribution")
        self.assertIn("record the agent session that produced them", app,
                      "an empty table must be explained by a coverage line, not left ambiguous")

    def test_the_estimate_tag_carries_a_non_colour_marker(self) -> None:
        """Measured vs estimate must survive greyscale and colour-vision differences."""
        css = (PRODUCTION / "production-delta.css").read_text(encoding="utf-8")
        self.assertIn('.usage-tag[data-measured="0"]::before', css)

    def test_the_panel_never_hardcodes_a_currency_symbol(self) -> None:
        """Rates carry their own currency; a baked-in $ would mislabel every non-USD profile."""
        app = (PRODUCTION / "app.js").read_text(encoding="utf-8")
        usage_code = app[app.index("function usageCostCell"):app.index("function usageSection")]
        self.assertNotIn("$", usage_code)


if __name__ == "__main__":
    unittest.main()
