from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scripts.ai_plane.config as config_module
import scripts.ai_plane.prompts as prompts
import scripts.ai_plane.tasks as tasks
from scripts.ai_plane.tasks import creation_commands, creation_routing


class RoutingIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        config_module.initialize_runtime_config()

    def test_creation_is_pending_without_explicit_shape_and_rejects_partial_shape(self) -> None:
        pending = argparse.Namespace(tool=None, review_tool=None, routing_zone=None, routing_axis=None)
        self.assertEqual(("pending", "pending", {}), creation_routing(pending))
        partial = argparse.Namespace(
            tool=config_module.TOOLS[0], review_tool=None,
            routing_zone="bounded_implementation", routing_axis=None,
        )
        with self.assertRaises(SystemExit):
            creation_routing(partial)

    def test_pending_task_contract_is_valid_and_has_exact_guidance(self) -> None:
        with tempfile.TemporaryDirectory(dir=".") as temp_dir, mock.patch.object(
            tasks.constants, "AI", Path(temp_dir) / ".ai"
        ):
            task_dir = tasks.create_task(
                title="Pending shape", feature="routing", risk="medium",
                preferred_tool="pending", review_tool="pending", isolation_strategy="patch",
                brief="brief", context="context",
            )
            data = tasks.parse_simple_yaml(task_dir / "task.yaml")
            self.assertEqual([], tasks.task_contract_vocabulary_violations(task_dir, data))
            self.assertIn(f"ai route explain {data['id']}", data["assignment_guidance"])
            self.assertIn(f"ai route apply {data['id']}", data["assignment_guidance"])
            self.assertFalse(any(key.startswith("routing_") for key in data))

    def test_complete_creation_shape_is_normalized_without_text_inference(self) -> None:
        taxonomy = config_module.ROUTING_TAXONOMY
        assert taxonomy is not None
        axes = [f"{axis}=low" for axis in taxonomy["axes"]]
        args = argparse.Namespace(
            tool=config_module.TOOLS[0], review_tool=config_module.TOOLS[-1],
            routing_zone="bounded_implementation", routing_axis=axes,
            routing_rationale="Explicit bounded task shape.",
        )
        tool, reviewer, metadata = creation_routing(args)
        self.assertEqual(config_module.TOOLS[0], tool)
        self.assertEqual(config_module.TOOLS[-1], reviewer)
        self.assertEqual(axes, metadata["routing_axes"])
        self.assertEqual("Explicit bounded task shape.", metadata["assignment_rationale"])
        self.assertNotIn("title", metadata)

    def test_nonpending_creation_commands_preserve_argument_conventions(self) -> None:
        tool = config_module.TOOLS[0]
        commands = creation_commands(
            tool, ("PLAN", "feature-slug"), ("TASKS", None),
            ("DISPATCH", "<task_id>"),
        )
        self.assertEqual("python scripts/ai_cli.py route apply <task_id>", commands[0])
        self.assertEqual(
            config_module.configured_command(tool, "PLAN") + " feature-slug", commands[1]
        )
        self.assertEqual(config_module.configured_command(tool, "TASKS"), commands[2])
        self.assertEqual(
            config_module.configured_command(tool, "DISPATCH") + " <task_id>", commands[3]
        )

    def test_owner_rationale_with_hash_round_trips_task_creation(self) -> None:
        with tempfile.TemporaryDirectory(dir=".") as temp_dir, mock.patch.object(
            tasks.constants, "AI", Path(temp_dir) / ".ai"
        ):
            rationale = "Bounded fix for issue #42 raised by owner"
            task_dir = tasks.create_task(
                title="Explicit shape", feature="routing", risk="medium",
                preferred_tool=config_module.TOOLS[0],
                review_tool=config_module.TOOLS[-1], isolation_strategy="patch",
                brief="brief", context="context",
                routing_metadata={"assignment_rationale": rationale},
            )
            parsed = tasks.parse_simple_yaml(task_dir / "task.yaml")
            self.assertEqual(rationale, parsed["assignment_rationale"])

    def test_prompt_contains_exact_assignment_and_honest_transport_boundary(self) -> None:
        with tempfile.TemporaryDirectory(dir=".") as temp_dir:
            task_dir = Path(temp_dir)
            (task_dir / "brief.md").write_text("brief\n", encoding="utf-8")
            (task_dir / "context.md").write_text("context\n", encoding="utf-8")
            tool = config_module.TOOLS[0]
            data = {
                "id": "task_prompt", "title": "Prompt", "feature": "routing",
                "status": "queue", "risk": "medium", "preferred_tool": tool,
                "review_tool": config_module.TOOLS[-1], "isolation_strategy": "patch",
                "routing_policy_version": "2", "routing_zone": "bounded_implementation",
                "routing_complexity_band": "substantial", "routing_profile_tool": tool,
                "routing_profile": next(iter(config_module.TOOL_PROFILES[tool])),
                "routing_reasoning_level": "high", "routing_provenance": "router",
                "routing_rationale": "capability match; least sufficient profile",
                "assignment_availability_provenance": "manual",
                "assignment_resolution_selector": "app_default",
                "routing_axes": ["context_breadth=moderate", "logic_depth=moderate"],
                "commands": [], "target_files": [], "forbidden_files": [],
                "depends_on": [], "acceptance_tests": [], "known_risks": [],
            }
            with mock.patch.object(prompts, "git_value", return_value="fixture"):
                rendered = prompts.prompt_common(task_dir, data, tool, "dispatch")
        for expected in (
            "Profile:", "Reasoning level: `high`", "context_breadth=moderate",
            "capability match; least sufficient profile", "Availability provenance: `manual`",
            "does not launch a provider", "submit a prompt", "human must press Send",
        ):
            self.assertIn(expected, rendered)


if __name__ == "__main__":
    unittest.main()
