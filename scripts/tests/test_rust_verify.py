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
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "rust_verify.py"
SPEC = importlib.util.spec_from_file_location("task_144_rust_verify", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load {MODULE_PATH}")
rv = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rv)
# task_181: the stack-agnostic substrate (lock, evidence, run/git/process seams, contract
# check, change-set) now lives in scripts/verify_core.py; rust_verify.py re-exports it via
# `from scripts.verify_core import ...`, so loading rv already imported the substrate module
# under its canonical package name. Grab that same object so seam patches reach substrate-
# internal callers (e.g. VerificationLock, whose methods resolve temp_dir/lock_operations
# through verify_core's own globals). There is exactly one scripts.verify_core object (Q181-5).
vc = sys.modules["scripts.verify_core"]
REAL_RUN_COMMAND = rv.run_command
REAL_PROCESS_LISTING = rv.process_listing

LIVE_CONFIG_TEXT = (REPO_ROOT / ".ai" / "project" / "rust-verification.json").read_text(
    encoding="utf-8"
)
LIVE_AI_CONFIG_TEXT = (REPO_ROOT / ".ai" / "config.yaml").read_text(encoding="utf-8")


def digest_files(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        if not path.is_file():
            continue
        digest.update(str(path).encode("utf-8"))
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
    tasks = REPO_ROOT / ".ai" / "tasks"
    evidence = list(tasks.glob("**/rust-verification-evidence.json"))
    receipts = list(tasks.glob("**/receipt*.yaml"))
    cache_log = REPO_ROOT / ".ai" / "project" / "rust-verification-cache-log.json"
    target = REPO_ROOT / "target"
    profile = Path.home() / ".codex" / "config.toml"
    return {
        "git_head": git_head,
        "git_status": git_status,
        "receipts": digest_files(receipts),
        "evidence": digest_files(evidence),
        "cache_log": cache_log.read_bytes() if cache_log.exists() else None,
        "target_top": sorted(p.name for p in target.iterdir()) if target.is_dir() else [],
        "temp_scratch": sorted(
            p.name for p in Path(tempfile.gettempdir()).glob("maw-rust-*")
        ),
        "profile": profile.read_bytes() if profile.exists() else None,
    }


class LiveStateGuard:
    """Class-level guard: fixtures must never touch the live repo, caches, or profile."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.live_before = live_state_snapshot()

    @classmethod
    def tearDownClass(cls) -> None:
        live_after = live_state_snapshot()
        if cls.live_before != live_after:
            raise AssertionError(
                "fixture tests changed the live repository, evidence, caches, target, or profile"
            )


# ---------------------------------------------------------------------------
# Fixture primitives
# ---------------------------------------------------------------------------


def name_status_payload(entries: list[tuple]) -> str:
    payload = ""
    for entry in entries:
        if len(entry) == 3:
            status, old, new = entry
            payload += f"{status}\0{old}\0{new}\0"
        else:
            status, path = entry
            payload += f"{status}\0{path}\0"
    return payload


class FakeGit:
    """Deterministic base-to-working-tree change-set fixture."""

    def __init__(self, base: str = "base0", head: str = "head0") -> None:
        self.base = base
        self.head = head
        self.committed: list[tuple] = []
        self.staged: list[tuple] = []
        self.unstaged: list[tuple] = []
        self.untracked: list[str] = []
        self.valid_base = True
        self.head_moves = False
        self.shown: dict[str, str] = {}
        self._head_calls = 0

    def __call__(self, root: Path, args: list[str]) -> str:
        if args[:2] == ["cat-file", "-e"]:
            if self.valid_base and args[2] == f"{self.base}^{{commit}}":
                return ""
            raise rv.GitError(f"unknown revision {args[2]}")
        if args == ["rev-parse", "HEAD"]:
            self._head_calls += 1
            if self.head_moves and self._head_calls > 1:
                return "head1\n"
            return f"{self.head}\n"
        if args == ["diff", "--name-status", "-z", self.base, self.head]:
            return name_status_payload(self.committed)
        if args == ["diff", "--name-status", "-z", "--cached", self.head]:
            return name_status_payload(self.staged)
        if args == ["diff", "--name-status", "-z"]:
            return name_status_payload(self.unstaged)
        if args == ["ls-files", "--others", "--exclude-standard", "-z"]:
            return "".join(f"{path}\0" for path in self.untracked)
        if len(args) == 2 and args[0] == "show" and args[1] in self.shown:
            return self.shown[args[1]]
        raise rv.GitError(f"unexpected git argv: {args}")


def workspace_metadata(root: Path, graph: dict[str, list[tuple]]) -> dict[str, object]:
    """Synthetic `cargo metadata --no-deps` JSON. Deps: (target, kind|None)."""
    packages = []
    for name, deps in graph.items():
        packages.append({
            "name": name,
            "manifest_path": str(root / "crates" / name / "Cargo.toml"),
            "dependencies": [
                {
                    "name": target,
                    "kind": kind,
                    "path": str(root / "crates" / target),
                    "req": "0.1.0",
                }
                for target, kind in deps
            ],
        })
    return {"packages": packages, "workspace_members": sorted(graph), "version": 1}


def fixture_graph(root: Path) -> dict[str, object]:
    graph: dict[str, list[tuple]] = {
        "pkg-core": [],
        "pkg-runtime": [("pkg-core", None)],
        "pkg-tools": [("pkg-core", "build")],
        "pkg-ui": [("pkg-core", None)],
        "pkg-ir": [("pkg-core", "dev")],
        "pkg-graph": [],
        "pkg-spatial": [("pkg-graph", None)],
        "pkg-render-core": [("pkg-graph", "dev"), ("pkg-spatial", None)],
    }
    # Filler packages keep ordinary selections below the half-workspace threshold.
    for index in range(1, 9):
        graph[f"pkg-filler-{index}"] = []
    return workspace_metadata(root, graph)


class FakeRunner:
    """Argv-array recorder; never a shell string."""

    def __init__(
        self,
        metadata: object = None,
        exit_map: dict[tuple, int] | None = None,
        delay: float = 0.0,
        on_call=None,
    ) -> None:
        self.calls: list[tuple[list[str], str]] = []
        self.metadata = metadata
        self.exit_map = exit_map or {}
        self.delay = delay
        self.on_call = on_call

    def __call__(self, argv: list[str], cwd: Path) -> dict[str, object]:
        assert isinstance(argv, list) and all(isinstance(a, str) for a in argv)
        self.calls.append((list(argv), str(cwd)))
        if self.on_call is not None:
            self.on_call(argv, cwd)
        if self.delay:
            time.sleep(self.delay)
        result = {
            "argv": list(argv),
            "started_utc": "fixture-utc",
            "duration_seconds": 0.001,
            "exit_status": 0,
            "output": "",
        }
        if argv[:2] == ["cargo", "metadata"]:
            if self.metadata == "FAIL":
                result.update(exit_status=101, output="metadata exploded")
            elif self.metadata == "INVALID":
                result.update(output="this is not json")
            else:
                result.update(output=json.dumps(self.metadata))
            return result
        if len(argv) == 2 and argv[1] == "--version" and argv[0] in ("rustc", "cargo"):
            result.update(output=f"{argv[0]} 1.97.0 (fixture 2026-07-22)\n")
            return result
        status = self.exit_map.get(tuple(argv[:2]), 0)
        label = argv[1] if len(argv) > 1 else argv[0]
        result.update(exit_status=status, output=f"fixture output for {label}")
        return result


class FakeLockState:
    """Cross-'process' advisory-lock emulation behind the platform seam."""

    def __init__(self) -> None:
        self.held: dict[str, int] = {}
        self.guard = threading.Lock()
        self.log: list[tuple[str, str, float]] = []
        self.names: list[str] = []

    def factory(self, name: str):
        self.names.append(name)

        def acquire(path: Path, fd: int) -> None:
            with self.guard:
                if str(path) in self.held:
                    raise BlockingIOError("fixture lock held")
                self.held[str(path)] = fd
                self.log.append(("acquire", name, time.monotonic()))

        def release(path: Path, fd: int) -> None:
            with self.guard:
                self.held.pop(str(path), None)
                self.log.append(("release", name, time.monotonic()))

        return acquire, release


class RustVerifyFixture(LiveStateGuard, unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.ai = self.root / ".ai"
        for state in ("queue", "active", "done", "archive"):
            (self.ai / "tasks" / state).mkdir(parents=True)
        (self.ai / "project").mkdir(parents=True, exist_ok=True)
        (self.ai / "project" / "rust-verification.json").write_text(
            LIVE_CONFIG_TEXT, encoding="utf-8"
        )
        (self.ai / "config.yaml").write_text(LIVE_AI_CONFIG_TEXT, encoding="utf-8")
        (self.root / "Cargo.toml").write_text(
            '[workspace]\nresolver = "3"\nmembers = ["crates/*"]\n', encoding="utf-8"
        )
        self.fake_temp = self.root / "ostemp"
        self.fake_temp.mkdir()
        self.config = rv.load_config(self.ai)
        self.git = FakeGit()
        self.runner = FakeRunner(metadata=fixture_graph(self.root))
        self.locks = FakeLockState()
        self.patch("temp_dir", lambda: self.fake_temp)
        self.patch("git_output", self.git)
        self.patch("run_command", self.runner)
        self.patch("lock_operations", self.locks.factory)
        self.patch("process_listing", lambda _name: "")

    def patch(self, name: str, value) -> None:
        patcher = mock.patch.object(rv, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)
        # task_181: a seam moved into the substrate module is resolved through verify_core's
        # own globals by substrate-internal callers, so patch it there too. rust_verify
        # re-exports the same object, so both namespaces stay consistent.
        if hasattr(vc, name):
            vc_patcher = mock.patch.object(vc, name, value)
            vc_patcher.start()
            self.addCleanup(vc_patcher.stop)

    def create_task(
        self,
        task_id: str,
        *,
        scope: str | None = "affected-plus-neighbors",
        targets: list[str] | None = None,
        forbidden: list[str] | None = None,
    ) -> Path:
        task_dir = self.ai / "tasks" / "queue" / task_id
        task_dir.mkdir(parents=True)
        lines = [f"id: {task_id}", "risk: medium"]
        if scope is not None:
            lines.append(f'verification_scope: "{scope}"')
        lines.append("target_files:")
        for item in targets if targets is not None else ["crates/**"]:
            lines.append(f'  - "{item}"')
        lines.append("forbidden_files:")
        for item in forbidden or []:
            lines.append(f'  - "{item}"')
        (task_dir / "task.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return task_dir

    def capture(self, function, *args, **kwargs) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        code = 0
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                function(*args, **kwargs)
            except SystemExit as error:
                code = int(error.code or 0)
        return code, stdout.getvalue(), stderr.getvalue()

    def verify_args(self, task_id: str, *, plan: bool) -> argparse.Namespace:
        return argparse.Namespace(
            task_id=task_id, base=self.git.base, plan=plan, run=not plan
        )

    def change(self, *paths: str, origin: str = "unstaged", status: str = "M") -> None:
        entries = [(status, path) for path in paths]
        getattr(self.git, origin).extend(entries)

    def read_evidence(self, task_dir: Path) -> dict:
        path = task_dir / self.config["evidence"]["file_name"]
        return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Config and scope
# ---------------------------------------------------------------------------


class ConfigTests(RustVerifyFixture):
    def test_live_config_file_parses_and_validates(self) -> None:
        config = rv.load_config(self.ai)
        self.assertEqual(1, config["schema_version"])
        self.assertIn("affected-plus-neighbors", config["verification_scopes"])

    def test_missing_config_fails_closed(self) -> None:
        (self.ai / "project" / "rust-verification.json").unlink()
        with self.assertRaises(rv.ConfigError):
            rv.load_config(self.ai)

    def test_invalid_json_fails_closed(self) -> None:
        (self.ai / "project" / "rust-verification.json").write_text("not json", encoding="utf-8")
        with self.assertRaises(rv.ConfigError):
            rv.load_config(self.ai)

    def test_wrong_schema_version_fails_closed(self) -> None:
        (self.ai / "project" / "rust-verification.json").write_text(
            json.dumps({"schema_version": 99}), encoding="utf-8"
        )
        with self.assertRaises(rv.ConfigError):
            rv.load_config(self.ai)

    def test_missing_legacy_scope_defaults_to_affected_plus_neighbors(self) -> None:
        self.create_task("task_legacy", scope=None, targets=["**"])
        self.change("docs/guide.md")
        code, stdout, _stderr = self.capture(
            rv.cmd_verify, self.verify_args("task_legacy", plan=True),
            root=self.root, ai=self.ai,
        )
        self.assertEqual(0, code)
        payload = json.loads(stdout)
        self.assertEqual("affected-plus-neighbors", payload["selection"]["declared_scope"])

    def test_unknown_scope_fails_closed(self) -> None:
        self.create_task("task_bad_scope", scope="everything")
        self.change("docs/guide.md")
        code, _stdout, stderr = self.capture(
            rv.cmd_verify, self.verify_args("task_bad_scope", plan=True),
            root=self.root, ai=self.ai,
        )
        self.assertEqual(1, code)
        self.assertIn("Unknown verification_scope", stderr)
        self.assertNotIn("Traceback", stderr)


# ---------------------------------------------------------------------------
# Change-set discovery
# ---------------------------------------------------------------------------


class ChangeSetTests(RustVerifyFixture):
    def test_all_git_states_have_deterministic_fixtures(self) -> None:
        self.git.committed = [("M", "scripts/ai_cli.py")]
        self.git.staged = [("A", "scripts/new.py")]
        self.git.unstaged = [("D", "scripts/old.py")]
        self.git.untracked = ["scripts/fresh.py"]
        change_set = rv.collect_change_set(self.root, self.git.base, self.git)
        by_path = {entry["path"]: entry for entry in change_set["entries"]}
        self.assertEqual("committed", by_path["scripts/ai_cli.py"]["origin"])
        self.assertEqual("staged", by_path["scripts/new.py"]["origin"])
        self.assertEqual("unstaged", by_path["scripts/old.py"]["origin"])
        self.assertEqual("D", by_path["scripts/old.py"]["status"])
        self.assertEqual("untracked", by_path["scripts/fresh.py"]["origin"])

    def test_renamed_paths_include_old_and_new(self) -> None:
        self.git.committed = [("R100", "crates/pkg-graph/old.rs",
                               "crates/pkg-graph/new.rs")]
        change_set = rv.collect_change_set(self.root, self.git.base, self.git)
        entry = change_set["entries"][0]
        self.assertEqual("crates/pkg-graph/old.rs", entry["old_path"])
        self.assertIn("crates/pkg-graph/old.rs", change_set["paths"])
        self.assertIn("crates/pkg-graph/new.rs", change_set["paths"])

    def test_invalid_base_fails_closed(self) -> None:
        self.git.valid_base = False
        with self.assertRaises(rv.GitError):
            rv.collect_change_set(self.root, "deadbeef", self.git)

    def test_moving_head_fails_closed(self) -> None:
        self.git.head_moves = True
        with self.assertRaises(rv.GitError) as ctx:
            rv.collect_change_set(self.root, self.git.base, self.git)
        self.assertIn("HEAD moved", str(ctx.exception))

    def test_diff_fingerprint_is_stable_and_sensitive(self) -> None:
        self.change("scripts/ai_cli.py")
        first = rv.collect_change_set(self.root, self.git.base, self.git)
        self.git._head_calls = 0
        second = rv.collect_change_set(self.root, self.git.base, self.git)
        self.assertEqual(first["diff_fingerprint"], second["diff_fingerprint"])
        self.git.unstaged.append(("M", "scripts/rust_verify.py"))
        third = rv.collect_change_set(self.root, self.git.base, self.git)
        self.assertNotEqual(first["diff_fingerprint"], third["diff_fingerprint"])

    def test_out_of_target_changes_fail_closed_before_cargo(self) -> None:
        self.create_task("task_scope", targets=["docs/**"])
        self.change("crates/pkg-graph/src/lib.rs")
        code, stdout, stderr = self.capture(
            rv.cmd_verify, self.verify_args("task_scope", plan=True),
            root=self.root, ai=self.ai,
        )
        self.assertEqual(1, code)
        payload = json.loads(stdout)
        self.assertEqual(
            ["crates/pkg-graph/src/lib.rs"],
            payload["contract_check"]["target_violations"],
        )
        self.assertIn("Contract check failed", stderr)
        self.assertEqual([], self.runner.calls)  # Cargo never invoked

    def test_forbidden_changes_fail_closed_before_cargo(self) -> None:
        self.create_task("task_forbid", targets=["crates/**"],
                         forbidden=["crates/**"])
        self.change("crates/pkg-graph/src/lib.rs")
        code, stdout, stderr = self.capture(
            rv.cmd_verify, self.verify_args("task_forbid", plan=True),
            root=self.root, ai=self.ai,
        )
        self.assertEqual(1, code)
        payload = json.loads(stdout)
        self.assertEqual(
            ["crates/pkg-graph/src/lib.rs"],
            payload["contract_check"]["forbidden_violations"],
        )
        self.assertEqual([], self.runner.calls)


class ContractGlobTests(RustVerifyFixture):
    def test_task_144_forbidden_fixture_preserves_conjunction_followed_glob(self) -> None:
        forbidden_fixture = [
            "crates/**, Cargo.toml, Cargo.lock, "
            "rust-toolchain.toml, .cargo/**, or target/**",
            ".ai/tasks/done/**, .ai/tasks/archive/**, other task contracts/receipts/prompts, "
            "or historical evidence",
        ]
        globs = rv.extract_glob_tokens(forbidden_fixture)
        self.assertIn("target/**", globs)

    def test_task_174_extensionless_root_target_is_exact(self) -> None:
        target_fixture = [
            ".ai/tasks/queue/task_174_launcher_hardening_doctor/**",
            "ai",
            "ai.cmd",
            "scripts/**",
        ]
        globs = rv.extract_glob_tokens(target_fixture)
        self.assertIn("ai", globs)
        self.assertTrue(rv.matches_glob("ai", "ai"))
        self.assertFalse(rv.matches_glob("ai/nested", "ai"))

    def test_single_star_never_crosses_separator_but_double_star_does(self) -> None:
        # Reverting matches_glob to fnmatch.fnmatchcase makes the first assertion fail.
        self.assertFalse(rv.matches_glob("crates/demo/src/lib.rs", "crates/*/lib.rs"))
        self.assertTrue(rv.matches_glob("crates/demo/src/lib.rs", "crates/**/lib.rs"))
        self.assertTrue(rv.matches_glob("crates/demo", "crates/*"))


class ManifestContractTests(RustVerifyFixture):
    def manifest_entry(self, path: str, content: bytes, command: str = "ai sync") -> dict[str, str]:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return {
            "path": path,
            "sha256": hashlib.sha256(content).hexdigest(),
            "command": command,
        }

    def install_manifests(
        self,
        current_entries: list[dict[str, str]],
        base_entries: list[dict[str, str]] | None = None,
    ) -> None:
        current = {entry["path"]: entry for entry in current_entries}
        base = {entry["path"]: entry for entry in (base_entries if base_entries is not None else current_entries)}
        (self.ai / "_manifest.json").write_bytes(rv.ai_cli.serialize_manifest(current))
        self.git.shown[f"{self.git.base}:.ai/_manifest.json"] = rv.ai_cli.serialize_manifest(base).decode("utf-8")

    def test_base_manifest_exact_hash_exempts_generated_adapter_copy(self) -> None:
        self.create_task("task_manifest", scope="control-plane", targets=["docs/**"])
        path = ".ai/adapters/codex/review/task_old.prompt.md"
        entry = self.manifest_entry(path, b"generated prompt\n")
        self.install_manifests([entry])
        self.change(path)
        code, stdout, stderr = self.capture(
            rv.cmd_verify, self.verify_args("task_manifest", plan=True), root=self.root, ai=self.ai
        )
        self.assertEqual((0, ""), (code, stderr))
        payload = json.loads(stdout)
        self.assertEqual([path], payload["contract_check"]["manifest_exemptions"])
        self.assertEqual([], payload["contract_check"]["target_violations"])

    def test_modified_manifest_tracked_byte_is_still_a_contract_violation(self) -> None:
        self.create_task("task_manifest", scope="control-plane", targets=["docs/**"])
        path = ".ai/adapters/codex/review/task_old.prompt.md"
        entry = self.manifest_entry(path, b"generated prompt\n")
        self.install_manifests([entry])
        (self.root / path).write_bytes(b"generated prompt!\n")
        self.change(path)
        code, stdout, _stderr = self.capture(
            rv.cmd_verify, self.verify_args("task_manifest", plan=True), root=self.root, ai=self.ai
        )
        self.assertEqual(1, code)
        self.assertEqual([path], json.loads(stdout)["contract_check"]["target_violations"])

    def test_in_diff_manifest_entry_cannot_self_authorize_out_of_scope_edit(self) -> None:
        self.create_task(
            "task_adversarial", scope="control-plane", targets=[".ai/_manifest.json"]
        )
        outside = "outside.txt"
        entry = self.manifest_entry(outside, b"smuggled\n", command="ai sync")
        self.install_manifests([entry], base_entries=[])
        self.change(outside, ".ai/_manifest.json")
        code, stdout, _stderr = self.capture(
            rv.cmd_verify, self.verify_args("task_adversarial", plan=True), root=self.root, ai=self.ai
        )
        self.assertEqual(1, code)
        payload = json.loads(stdout)
        self.assertEqual([outside], payload["contract_check"]["target_violations"])
        self.assertEqual([], payload["contract_check"]["manifest_exemptions"])

    def test_missing_unparseable_or_self_referential_manifest_disables_exemption(self) -> None:
        self.create_task("task_closed", scope="control-plane", targets=["docs/**"])
        path = ".ai/adapters/codex/review/task_old.prompt.md"
        entry = self.manifest_entry(path, b"generated\n")
        self.git.shown[f"{self.git.base}:.ai/_manifest.json"] = rv.ai_cli.serialize_manifest({path: entry}).decode("utf-8")
        self.change(path)
        for content in (
            None,
            b"not json\n",
            json.dumps({
                "schema_version": 1,
                "entries": [{
                    "path": ".ai/_manifest.json",
                    "sha256": "0" * 64,
                    "command": "ai sync",
                }],
            }).encode("utf-8"),
        ):
            manifest = self.ai / "_manifest.json"
            if content is None:
                manifest.unlink(missing_ok=True)
            else:
                manifest.write_bytes(content)
            self.git._head_calls = 0
            code, stdout, _stderr = self.capture(
                rv.cmd_verify, self.verify_args("task_closed", plan=True), root=self.root, ai=self.ai
            )
            self.assertEqual(1, code)
            self.assertEqual([path], json.loads(stdout)["contract_check"]["target_violations"])

    def test_exact_prompt_pair_and_reconstructed_manifest_delta_pass(self) -> None:
        task_dir = self.create_task(
            "task_later",
            scope="control-plane",
            targets=[".ai/tasks/queue/task_later/**"],
        )
        base_entry = self.manifest_entry("AGENTS.md", b"base generated\n")
        task_prompt = ".ai/tasks/queue/task_later/prompt.claude.review.md"
        adapter_prompt = ".ai/adapters/claude/review/task_later.prompt.md"
        prompt_bytes = b"review prompt\r\n"
        task_entry = self.manifest_entry(
            task_prompt, prompt_bytes, "ai review task_later --tool claude"
        )
        adapter_entry = self.manifest_entry(
            adapter_prompt, prompt_bytes, "ai review task_later --tool claude"
        )
        self.install_manifests(
            [base_entry, task_entry, adapter_entry],
            base_entries=[base_entry],
        )
        self.change(task_prompt, adapter_prompt, ".ai/_manifest.json")
        code, stdout, stderr = self.capture(
            rv.cmd_verify, self.verify_args("task_later", plan=True), root=self.root, ai=self.ai
        )
        self.assertEqual((0, ""), (code, stderr))
        exemptions = json.loads(stdout)["contract_check"]["manifest_exemptions"]
        self.assertEqual(
            [".ai/_manifest.json", adapter_prompt, task_prompt], exemptions
        )

        extra = self.manifest_entry("outside.txt", b"extra\n")
        self.install_manifests(
            [base_entry, task_entry, adapter_entry, extra],
            base_entries=[base_entry],
        )
        self.git._head_calls = 0
        code, stdout, _stderr = self.capture(
            rv.cmd_verify, self.verify_args("task_later", plan=True), root=self.root, ai=self.ai
        )
        self.assertEqual(1, code)
        violations = json.loads(stdout)["contract_check"]["target_violations"]
        self.assertIn(adapter_prompt, violations)
        self.assertIn(".ai/_manifest.json", violations)

        altered_adapter_entry = dict(adapter_entry)
        altered_adapter_entry["command"] = "ai review task_later --tool codex"
        self.install_manifests(
            [base_entry, task_entry, altered_adapter_entry],
            base_entries=[base_entry],
        )
        self.git._head_calls = 0
        code, stdout, _stderr = self.capture(
            rv.cmd_verify, self.verify_args("task_later", plan=True), root=self.root, ai=self.ai
        )
        self.assertEqual(1, code)
        violations = json.loads(stdout)["contract_check"]["target_violations"]
        self.assertIn(adapter_prompt, violations)
        self.assertIn(".ai/_manifest.json", violations)


# ---------------------------------------------------------------------------
# Package selection
# ---------------------------------------------------------------------------


class SelectionTests(RustVerifyFixture):
    def select(self, paths: list[str], scope: str = "affected-plus-neighbors") -> dict:
        for path in paths:
            self.change(path)
        change_set = rv.collect_change_set(self.root, self.git.base, self.git)
        return rv.select_packages(self.root, change_set, scope, self.config, self.runner)

    def test_ui_spike_selects_only_itself(self) -> None:
        selection = self.select(["crates/pkg-ui/src/lib.rs"])
        self.assertEqual(["pkg-ui"], selection["packages"])
        self.assertEqual([], selection["escalations"])

    def test_project_selects_reverse_set_including_dev_consumer(self) -> None:
        selection = self.select(["crates/pkg-core/src/lib.rs"])
        self.assertEqual(
            ["pkg-core", "pkg-ir", "pkg-runtime", "pkg-tools", "pkg-ui"],
            selection["packages"],
        )
        self.assertEqual(["pkg-core"], selection["owners"])
        self.assertEqual(
            ["pkg-ir", "pkg-runtime", "pkg-tools", "pkg-ui"],
            selection["reverse_dependents"],
        )

    def test_graph_selects_exact_direct_reverse_set(self) -> None:
        selection = self.select(["crates/pkg-graph/src/lib.rs"])
        self.assertEqual(
            ["pkg-graph", "pkg-render-core", "pkg-spatial"],
            selection["packages"],
        )

    def test_normal_build_and_dev_edges_are_included(self) -> None:
        change_set = {"paths": []}
        packages = rv.workspace_packages(self.root, fixture_graph(self.root), self.config)
        reverse = rv.reverse_dependency_map(self.root, packages)
        self.assertEqual(
            {"pkg-runtime", "pkg-tools", "pkg-ui", "pkg-ir"},
            reverse["pkg-core"],
        )
        self.assertEqual({"pkg-spatial", "pkg-render-core"}, reverse["pkg-graph"])
        self.assertEqual(set(), reverse["pkg-ui"])
        self.assertEqual(change_set["paths"], [])

    def test_unions_are_sorted_and_deduplicated(self) -> None:
        selection = self.select([
            "crates/pkg-core/src/lib.rs",
            "crates/pkg-runtime/src/lib.rs",
        ])
        packages = selection["packages"]
        self.assertEqual(sorted(set(packages)), packages)
        self.assertEqual(
            ["pkg-core", "pkg-ir", "pkg-runtime", "pkg-tools", "pkg-ui"],
            packages,
        )

    def test_affected_scope_selects_owners_only(self) -> None:
        selection = self.select(["crates/pkg-core/src/lib.rs"], scope="affected")
        self.assertEqual(["pkg-core"], selection["packages"])

    def test_workspace_scope_selects_everything_without_spurious_escalation(self) -> None:
        selection = self.select(["crates/pkg-graph/src/lib.rs"], scope="workspace")
        self.assertEqual(16, len(selection["packages"]))
        self.assertEqual([], selection["escalations"])

    def test_docs_only_changes_schedule_no_cargo(self) -> None:
        runner = FakeRunner(metadata=None)  # metadata must not be requested
        self.patch("run_command", runner)
        selection = self.select(["docs/guide.md", "CHANGELOG.md", ".ai/rules/x.md"])
        self.assertFalse(selection["schedule_cargo"])
        self.assertEqual([], selection["packages"])
        self.assertEqual([], runner.calls)

    def test_control_plane_scope_schedules_no_rust_build(self) -> None:
        runner = FakeRunner(metadata=None)
        self.patch("run_command", runner)
        selection = self.select(
            ["crates/pkg-graph/src/lib.rs"], scope="control-plane"
        )
        self.assertFalse(selection["schedule_cargo"])
        self.assertEqual([], runner.calls)


class EscalationTests(RustVerifyFixture):
    def select(self, paths: list[tuple[str, str, str]]) -> dict:
        for origin, status, path in paths:
            if origin == "untracked":
                self.git.untracked.append(path)
            else:
                getattr(self.git, origin).append((status, path))
        change_set = rv.collect_change_set(self.root, self.git.base, self.git)
        return rv.select_packages(
            self.root, change_set, "affected-plus-neighbors", self.config, self.runner
        )

    def assert_escalates(self, paths: list[tuple[str, str, str]], reason: str) -> dict:
        selection = self.select(paths)
        reasons = [item["reason"] for item in selection["escalations"]]
        self.assertIn(reason, reasons)
        self.assertEqual("workspace", selection["effective_scope"])
        self.assertEqual(16, len(selection["packages"]))
        return selection

    def test_root_manifest_change_escalates(self) -> None:
        self.assert_escalates([("unstaged", "M", "Cargo.toml")],
                              "root-workspace-manifest-changed")

    def test_lockfile_change_escalates(self) -> None:
        self.assert_escalates([("unstaged", "M", "Cargo.lock")],
                              "workspace-lockfile-changed")

    def test_toolchain_change_escalates(self) -> None:
        self.assert_escalates([("unstaged", "M", "rust-toolchain.toml")],
                              "rust-toolchain-changed")

    def test_cargo_config_change_escalates(self) -> None:
        self.assert_escalates([("unstaged", "M", ".cargo/config.toml")],
                              "cargo-config-changed")

    def test_crate_remove_escalates(self) -> None:
        self.assert_escalates([("committed", "D", "crates/pkg-graph/Cargo.toml")],
                              "crate-add-or-remove")

    def test_crate_add_escalates(self) -> None:
        self.assert_escalates([("untracked", "?", "crates/pkg-new/Cargo.toml")],
                              "crate-add-or-remove")

    def test_unknown_rust_relevant_path_escalates(self) -> None:
        self.assert_escalates([("unstaged", "M", "crates/README.md")],
                              "unknown-rust-relevant-path")

    def test_half_workspace_escalates_with_reason(self) -> None:
        self.runner = FakeRunner(metadata=workspace_metadata(self.root, {
            "aa": [], "bb": [], "cc": [], "dd": [],
        }))
        selection = self.select([
            ("unstaged", "M", "crates/aa/src/lib.rs"),
            ("unstaged", "M", "crates/bb/src/lib.rs"),
        ])
        reasons = [item["reason"] for item in selection["escalations"]]
        self.assertIn("affected-set-reached-half-workspace", reasons)
        self.assertEqual("workspace", selection["effective_scope"])
        self.assertEqual(["aa", "bb", "cc", "dd"], selection["packages"])

    def test_metadata_failure_fails_closed(self) -> None:
        runner = FakeRunner(metadata="FAIL")
        self.patch("run_command", runner)
        self.create_task("task_meta_fail")
        self.change("crates/pkg-graph/src/lib.rs")
        code, _stdout, stderr = self.capture(
            rv.cmd_verify, self.verify_args("task_meta_fail", plan=True),
            root=self.root, ai=self.ai,
        )
        self.assertEqual(1, code)
        self.assertIn("cargo metadata failed", stderr)
        self.assertNotIn("Traceback", stderr)

    def test_invalid_metadata_json_fails_closed(self) -> None:
        runner = FakeRunner(metadata="INVALID")
        self.patch("run_command", runner)
        self.create_task("task_meta_bad")
        self.change("crates/pkg-graph/src/lib.rs")
        code, _stdout, stderr = self.capture(
            rv.cmd_verify, self.verify_args("task_meta_bad", plan=True),
            root=self.root, ai=self.ai,
        )
        self.assertEqual(1, code)
        self.assertIn("invalid JSON", stderr)

    def test_malformed_metadata_package_fails_closed(self) -> None:
        runner = FakeRunner(metadata={"packages": [{"name": "x"}]})
        self.patch("run_command", runner)
        with self.assertRaises(rv.MetadataError):
            rv.workspace_packages(self.root, {"packages": [{"name": "x"}]})


# ---------------------------------------------------------------------------
# Plan and run modes
# ---------------------------------------------------------------------------


class PlanModeTests(RustVerifyFixture):
    def test_plan_is_stable_json_with_no_cargo_and_no_lock(self) -> None:
        self.create_task("task_plan", targets=["**"])
        self.change("docs/guide.md")
        runner = mock.Mock(side_effect=AssertionError("plan must not invoke Cargo"))
        lock_ops = mock.Mock(side_effect=AssertionError("plan must not take the lock"))
        self.patch("run_command", runner)
        self.patch("lock_operations", lock_ops)
        first = self.capture(
            rv.cmd_verify, self.verify_args("task_plan", plan=True),
            root=self.root, ai=self.ai,
        )
        second = self.capture(
            rv.cmd_verify, self.verify_args("task_plan", plan=True),
            root=self.root, ai=self.ai,
        )
        self.assertEqual(0, first[0])
        self.assertEqual(first[1], second[1])  # stable output
        payload = json.loads(first[1])
        self.assertEqual(sorted(payload), list(payload))  # sorted keys
        self.assertFalse(payload["schedule_cargo"])
        runner.assert_not_called()
        lock_ops.assert_not_called()
        self.assertFalse(
            (self.ai / "tasks" / "queue" / "task_plan"
             / self.config["evidence"]["file_name"]).exists()
        )

    def test_plan_with_rust_changes_is_deterministic(self) -> None:
        self.create_task("task_plan_rust")
        self.change("crates/pkg-graph/src/lib.rs")
        first = self.capture(
            rv.cmd_verify, self.verify_args("task_plan_rust", plan=True),
            root=self.root, ai=self.ai,
        )
        second = self.capture(
            rv.cmd_verify, self.verify_args("task_plan_rust", plan=True),
            root=self.root, ai=self.ai,
        )
        self.assertEqual(first[1], second[1])
        payload = json.loads(first[1])
        self.assertTrue(payload["schedule_cargo"])
        self.assertEqual(
            ["pkg-graph", "pkg-render-core", "pkg-spatial"],
            payload["selection"]["packages"],
        )


class RunModeTests(RustVerifyFixture):
    def run_task(self, task_id: str = "task_run") -> tuple[int, str, str]:
        return self.capture(
            rv.cmd_verify, self.verify_args(task_id, plan=False),
            root=self.root, ai=self.ai,
        )

    def test_run_uses_argv_arrays_with_correct_flags(self) -> None:
        task_dir = self.create_task("task_run")
        self.change("crates/pkg-graph/src/lib.rs")
        code, stdout, _stderr = self.run_task()
        self.assertEqual(0, code)
        cargo_calls = [argv for argv, _cwd in self.runner.calls if argv[:1] == ["cargo"]
                       and argv[1] not in ("metadata", "--version")]
        self.assertEqual(3, len(cargo_calls))
        fmt, clippy, test = cargo_calls
        for argv in cargo_calls:
            self.assertIsInstance(argv, list)  # argv arrays, never shell strings
        # fmt: non-mutating --check, no --locked (fmt does not support it)
        self.assertEqual(["cargo", "fmt"], fmt[:2])
        self.assertIn("--check", fmt)
        self.assertNotIn("--locked", fmt)
        self.assertLess(fmt.index("--"), fmt.index("--check"))
        self.assertIn("--package", fmt)
        self.assertIn("pkg-graph", fmt)
        # clippy: one strict selected-package command with --locked and deny flags
        self.assertEqual(["cargo", "clippy"], clippy[:2])
        self.assertIn("--locked", clippy)
        self.assertEqual(["--", "-D", "warnings"], clippy[-3:])
        self.assertIn("--all-targets", clippy)
        # test: one selected-package command with --locked
        self.assertEqual(["cargo", "test"], test[:2])
        self.assertIn("--locked", test)
        self.assertIn("Passed", stdout.replace("passed", "Passed"))

    def test_run_writes_atomic_versioned_evidence_without_checkout_paths(self) -> None:
        task_dir = self.create_task("task_run")
        self.change("crates/pkg-graph/src/lib.rs")
        code, stdout, _stderr = self.run_task()
        self.assertEqual(0, code)
        evidence_file = task_dir / self.config["evidence"]["file_name"]
        self.assertTrue(evidence_file.exists())
        self.assertEqual([], list(task_dir.glob("*.tmp")))  # atomic temp + os.replace
        text = evidence_file.read_text(encoding="utf-8")
        self.assertNotIn(str(self.root), text)  # never a checkout path
        self.assertNotIn(str(self.root).replace("\\", "/"), text)
        record = json.loads(text)
        self.assertEqual(self.config["evidence"]["kind"], record["kind"])
        self.assertEqual(1, record["schema_version"])
        invocation = record["invocations"][0]
        self.assertEqual("run", invocation["mode"])
        self.assertEqual(0, invocation["result"]["exit_status"])
        self.assertEqual(3, len(invocation["commands"]))
        self.assertEqual("base0", invocation["base_commit"])
        self.assertEqual("head0", invocation["head_commit"])
        self.assertTrue(invocation["diff_fingerprint"])
        self.assertTrue(invocation["lock"]["name"].startswith("maw-rust-verify-"))
        self.assertIn("fixture", invocation["toolchain"]["rustc"])
        self.assertTrue(invocation["platform"]["os"])
        self.assertIn("duration_seconds", invocation["timing"])
        self.assertIn("Evidence:", stdout)
        self.assertTrue(rv.has_canonical_evidence(task_dir, self.config))

    def test_missing_cargo_fails_cleanly_and_records_unavailable_toolchain(self) -> None:
        task_dir = self.create_task("task_run")
        self.change("crates/pkg-graph/src/lib.rs")
        delegate = self.runner

        def missing_cargo(argv: list[str], cwd: Path) -> dict[str, object]:
            if argv == ["cargo", "--version"] or argv[:2] == ["cargo", "fmt"]:
                raise FileNotFoundError("cargo fixture is absent")
            return delegate(argv, cwd)

        self.patch("run_command", missing_cargo)
        code, _stdout, stderr = self.run_task()
        self.assertEqual(1, code)
        self.assertIn("Unable to launch 'cargo'", stderr)
        self.assertIn("PATH", stderr)
        self.assertNotIn("Traceback", stderr)
        invocation = self.read_evidence(task_dir)["invocations"][0]
        self.assertEqual("recorded-unavailable", invocation["toolchain"]["cargo"])
        self.assertNotEqual("unknown", invocation["toolchain"]["rustc"])
        self.assertEqual(1, invocation["result"]["exit_status"])

    def test_real_run_command_wraps_unlaunchable_binary_as_verify_error(self) -> None:
        with mock.patch.object(rv.subprocess, "run", side_effect=FileNotFoundError("absent")):
            with self.assertRaises(rv.VerifyError) as raised:
                REAL_RUN_COMMAND(["cargo", "--version"], self.root)
        self.assertIn("Unable to launch 'cargo'", str(raised.exception))
        self.assertIn("PATH", str(raised.exception))

    def test_written_evidence_scrubs_raw_slash_and_json_escaped_roots(self) -> None:
        task_dir = self.create_task("task_scrub", scope="control-plane", targets=["**"])
        raw = str(self.root)
        variants = [raw, raw.replace("\\", "/"), raw.replace("\\", "\\\\")]
        invocation = {"task_id": "task_scrub", "paths": variants}
        rv.append_invocation(task_dir, self.config, rv.scrub_invocation(invocation, self.root))
        written = (task_dir / self.config["evidence"]["file_name"]).read_text(encoding="utf-8")
        for variant in variants:
            self.assertNotIn(variant, written)
            self.assertNotIn(json.dumps(variant)[1:-1], written)

    def test_run_stops_at_first_failure_and_records_exit_status(self) -> None:
        task_dir = self.create_task("task_run")
        self.runner.exit_map = {("cargo", "fmt"): 1}
        self.change("crates/pkg-graph/src/lib.rs")
        code, _stdout, stderr = self.run_task()
        self.assertEqual(1, code)
        record = self.read_evidence(task_dir)
        invocation = record["invocations"][0]
        self.assertEqual(1, invocation["result"]["exit_status"])
        self.assertEqual(1, len(invocation["commands"]))  # clippy/test never ran
        self.assertEqual("fmt", invocation["commands"][0]["argv"][1])
        self.assertFalse(rv.has_canonical_evidence(task_dir, self.config))

    def test_docs_only_run_schedules_no_cargo_but_still_evidences(self) -> None:
        task_dir = self.create_task("task_run", targets=["**"])
        self.change("docs/guide.md")
        code, stdout, _stderr = self.run_task()
        self.assertEqual(0, code)
        self.assertIn("No Cargo verification scheduled", stdout)
        cargo_calls = [c for c in self.runner.calls if c[0][:1] == ["cargo"]
                       and c[0][1] != "--version"]
        self.assertEqual([], cargo_calls)
        record = self.read_evidence(task_dir)
        self.assertEqual(0, record["invocations"][0]["result"]["exit_status"])
        self.assertTrue(rv.has_canonical_evidence(task_dir, self.config))

    def test_generic_verifier_never_claims_visual_inspection(self) -> None:
        task_dir = self.create_task("task_run")
        self.change("crates/pkg-graph/src/lib.rs")
        code, _stdout, _stderr = self.run_task()
        self.assertEqual(0, code)
        text = (task_dir / self.config["evidence"]["file_name"]).read_text(encoding="utf-8")
        self.assertNotIn("visual", text.lower())

    def test_raw_cargo_output_cannot_satisfy_receipt_gate(self) -> None:
        task_dir = self.create_task("task_raw")
        (task_dir / "raw-cargo-output.txt").write_text(
            "test result: ok. 929 passed\n", encoding="utf-8"
        )
        self.assertFalse(rv.has_canonical_evidence(task_dir, self.config))


# ---------------------------------------------------------------------------
# Lock protocol
# ---------------------------------------------------------------------------


class LockTests(RustVerifyFixture):
    def make_lock(self, task_id: str = "task_lock") -> rv.VerificationLock:
        return rv.VerificationLock(self.root, self.config, task_id,
                                   ["ai", "verify", task_id])

    def test_platform_seam_selects_nt_and_posix_implementations(self) -> None:
        for name in ("nt", "posix"):
            self.patch("os_name", lambda n=name: n)
            lock = self.make_lock(f"task_{name}")
            lock.acquire(wait_seconds=0.2, poll_seconds=0.01)
            lock.release()
            self.assertIn(name, self.locks.names)
        self.assertEqual([], list(self.fake_temp.glob("*.meta.json")))

    def test_holder_metadata_and_heartbeat_refresh(self) -> None:
        config = json.loads(json.dumps(self.config))
        config["lock"]["heartbeat_seconds"] = 0.05
        lock = rv.VerificationLock(self.root, config, "task_heart", ["ai", "verify"])
        lock.acquire(wait_seconds=0.2, poll_seconds=0.01)
        try:
            first = lock.holder_metadata()
            self.assertEqual(os_getpid(), first["pid"])
            self.assertEqual("task_heart", first["task_id"])
            self.assertEqual(["ai", "verify"], first["argv"])
            self.assertTrue(first["started_utc"])
            time.sleep(0.15)
            second = lock.holder_metadata()
            self.assertGreater(second["heartbeat_utc"], first["heartbeat_utc"])
        finally:
            lock.release()

    def test_two_run_mode_processes_cannot_overlap(self) -> None:
        self.create_task("task_a")
        self.create_task("task_b")
        self.change("crates/pkg-graph/src/lib.rs")
        runner = FakeRunner(metadata=fixture_graph(self.root), delay=0.05)
        self.patch("run_command", runner)
        outcomes: dict[str, int] = {}

        def run(task_id: str) -> None:
            try:
                rv.cmd_verify(self.verify_args(task_id, plan=False),
                              root=self.root, ai=self.ai)
                outcomes[task_id] = 0
            except SystemExit as error:
                outcomes[task_id] = int(error.code or 0)

        thread_a = threading.Thread(target=run, args=("task_a",))
        thread_b = threading.Thread(target=run, args=("task_b",))
        thread_a.start()
        time.sleep(0.05)  # A is inside the lock before B contends
        thread_b.start()
        thread_a.join(timeout=30)
        thread_b.join(timeout=30)
        self.assertEqual({"task_a": 0, "task_b": 0}, outcomes)
        kinds = [kind for kind, _name, _ts in self.locks.log]
        # Strict serialization: acquire, release, acquire, release — never two holds.
        self.assertEqual(["acquire", "release", "acquire", "release"], kinds)

    def test_waiter_reports_holder_metadata_and_heartbeat(self) -> None:
        holder = self.make_lock("task_holder")
        holder.acquire(wait_seconds=0.2, poll_seconds=0.01)
        waiter_seen = io.StringIO()
        acquired_after_release: list[float] = []

        def wait() -> None:
            waiter = self.make_lock("task_waiter")
            with contextlib.redirect_stderr(waiter_seen):
                waiter.acquire(wait_seconds=5, poll_seconds=0.02)
            acquired_after_release.append(time.monotonic())
            waiter.release()

        thread = threading.Thread(target=wait)
        thread.start()
        time.sleep(0.15)  # let the waiter observe the holder, then release
        holder.heartbeat()
        released_at = time.monotonic()
        holder.release()
        thread.join(timeout=10)
        report = waiter_seen.getvalue()
        self.assertIn("task_holder", report)
        self.assertIn("heartbeat=", report)
        self.assertIn("pid=", report)
        self.assertTrue(acquired_after_release)
        self.assertGreaterEqual(acquired_after_release[0], released_at - 0.5)

    def test_lock_timeout_fails_closed_with_holder_details(self) -> None:
        holder = self.make_lock("task_owner")
        holder.acquire(wait_seconds=0.2, poll_seconds=0.01)
        try:
            waiter = self.make_lock("task_late")
            with self.assertRaises(rv.LockError):
                waiter.acquire(wait_seconds=0.05, poll_seconds=0.01)
        finally:
            holder.release()


def os_getpid() -> int:
    import os

    return os.getpid()


# ---------------------------------------------------------------------------
# ai cargo wrapper
# ---------------------------------------------------------------------------


class CargoWrapperTests(RustVerifyFixture):
    def cargo_args(self, task_id: str, argv: list[str], label: str | None = None):
        return argparse.Namespace(
            task_id=task_id, base=self.git.base, label=label, cargo_argv=argv
        )

    def test_wrapper_preserves_arbitrary_argv_and_label(self) -> None:
        task_dir = self.create_task("task_wrap")
        self.change("crates/pkg-graph/src/lib.rs")
        code, _stdout, _stderr = self.capture(
            rv.cmd_cargo,
            self.cargo_args("task_wrap",
                            ["--", "test", "-p", "pkg-graph", "--test", "adversarial", "--", "--exact"],
                            label="pre-adversarial"),
            root=self.root, ai=self.ai,
        )
        self.assertEqual(0, code)
        record = self.read_evidence(task_dir)
        invocation = record["invocations"][0]
        self.assertEqual("cargo", invocation["mode"])
        self.assertEqual("pre-adversarial", invocation["label"])
        self.assertEqual(
            ["cargo", "test", "-p", "pkg-graph", "--test", "adversarial", "--", "--exact"],
            invocation["commands"][0]["argv"],
        )
        # Seam-level boundary check: the executed argv[0] is the cargo executable.
        self.assertTrue(self.runner.calls)
        self.assertEqual("cargo", self.runner.calls[-1][0][0])
        self.assertTrue(self.locks.log)  # shares the verification lock

    def test_wrapper_rejects_clean(self) -> None:
        self.create_task("task_clean")
        lock_ops = mock.Mock(side_effect=AssertionError("clean must never reach the lock"))
        self.patch("lock_operations", lock_ops)
        code, _stdout, stderr = self.capture(
            rv.cmd_cargo, self.cargo_args("task_clean", ["--", "clean"]),
            root=self.root, ai=self.ai,
        )
        self.assertEqual(1, code)
        self.assertIn("rejects `clean`", stderr)
        lock_ops.assert_not_called()
        self.assertEqual([], self.runner.calls)

    def test_wrapper_requires_argv_after_double_dash(self) -> None:
        self.create_task("task_empty")
        code, _stdout, stderr = self.capture(
            rv.cmd_cargo, self.cargo_args("task_empty", ["--"]),
            root=self.root, ai=self.ai,
        )
        self.assertEqual(1, code)
        self.assertIn("requires a cargo argv", stderr)

    def test_wrapper_appends_to_same_evidence_record_as_verify(self) -> None:
        task_dir = self.create_task("task_shared")
        self.change("crates/pkg-graph/src/lib.rs")
        code, _stdout, _stderr = self.capture(
            rv.cmd_verify, self.verify_args("task_shared", plan=False),
            root=self.root, ai=self.ai,
        )
        self.assertEqual(0, code)
        code, _stdout, _stderr = self.capture(
            rv.cmd_cargo, self.cargo_args("task_shared", ["--", "check", "--locked"]),
            root=self.root, ai=self.ai,
        )
        self.assertEqual(0, code)
        record = self.read_evidence(task_dir)
        self.assertEqual(["run", "cargo"],
                         [inv["mode"] for inv in record["invocations"]])
        self.assertEqual(["cargo", "check", "--locked"],
                         record["invocations"][1]["commands"][0]["argv"])
        self.assertTrue(rv.has_canonical_evidence(task_dir, self.config))

    def test_wrapper_propagates_cargo_exit_status(self) -> None:
        self.create_task("task_fail")
        self.runner.exit_map = {("cargo", "check"): 23}
        self.change("crates/pkg-graph/src/lib.rs")
        code, _stdout, _stderr = self.capture(
            rv.cmd_cargo, self.cargo_args("task_fail", ["--", "check"]),
            root=self.root, ai=self.ai,
        )
        self.assertEqual(23, code)

    def test_wrapper_rejects_clean_behind_toolchain_or_global_flags(self) -> None:
        self.create_task("task_sneaky_clean")
        lock_ops = mock.Mock(side_effect=AssertionError("clean must never reach the lock"))
        self.patch("lock_operations", lock_ops)
        for argv in (["--", "+nightly", "clean"], ["--", "--locked", "clean"]):
            code, _stdout, stderr = self.capture(
                rv.cmd_cargo, self.cargo_args("task_sneaky_clean", argv),
                root=self.root, ai=self.ai,
            )
            self.assertEqual(1, code)
            self.assertIn("rejects `clean`", stderr)
        lock_ops.assert_not_called()
        self.assertEqual([], self.runner.calls)


# ---------------------------------------------------------------------------
# Cache lifecycle
# ---------------------------------------------------------------------------


class CacheInspectTests(RustVerifyFixture):
    def build_target_tree(self) -> None:
        target = self.root / "target" / "debug"
        target.mkdir(parents=True)
        (target / "libx.rlib").write_bytes(b"x" * 100)
        (self.root / "target" / "CACHEDIR.TAG").write_bytes(b"tag")
        nested = self.root / "crates" / "pkg-graph" / "target"
        nested.mkdir(parents=True)
        (nested / "stray.bin").write_bytes(b"y" * 10)

    def test_inspect_is_read_only_and_reports_all_sections(self) -> None:
        self.build_target_tree()
        valid = rv.create_scratch_root(self.root, self.config, "fixture scratch")
        malformed = self.fake_temp / "maw-rust-scratch-bad"
        malformed.mkdir()
        (malformed / "maw-rust-scratch.json").write_text("not json", encoding="utf-8")
        before = digest_files(list((self.root / "target").rglob("*")))
        code, stdout, _stderr = self.capture(
            rv.cmd_cargo_cache,
            argparse.Namespace(cargo_cache_command="inspect"),
            root=self.root, ai=self.ai,
        )
        self.assertEqual(0, code)
        report = json.loads(stdout)
        self.assertEqual("maw-rust-cache-inspect", report["kind"])
        self.assertTrue(report["project_target"]["exists"])
        self.assertGreaterEqual(report["project_target"]["total_bytes"], 103)
        contributors = {c["path"] for c in report["project_target"]["contributors"]}
        self.assertIn("target/debug", contributors)
        self.assertEqual(["crates/pkg-graph/target"], report["nested_target_dirs"])
        scratch = {s["name"]: s for s in report["scratch_roots"]}
        self.assertTrue(scratch[valid.name]["valid"])
        self.assertFalse(scratch[malformed.name]["valid"])
        self.assertFalse(report["lock"]["held"])
        # Read-only: nothing changed, nothing deleted.
        after = digest_files(list((self.root / "target").rglob("*")))
        self.assertEqual(before, after)
        self.assertTrue(valid.exists())
        self.assertTrue(malformed.exists())

    def test_inspect_reports_lock_state_with_holder_metadata(self) -> None:
        holder = rv.VerificationLock(self.root, self.config, "task_busy", ["ai", "verify"])
        holder.acquire(wait_seconds=0.2, poll_seconds=0.01)
        try:
            code, stdout, _stderr = self.capture(
                rv.cmd_cargo_cache,
                argparse.Namespace(cargo_cache_command="inspect"),
                root=self.root, ai=self.ai,
            )
            self.assertEqual(0, code)
            report = json.loads(stdout)
            self.assertTrue(report["lock"]["held"])
            self.assertEqual("task_busy", report["lock"]["holder"]["task_id"])
        finally:
            holder.release()


class ScratchCleanTests(RustVerifyFixture):
    def clean_args(self, yes: bool = True):
        return argparse.Namespace(
            cargo_cache_command="clean", scratch=True, workspace=False, yes=yes
        )

    def test_requires_yes(self) -> None:
        valid = rv.create_scratch_root(self.root, self.config, "fixture")
        code, _stdout, stderr = self.capture(
            rv.cmd_cargo_cache, self.clean_args(yes=False), root=self.root, ai=self.ai
        )
        self.assertEqual(1, code)
        self.assertIn("--yes", stderr)
        self.assertTrue(valid.exists())

    def test_deletes_only_marker_owned_roots_and_rejects_hostile_fixtures(self) -> None:
        valid = rv.create_scratch_root(self.root, self.config, "fixture")
        (valid / "payload.bin").write_bytes(b"z" * 5)
        malformed = self.fake_temp / "maw-rust-scratch-mal"
        malformed.mkdir()
        (malformed / "maw-rust-scratch.json").write_text("{oops", encoding="utf-8")
        foreign = self.fake_temp / "maw-rust-scratch-foreign"
        foreign.mkdir()
        (foreign / "maw-rust-scratch.json").write_text(json.dumps({
            "schema_version": 1,
            "repo_key_sha256": "0" * 64,
        }), encoding="utf-8")
        unmarked = self.fake_temp / "maw-rust-scratch-unmarked"
        unmarked.mkdir()
        bystander = self.fake_temp / "unrelated-dir"
        bystander.mkdir()
        (bystander / "keep.txt").write_text("keep", encoding="utf-8")

        code, stdout, _stderr = self.capture(
            rv.cmd_cargo_cache, self.clean_args(), root=self.root, ai=self.ai
        )
        self.assertEqual(0, code)
        result = json.loads(stdout)
        self.assertEqual([valid.name], result["deleted"])
        rejected = {item["name"]: item["reason"] for item in result["rejected"]}
        self.assertEqual("malformed-marker", rejected[malformed.name])
        self.assertEqual("foreign-marker", rejected[foreign.name])
        self.assertEqual("unmarked", rejected[unmarked.name])
        self.assertFalse(valid.exists())
        self.assertTrue(malformed.exists())
        self.assertTrue(foreign.exists())
        self.assertTrue(unmarked.exists())
        self.assertTrue((bystander / "keep.txt").exists())

    def test_hostile_candidate_validation_categories(self) -> None:
        config = self.config
        # root: the temp root itself is never a candidate
        ok, reason = rv.validate_scratch_candidate(
            self.fake_temp, self.fake_temp, config, self.root)
        self.assertEqual((False, "root"), (ok, reason))
        # out-of-prefix: resolves outside the temp dir
        outside = self.root / "elsewhere"
        outside.mkdir()
        ok, reason = rv.validate_scratch_candidate(outside, self.fake_temp, config, self.root)
        self.assertEqual((False, "out-of-prefix"), (ok, reason))
        # current-dir: never delete the process working directory or its ancestor
        cwd_candidate = self.fake_temp / "maw-rust-scratch-cwd"
        cwd_candidate.mkdir()
        self.patch("getcwd", lambda: cwd_candidate)
        ok, reason = rv.validate_scratch_candidate(
            cwd_candidate, self.fake_temp, config, self.root)
        self.assertEqual((False, "current-dir"), (ok, reason))
        # symlink/reparse point
        link_candidate = self.fake_temp / "maw-rust-scratch-link"
        link_candidate.mkdir()
        self.patch("is_link_or_reparse", lambda _path: True)
        ok, reason = rv.validate_scratch_candidate(
            link_candidate, self.fake_temp, config, self.root)
        self.assertEqual((False, "symlink-or-reparse-point"), (ok, reason))

    def test_scratch_clean_refuses_while_unwrapped_cargo_is_active(self) -> None:
        valid = rv.create_scratch_root(self.root, self.config, "fixture")
        self.patch("process_listing", lambda _name: '"cargo.exe","4242"\r\n')
        code, _stdout, stderr = self.capture(
            rv.cmd_cargo_cache, self.clean_args(), root=self.root, ai=self.ai
        )
        self.assertEqual(1, code)
        self.assertIn("unwrapped cargo/rustc", stderr)
        self.assertTrue(valid.exists())

    def test_process_listing_failure_refuses_cleanup_before_deletion_path(self) -> None:
        valid = rv.create_scratch_root(self.root, self.config, "fixture")
        unavailable = rv.ProcessActiveError(
            "process-listing-unavailable: fixture process scan failed"
        )
        self.patch("process_listing", mock.Mock(side_effect=unavailable))
        validate = mock.Mock(side_effect=AssertionError("deletion path must not execute"))
        self.patch("validate_scratch_candidate", validate)
        code, _stdout, stderr = self.capture(
            rv.cmd_cargo_cache, self.clean_args(), root=self.root, ai=self.ai
        )
        self.assertEqual(1, code)
        self.assertIn("process-listing-unavailable", stderr)
        validate.assert_not_called()
        self.assertTrue(valid.exists())

    def test_real_process_listing_failure_has_named_fail_closed_reason(self) -> None:
        with mock.patch.object(rv.subprocess, "run", side_effect=OSError("scan absent")):
            with self.assertRaises(rv.ProcessActiveError) as raised:
                REAL_PROCESS_LISTING("nt")
        self.assertIn("process-listing-unavailable", str(raised.exception))

    def test_process_scan_parses_windows_and_posix_listings(self) -> None:
        windows = '"cargo.exe","1234"\r\n"rustc.exe","1235"\r\n"python.exe","9"\r\n'
        posix = "cargo\nrustc\npython3\n"
        self.assertEqual(["cargo.exe", "rustc.exe"], rv.detect_unwrapped_cargo(windows))
        self.assertEqual(["cargo", "rustc"], rv.detect_unwrapped_cargo(posix))
        self.assertEqual([], rv.detect_unwrapped_cargo('"msedge.exe","1"\r\n'))


class WorkspaceCleanTests(RustVerifyFixture):
    def clean_args(self, yes: bool = True):
        return argparse.Namespace(
            cargo_cache_command="clean", scratch=False, workspace=True, yes=yes
        )

    def test_requires_yes(self) -> None:
        code, _stdout, stderr = self.capture(
            rv.cmd_cargo_cache, self.clean_args(yes=False), root=self.root, ai=self.ai
        )
        self.assertEqual(1, code)
        self.assertIn("--yes", stderr)
        self.assertEqual([], self.runner.calls)

    def test_refuses_while_another_process_owns_the_lock(self) -> None:
        holder = rv.VerificationLock(self.root, self.config, "task_busy", ["ai", "verify"])
        holder.acquire(wait_seconds=0.2, poll_seconds=0.01)
        try:
            code, _stdout, stderr = self.capture(
                rv.cmd_cargo_cache, self.clean_args(), root=self.root, ai=self.ai
            )
            self.assertEqual(1, code)
            self.assertIn("lock", stderr)
            self.assertEqual([], self.runner.calls)
        finally:
            holder.release()

    def test_refuses_while_unwrapped_cargo_is_active(self) -> None:
        self.patch("process_listing", lambda _name: "rustc\n")
        code, _stdout, stderr = self.capture(
            rv.cmd_cargo_cache, self.clean_args(), root=self.root, ai=self.ai
        )
        self.assertEqual(1, code)
        self.assertIn("unwrapped cargo/rustc", stderr)
        self.assertEqual([], self.runner.calls)

    def test_refuses_when_canonical_manifest_invalid(self) -> None:
        (self.root / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
        code, _stdout, stderr = self.capture(
            rv.cmd_cargo_cache, self.clean_args(), root=self.root, ai=self.ai
        )
        self.assertEqual(1, code)
        self.assertIn("manifest", stderr)
        self.assertEqual([], self.runner.calls)

    def test_invokes_cargo_clean_on_canonical_manifest_while_holding_lock(self) -> None:
        held_during_clean: list[bool] = []

        def on_call(argv, _cwd) -> None:
            if argv[:2] == ["cargo", "clean"]:
                held_during_clean.append(bool(self.locks.held))

        self.runner.on_call = on_call
        code, _stdout, stderr = self.capture(
            rv.cmd_cargo_cache, self.clean_args(), root=self.root, ai=self.ai
        )
        self.assertEqual(0, code, stderr)
        clean_calls = [argv for argv, cwd in self.runner.calls if argv[:2] == ["cargo", "clean"]]
        self.assertEqual(
            [["cargo", "clean", "--manifest-path", "Cargo.toml"]], clean_calls
        )
        self.assertEqual([True], held_during_clean)  # own lock retained through clean
        self.assertFalse(self.locks.held)  # released afterward
        log_path = self.ai / "project" / "rust-verification-cache-log.json"
        log = json.loads(log_path.read_text(encoding="utf-8"))
        entry = log["entries"][0]
        self.assertEqual("clean-workspace", entry["action"])
        self.assertEqual(0, entry["exit_status"])
        self.assertNotIn(str(self.root), json.dumps(entry))


if __name__ == "__main__":
    unittest.main()
