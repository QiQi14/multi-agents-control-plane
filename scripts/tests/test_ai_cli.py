from __future__ import annotations

import hashlib
import io
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

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


class AiCliLauncherAndShimTests(unittest.TestCase):
    """Facade, launcher, and entry-point tests."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.live_before = live_state_snapshot()

    @classmethod
    def tearDownClass(cls) -> None:
        live_after = live_state_snapshot()
        if cls.live_before != live_after:
            raise AssertionError("launcher tests changed the live repository")

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.ai = self.root / ".ai"
        self.ai.mkdir()

    def test_probe_python_candidate_filtering_and_alias_skip(self) -> None:
        alias_stub = self.root / "AppExecutionAlias.exe"
        alias_stub.touch()

        def mock_run(argv, **_kwargs):
            if argv[0] == str(alias_stub):
                return mock.Mock(
                    returncode=9009,
                    stdout="",
                    stderr="The system cannot execute the specified program.",
                )
            return mock.Mock(returncode=0, stdout="3.11.0", stderr="")

        result = ai_cli.collect_doctor_checks(
            root=self.root,
            ai=self.ai,
            version_info=(3, 11, 0),
        )
        self.assertTrue(any(check["name"] == "Python" and check["status"] == "PASS" for check in result))

    def impact_args(self) -> object:
        return mock.Mock(
            symbol="demo::render",
            manifest="project/Cargo.toml",
            database=".ai/.local/impact.sqlite",
            content_audit=False,
        )

    def test_impact_bridge_delegates_to_present_binary(self) -> None:
        binary = self.root / "ai-impact"
        binary.touch()
        database = self.root / ".ai/.local/impact.sqlite"
        database.parent.mkdir(parents=True)
        database.touch()
        raw_output = b"complete\r\n\xef\xbb\xbf"
        run = mock.Mock(return_value=mock.Mock(returncode=0, stdout=raw_output, stderr=b""))
        output = mock.Mock()
        output.buffer = io.BytesIO()
        with mock.patch.object(ai_cli.sys, "stdout", output):
            ai_cli.cmd_impact(
                self.impact_args(),
                root=self.root,
                run=run,
                binary_candidates=[binary],
            )
        self.assertEqual(raw_output, output.buffer.getvalue())
        self.assertEqual(
            [
                str(binary),
                "impact",
                "--manifest",
                "project/Cargo.toml",
                "--database",
                ".ai/.local/impact.sqlite",
                "demo::render",
            ],
            run.call_args.args[0],
        )
        self.assertNotIn("text", run.call_args.kwargs)

    def test_impact_bridge_missing_binary_is_success_shaped_guidance(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            ai_cli.cmd_impact(
                self.impact_args(),
                root=self.root,
                run=mock.Mock(),
                binary_candidates=[self.root / "missing-ai-impact"],
            )
        self.assertIn("advisory unavailable", output.getvalue())
        self.assertIn(
            "cargo build --manifest-path tools/ai-impact/Cargo.toml", output.getvalue()
        )

    def test_impact_bridge_missing_index_is_success_shaped_guidance(self) -> None:
        binary = self.root / "ai-impact"
        binary.touch()
        run = mock.Mock()
        output = io.StringIO()
        with redirect_stdout(output):
            ai_cli.cmd_impact(
                self.impact_args(),
                root=self.root,
                run=run,
                binary_candidates=[binary],
            )
        self.assertIn("initialize the index", output.getvalue())
        run.assert_not_called()

    def test_impact_bridge_preserves_symbol_not_in_index_silence(self) -> None:
        binary = self.root / "ai-impact"
        binary.touch()
        database = self.root / ".ai/.local/impact.sqlite"
        database.parent.mkdir(parents=True)
        database.touch()
        run = mock.Mock(
            return_value=mock.Mock(
                returncode=0,
                stdout="computed at abc1234; 0 files changed since\n"
                "advisory silent: unresolved-symbol\n",
                stderr="",
            )
        )
        output = io.StringIO()
        with redirect_stdout(output):
            ai_cli.cmd_impact(
                self.impact_args(),
                root=self.root,
                run=run,
                binary_candidates=[binary],
            )
        self.assertIn("advisory silent: unresolved-symbol", output.getvalue())

    def test_root_ai_shim_is_cwd_independent(self) -> None:
        command = [sys.executable, str(REPO_ROOT / "ai"), "--help"]
        from_root = subprocess.run(
            command, cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=30,
        )
        from_subdirectory = subprocess.run(
            command, cwd=REPO_ROOT / "scripts", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=30,
        )
        self.assertEqual(from_root.returncode, from_subdirectory.returncode)
        self.assertEqual(from_root.stdout, from_subdirectory.stdout)
        self.assertEqual(from_root.stderr, from_subdirectory.stderr)
        self.assertEqual(0, from_root.returncode)


class PassthroughSplitTests(unittest.TestCase):
    """Everything after `--` is split off before argparse sees it.

    argparse changed between 3.11 and 3.12: on 3.11 an option-like token after `--` inside an
    `nargs="*"` positional is rejected as an unrecognized argument, so
    `ai cargo t --base b -- clippy --locked` worked on 3.12 and exited 2 on 3.11. Splitting first
    gives the CLI one contract on every supported version.
    """

    def setUp(self) -> None:
        import scripts.ai_cli as ai_cli
        self.split = ai_cli.split_passthrough
        self.dests = ai_cli.PASSTHROUGH_DEST

    def test_option_like_tokens_after_the_separator_are_passthrough(self) -> None:
        head, rest = self.split(
            ["cargo", "task_x", "--base", "b0", "--", "clippy", "-p", "graph", "--locked"])
        self.assertEqual(["cargo", "task_x", "--base", "b0"], head)
        self.assertEqual(["clippy", "-p", "graph", "--locked"], rest)

    def test_argv_without_a_separator_is_untouched(self) -> None:
        argv = ["docs", "build"]
        head, rest = self.split(argv)
        self.assertEqual(argv, head)
        self.assertEqual([], rest)

    def test_only_the_first_separator_splits(self) -> None:
        # A second `--` belongs to the forwarded command, not to us.
        head, rest = self.split(["cargo", "t", "--", "test", "--", "--nocapture"])
        self.assertEqual(["cargo", "t"], head)
        self.assertEqual(["test", "--", "--nocapture"], rest)

    def test_a_trailing_separator_yields_no_passthrough(self) -> None:
        head, rest = self.split(["cargo", "t", "--"])
        self.assertEqual(["cargo", "t"], head)
        self.assertEqual([], rest)

    def test_every_passthrough_command_declares_a_destination(self) -> None:
        self.assertEqual({"cargo": "cargo_argv", "ext": "args"}, self.dests)


if __name__ == "__main__":
    unittest.main()
