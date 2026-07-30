from __future__ import annotations

import copy
import json
import unittest
from contextlib import ExitStack
from unittest import mock

import scripts.ai_plane.config as config_module
from scripts.ai_plane.route import explain_route
from scripts.ai_plane.routing_profile import ToolProfile


def _task(zone: str = "bounded_implementation", **overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "id": "task_acceptance",
        "risk": "medium",
        "preferred_tool": "codex",
        "review_tool": "claude",
        "routing_policy_version": "2",
        "routing_zone": zone,
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


def _catalog(provenance: str = "unknown") -> dict[str, object]:
    observed = [] if provenance == "unknown" else [{
        "model": "fixture-model",
        "provenance": provenance,
        "capabilities": ["bounded_patch_loop"],
        "reasoning_levels": ["high"],
        "logical_profiles": ["bounded_execution"],
        "preference": 0,
    }]
    return {
        "provenance": provenance,
        "source": f"{provenance}-fixture",
        "observed_at": "2030-01-01T00:00:00Z",
        "fetched_at": "2030-01-01T00:00:00Z",
        "evaluation_time": "2030-01-01T00:00:30Z",
        "ttl_seconds": 60,
        "selector": "latest_compatible",
        "exact_pin": None,
        "observations": observed,
    }


def _local(*tools: str) -> ToolProfile:
    profiles = {
        "antigravity": ("workspace_general",),
        "codex": ("bounded_execution",),
        "claude": ("balanced_reasoning", "intensive_reasoning"),
    }
    default = tools[0]
    return ToolProfile(
        enabled_tools=tools,
        defaults={
            "research_tool": default,
            "planning_tool": default,
            "implementation_tool": default,
            "review_tool": tools[-1],
        },
        profiles={tool: profiles[tool] for tool in tools},
        catalogs={tool: _catalog() for tool in tools},
    )


class RouteAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        config_module.initialize_runtime_config()

    def test_explicit_executor_owner_reaches_filter_and_fails_without_fallback(self) -> None:
        result = explain_route(
            "task_acceptance",
            _task(
                routing_profile_tool="codex",
                routing_profile="bounded_execution",
                routing_reasoning_level="high",
                routing_provenance="explicit_owner",
                routing_rationale="Owner selected a valid but locally unavailable profile.",
            ),
            local_profile=_local("claude"),
        )
        self.assertEqual("conflict", result["status"])
        self.assertEqual("explicit-owner-ineligible", result["conflicts"][0]["code"])
        self.assertTrue(any(
            item["tool"] == "codex" and "not explicitly enabled" in " ".join(item["reasons"])
            for item in result["rejected_candidates"]
        ))

    def test_explicit_reviewer_is_a_hard_constraint_with_eligible_alternative(self) -> None:
        result = explain_route(
            "task_acceptance",
            _task(review_tool="antigravity"),
            local_profile=_local("codex", "claude"),
        )
        self.assertEqual("conflict", result["status"])
        self.assertEqual("explicit-reviewer-ineligible", result["conflicts"][0]["code"])
        self.assertEqual([], result["reviewer_candidates"])

    def test_absent_reviewer_preserves_all_eligible_cross_family_candidates(self) -> None:
        unpinned = _task()
        del unpinned["review_tool"]
        result = explain_route(
            "task_acceptance",
            unpinned,
            local_profile=_local("codex", "claude"),
        )
        self.assertEqual("selected", result["status"])
        self.assertEqual(
            ["balanced_reasoning", "intensive_reasoning"],
            [candidate["logical_profile"] for candidate in result["reviewer_candidates"]],
        )
        self.assertFalse(any("owner waiver" in reason for reason in result["decision_reasons"]))
    def test_mass_and_horizontal_routes_have_independent_reviewers(self) -> None:
        profile = _local("antigravity", "codex")
        for zone in ("mass_digest", "horizontal_refactor"):
            with self.subTest(zone=zone):
                result = explain_route(
                    "task_acceptance",
                    _task(zone, preferred_tool="antigravity", review_tool="codex"),
                    local_profile=profile,
                )
                self.assertEqual("selected", result["status"])
                self.assertEqual("antigravity", result["selected_candidate"]["tool"])
                self.assertEqual(["codex"], [
                    candidate["tool"] for candidate in result["reviewer_candidates"]
                ])

    def test_manual_and_bundled_catalog_provenance_are_distinct(self) -> None:
        base = _local("codex", "claude")
        results = {}
        for provenance in ("manual", "bundled"):
            profile = ToolProfile(
                base.enabled_tools,
                base.defaults,
                base.profiles,
                {**base.catalogs, "codex": _catalog(provenance)},
            )
            results[provenance] = explain_route(
                "task_acceptance", _task(), local_profile=profile
            )
        self.assertEqual("manual", results["manual"]["resolution"]["catalog"]["provenance"])
        self.assertEqual("symbolic", results["manual"]["resolution"]["kind"])
        self.assertIn("policy-untrusted", results["manual"]["resolution"]["reason"])
        self.assertEqual("bundled", results["bundled"]["resolution"]["catalog"]["provenance"])
        self.assertEqual("exact", results["bundled"]["resolution"]["kind"])

    def test_every_declared_zone_routes_with_renamed_tools_and_stable_ties(self) -> None:
        taxonomy = copy.deepcopy(config_module.ROUTING_TAXONOMY)
        assert taxonomy is not None
        capabilities = tuple(taxonomy["capability_tags"])
        bands = list(taxonomy["complexity_bands"]["order"])
        reasoning = list(taxonomy["reasoning_levels"])
        tools = ("zeta", "beta")
        profiles = {
            "zeta": {
                "unused": {
                    "reasoning_levels": reasoning,
                    "complexity_bands": bands,
                    "preference_rank": 99,
                },
                "primary": {
                    "reasoning_levels": reasoning,
                    "complexity_bands": bands,
                    "preference_rank": 0,
                }
            },
            "beta": {
                "reviewer": {
                    "reasoning_levels": reasoning,
                    "complexity_bands": bands,
                    "preference_rank": 1,
                }
            },
        }
        local = ToolProfile(
            tools,
            {field: "zeta" for field in (
                "research_tool", "planning_tool", "implementation_tool", "review_tool"
            )},
            {"zeta": ("primary",), "beta": ("reviewer",)},
            {"zeta": _catalog(), "beta": _catalog()},
        )
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(config_module, "TOOLS", tools))
            stack.enter_context(mock.patch.object(
                config_module, "TOOL_FAMILIES", {"zeta": "family_z", "beta": "family_b"}
            ))
            stack.enter_context(mock.patch.object(
                config_module,
                "TOOL_CAPABILITIES",
                {"zeta": capabilities, "beta": capabilities},
            ))
            stack.enter_context(mock.patch.object(config_module, "TOOL_PROFILES", profiles))
            first_bytes = None
            for zone in taxonomy["zones"]:
                with self.subTest(zone=zone):
                    result = explain_route(
                        "task_acceptance",
                        _task(zone, preferred_tool="zeta", review_tool="beta"),
                        local_profile=local,
                    )
                    self.assertEqual("selected", result["status"])
                    self.assertEqual("zeta", result["selected_candidate"]["tool"])
                    self.assertEqual(["beta"], [
                        candidate["tool"] for candidate in result["reviewer_candidates"]
                    ])
                    encoded = json.dumps(result, sort_keys=True)
                    self.assertEqual(
                        encoded,
                        json.dumps(
                            explain_route(
                                "task_acceptance",
                                _task(zone, preferred_tool="zeta", review_tool="beta"),
                                local_profile=local,
                            ),
                            sort_keys=True,
                        ),
                    )
                    first_bytes = first_bytes or encoded
            self.assertIsNotNone(first_bytes)

            profiles["beta"]["reviewer"]["preference_rank"] = 0
            default_ties = explain_route(
                "task_acceptance",
                _task(preferred_tool="zeta", review_tool="beta"),
                local_profile=local, respect_assignments=False,
            )
            self.assertEqual("zeta", default_ties["selected_candidate"]["tool"])
            with mock.patch.object(config_module, "TOOLS", ("beta", "zeta")):
                tied = explain_route(
                    "task_acceptance",
                    _task(preferred_tool="beta", review_tool="zeta"),
                    local_profile=local, respect_assignments=False,
                )
                repeated = explain_route(
                    "task_acceptance",
                    _task(preferred_tool="beta", review_tool="zeta"),
                    local_profile=local, respect_assignments=False,
                )
            self.assertEqual("beta", tied["selected_candidate"]["tool"])
            self.assertEqual(
                json.dumps(tied, sort_keys=True),
                json.dumps(repeated, sort_keys=True),
            )

            changed_ties = copy.deepcopy(taxonomy)
            changed_ties["profile_preferences"]["tie_breakers"] = [
                "profile_order", "tool_order", "preference_rank"
            ]
            with mock.patch.object(config_module, "ROUTING_TAXONOMY", changed_ties):
                reordered_ties = explain_route(
                    "task_acceptance",
                    _task(preferred_tool="zeta", review_tool="zeta"),
                    local_profile=local, respect_assignments=False,
                )
            self.assertEqual("beta", reordered_ties["selected_candidate"]["tool"])
            self.assertNotEqual(
                default_ties["selected_candidate"]["tool"],
                reordered_ties["selected_candidate"]["tool"],
            )


if __name__ == "__main__":
    unittest.main()
