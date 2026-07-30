from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scripts.ai_plane.config as config_module
import scripts.ai_plane.route_apply as route_apply
from scripts.ai_plane.primitives import parse_simple_yaml
from scripts.ai_plane.route import explain_route
from scripts.ai_plane.routing_profile import ToolProfile


def catalog() -> dict[str, object]:
    return {
        "provenance": "unknown",
        "source": "fixture",
        "observed_at": None,
        "fetched_at": None,
        "evaluation_time": "2030-01-01T00:00:00Z",
        "ttl_seconds": 0,
        "selector": "app_default",
        "exact_pin": None,
        "observations": [],
    }


def profile() -> ToolProfile:
    return ToolProfile(
        ("codex", "claude"),
        {
            "research_tool": "codex",
            "planning_tool": "codex",
            "implementation_tool": "codex",
            "review_tool": "claude",
        },
        {
            "codex": ("bounded_execution",),
            "claude": ("balanced_reasoning", "intensive_reasoning"),
        },
        {"codex": catalog(), "claude": catalog()},
    )


def task(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "id": "task_apply",
        "risk": "medium",
        "preferred_tool": "codex",
        "review_tool": "claude",
        "routing_policy_version": "2",
        "routing_zone": "bounded_implementation",
        "routing_axes": [
            "context_breadth=moderate",
            "logic_depth=moderate",
            "visual_interaction=low",
            "change_breadth=moderate",
            "uncertainty=low",
            "feedback_loop_intensity=moderate",
        ],
    }
    data.update(overrides)
    return data


class RouteApplyTests(unittest.TestCase):
    def setUp(self) -> None:
        config_module.initialize_runtime_config()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "task.yaml"

    def write(self, data: dict[str, object]) -> bytes:
        source = (
            'id: "task_apply"\n'
            'title: "Preserve this exact source"\n'
            'risk: "medium"\n'
            f'preferred_tool: "{data["preferred_tool"]}"\n'
            f'review_tool: "{data["review_tool"]}"\n'
            'routing_policy_version: "2"\n'
            'routing_zone: "bounded_implementation"\n'
            "routing_axes:\n"
            + "".join(f'  - "{axis}"\n' for axis in data["routing_axes"])
        )
        self.path.write_text(source, encoding="utf-8")
        return self.path.read_bytes()

    def apply(self, data: dict[str, object], **kwargs: object) -> dict[str, object]:
        with mock.patch.object(route_apply, "load_profile", return_value=profile()):
            return route_apply.apply_route("task_apply", data, self.path, **kwargs)

    def test_fill_only_is_atomic_byte_preserving_and_second_run_idempotent(self) -> None:
        data = task()
        before = self.write(data)
        first = self.apply(data)
        after = self.path.read_bytes()
        self.assertTrue(first["changed"])
        self.assertTrue(after.startswith(before))
        self.assertIn(b"routing_profile: bounded_execution", after)
        parsed = {**data, **first["updates"]}
        second = self.apply(parsed)
        self.assertFalse(second["changed"])
        self.assertEqual(after, self.path.read_bytes())
        self.assertEqual([], list(self.path.parent.glob(".task.*.tmp")))

    def test_explain_and_apply_agree_on_the_same_unmodified_contract(self) -> None:
        data = task(preferred_tool="claude", review_tool="codex")
        self.write(data)
        explained = explain_route("task_apply", data, local_profile=profile())
        applied = self.apply(data)
        self.assertEqual("selected", explained["status"])
        self.assertEqual(
            explained["selected_candidate"], applied["route"]["selected_candidate"]
        )

    def test_pending_assignment_is_treated_as_unset(self) -> None:
        data = task(preferred_tool="pending", review_tool="pending")
        self.write(data)
        result = self.apply(data)
        rendered = self.path.read_text(encoding="utf-8")
        self.assertTrue(result["changed"])
        self.assertIn("preferred_tool: codex", rendered)
        self.assertIn("review_tool: claude", rendered)

    def test_explicit_assignment_is_preserved_without_replace(self) -> None:
        data = task(preferred_tool="antigravity")
        before = self.write(data)
        with self.assertRaisesRegex(ValueError, "explicit-owner-ineligible"):
            self.apply(data)
        self.assertEqual(before, self.path.read_bytes())

    def test_replace_round_trips_reconciliation_and_records_before_state(self) -> None:
        data = task(preferred_tool="antigravity", review_tool="codex")
        self.write(data)
        with self.assertRaisesRegex(ValueError, "--replace requires"):
            self.apply(data, replace=True)
        with self.assertRaisesRegex(ValueError, "--reconciliation requires"):
            self.apply(data, reconciliation="ignored")
        rationale = "Owner override per meeting #7; reassign after review"
        result = self.apply(data, replace=True, reconciliation=rationale)
        parsed = parse_simple_yaml(self.path)
        self.assertTrue(result["changed"])
        self.assertEqual("codex", parsed["preferred_tool"])
        self.assertEqual(rationale, parsed["assignment_reconciliation"])
        self.assertEqual("antigravity", parsed["assignment_replaced_preferred_tool"])
        self.assertEqual("codex", parsed["assignment_replaced_review_tool"])

    def test_apply_has_no_provider_surface_and_writes_only_task_yaml(self) -> None:
        for forbidden in (
            "subprocess", "webbrowser", "socket", "urllib", "auto_dispatch", "startfile"
        ):
            self.assertFalse(hasattr(route_apply, forbidden), forbidden)
        data = task()
        self.write(data)
        real_replace = route_apply.os.replace
        with mock.patch.object(route_apply.os, "replace", wraps=real_replace) as replaced:
            self.apply(data)
        self.assertEqual({self.path}, set(self.path.parent.iterdir()))
        replaced.assert_called_once()
        source, destination = replaced.call_args.args
        self.assertEqual(self.path, Path(destination))
        self.assertEqual(self.path.parent, Path(source).parent)


if __name__ == "__main__":
    unittest.main()
