from __future__ import annotations

import json

import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scripts.ai_plane.auto_dispatch as auto_dispatch_module
import scripts.ai_plane.routing_profile as tool_profile
import scripts.ai_plane.config as config_module

REPO_ROOT = Path(__file__).resolve().parents[2]
import scripts.ai_cli as ai_cli


ADAPTER_COMMANDS = """      commands:
        INIT: "ai init"
        SYNC: "ai sync"
        AUDIT_FRAMEWORK: "ai audit-framework"
        FEATURE: "ai feature"
        RESEARCH: "ai research"
        PLAN: "ai plan"
        TASKS: "ai tasks"
        TASK: "ai task"
        DISPATCH: "ai dispatch"
        REVIEW: "ai review"
        QA: "ai qa"
        MERGE: "ai merge"
        LEARN: "ai learn"
        ARCHIVE: "ai archive"
        VERIFY: "ai verify"
        CARGO_CACHE: "ai cargo-cache"
        CARGO: "ai cargo"
        BLUEPRINT: "ai blueprint"
"""


def tool_block(name: str, output_dir: str, marker: str, dispatch_lines: str = "") -> str:
    # task_190a: a tool selects rendering via adapter.render_source; a null/omitted source uses the
    # vendor-free core neutral descriptor (no integration needed). These dispatch fixtures exercise
    # the auto-dispatch lane, not adapter rendering, so the neutral descriptor suffices. output_dir
    # and marker remain in the signature for call-site compatibility but no longer shape config.
    return f'''  {name}:
    role: fixture_role
    default_isolation: patch
    notes:
      - Fixture note for {name}.
{dispatch_lines}    adapter:
      render_source: null
'''


EXEC_DISPATCH = (
    "    dispatch:\n"
    "      exec:\n"
    "        argv:\n"
    '          - "fixture-tool"\n'
    '          - "run"\n'
    '          - "--task"\n'
    '          - "{task_id}"\n'
    '          - "--file"\n'
    '          - "{prompt_path}"\n'
)

DEEPLINK_DISPATCH = (
    "    dispatch:\n"
    "      deeplink:\n"
    '        url_template: "fixture://open?task={task_id}&prompt={prompt_encoded}"\n'
)

_REAL_SUBPROCESS_RUN = subprocess.run


def selective_run(marker: str, *, raises: Exception | None = None, returncode: int = 0):
    """subprocess.run stand-in that only intercepts argv starting with `marker`.

    Everything else (e.g. tasks.git_value's own subprocess.run calls, since `subprocess` is one
    shared module object) passes through to the real implementation so patching the module-global
    `run` attribute for the launcher seam does not also fake out unrelated git calls.
    """

    def side_effect(argv, *args, **kwargs):
        if argv and argv[0] == marker:
            if raises is not None:
                raise raises
            return subprocess.CompletedProcess(args=argv, returncode=returncode)
        return _REAL_SUBPROCESS_RUN(argv, *args, **kwargs)

    return side_effect


def fixture_calls(run_mock, marker: str) -> list:
    return [call for call in run_mock.call_args_list if call.args and call.args[0] and call.args[0][0] == marker]


class DispatchTestBase(unittest.TestCase):
    """Shared fixture: isolated .ai root, restorable config-module runtime globals."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.live_before = (REPO_ROOT / "AGENTS.md").read_bytes(), (REPO_ROOT / "CLAUDE.md").read_bytes()

    @classmethod
    def tearDownClass(cls) -> None:
        live_after = (REPO_ROOT / "AGENTS.md").read_bytes(), (REPO_ROOT / "CLAUDE.md").read_bytes()
        if cls.live_before != live_after:
            raise AssertionError("dispatch tests changed the live repository adapters")

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.ai = self.root / ".ai"
        self.ai.mkdir()
        for d in ("rules", "workflows", "agents", "project", "memory", "skills", "migration"):
            (self.ai / d).mkdir()
        for state in ("queue", "active", "done", "archive"):
            (self.ai / "tasks" / state).mkdir(parents=True)
        self.original_runtime = (
            config_module.TOOLS,
            config_module.TOOL_NOTES,
            config_module.TOOL_DEFAULTS,
            config_module.TOOL_ROLES,
            config_module.COMMAND_TOOL_DEFAULTS,
            config_module.GENERATED_DISPATCH_ROOT,
            config_module.ADAPTERS,
            config_module.TASK_CONTRACT_VOCABULARY,
            config_module.AUTO_DISPATCH_ENABLED,
            config_module.DISPATCH_DESCRIPTORS,
        )
        root_patch = mock.patch.object(ai_cli.constants, "ROOT", self.root)
        ai_patch = mock.patch.object(ai_cli.constants, "AI", self.ai)
        root_patch.start()
        ai_patch.start()
        self.addCleanup(root_patch.stop)
        self.addCleanup(ai_patch.stop)
        self.addCleanup(self.restore_runtime)

    def restore_runtime(self) -> None:
        (
            config_module.TOOLS,
            config_module.TOOL_NOTES,
            config_module.TOOL_DEFAULTS,
            config_module.TOOL_ROLES,
            config_module.COMMAND_TOOL_DEFAULTS,
            config_module.GENERATED_DISPATCH_ROOT,
            config_module.ADAPTERS,
            config_module.TASK_CONTRACT_VOCABULARY,
            config_module.AUTO_DISPATCH_ENABLED,
            config_module.DISPATCH_DESCRIPTORS,
        ) = self.original_runtime

    def write_config(self, *, auto_dispatch: bool = False, tools_yaml: str | None = None) -> Path:
        path = self.ai / "config.yaml"
        if tools_yaml is None:
            tools_yaml = tool_block("codex", ".", "AGENTS.md")
        auto_value = "true" if auto_dispatch else "false"
        path.write_text(
            f"""version: 1
defaults:
  research_tool: codex
  planning_tool: codex
  implementation_tool: codex
  review_tool: codex
  generated_dispatch_root: .ai/adapters
  auto_dispatch: {auto_value}
tools:
{tools_yaml}""",
            encoding="utf-8",
        )
        return path

    def write_profile(self, *enabled: str) -> Path:
        selected = enabled or ("codex",)
        path = tool_profile.profile_path(self.ai)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "version": 1,
            "enabled_tools": list(selected),
            "defaults": {field: selected[0] for field in tool_profile.ROLE_FIELDS},
        }), encoding="utf-8")
        return path


    def write_task(self, task_id: str, *, preferred_tool: str = "codex", review_tool: str = "codex") -> Path:
        task_dir = self.ai / "tasks" / "queue" / task_id
        task_dir.mkdir(parents=True)
        (task_dir / "task.yaml").write_text(
            f"""id: "{task_id}"
title: "Fixture task"
feature: "fixture"
status: "queued"
risk: "medium"
preferred_tool: "{preferred_tool}"
review_tool: "{review_tool}"
isolation_strategy: "patch"
target_files:
  - "fixture/**"
forbidden_files:
  - "other/**"
input_contract: "fixture input"
output_contract: "fixture output"
acceptance_tests:
  - "fixture passes"
commands:
  - "fixture command"
known_risks: "none"
""",
            encoding="utf-8",
        )
        return task_dir


class ConfigDescriptorValidationTests(DispatchTestBase):
    def test_auto_dispatch_defaults_false_and_accepts_explicit_bool(self) -> None:
        self.write_config(auto_dispatch=False)
        ai_cli.initialize_runtime_config(self.ai / "config.yaml")
        self.assertFalse(ai_cli.AUTO_DISPATCH_ENABLED)
        self.write_config(auto_dispatch=True)
        ai_cli.initialize_runtime_config(self.ai / "config.yaml")
        self.assertTrue(ai_cli.AUTO_DISPATCH_ENABLED)

    def test_auto_dispatch_rejects_non_boolean(self) -> None:
        path = self.ai / "config.yaml"
        path.write_text(
            f"""version: 1
defaults:
  research_tool: codex
  planning_tool: codex
  implementation_tool: codex
  review_tool: codex
  generated_dispatch_root: .ai/adapters
  auto_dispatch: "maybe"
tools:
{tool_block("codex", ".", "AGENTS.md")}""",
            encoding="utf-8",
        )
        with self.assertRaises(config_module.ConfigError) as ctx:
            ai_cli.load_tool_registry(path)
        self.assertIn("defaults.auto_dispatch must be a boolean", str(ctx.exception))

    def test_tool_without_descriptor_has_no_auto_lane(self) -> None:
        self.write_config()
        ai_cli.initialize_runtime_config(self.ai / "config.yaml")
        self.assertNotIn("codex", ai_cli.DISPATCH_DESCRIPTORS)

    def test_exec_descriptor_parses_and_rejects_unknown_placeholder(self) -> None:
        self.write_config(tools_yaml=tool_block("codex", ".", "AGENTS.md", EXEC_DISPATCH))
        ai_cli.initialize_runtime_config(self.ai / "config.yaml")
        self.assertEqual(
            ["fixture-tool", "run", "--task", "{task_id}", "--file", "{prompt_path}"],
            ai_cli.DISPATCH_DESCRIPTORS["codex"]["exec"]["argv"],
        )
        bad = EXEC_DISPATCH.replace("{prompt_path}", "{not_a_real_placeholder}")
        self.write_config(tools_yaml=tool_block("codex", ".", "AGENTS.md", bad))
        with self.assertRaises(config_module.ConfigError) as ctx:
            ai_cli.load_tool_registry(self.ai / "config.yaml")
        self.assertIn("unknown placeholder", str(ctx.exception))

    def test_deeplink_descriptor_parses_and_rejects_extra_keys(self) -> None:
        self.write_config(tools_yaml=tool_block("codex", ".", "AGENTS.md", DEEPLINK_DISPATCH))
        ai_cli.initialize_runtime_config(self.ai / "config.yaml")
        self.assertIn("url_template", ai_cli.DISPATCH_DESCRIPTORS["codex"]["deeplink"])
        malformed = "    dispatch:\n      unknown_form:\n        x: 1\n"
        self.write_config(tools_yaml=tool_block("codex", ".", "AGENTS.md", malformed))
        with self.assertRaises(config_module.ConfigError) as ctx:
            ai_cli.load_tool_registry(self.ai / "config.yaml")
        self.assertIn("unknown form(s)", str(ctx.exception))

    def test_kimi_code_descriptor_added_as_pure_config_data(self) -> None:
        """A brand-new tool name with a dispatch descriptor needs zero Python changes."""
        tools_yaml = tool_block("codex", ".", "AGENTS.md") + tool_block(
            "kimi-code", "./kimi_out", "KIMI.md", EXEC_DISPATCH
        )
        self.write_config(auto_dispatch=True, tools_yaml=tools_yaml)
        ai_cli.initialize_runtime_config(self.ai / "config.yaml")
        self.assertIn("kimi-code", ai_cli.TOOLS)
        self.assertIn("kimi-code", ai_cli.DISPATCH_DESCRIPTORS)


class PlaceholderRenderingTests(unittest.TestCase):
    def test_deeplink_percent_encodes_spaces_quotes_and_non_ascii(self) -> None:
        context = auto_dispatch_module.placeholder_context(
            "task_09", "codex", Path("queue/task_09/prompt.codex.md"), 'a "quoted" prompt with spaces and café'
        )
        url = auto_dispatch_module.render_template("fixture://open?prompt={prompt_encoded}", context)
        self.assertNotIn(" ", url)
        self.assertNotIn('"', url)
        self.assertIn("caf%C3%A9", url)
        self.assertIn("%22quoted%22", url)

    def test_render_template_only_substitutes_known_tokens(self) -> None:
        context = auto_dispatch_module.placeholder_context("task_09", "codex", Path("x/y.md"), "hi")
        rendered = auto_dispatch_module.render_template("--task {task_id} --tool {tool}", context)
        self.assertEqual("--task task_09 --tool codex", rendered)


class AutoDispatchLaneTests(DispatchTestBase):
    def capture(self, argv: list[str]) -> tuple[str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            ai_cli.main(argv)
        return out.getvalue(), err.getvalue()

    def test_manual_lane_output_is_unchanged_when_auto_is_not_requested(self) -> None:
        self.write_config(auto_dispatch=False)
        ai_cli.initialize_runtime_config(self.ai / "config.yaml")
        self.write_task("task_a")
        out_no_flag, _ = self.capture(["dispatch", "task_a", "--tool", "codex"])
        self.assertEqual(
            "Prompt written: .ai/tasks/queue/task_a/prompt.codex.md\n"
            "Adapter copy: .ai/adapters/codex/dispatch/task_a.prompt.md\n",
            out_no_flag,
        )
        self.assertNotIn("Auto-dispatch", out_no_flag)

    def test_auto_flag_with_gate_disabled_launches_nothing_and_reports_manual(self) -> None:
        self.write_config(auto_dispatch=False, tools_yaml=tool_block("codex", ".", "AGENTS.md", EXEC_DISPATCH))
        ai_cli.initialize_runtime_config(self.ai / "config.yaml")
        self.write_profile("codex")
        self.write_task("task_a")
        with mock.patch.object(
            auto_dispatch_module.subprocess, "run", side_effect=selective_run("fixture-tool")
        ) as run_mock:
            out, _ = self.capture(["dispatch", "task_a", "--tool", "codex", "--auto"])
        self.assertEqual([], fixture_calls(run_mock, "fixture-tool"))
        self.assertIn("Auto-dispatch unavailable", out)
        self.assertIn("auto_dispatch is disabled by project config", out)
        prompt_path = self.ai / "tasks" / "queue" / "task_a" / "prompt.codex.md"
        self.assertTrue(prompt_path.exists())
        record = ai_cli.parse_simple_yaml(self.ai / "tasks" / "queue" / "task_a" / "dispatch-record.yaml")
        self.assertEqual("manual", record["lane_used"])
        self.assertEqual("false", record["success"])

    def test_explicit_manual_tool_does_not_authorize_auto_launch(self) -> None:
        self.write_config(auto_dispatch=True, tools_yaml=tool_block("codex", ".", "AGENTS.md", EXEC_DISPATCH))
        ai_cli.initialize_runtime_config(self.ai / "config.yaml")
        self.write_task("task_a")
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(
            auto_dispatch_module.subprocess, "run", side_effect=selective_run("fixture-tool")
        ) as run_mock:
            with mock.patch.object(
                tool_profile,
                "load_profile",
                side_effect=tool_profile.ToolProfileError("tool-profile-required", "fixture profile missing"),
            ):
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    with self.assertRaises(SystemExit):
                        ai_cli.main(["dispatch", "task_a", "--tool", "codex", "--auto"])
        self.assertEqual([], fixture_calls(run_mock, "fixture-tool"))
        self.assertIn("tool-profile-required", err.getvalue())
        self.assertIn("tools configure", err.getvalue())
        self.assertTrue((self.ai / "tasks" / "queue" / "task_a" / "prompt.codex.md").exists())
        self.assertFalse(
            (self.ai / "tasks" / "queue" / "task_a" / "dispatch-record.yaml").exists()
        )


    def test_auto_flag_with_enabled_exec_descriptor_invokes_recorded_argv(self) -> None:
        self.write_config(auto_dispatch=True, tools_yaml=tool_block("codex", ".", "AGENTS.md", EXEC_DISPATCH))
        ai_cli.initialize_runtime_config(self.ai / "config.yaml")
        self.write_profile("codex")
        self.write_task("task_a")
        with mock.patch.object(
            auto_dispatch_module.subprocess, "run", side_effect=selective_run("fixture-tool")
        ) as run_mock:
            out, _ = self.capture(["dispatch", "task_a", "--tool", "codex", "--auto"])
        calls = fixture_calls(run_mock, "fixture-tool")
        self.assertEqual(1, len(calls))
        called_argv = calls[0].args[0]
        self.assertEqual(["fixture-tool", "run", "--task", "task_a", "--file"], called_argv[:5])
        self.assertTrue(called_argv[-1].endswith("prompt.codex.md"))
        self.assertIn("Auto-dispatch: launched codex via the auto-exec lane.", out)
        record = ai_cli.parse_simple_yaml(self.ai / "tasks" / "queue" / "task_a" / "dispatch-record.yaml")
        self.assertEqual("auto-exec", record["lane_used"])
        self.assertEqual("true", record["success"])

    def test_absent_binary_falls_back_to_manual_handoff_and_exits_cleanly(self) -> None:
        self.write_config(auto_dispatch=True, tools_yaml=tool_block("codex", ".", "AGENTS.md", EXEC_DISPATCH))
        ai_cli.initialize_runtime_config(self.ai / "config.yaml")
        self.write_profile("codex")
        self.write_task("task_a")
        with mock.patch.object(
            auto_dispatch_module.subprocess,
            "run",
            side_effect=selective_run("fixture-tool", raises=FileNotFoundError("no such file")),
        ):
            out, _ = self.capture(["dispatch", "task_a", "--tool", "codex", "--auto"])
        self.assertIn("Auto-dispatch unavailable", out)
        self.assertIn("launch failed", out)
        prompt_path = self.ai / "tasks" / "queue" / "task_a" / "prompt.codex.md"
        self.assertTrue(prompt_path.exists())
        record = ai_cli.parse_simple_yaml(self.ai / "tasks" / "queue" / "task_a" / "dispatch-record.yaml")
        self.assertEqual("auto-exec", record["lane_used"])
        self.assertEqual("false", record["success"])

    def test_deeplink_descriptor_launches_via_webbrowser_seam(self) -> None:
        self.write_config(auto_dispatch=True, tools_yaml=tool_block("codex", ".", "AGENTS.md", DEEPLINK_DISPATCH))
        ai_cli.initialize_runtime_config(self.ai / "config.yaml")
        self.write_profile("codex")
        self.write_task("task_a")
        with mock.patch.object(auto_dispatch_module.webbrowser, "open", return_value=True) as open_mock:
            out, _ = self.capture(["dispatch", "task_a", "--tool", "codex", "--auto"])
        open_mock.assert_called_once()
        url = open_mock.call_args.args[0]
        self.assertTrue(url.startswith("fixture://open?task=task_a&prompt="))
        self.assertIn("Auto-dispatch: launched codex via the auto-deeplink lane.", out)

    def test_auto_flag_never_used_by_default_manual_path(self) -> None:
        self.write_config(auto_dispatch=True, tools_yaml=tool_block("codex", ".", "AGENTS.md", EXEC_DISPATCH))
        ai_cli.initialize_runtime_config(self.ai / "config.yaml")
        self.write_task("task_a")
        with mock.patch.object(
            auto_dispatch_module.subprocess, "run", side_effect=selective_run("fixture-tool")
        ) as run_mock:
            out, _ = self.capture(["dispatch", "task_a", "--tool", "codex"])
        self.assertEqual([], fixture_calls(run_mock, "fixture-tool"))
        self.assertEqual(
            "Prompt written: .ai/tasks/queue/task_a/prompt.codex.md\n"
            "Adapter copy: .ai/adapters/codex/dispatch/task_a.prompt.md\n",
            out,
        )
        self.assertFalse((self.ai / "tasks" / "queue" / "task_a" / "dispatch-record.yaml").exists())

    def test_missing_configured_reviewer_reports_alternatives_without_substitution(self) -> None:
        tools_yaml = (
            tool_block("codex", ".", "AGENTS.md")
            + tool_block("claude", ".claude", "CLAUDE.md")
        )
        self.write_config(tools_yaml=tools_yaml)
        ai_cli.initialize_runtime_config(self.ai / "config.yaml")
        self.write_profile("codex")
        task_dir = self.write_task("task_a", review_tool="claude")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit):
                ai_cli.main(["review", "task_a"])
        rendered = err.getvalue()
        self.assertIn("tool-not-enabled", rendered)
        self.assertIn("Independent review is unavailable", rendered)
        self.assertIn("Enabled alternatives: codex", rendered)
        self.assertIn("No reviewer was substituted", rendered)
        self.assertIn("same-family substitute does not satisfy", rendered)
        self.assertIn("owner explicitly waives", rendered)
        self.assertEqual("", out.getvalue())
        self.assertFalse((task_dir / "prompt.codex.review.md").exists())
        self.assertFalse((task_dir / "prompt.claude.review.md").exists())

    def test_enabled_manual_only_tool_remains_available_for_manual_handoff(self) -> None:
        tools_yaml = (
            tool_block("codex", ".", "AGENTS.md")
            + tool_block("claude", ".claude", "CLAUDE.md")
        )
        self.write_config(tools_yaml=tools_yaml)
        ai_cli.initialize_runtime_config(self.ai / "config.yaml")
        self.write_profile("claude")
        task_dir = self.write_task("task_a")
        out, _ = self.capture(["dispatch", "task_a", "--tool", "claude"])
        self.assertIn("Prompt written", out)
        self.assertTrue((task_dir / "prompt.claude.md").is_file())
        self.assertFalse((task_dir / "dispatch-record.yaml").exists())


if __name__ == "__main__":
    unittest.main()
