from __future__ import annotations

import argparse
import io
import json
import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
import scripts.ai_cli as ai_cli
import scripts.ai_plane.config as config_module


ADAPTER_COMMANDS = "\n".join(
    f"        {tok}: \"ai {tok.lower()}\""
    for tok in ("INIT", "SYNC", "AUDIT_FRAMEWORK", "FEATURE", "RESEARCH", "PLAN", "TASKS", "TASK",
                "DISPATCH", "REVIEW", "QA", "MERGE", "LEARN", "ARCHIVE", "VERIFY", "CARGO_CACHE",
                "CARGO", "BLUEPRINT")
)


def config_yaml(enabled: list[str]) -> str:
    enabled_yaml = ("  enabled:\n" + "\n".join(f"    - {e}" for e in enabled)) if enabled else "  enabled: []"
    return f"""version: 1
defaults:
  research_tool: codex
  planning_tool: codex
  implementation_tool: codex
  review_tool: codex
  generated_dispatch_root: .ai/adapters
extensions:
  roots:
    - scripts/extensions
{enabled_yaml}
tools:
  codex:
    role: r
    default_isolation: patch
    notes:
      - n
    adapter:
      render_source: null
"""


class _ConsumerFixture(unittest.TestCase):
    """Shared fixture: an isolated repo with a pack extension, restorable config runtime globals,
    and helpers to write config / run sync / read the manifest."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ai = self.root / ".ai"
        for d in ("rules", "workflows", "agents", "project", "memory", "skills", "migration"):
            (self.ai / d).mkdir(parents=True)
        (self.ai / "agents" / "executor.md").write_text("# Fixture executor\n", encoding="utf-8")
        # a pack extension contributing one file
        pack = self.root / "scripts" / "extensions" / "mypack"
        (pack / "content").mkdir(parents=True)
        (pack / "content" / "rule.md").write_text("PACK RULE BODY\n", encoding="utf-8")
        (pack / "extension.json").write_text(json.dumps({
            "id": "mypack", "version": "1.0.0", "api_version": 1, "types": ["pack"],
            "root": "scripts/extensions/mypack", "scopes": ["docs-lint"],
            "write_roots": [".ai/adapters/packs/mypack"],
            "contributes": {"files": [{"from": "content/rule.md", "to": ".ai/adapters/packs/mypack/rule.md"}]},
        }), encoding="utf-8")
        self.dest = self.root / ".ai" / "adapters" / "packs" / "mypack" / "rule.md"

        self.original_runtime = (
            config_module.TOOLS, config_module.TOOL_NOTES, config_module.TOOL_DEFAULTS,
            config_module.TOOL_ROLES, config_module.COMMAND_TOOL_DEFAULTS,
            config_module.GENERATED_DISPATCH_ROOT, config_module.ADAPTERS,
            config_module.TASK_CONTRACT_VOCABULARY,
        )
        rp = mock.patch.object(ai_cli.constants, "ROOT", self.root)
        ap = mock.patch.object(ai_cli.constants, "AI", self.ai)
        rp.start(); ap.start()
        self.addCleanup(rp.stop); self.addCleanup(ap.stop)
        self.addCleanup(self.restore)

    def restore(self) -> None:
        (config_module.TOOLS, config_module.TOOL_NOTES, config_module.TOOL_DEFAULTS,
         config_module.TOOL_ROLES, config_module.COMMAND_TOOL_DEFAULTS,
         config_module.GENERATED_DISPATCH_ROOT, config_module.ADAPTERS,
         config_module.TASK_CONTRACT_VOCABULARY) = self.original_runtime

    def write_config(self, enabled: list[str]) -> None:
        (self.ai / "config.yaml").write_text(config_yaml(enabled), encoding="utf-8")
        ai_cli.initialize_runtime_config(self.ai / "config.yaml")

    def sync(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            ai_cli.cmd_sync(argparse.Namespace())

    def manifest_paths(self) -> set[str]:
        return set(ai_cli.read_generated_manifest())

    def write_pack(self, ext_id: str, to: str, body: str) -> None:
        pack = self.root / "scripts" / "extensions" / ext_id
        (pack / "content").mkdir(parents=True, exist_ok=True)
        (pack / "content" / "f.md").write_text(body, encoding="utf-8")
        (pack / "extension.json").write_text(json.dumps({
            "id": ext_id, "version": "1.0.0", "api_version": 1, "types": ["pack"],
            "root": f"scripts/extensions/{ext_id}",
            "write_roots": [str(Path(to).parent).replace("\\", "/")],
            "contributes": {"files": [{"from": "content/f.md", "to": to}]},
        }), encoding="utf-8")

    def sync_expecting_exit(self) -> int:
        code = 0
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                ai_cli.cmd_sync(argparse.Namespace())
            except SystemExit as exit_err:
                code = exit_err.code if isinstance(exit_err.code, int) else 1
        return code


class PackRoundTripTests(_ConsumerFixture):
    """AC5: enable/disable an extension and prove deterministic, manifest-safe generated-output
    composition and removal. A pack's declared contribution appears in the generated set when the
    pack is enabled and is pruned (manifest-safe) when it is disabled."""

    def test_enable_then_disable_round_trip_is_manifest_safe(self) -> None:
        # enable -> the contribution is generated and manifest-tracked
        self.write_config(["mypack"])
        self.sync()
        self.assertTrue(self.dest.is_file(), "pack contribution not generated on enable")
        self.assertEqual("PACK RULE BODY\n", self.dest.read_text(encoding="utf-8"))
        self.assertIn(".ai/adapters/packs/mypack/rule.md", self.manifest_paths())

        # disable -> the next sync prunes it (manifest-safe removal), no orphan left behind
        self.write_config([])
        self.sync()
        self.assertFalse(self.dest.exists(), "pack contribution not pruned on disable")
        self.assertNotIn(".ai/adapters/packs/mypack/rule.md", self.manifest_paths())

    def test_sync_is_idempotent_with_a_pack_enabled(self) -> None:
        self.write_config(["mypack"])
        self.sync()
        first = ai_cli.manifest_path().read_bytes()
        self.sync()
        self.assertEqual(first, ai_cli.manifest_path().read_bytes())

    def test_integration_contributes_files_the_same_declarative_way(self) -> None:
        intg = self.root / "scripts" / "extensions" / "myintg"
        (intg / "content").mkdir(parents=True)
        (intg / "content" / "adapter.md").write_text("INTEGRATION DATA\n", encoding="utf-8")
        (intg / "extension.json").write_text(json.dumps({
            "id": "myintg", "version": "1.0.0", "api_version": 1, "types": ["integration"],
            "root": "scripts/extensions/myintg",
            "write_roots": [".ai/adapters/integrations/myintg"],
            "contributes": {"files": [{"from": "content/adapter.md", "to": ".ai/adapters/integrations/myintg/adapter.md"}]},
        }), encoding="utf-8")
        self.write_config(["myintg"])
        self.sync()
        dest = self.root / ".ai" / "adapters" / "integrations" / "myintg" / "adapter.md"
        self.assertTrue(dest.is_file())
        self.assertEqual("INTEGRATION DATA\n", dest.read_text(encoding="utf-8"))


class InvalidRosterAndCollisionTests(_ConsumerFixture):
    """R3-Q181-1: extension sync is fail-closed BEFORE any write and deterministically idempotent."""

    def test_invalid_roster_fails_closed_without_pruning_prior_output(self) -> None:
        # A valid pack generates output.
        self.write_config(["mypack"])
        self.sync()
        self.assertTrue(self.dest.is_file())
        # Now the roster becomes invalid (an enabled id with no manifest). Sync must fail closed
        # WITHOUT touching files or the manifest — the prior valid output survives.
        self.write_config(["mypack", "ghost"])
        code = self.sync_expecting_exit()
        self.assertEqual(1, code)
        self.assertTrue(self.dest.is_file(), "invalid roster pruned the prior valid output")
        self.assertIn(".ai/adapters/packs/mypack/rule.md", self.manifest_paths())

    def test_missing_roster_fails_closed_byte_identical(self) -> None:
        # R4-Q181-1: a MISSING extensions block (vs explicit enabled: []) must fail closed like an
        # invalid one, leaving files and the manifest byte-identical — never silently zero.
        self.write_config(["mypack"])
        self.sync()
        self.assertTrue(self.dest.is_file())
        manifest_before = ai_cli.manifest_path().read_bytes()
        dest_before = self.dest.read_bytes()
        # remove the entire extensions block from the config
        import re as _re
        cfg = (self.ai / "config.yaml").read_text(encoding="utf-8")
        cfg = _re.sub(r"(?m)^extensions:\n(?: .*\n| *\n)*", "", cfg)
        (self.ai / "config.yaml").write_text(cfg, encoding="utf-8")
        ai_cli.initialize_runtime_config(self.ai / "config.yaml")
        code = self.sync_expecting_exit()
        self.assertEqual(1, code)
        self.assertEqual(dest_before, self.dest.read_bytes(), "missing roster pruned prior output")
        self.assertEqual(manifest_before, ai_cli.manifest_path().read_bytes(), "missing roster changed the manifest")

    def test_missing_enabled_key_fails_closed_byte_identical(self) -> None:
        # R5-Q181-1: an extensions block that OMITS the enabled key (a partial-config typo, e.g.
        # only roots declared) must fail closed like a missing block — NOT be silently read as
        # enabled: [] (which would mean "disable all" and prune the last valid composition). The
        # decisive real-sync probe: exit 1, with files and the manifest byte-identical.
        self.write_config(["mypack"])
        self.sync()
        self.assertTrue(self.dest.is_file())
        manifest_before = ai_cli.manifest_path().read_bytes()
        dest_before = self.dest.read_bytes()
        # drop only the enabled roster key, leaving the extensions block (its roots) present.
        import re as _re
        cfg = (self.ai / "config.yaml").read_text(encoding="utf-8")
        cfg = _re.sub(r"(?m)^  enabled:.*\n(?:    - .*\n)*", "", cfg)
        self.assertIn("extensions:", cfg)
        self.assertNotIn("enabled:", cfg)
        (self.ai / "config.yaml").write_text(cfg, encoding="utf-8")
        ai_cli.initialize_runtime_config(self.ai / "config.yaml")
        code = self.sync_expecting_exit()
        self.assertEqual(1, code)
        self.assertEqual(dest_before, self.dest.read_bytes(), "missing enabled key pruned prior output")
        self.assertEqual(manifest_before, ai_cli.manifest_path().read_bytes(),
                         "missing enabled key changed the manifest")

    def test_duplicate_contribution_destination_fails_closed(self) -> None:
        self.write_pack("packa", ".ai/adapters/shared/x.md", "A\n")
        self.write_pack("packb", ".ai/adapters/shared/x.md", "B\n")
        self.write_config(["packa", "packb"])
        code = self.sync_expecting_exit()
        self.assertEqual(1, code)
        self.assertFalse((self.root / ".ai" / "adapters" / "shared" / "x.md").exists())

    def test_multi_extension_composition_is_idempotent(self) -> None:
        self.write_pack("packa", ".ai/adapters/a/x.md", "A\n")
        self.write_pack("packb", ".ai/adapters/b/y.md", "B\n")
        self.write_config(["mypack", "packa", "packb"])
        self.sync()
        first = ai_cli.manifest_path().read_bytes()
        self.sync()
        self.assertEqual(first, ai_cli.manifest_path().read_bytes())


class CommandDispatcherTests(_ConsumerFixture):
    """AC1 (command): a registered command capability is invokable via `ai ext run`, executed as an
    argv array with the shell disabled; an unknown command fails closed."""

    def write_command_ext(self) -> None:
        cmd = self.root / "scripts" / "extensions" / "mycmd"
        cmd.mkdir(parents=True, exist_ok=True)
        (cmd / "cmd.py").write_text("X = 1\n", encoding="utf-8")
        (cmd / "extension.json").write_text(json.dumps({
            "id": "mycmd", "version": "1.0.0", "api_version": 1, "types": ["command"],
            "root": "scripts/extensions/mycmd", "entrypoints": {"command": "cmd.py"},
            "executables": ["fixture-index"],
            "commands": {"index": {"executable": "fixture-index", "args": ["--scan"]}},
        }), encoding="utf-8")

    def capture_main(self, argv: list[str]) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                ai_cli.main(argv)
            except SystemExit as exit_err:
                code = exit_err.code if isinstance(exit_err.code, int) else 1
        return code, out.getvalue() + err.getvalue()

    def test_ext_run_executes_declared_argv_shell_disabled(self) -> None:
        # task_190b (design.md "Caller arguments"): a command's declared args are now its COMPLETE
        # invocation convention, so this happy path invokes with NO caller argv and proves the
        # declared argv reaches the substrate with the shell disabled. Caller-argv rejection is
        # covered by test_ext_run_rejects_caller_arguments.
        self.write_command_ext()
        self.write_config(["mycmd"])
        import subprocess as _sp
        completed = _sp.CompletedProcess(args=[], returncode=0)
        with mock.patch("subprocess.run", return_value=completed) as run_mock:
            code, _ = self.capture_main(["ext", "run", "index"])
        self.assertEqual(0, code)
        run_mock.assert_called_once()
        called_argv = run_mock.call_args.args[0]
        self.assertEqual(["fixture-index", "--scan"], called_argv)
        self.assertFalse(run_mock.call_args.kwargs.get("shell", False))

    def test_ext_run_rejects_caller_arguments(self) -> None:
        # task_190b (design.md "Caller arguments"): caller argv beyond the declared convention is
        # rejected with a named reason (unexpected-command-arguments) and the process is NEVER
        # launched — a command cannot gain an undeclared argument through the `ai ext run` boundary.
        self.write_command_ext()
        self.write_config(["mycmd"])
        with mock.patch("subprocess.run") as run_mock:
            code, out = self.capture_main(["ext", "run", "index", "extra-arg"])
        self.assertEqual(1, code)
        self.assertIn("unexpected-command-arguments", out)
        run_mock.assert_not_called()

    def test_ext_run_unknown_command_fails_closed(self) -> None:
        self.write_config([])
        code, out = self.capture_main(["ext", "run", "ghost"])
        self.assertEqual(1, code)
        self.assertIn("unknown-command", out)

    def test_ext_list_reports_the_command_with_origin(self) -> None:
        self.write_command_ext()
        self.write_config(["mycmd"])
        code, out = self.capture_main(["ext", "list"])
        self.assertEqual(0, code)
        report = json.loads(out)
        self.assertIn({"command": "index", "origin": "mycmd"}, report["commands"])
        self.assertEqual(["mycmd"], report["capabilities"]["command"])


if __name__ == "__main__":
    unittest.main()
