from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import scripts.ai_plane.config as config_module
import scripts.ai_plane.doctor as doctor_module


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


class AiCliDoctorAndLauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.live_before = live_state_snapshot()

    @classmethod
    def tearDownClass(cls) -> None:
        live_after = live_state_snapshot()
        if cls.live_before != live_after:
            raise AssertionError("doctor/launcher fixtures changed the live repository")

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.ai = self.root / ".ai"
        root_patch = mock.patch.object(ai_cli.constants, "ROOT", self.root)
        ai_patch = mock.patch.object(ai_cli.constants, "AI", self.ai)
        root_patch.start()
        ai_patch.start()
        self.addCleanup(root_patch.stop)
        self.addCleanup(ai_patch.stop)
        for directory in ("rules", "workflows", "agents", "project", "memory", "skills", "migration"):
            (self.ai / directory).mkdir(parents=True)
        for state in ai_cli.TASK_STATES:
            (self.ai / "tasks" / state).mkdir(parents=True)
        (self.ai / "tasks" / "queue" / "task_fixture").mkdir()
        (self.ai / "config.yaml").write_text(live_config_text(), encoding="utf-8")
        local_profile = self.ai / ".local" / "tools.json"
        local_profile.parent.mkdir()
        local_profile.write_text(
            json.dumps({
                "version": 1,
                "enabled_tools": ["codex"],
                "defaults": {field: "codex" for field in ("research_tool", "planning_tool", "implementation_tool", "review_tool")},
            }),
            encoding="utf-8",
        )
        rust_verify_json = REPO_ROOT / ".ai" / "project" / "rust-verification.json"
        if rust_verify_json.exists():
            (self.ai / "project" / "rust-verification.json").write_text(
                rust_verify_json.read_text(encoding="utf-8"), encoding="utf-8"
            )

        registry = (json.dumps(ai_cli.generate_registry(self.ai), indent=2, sort_keys=True) + "\n").encode("utf-8")
        (self.ai / "_registry.json").write_bytes(registry)
        entry = {
            "path": ".ai/_registry.json",
            "sha256": hashlib.sha256(registry).hexdigest(),
            "command": "ai sync",
        }
        (self.ai / "_manifest.json").write_bytes(
            ai_cli.serialize_manifest({entry["path"]: entry})
        )

    @staticmethod
    def lock(status: str = "PASS", guidance: str = ""):
        default_guidance = (
            "No repair is required. Confirm the idle state with: python scripts/ai_cli.py cargo-cache inspect"
            if status == "WARN" and not guidance
            else guidance
        )
        return lambda _root, _ai: ai_cli.doctor_check(
            status,
            "Verification lock",
            "free (no lock file present)",
            default_guidance,
        )

    def collect(self, **kwargs) -> list[dict[str, str]]:
        params = {"root": self.root, "ai": self.ai, "lock_inspector": self.lock()}
        params.update(kwargs)
        return ai_cli.collect_doctor_checks(**params)

    def test_doctor_pass_fixture_is_all_success(self) -> None:
        checks = self.collect()
        self.assertEqual(set(), {check["status"] for check in checks} - {"PASS"})
        self.assertIn("Manifest", {check["name"] for check in checks})

    def test_doctor_expected_gaps_exit_zero_with_exact_advice(self) -> None:
        (self.ai / "tasks" / "queue" / "task_fixture").rmdir()
        (self.ai / "_registry.json").write_text("{}\n", encoding="utf-8")
        checks = self.collect(lock_inspector=self.lock("WARN"))
        self.assertFalse(any(check["status"] == "FAIL" for check in checks))
        warnings = {check["name"]: check for check in checks if check["status"] == "WARN"}
        self.assertTrue({"Queue", "Registry", "Manifest", "Verification lock"} <= set(warnings))
        self.assertIn("python scripts/ai_cli.py feature new", warnings["Queue"]["guidance"])
        self.assertIn("python scripts/ai_cli.py sync", warnings["Registry"]["guidance"])
        self.assertIn("python scripts/ai_cli.py sync", warnings["Manifest"]["guidance"])
        self.assertIn("python scripts/ai_cli.py cargo-cache inspect", warnings["Verification lock"]["guidance"])
        with mock.patch.object(ai_cli, "collect_doctor_checks", return_value=checks):

            code, stdout, _stderr = self.capture_doctor()
        self.assertEqual(0, code)
        self.assertIn("[WARN] Queue", stdout)
        self.assertIn("0 failure(s)", stdout)

    def test_doctor_genuine_malfunctions_exit_nonzero(self) -> None:
        (self.ai / "config.yaml").write_text("tools: []\n", encoding="utf-8")
        (self.ai / "tasks" / "active").rmdir()
        checks = self.collect(version_info=(3, 9, 9), which=lambda _name: None)
        failures = {check["name"] for check in checks if check["status"] == "FAIL"}
        self.assertTrue({"Python", "Git", "Task states", "Config"} <= failures)
        with mock.patch.object(ai_cli, "collect_doctor_checks", return_value=checks):

            code, stdout, _stderr = self.capture_doctor()
        self.assertEqual(1, code)
        self.assertIn("[FAIL] Config", stdout)

    @staticmethod
    def capture_doctor() -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        code = 0
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                ai_cli.cmd_doctor(argparse.Namespace())
            except SystemExit as exit_err:
                code = exit_err.code if isinstance(exit_err.code, int) else 1
        return code, stdout.getvalue(), stderr.getvalue()

    def test_doctor_lock_reports_holder_metadata_and_stale_heartbeat(self) -> None:
        lock_path = self.root / ".verification.lock"
        lock_path.write_text("{}", encoding="utf-8")
        holder = {
            "pid": 4242,
            "task_id": "task_holder",
            "heartbeat_utc": "2026-07-24T06:58:00+00:00",
        }
        lock = mock.Mock(path=lock_path)
        lock.holder_metadata.side_effect = lambda: dict(holder)
        rust_verify = mock.Mock()
        rust_verify.load_config.return_value = {"lock": {"heartbeat_seconds": 5}}
        rust_verify.VerificationLock.return_value = lock
        rust_verify.os_name.return_value = "nt"
        rust_verify.lock_operations.return_value = (
            mock.Mock(side_effect=OSError("held")),
            mock.Mock(),
        )
        with mock.patch.object(ai_cli, "_load_rust_verify", return_value=rust_verify):

            stale = ai_cli.inspect_doctor_lock(
                self.root,
                self.ai,
                now=datetime(2026, 7, 24, 7, 0, tzinfo=timezone.utc),
            )
            self.assertEqual("WARN", stale["status"])
            self.assertIn("pid=4242 task=task_holder", stale["detail"])
            self.assertIn("stale", stale["detail"])

            holder["heartbeat_utc"] = "2026-07-24T06:59:55+00:00"
            active = ai_cli.inspect_doctor_lock(
                self.root,
                self.ai,
                now=datetime(2026, 7, 24, 7, 0, tzinfo=timezone.utc),
            )
            self.assertEqual("PASS", active["status"])
            self.assertIn("active process", active["detail"])


    def test_ai_cmd_doctor_subcommand_wiring(self) -> None:
        checks = [ai_cli.doctor_check("PASS", "Python", "3.10.0")]
        with mock.patch.object(ai_cli, "collect_doctor_checks", return_value=checks):


            code, stdout, _stderr = self.capture_doctor()
        self.assertEqual(0, code)
        self.assertIn("AI doctor", stdout)

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
            lock_inspector=self.lock(),
        )
        self.assertTrue(any(check["name"] == "Python" and check["status"] == "PASS" for check in result))

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


class AiCliRustVerifyWiringTests(unittest.TestCase):
    """Thin-wrapper wiring: ai_cli delegates verify/cargo/cargo-cache to rust_verify."""

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
        self.ai.mkdir()
        self.rust_verify = mock.Mock()
        loader_patch = mock.patch.object(
            ai_cli, "_load_rust_verify", lambda: self.rust_verify
        )
        root_patch = mock.patch.object(ai_cli.constants, "ROOT", self.root)
        ai_patch = mock.patch.object(ai_cli.constants, "AI", self.ai)
        loader_patch.start()
        root_patch.start()
        ai_patch.start()
        self.addCleanup(loader_patch.stop)
        self.addCleanup(root_patch.stop)
        self.addCleanup(ai_patch.stop)

    def test_verify_plan_mode_wiring(self) -> None:
        ai_cli.main(["verify", "task_x", "--base", "base0", "--plan"])
        self.rust_verify.cmd_verify.assert_called_once()
        args, kwargs = self.rust_verify.cmd_verify.call_args
        namespace = args[0]
        self.assertEqual("task_x", namespace.task_id)
        self.assertEqual("base0", namespace.base)
        self.assertTrue(namespace.plan)
        self.assertFalse(namespace.run)
        self.assertEqual({"root": self.root, "ai": self.ai}, kwargs)

    def test_verify_run_mode_wiring(self) -> None:
        ai_cli.main(["verify", "task_x", "--base", "base0", "--run"])
        namespace = self.rust_verify.cmd_verify.call_args[0][0]
        self.assertTrue(namespace.run)
        self.assertFalse(namespace.plan)

    def test_verify_requires_exactly_one_mode(self) -> None:
        with self.assertRaises(SystemExit):
            ai_cli.main(["verify", "task_x", "--base", "base0"])
        self.rust_verify.cmd_verify.assert_not_called()

    def test_cargo_wiring_preserves_remainder_argv_and_label(self) -> None:
        ai_cli.main([
            "cargo", "task_x", "--base", "base0", "--label", "pre-gate",
            "--", "clippy", "-p", "graph", "--locked",
        ])
        self.rust_verify.cmd_cargo.assert_called_once()
        args, kwargs = self.rust_verify.cmd_cargo.call_args
        namespace = args[0]
        self.assertEqual("task_x", namespace.task_id)
        self.assertEqual("base0", namespace.base)
        self.assertEqual("pre-gate", namespace.label)
        remainder = [a for a in namespace.cargo_argv if a != "--"]
        self.assertEqual(["clippy", "-p", "graph", "--locked"], remainder)
        self.assertEqual({"root": self.root, "ai": self.ai}, kwargs)

    def test_cargo_cache_inspect_and_clean_wiring(self) -> None:
        ai_cli.main(["cargo-cache", "inspect"])
        namespace = self.rust_verify.cmd_cargo_cache.call_args[0][0]
        self.assertEqual("inspect", namespace.cargo_cache_command)

        ai_cli.main(["cargo-cache", "clean", "--scratch", "--yes"])
        namespace = self.rust_verify.cmd_cargo_cache.call_args[0][0]
        self.assertEqual("clean", namespace.cargo_cache_command)
        self.assertTrue(namespace.scratch)

    def test_cargo_cache_requires_subcommand(self) -> None:
        with self.assertRaises(SystemExit):
            ai_cli.main(["cargo-cache"])
        self.rust_verify.cmd_cargo_cache.assert_not_called()


if __name__ == "__main__":
    unittest.main()
