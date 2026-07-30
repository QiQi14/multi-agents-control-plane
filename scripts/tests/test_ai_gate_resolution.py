from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
import scripts.ai_cli as ai_cli
import scripts.ai_plane.doctor as doctor_module
import scripts.extension_registry as reg
import scripts.verify_contract as verify_contract


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, stdout=subprocess.PIPE, text=True
    ).stdout


def init_repo(root: Path) -> str:
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base", "--allow-empty")
    return git(root, "rev-parse", "HEAD").strip()


ZERO_CONFIG = """version: 1
defaults:
  research_tool: codex
  planning_tool: codex
  implementation_tool: codex
  review_tool: codex
  generated_dispatch_root: .ai/adapters
extensions:
  roots:
    - scripts/extensions
  enabled: []
tools:
  codex:
    role: r
    default_isolation: patch
    notes:
      - n
    adapter:
      render_source: null
"""

TASK_YAML = """id: "task_x"
title: "t"
feature: "f"
status: "queued"
risk: "low"
preferred_tool: "codex"
review_tool: "codex"
isolation_strategy: "patch"
verification_scope: "control-plane"
target_files:
  - ".ai/**"
forbidden_files:
  - "secret/**"
input_contract: "in"
output_contract: "out"
acceptance_tests:
  - "a"
commands:
  - "c"
known_risks: "none"
"""


class DegradationFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ai = self.root / ".ai"
        for state in ("queue", "active", "done", "archive"):
            (self.ai / "tasks" / state).mkdir(parents=True)
        (self.ai / "config.yaml").write_text(ZERO_CONFIG, encoding="utf-8")
        (self.ai / "tasks" / "queue" / "task_x").mkdir()
        (self.ai / "tasks" / "queue" / "task_x" / "task.yaml").write_text(TASK_YAML, encoding="utf-8")
        self.base = init_repo(self.root)

    def make_change(self, rel: str, content: str) -> None:
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    CORE_SCOPES = list(reg.CORE_SCOPES)
    # task_x's target_files is ".ai/**"; widen it so classification (not the target check) is what
    # rejects a product path, exactly reproducing the reviewer's probe.
    CONTROL_PLANE_PREFIXES = [".ai", "scripts", "AGENTS.md", "CLAUDE.md", "GEMINI.md", ".claude", ".agents"]

    def widen_targets(self) -> None:
        task = self.ai / "tasks" / "queue" / "task_x" / "task.yaml"
        text = task.read_text(encoding="utf-8")
        text = text.replace('  - ".ai/**"', '  - ".ai/**"\n  - "project/**"\n  - "scripts/**"')
        task.write_text(text, encoding="utf-8")

    def set_scope(self, scope: str | None) -> None:
        task = self.ai / "tasks" / "queue" / "task_x" / "task.yaml"
        text = task.read_text(encoding="utf-8")
        import re as _re
        if scope is None:
            text = _re.sub(r'(?m)^verification_scope:.*\n', "", text)
        else:
            text = _re.sub(r'(?m)^verification_scope:.*$', f'verification_scope: "{scope}"', text)
        task.write_text(text, encoding="utf-8")

    def run_control_plane(self, plan: bool = False, scopes: list[str] | None = None):
        ns = mock.Mock(task_id="task_x", base=self.base, plan=plan)
        out = io.StringIO()
        err = io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                verify_contract.control_plane_verify(
                    ns, root=self.root, ai=self.ai,
                    effective_scopes=scopes if scopes is not None else self.CORE_SCOPES,
                    control_plane_prefixes=self.CONTROL_PLANE_PREFIXES,
                )
            except SystemExit as exit_err:
                code = exit_err.code if isinstance(exit_err.code, int) else 1
        return code, out.getvalue() + err.getvalue()


class ControlPlaneDegradationTests(DegradationFixture):
    def test_docs_only_control_plane_scope_passes_with_clear_no_gate_message(self) -> None:
        self.make_change(".ai/notes.md", "docs\n")  # task_x declares control-plane
        code, out = self.run_control_plane()
        self.assertEqual(0, code)
        self.assertIn("No evidence gate is registered", out)
        self.assertIn("Control-plane verification passed.", out)

    def test_non_control_plane_scope_fails_closed_missing_gate(self) -> None:
        # The reviewer's Q181-2 fixture: a stack-scope task with no gate must NOT exit 0.
        self.widen_targets()
        self.set_scope("affected")
        self.make_change("project/src/lib.rs", "fn x() {}\n")
        code, out = self.run_control_plane()
        self.assertEqual(1, code)
        self.assertIn("missing-evidence-gate", out)

    def test_mislabeled_control_plane_product_change_fails_closed(self) -> None:
        # R2-Q181-2: scope LABEL says control-plane but a product path changed -> must fail on
        # CLASSIFICATION, not trust the label.
        self.widen_targets()  # keep control-plane scope (task_x default), allow project/**
        self.make_change("project/src/lib.rs", "fn x() {}\n")
        code, out = self.run_control_plane()
        self.assertEqual(1, code)
        self.assertIn("missing-evidence-gate", out)
        self.assertIn("not control-plane-managed", out)

    def test_mixed_control_plane_and_product_change_fails_closed(self) -> None:
        # R2-Q181-2: mixed .ai + product change, control-plane label -> the product path must
        # still force a fail.
        self.widen_targets()
        self.make_change(".ai/notes.md", "docs\n")
        self.make_change("project/src/lib.rs", "fn x() {}\n")
        code, out = self.run_control_plane()
        self.assertEqual(1, code)
        self.assertIn("missing-evidence-gate", out)

    def test_unknown_scope_fails_closed(self) -> None:
        self.set_scope("galaxy")
        self.make_change(".ai/notes.md", "docs\n")
        code, out = self.run_control_plane()
        self.assertEqual(1, code)
        self.assertIn("unknown-scope", out)

    def test_missing_scope_defaults_to_gate_requiring_and_fails_closed(self) -> None:
        self.set_scope(None)
        self.make_change(".ai/notes.md", "docs\n")
        code, out = self.run_control_plane()
        self.assertEqual(1, code)
        self.assertIn("missing-evidence-gate", out)

    def test_forbidden_write_still_fails_closed(self) -> None:
        self.make_change("secret/leak.txt", "x\n")
        code, out = self.run_control_plane()
        self.assertEqual(1, code)

    def test_out_of_area_write_fails_closed(self) -> None:
        self.make_change("outside/file.txt", "x\n")  # not under .ai/**
        code, _ = self.run_control_plane()
        self.assertEqual(1, code)

    def test_plan_mode_emits_gate_null_json(self) -> None:
        self.make_change(".ai/notes.md", "docs\n")
        code, out = self.run_control_plane(plan=True)
        self.assertEqual(0, code)
        payload = json.loads(out)
        self.assertIsNone(payload["gate"])
        self.assertEqual("control-plane", payload["verification_scope"])
        self.assertIsNone(payload["selection"])


class GateResolutionTests(unittest.TestCase):
    def _resolve_with(self, enabled: list[str], gate_ids: list[str] | None = None):
        """Build a temp repo whose config enables `enabled` and whose disk carries a gate
        manifest for each id in `gate_ids` (defaults to `enabled`). Returns the loaded gate."""
        gate_ids = enabled if gate_ids is None else gate_ids
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".ai").mkdir()
            enabled_yaml = ("  enabled:\n" + "\n".join(f"    - {e}" for e in enabled)) if enabled else "  enabled: []"
            (root / ".ai" / "config.yaml").write_text(
                "version: 1\nextensions:\n  roots:\n    - scripts/extensions\n" + enabled_yaml + "\n",
                encoding="utf-8",
            )
            for gid in gate_ids:
                ext = root / "scripts" / "extensions" / gid
                ext.mkdir(parents=True)
                (ext / "extension.json").write_text(json.dumps({
                    "id": gid, "version": "1.0.0", "api_version": 1, "types": ["gate"],
                    "root": "scripts", "entrypoints": {"gate": f"{gid}_gate.py"},
                }), encoding="utf-8")
                (root / "scripts" / f"{gid}_gate.py").write_text(
                    f"GATE = {gid!r}\ndef cmd_verify(args, *, root, ai):\n    pass\n", encoding="utf-8")
            with mock.patch.object(doctor_module.constants, "ROOT", root), \
                 mock.patch.object(doctor_module.constants, "AI", root / ".ai"):
                return doctor_module._load_rust_verify()

    def test_load_returns_none_when_no_gate_enabled(self) -> None:
        self.assertIsNone(self._resolve_with([], gate_ids=["rust"]))

    def test_resolves_a_custom_non_rust_gate_generically(self) -> None:
        # Q181-3: resolution must NOT be hardcoded to id 'rust'.
        module = self._resolve_with(["mygate"])
        self.assertIsNotNone(module)
        self.assertEqual("mygate", module.GATE)

    def test_multiple_enabled_gates_fail_closed(self) -> None:
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                self._resolve_with(["rust", "extra"])

    def test_enabled_without_manifest_fails_closed(self) -> None:
        with self.assertRaises(reg.RegistryError) as ctx:
            self._resolve_with(["rust"], gate_ids=[])
        self.assertEqual("unknown-enabled-id", ctx.exception.reason)


class CmdVerifyRoutingTests(unittest.TestCase):
    def test_cmd_verify_routes_to_control_plane_when_gate_is_none(self) -> None:
        ns = mock.Mock(task_id="task_x", base="HEAD", plan=False)
        fake_core = mock.Mock()
        with mock.patch.object(ai_cli, "_load_rust_verify", return_value=None), \
             mock.patch.object(ai_cli, "_load_verify_contract", return_value=fake_core):
            ai_cli.cmd_verify(ns)
        fake_core.control_plane_verify.assert_called_once()

    def test_cmd_verify_delegates_to_gate_when_present(self) -> None:
        ns = mock.Mock(task_id="task_x", base="HEAD", plan=False)
        gate = mock.Mock()
        with mock.patch.object(ai_cli, "_load_rust_verify", return_value=gate):
            ai_cli.cmd_verify(ns)
        gate.cmd_verify.assert_called_once()

    def test_cmd_cargo_fails_closed_without_gate(self) -> None:
        ns = mock.Mock(task_id="task_x", base="HEAD")
        with mock.patch.object(ai_cli, "_load_rust_verify", return_value=None):
            with self.assertRaises(SystemExit):
                with contextlib.redirect_stderr(io.StringIO()):
                    ai_cli.cmd_cargo(ns)


if __name__ == "__main__":
    unittest.main()
