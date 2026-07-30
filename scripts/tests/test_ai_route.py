from __future__ import annotations

import copy
import json
import unittest

import scripts.ai_plane.config as config_module
from scripts.ai_plane.route import catalog_freshness, explain_route, render_human
from scripts.ai_plane.routing_profile import ToolProfile, validate_profile_data


def task(**overrides):
    data = {
        "id": "task_fixture",
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


def unknown_catalog(selector="app_default"):
    return {
        "provenance": "unknown",
        "source": "fixture",
        "observed_at": None,
        "fetched_at": None,
        "evaluation_time": "2030-01-01T00:00:00Z",
        "ttl_seconds": 0,
        "selector": selector,
        "exact_pin": None,
        "observations": [],
    }


def local_profile():
    return ToolProfile(
        enabled_tools=("codex", "claude"),
        defaults={
            "research_tool": "codex",
            "planning_tool": "codex",
            "implementation_tool": "codex",
            "review_tool": "claude",
        },
        profiles={
            "codex": ("bounded_execution",),
            "claude": ("balanced_reasoning", "intensive_reasoning"),
        },
        catalogs={"codex": unknown_catalog(), "claude": unknown_catalog()},
    )


class RouteExplainTests(unittest.TestCase):
    def setUp(self) -> None:
        config_module.initialize_runtime_config()

    def test_stable_route_selects_logical_profile_and_symbolic_model(self) -> None:
        first = explain_route("task_fixture", task(), local_profile=local_profile())
        second = explain_route("task_fixture", task(), local_profile=local_profile())
        self.assertEqual(first, second)
        self.assertEqual("selected", first["status"])
        self.assertEqual("bounded_execution", first["selected_candidate"]["logical_profile"])
        self.assertEqual("substantial", first["complexity"]["band"])
        self.assertEqual("symbolic", first["resolution"]["kind"])
        self.assertEqual("app_default", first["resolution"]["selector"])
        self.assertIsNone(first["resolution"]["model"])
        self.assertTrue(first["reviewer_candidates"])
        self.assertIn("record the actual model", first["resolution"]["receipt_requirement"])
        self.assertEqual(
            json.dumps(first, sort_keys=True),
            json.dumps(second, sort_keys=True),
        )
        self.assertIn("Read-only evaluation", render_human(first))

    def test_advisory_detection_cannot_change_any_recommendation_byte(self) -> None:
        baseline = explain_route("task_fixture", task(), local_profile=local_profile(), detection=None)
        for status in ("present", "absent", "unknown", "error"):
            observed = explain_route(
                "task_fixture", task(), local_profile=local_profile(),
                detection={"codex": {"status": status, "reason": "ignored fixture"}},
            )
            self.assertEqual(baseline, observed)

    def test_explicit_owner_constraints_fail_closed_without_fallback(self) -> None:
        result = explain_route(
            "task_fixture",
            task(
                preferred_tool="antigravity",
                routing_profile_tool="antigravity",
                routing_profile="workspace_general",
                routing_reasoning_level="high",
                routing_provenance="explicit_owner",
                routing_rationale="Owner selected the workspace surface.",
            ),
            local_profile=local_profile(),
        )
        self.assertEqual("conflict", result["status"])
        self.assertEqual("routing-vector-invalid", result["conflicts"][0]["code"])

    def test_api_account_observation_never_proves_desktop_pin(self) -> None:
        profile = local_profile()
        catalog = {
            "provenance": "desktop_app",
            "source": "desktop-fixture",
            "observed_at": "2030-01-01T00:00:00Z",
            "fetched_at": "2030-01-01T00:00:00Z",
            "evaluation_time": "2030-01-01T00:00:30Z",
            "ttl_seconds": 60,
            "selector": "latest_compatible",
            "exact_pin": "same-string",
            "observations": [{
                "model": "same-string",
                "provenance": "api_account",
                "capabilities": ["bounded_patch_loop"],
                "reasoning_levels": ["high"],
                "logical_profiles": ["bounded_execution"],
                "preference": 0,
            }],
        }
        profile = ToolProfile(
            profile.enabled_tools, profile.defaults, profile.profiles,
            {**profile.catalogs, "codex": catalog},
        )
        result = explain_route("task_fixture", task(), local_profile=profile)
        self.assertEqual("conflict", result["status"])
        self.assertEqual("exact-pin-unproven", result["conflicts"][0]["code"])
        self.assertIsNone(result["resolution"]["model"])

    def test_fresh_compatible_catalog_can_late_bind_exact_identity(self) -> None:
        profile = local_profile()
        catalog = {
            "provenance": "desktop_app",
            "source": "desktop-fixture",
            "observed_at": "2030-01-01T00:00:00Z",
            "fetched_at": "2030-01-01T00:00:00Z",
            "evaluation_time": "2030-01-01T00:00:30Z",
            "ttl_seconds": 60,
            "selector": "latest_compatible",
            "exact_pin": None,
            "observations": [{
                "model": "catalog-only-monthly-id",
                "provenance": "desktop_app",
                "capabilities": ["bounded_patch_loop"],
                "reasoning_levels": ["high"],
                "logical_profiles": ["bounded_execution"],
                "preference": 0,
            }],
        }
        routed = ToolProfile(
            profile.enabled_tools, profile.defaults, profile.profiles,
            {**profile.catalogs, "codex": catalog},
        )
        result = explain_route("task_fixture", task(), local_profile=routed)
        self.assertEqual("selected", result["status"])
        self.assertEqual("exact", result["resolution"]["kind"])
        self.assertEqual("catalog-only-monthly-id", result["resolution"]["model"])

        changed = copy.deepcopy(catalog)
        changed["observations"][0]["model"] = "next-month-id"
        rerouted = ToolProfile(
            profile.enabled_tools, profile.defaults, profile.profiles,
            {**profile.catalogs, "codex": changed},
        )
        self.assertEqual(
            "next-month-id",
            explain_route("task_fixture", task(), local_profile=rerouted)["resolution"]["model"],
        )

    def test_stale_catalog_is_visible_and_cannot_resolve_exact(self) -> None:
        freshness = catalog_freshness({
            "fetched_at": "2030-01-01T00:00:00Z",
            "observed_at": None,
            "evaluation_time": "2030-01-01T00:02:00Z",
            "ttl_seconds": 60,
        })
        self.assertIs(True, freshness["stale"])
        self.assertEqual(120, freshness["age_seconds"])

    def test_version_one_profile_remains_readable_as_unknown_symbolic_state(self) -> None:
        old = {
            "version": 1,
            "enabled_tools": ["codex"],
            "defaults": {field: "codex" for field in (
                "research_tool", "planning_tool", "implementation_tool", "review_tool"
            )},
        }
        profile = validate_profile_data(
            old, config_module.TOOLS,
            declared_profiles=config_module.TOOL_PROFILES,
            taxonomy=config_module.ROUTING_TAXONOMY,
        )
        self.assertEqual(1, profile.source_version)
        self.assertEqual("unknown", profile.catalogs["codex"]["provenance"])
        result = explain_route("task_fixture", task(), local_profile=profile)
        self.assertEqual("symbolic", result["resolution"]["kind"])

    def test_profile_schema_rejects_secret_path_account_and_billing_shapes(self) -> None:
        profile = local_profile().as_json()
        for field in ("secret", "token", "credential", "path", "account_id", "billing"):
            candidate = copy.deepcopy(profile)
            candidate["routing"][field] = "forbidden"
            with self.assertRaises(Exception):
                validate_profile_data(
                    candidate, config_module.TOOLS,
                    declared_profiles=config_module.TOOL_PROFILES,
                    taxonomy=config_module.ROUTING_TAXONOMY,
                )


if __name__ == "__main__":
    unittest.main()
