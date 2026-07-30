"""The installer.

`install.py` is the first thing an adopter runs and the only shipped code that deletes files in
someone else's repository. These tests pin the properties that make that safe: a dry run writes
nothing, an install never silently overwrites, and an uninstall removes only files it recorded
placing and that nobody has edited since.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("plane_install", ROOT / "install.py")
install = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(install)


def args(**overrides) -> argparse.Namespace:
    base = {
        "update": False, "uninstall": False, "dry_run": False,
        "force": False, "with_tests": False, "include_generated": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def run(fn, target: Path, ns: argparse.Namespace) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = fn(target, ns)
    return code, out.getvalue() + err.getvalue()


class InstallerFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.target = Path(self.tmp.name)
        (self.target / ".git").mkdir()
        (self.target / "README.md").write_text("# their project\n", encoding="utf-8")
        (self.target / "src").mkdir()
        (self.target / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")

    def files(self) -> set[str]:
        return {
            p.relative_to(self.target).as_posix()
            for p in self.target.rglob("*")
            if p.is_file() and ".git" not in p.parts
        }

    def their_files(self) -> set[str]:
        return {"README.md", "src/app.py"}


class DryRunTests(InstallerFixture):
    def test_dry_run_writes_absolutely_nothing(self) -> None:
        before = self.files()
        code, output = run(install.do_install, self.target, args(dry_run=True))
        self.assertEqual(0, code)
        self.assertIn("Nothing was written", output)
        self.assertEqual(before, self.files())


class InstallTests(InstallerFixture):
    def test_install_places_the_payload_and_records_a_manifest(self) -> None:
        code, _ = run(install.do_install, self.target, args())
        self.assertEqual(0, code)
        placed = self.files()
        self.assertIn("scripts/ai_cli.py", placed)
        self.assertIn(".ai/config.yaml", placed)
        self.assertIn(".ai/rules/task-contracts.md", placed)
        self.assertTrue(self.their_files() <= placed, "the adopter's own files must survive")

        manifest = json.loads((self.target / install.MANIFEST_REL).read_text(encoding="utf-8"))
        self.assertEqual(install.MANIFEST_SCHEMA, manifest["schema_version"])
        self.assertIn("scripts/ai_cli.py", manifest["files"])

    def test_generated_adapters_are_not_installed(self) -> None:
        # `ai sync` produces these from `.ai/`. Shipping them too would install two copies of the
        # same truth, and the copies would drift.
        run(install.do_install, self.target, args())
        for name in ("AGENTS.md", "CLAUDE.md", "GEMINI.md"):
            self.assertNotIn(name, self.files())

    def test_the_plane_s_own_tests_are_opt_in(self) -> None:
        run(install.do_install, self.target, args())
        self.assertFalse(any(p.startswith("scripts/tests/") for p in self.files()))

    def test_an_unrelated_existing_file_blocks_the_install(self) -> None:
        squatter = self.target / ".ai" / "rules" / "task-contracts.md"
        squatter.parent.mkdir(parents=True)
        squatter.write_text("our own rule\n", encoding="utf-8")

        code, output = run(install.do_install, self.target, args())
        self.assertEqual(1, code)
        self.assertIn("Refusing to overwrite", output)
        self.assertEqual("our own rule\n", squatter.read_text(encoding="utf-8"))

    def test_force_overwrites_a_conflict(self) -> None:
        squatter = self.target / ".ai" / "rules" / "task-contracts.md"
        squatter.parent.mkdir(parents=True)
        squatter.write_text("our own rule\n", encoding="utf-8")
        code, _ = run(install.do_install, self.target, args(force=True))
        self.assertEqual(0, code)
        self.assertNotEqual("our own rule\n", squatter.read_text(encoding="utf-8"))

    def test_reinstall_recognises_its_own_manifest(self) -> None:
        """A second plain run must not report every file it placed as a conflict."""
        run(install.do_install, self.target, args())
        code, output = run(install.do_install, self.target, args())
        self.assertEqual(0, code)
        self.assertIn("Already installed", output)
        self.assertNotIn("Refusing to overwrite", output)

    def test_update_refreshes_the_plane_but_keeps_captured_memory(self) -> None:
        run(install.do_install, self.target, args())
        memory = self.target / ".ai" / "memory" / "gotchas.md"
        memory.write_text("# Gotchas\n\nOur hard-won note.\n", encoding="utf-8")

        code, _ = run(install.do_install, self.target, args(update=True))
        self.assertEqual(0, code)
        self.assertIn("Our hard-won note.", memory.read_text(encoding="utf-8"))


class UninstallTests(InstallerFixture):
    def test_uninstall_without_a_manifest_refuses_and_removes_nothing(self) -> None:
        before = self.files()
        code, output = run(install.do_uninstall, self.target, args(uninstall=True))
        self.assertEqual(1, code)
        self.assertIn("No install manifest", output)
        self.assertEqual(before, self.files())

    def test_uninstall_leaves_the_adopter_s_own_files_untouched(self) -> None:
        run(install.do_install, self.target, args())
        run(install.do_uninstall, self.target, args(uninstall=True))
        self.assertTrue(self.their_files() <= self.files())

    def test_a_file_edited_since_install_is_never_deleted(self) -> None:
        run(install.do_install, self.target, args())
        edited = self.target / ".ai" / "rules" / "task-contracts.md"
        edited.write_text(edited.read_text(encoding="utf-8") + "\n## our addition\n", encoding="utf-8")

        code, output = run(install.do_uninstall, self.target, args(uninstall=True))
        self.assertEqual(0, code)
        self.assertTrue(edited.is_file(), "an edited file must survive uninstall")
        self.assertIn("our addition", edited.read_text(encoding="utf-8"))
        self.assertIn("modified since install", output)

    def test_work_created_after_install_survives(self) -> None:
        run(install.do_install, self.target, args())
        task = self.target / ".ai" / "tasks" / "queue" / "task_01_ours" / "task.yaml"
        task.parent.mkdir(parents=True)
        task.write_text('id: "task_01_ours"\n', encoding="utf-8")

        run(install.do_uninstall, self.target, args(uninstall=True))
        self.assertTrue(task.is_file(), "a task the adopter authored is not ours to delete")

    def test_uninstall_dry_run_writes_nothing(self) -> None:
        run(install.do_install, self.target, args())
        before = self.files()
        code, output = run(install.do_uninstall, self.target, args(uninstall=True, dry_run=True))
        self.assertEqual(0, code)
        self.assertIn("Nothing was written", output)
        self.assertEqual(before, self.files())


class GeneratedAdapterRemovalTests(InstallerFixture):
    def write_plane_manifest(self, entries: dict[str, str]) -> None:
        payload = {
            "schema_version": 1,
            "entries": [
                {"path": rel, "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                 "command": "ai sync"}
                for rel, body in entries.items()
            ],
        }
        for rel, body in entries.items():
            path = self.target / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            # write_bytes, not write_text: on Windows text mode rewrites \n as \r\n, so the file on
            # disk would not hash to the digest computed from the string above.
            path.write_bytes(body.encode("utf-8"))
        (self.target / ".ai").mkdir(exist_ok=True)
        (self.target / ".ai" / "_manifest.json").write_text(
            json.dumps(payload), encoding="utf-8")

    def test_unmodified_generated_adapters_are_removed(self) -> None:
        self.write_plane_manifest({"AGENTS.md": "generated\n", ".agents/rules/x.md": "generated\n"})
        removed, kept = install.remove_generated(self.target)
        self.assertIn("AGENTS.md", removed)
        self.assertIn(".agents/rules/x.md", removed)
        self.assertEqual([], kept)
        self.assertFalse((self.target / "AGENTS.md").exists())

    def test_a_hand_edited_generated_adapter_is_kept(self) -> None:
        self.write_plane_manifest({"AGENTS.md": "generated\n"})
        (self.target / "AGENTS.md").write_bytes(b"generated\nplus my edit\n")
        removed, kept = install.remove_generated(self.target)
        self.assertEqual(["AGENTS.md"], kept)
        self.assertNotIn("AGENTS.md", removed)
        self.assertTrue((self.target / "AGENTS.md").is_file())

    def test_no_plane_manifest_means_nothing_is_removed(self) -> None:
        (self.target / "AGENTS.md").write_text("not ours\n", encoding="utf-8")
        removed, kept = install.remove_generated(self.target)
        self.assertEqual(([], []), (removed, kept))
        self.assertTrue((self.target / "AGENTS.md").is_file())


class WriteBoundaryTests(unittest.TestCase):
    def test_the_payload_never_escapes_the_target(self) -> None:
        for rel in install.payload(include_tests=True):
            with self.subTest(path=rel):
                self.assertFalse(rel.startswith(("/", "\\")), rel)
                self.assertNotIn("..", Path(rel).parts, rel)
                self.assertFalse(Path(rel).is_absolute(), rel)

    def test_installing_into_the_source_repository_is_refused(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = install.main([str(install.SOURCE)])
        self.assertEqual(1, code)
        self.assertIn("Choose the repository you want to install INTO", out.getvalue() + err.getvalue())


if __name__ == "__main__":
    unittest.main()
