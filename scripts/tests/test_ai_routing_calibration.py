from __future__ import annotations

import copy
import json
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

import scripts.ai_plane.config as config_module
from scripts.ai_plane.route import explain_route
from scripts.ai_plane.routing_profile import ToolProfile


FIXTURE = Path(__file__).parent / "fixtures" / "routing_calibration.json"


def catalog() -> dict[str, object]:
    return {
        "provenance": "unknown", "source": "calibration", "observed_at": None,
        "fetched_at": None, "evaluation_time": "2030-01-01T00:00:00Z",
        "ttl_seconds": 0, "selector": "app_default", "exact_pin": None,
        "observations": [],
    }


def task(zone: str) -> dict[str, object]:
    return {
        "id": "calibration", "risk": "medium", "review_tool": "unit_c",
        "routing_policy_version": "2", "routing_zone": zone,
        "routing_axes": [
            "context_breadth=moderate", "logic_depth=moderate",
            "visual_interaction=moderate", "change_breadth=moderate",
            "uncertainty=moderate", "feedback_loop_intensity=moderate",
        ],
    }


class ProviderNeutralCalibrationTests(unittest.TestCase):
    def setUp(self) -> None:
        config_module.initialize_runtime_config()
        self.cases = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]

    def route_case(
        self, case: dict[str, object], names=("unit_a", "unit_b", "unit_c"),
        enabled=None, tool_order=None,
    ):
        taxonomy = copy.deepcopy(config_module.ROUTING_TAXONOMY)
        assert taxonomy is not None
        winner, near_miss, reviewer = names
        required = list(case["winner_capabilities"])
        missing = case["near_miss_missing"]
        near_caps = [item for item in required if item != missing]
        bands = list(taxonomy["complexity_bands"]["order"])
        reasoning = list(taxonomy["reasoning_levels"])
        ranks = {tool: index for index, tool in enumerate(names)}
        profiles = {
            tool: {"sufficient": {
                "reasoning_levels": reasoning, "complexity_bands": bands,
                "preference_rank": ranks[tool],
            }, "prestige": {
                "reasoning_levels": [reasoning[-1]], "complexity_bands": bands,
                "preference_rank": 99,
            }} for tool in names
        }
        capabilities = {
            winner: tuple(required), near_miss: tuple(near_caps),
            reviewer: ("independent_review",),
        }
        local = ToolProfile(
            tuple(enabled or names), {field: winner for field in (
                "research_tool", "planning_tool", "implementation_tool", "review_tool"
            )},
            {tool: ("sufficient", "prestige") for tool in names},
            {tool: catalog() for tool in names},
        )
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                config_module, "TOOLS", tuple(tool_order or names)
            ))
            stack.enter_context(mock.patch.object(
                config_module, "TOOL_FAMILIES",
                {winner: "family_a", near_miss: "family_b", reviewer: "family_c"},
            ))
            stack.enter_context(mock.patch.object(config_module, "TOOL_CAPABILITIES", capabilities))
            stack.enter_context(mock.patch.object(config_module, "TOOL_PROFILES", profiles))
            return explain_route("calibration", task(str(case["zone"])), local_profile=local)

    def test_all_seven_zones_have_capability_winner_and_explained_near_miss(self) -> None:
        self.assertEqual(7, len(self.cases))
        for case in self.cases:
            with self.subTest(zone=case["zone"]):
                result = self.route_case(case)
                self.assertEqual("selected", result["status"])
                self.assertEqual("unit_a", result["selected_candidate"]["tool"])
                rejected = next(item for item in result["rejected_candidates"] if item["tool"] == "unit_b")
                self.assertTrue(any(str(case["near_miss_missing"]) in reason for reason in rejected["reasons"]))
                self.assertEqual("sufficient", result["selected_candidate"]["logical_profile"])
                self.assertNotEqual("maximum", result["selected_candidate"]["reasoning_level"])

    def test_tool_renaming_preserves_capability_outcome(self) -> None:
        case = self.cases[3]
        first = self.route_case(case)
        renamed = self.route_case(case, ("shape_x", "shape_y", "shape_z"))
        self.assertEqual(
            first["selected_candidate"]["capabilities"],
            renamed["selected_candidate"]["capabilities"],
        )
        self.assertEqual(
            [reason.split(":", 1)[0] for reason in first["decision_reasons"]],
            [reason.split(":", 1)[0] for reason in renamed["decision_reasons"]],
        )

    def test_tool_reordering_preserves_capability_winner(self) -> None:
        for case in self.cases:
            with self.subTest(zone=case["zone"]):
                first = self.route_case(case)
                reordered = self.route_case(
                    case, tool_order=("unit_c", "unit_b", "unit_a")
                )
                self.assertEqual(
                    first["selected_candidate"]["tool"],
                    reordered["selected_candidate"]["tool"],
                )
                self.assertEqual(
                    first["selected_candidate"]["capabilities"],
                    reordered["selected_candidate"]["capabilities"],
                )

    def test_disabled_winner_cannot_win(self) -> None:
        case = self.cases[0]
        result = self.route_case(case, enabled=("unit_b", "unit_c"))
        rejected = next(item for item in result["rejected_candidates"] if item["tool"] == "unit_a")
        self.assertTrue(any("not explicitly enabled" in reason for reason in rejected["reasons"]))
        self.assertNotEqual(
            "unit_a", (result.get("selected_candidate") or {}).get("tool")
        )


if __name__ == "__main__":
    unittest.main()
