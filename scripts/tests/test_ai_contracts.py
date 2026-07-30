from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scripts.ai_plane.config as config_module

REPO_ROOT = Path(__file__).resolve().parents[2]
import scripts.ai_cli as ai_cli



def live_config_text() -> str:
    return (REPO_ROOT / ".ai" / "config.yaml").read_text(encoding="utf-8")


def digest_files(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        if not path.is_file():
            continue
        digest.update(str(path.relative_to(REPO_ROOT)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def live_state_snapshot() -> dict[str, object]:
    git_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    git_status = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=REPO_ROOT,
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    receipts = list((REPO_ROOT / ".ai" / "tasks").glob("**/receipt*.yaml"))
    return {
        "git_head": git_head,
        "git_status_sha256": hashlib.sha256(git_status).hexdigest(),
        "receipts_digest": digest_files(receipts),
        "agents_md": (REPO_ROOT / "AGENTS.md").read_bytes(),
        "gemini_md": (REPO_ROOT / "GEMINI.md").read_bytes(),
        "claude_md": (REPO_ROOT / "CLAUDE.md").read_bytes(),
    }


class AiCliTaskContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.live_before = live_state_snapshot()

    @classmethod
    def tearDownClass(cls) -> None:
        live_after = live_state_snapshot()
        if cls.live_before != live_after:
            raise AssertionError("fixture tests changed the live repository, receipts, adapters, or profile")

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.ai = self.root / ".ai"
        for state in ai_cli.TASK_STATES:
            (self.ai / "tasks" / state).mkdir(parents=True)
        (self.ai / "config.yaml").write_text(live_config_text(), encoding="utf-8")
        ai_cli.initialize_runtime_config(self.ai / "config.yaml")
        self.profile = self.root / "profile.toml"
        self.profile.write_text("model = 'fixture-only'\n", encoding="utf-8")
        self.profile_before = self.profile.read_bytes()

        root_patch = mock.patch.object(ai_cli.constants, "ROOT", self.root)
        ai_patch = mock.patch.object(ai_cli.constants, "AI", self.ai)
        git_patch = mock.patch.object(ai_cli, "git_value", lambda *_args: "fixture-git")

        root_patch.start()
        ai_patch.start()
        git_patch.start()
        self.addCleanup(root_patch.stop)
        self.addCleanup(ai_patch.stop)
        self.addCleanup(git_patch.stop)
        self.addCleanup(self.assert_fixture_profile_unchanged)

    def assert_fixture_profile_unchanged(self) -> None:
        if self.profile.read_bytes() != self.profile_before:
            raise AssertionError("test modified fixture profile.toml directly")

    def write_task(
        self,
        state: str,
        name: str,
        *,
        task_id: str | None = None,
        risk: str = "medium",
        tool: str = "codex",
        review_tool: str = "claude",
        extra_yaml: str = "",
    ) -> Path:
        task_dir = self.ai / "tasks" / state / name
        task_dir.mkdir(parents=True, exist_ok=True)
        resolved_id = task_id or name
        task_yaml = task_dir / "task.yaml"
        task_yaml.write_text(
            f"id: {resolved_id}\n"
            f"feature: fixture\n"
            f"title: Title for {name}\n"
            f"risk: {risk}\n"
            f"preferred_tool: {tool}\n"
            f"review_tool: {review_tool}\n"
            f"isolation_strategy: patch\n"
            f"contract_version: 1\n"
            f"{extra_yaml}\n",
            encoding="utf-8",
        )
        return task_dir

    def capture_command(self, func, *args) -> tuple[int, str, str]:
        stdout = io_stdout = sys.stdout = mock.MagicMock()
        stderr = io_stderr = sys.stderr = mock.MagicMock()
        out_buf = []
        err_buf = []
        stdout.write.side_effect = out_buf.append
        stderr.write.side_effect = err_buf.append

        code = 0
        try:
            func(*args)
        except SystemExit as exit_err:
            code = exit_err.code if isinstance(exit_err.code, int) else 1
        finally:
            sys.stdout = sys.__stdout__
            sys.stderr = sys.__stderr__

        return code, "".join(out_buf), "".join(err_buf)

    def test_validator_accepts_only_exact_risk_and_tool_vocabularies(self) -> None:
        task_dir = self.write_task("queue", "task_valid", risk="low", tool="codex", review_tool="claude")
        data = ai_cli.parse_simple_yaml(task_dir / "task.yaml")
        self.assertEqual([], ai_cli.task_contract_vocabulary_violations(task_dir, data, fail=False))

        task_invalid_risk = self.write_task(
            "queue", "task_invalid_risk", risk="extreme", tool="codex", review_tool="claude"
        )
        data_invalid_risk = ai_cli.parse_simple_yaml(task_invalid_risk / "task.yaml")
        violations = ai_cli.task_contract_vocabulary_violations(task_invalid_risk, data_invalid_risk, fail=False)
        self.assertEqual(
            [("risk", "extreme", ("low", "medium", "high"))],
            violations,
        )

        task_invalid_tools = self.write_task(
            "queue", "task_invalid_tools", risk="high", tool="unknown_tool", review_tool="another_tool"
        )
        data_invalid_tools = ai_cli.parse_simple_yaml(task_invalid_tools / "task.yaml")
        violations = ai_cli.task_contract_vocabulary_violations(task_invalid_tools, data_invalid_tools, fail=False)

        self.assertTrue(any("preferred_tool" in v for v in violations))
        self.assertTrue(any("review_tool" in v for v in violations))

    def test_valid_risk_merge_gates_are_preserved(self) -> None:
        self.write_task("queue", "task_low", risk="low", tool="codex", review_tool="claude")
        code, stdout, stderr = self.capture_command(ai_cli.cmd_tasks, mock.MagicMock())
        self.assertEqual(0, code)
        self.assertEqual("", stderr)
        self.assertIn("task_low", stdout)

    def test_invalid_packet_does_not_strand_a_different_selected_task(self) -> None:
        self.write_task("queue", "task_selected", risk="low", tool="codex", review_tool="claude")
        self.write_task("queue", "task_invalid", risk="invalid_risk", tool="codex", review_tool="claude")
        code, stdout, stderr = self.capture_command(ai_cli.cmd_task_show, mock.MagicMock(task_id="task_selected"))
        self.assertEqual(0, code)
        self.assertEqual("", stderr)
        self.assertIn("task_selected", stdout)

    def test_inventory_is_exhaustive_annotated_and_nonzero(self) -> None:
        self.write_task("queue", "task_valid", risk="low", tool="codex", review_tool="claude")
        self.write_task("queue", "task_bad_risk", risk="medium-high", tool="codex", review_tool="claude")
        self.write_task("queue", "task_bad_tools", risk="medium", tool="['codex']", review_tool="<missing>")
        code, stdout, stderr = self.capture_command(ai_cli.cmd_tasks, mock.MagicMock())
        self.assertEqual(1, code)
        self.assertIn("inventory completed with 3 invalid live field(s)", stderr)
        self.assertIn("INVALID risk='medium-high' allowed=low|medium|high", stdout)

    def test_inventory_annotates_legacy_invalid_metadata_without_blocking_live_work(self) -> None:
        self.write_task("queue", "task_legacy_bad", risk="bogus", tool="codex", review_tool="claude")
        code, stdout, stderr = self.capture_command(ai_cli.cmd_tasks, mock.MagicMock())
        self.assertEqual(1, code)
        self.assertIn("inventory completed with 1 invalid live field(s)", stderr)
        self.assertIn("INVALID risk='bogus'", stdout)


if __name__ == "__main__":
    unittest.main()
