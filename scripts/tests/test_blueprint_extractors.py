from __future__ import annotations

import argparse
import hashlib
import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
RENDERER_DIR = ROOT / ".ai" / "templates" / "pr-blueprint" / "renderer"
if str(RENDERER_DIR) not in sys.path:
    sys.path.insert(0, str(RENDERER_DIR))

from build_report import build as build_report
from parse_spec import parse_spec
from validate_spec import validate_spec

from scripts.ai_plane import blueprint
from scripts.ai_plane.primitives import parse_simple_yaml


class BlueprintTaskExtractorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.task_dir = self.root / ".ai" / "tasks" / "queue" / "task_900_example"
        self.task_dir.mkdir(parents=True)
        self.task_text = """id: \"task_900_example\"
title: \"Example Task Extractor\"
input_contract: >
  Read canonical input facts.
output_contract: >
  Write deterministic output facts.
acceptance_tests:
  - \"First acceptance row.\"
  - \"Second acceptance row.\"
known_risks:
  - \"Fact drift.\"
"""
        (self.task_dir / "task.yaml").write_text(self.task_text, encoding="utf-8")

    def fake_git(self, *, diff: bytes = b"scripts/z.py\0scripts/a.py\0", untracked: bytes = b"docs/new.md\0"):
        calls: list[tuple[list[str], dict[str, object]]] = []

        def run(argv, **kwargs):
            calls.append((list(argv), dict(kwargs)))
            payload = diff if argv[1] == "diff" else untracked
            return subprocess.CompletedProcess(argv, 0, stdout=payload, stderr=b"")

        return run, calls

    def test_exact_task_resolution_and_deterministic_candidates(self) -> None:
        task, data, candidates = blueprint.resolve_task_source("task_900_example", self.root)
        self.assertEqual(self.task_dir, task)
        self.assertEqual("Example Task Extractor", data["title"])
        self.assertEqual([], candidates)

        task, data, candidates = blueprint.resolve_task_source("task_900_missing", self.root)
        self.assertIsNone(task)
        self.assertEqual({}, data)
        self.assertEqual(["task_900_example"], candidates)

    def test_maps_contract_receipts_and_git_inventory_then_builds(self) -> None:
        (self.task_dir / "receipt.executor.yaml").write_text(
            "status: completed\ntool: Codex\nbase_commit: abc123\ntest_result: pass\nchanged_files:\n  - scripts/a.py\n",
            encoding="utf-8",
        )
        (self.task_dir / "receipt.qa.yaml").write_text(
            "decision: accept\nreview_round: 2\nreviewer: Reviewer\n",
            encoding="utf-8",
        )
        task = parse_simple_yaml(self.task_dir / "task.yaml")
        run, calls = self.fake_git()
        before = self.folder_digest(self.task_dir)
        first = blueprint.task_blueprint_spec(
            self.task_dir, task, preset="backend", kind="tool", base="HEAD", root=self.root, run=run
        )
        second = blueprint.task_blueprint_spec(
            self.task_dir, task, preset="backend", kind="tool", base="HEAD", root=self.root, run=run
        )
        self.assertEqual(first, second)
        self.assertEqual(before, self.folder_digest(self.task_dir), "extraction mutated its source task folder")
        self.assertIn("<!-- ai:source task.yaml#acceptance_tests -->", first)
        self.assertIn("<!-- ai:source receipt.executor.yaml -->", first)
        self.assertIn("<!-- ai:source receipt.qa.yaml -->", first)
        self.assertLess(first.index("- docs/new.md"), first.index("- scripts/a.py"))
        self.assertLess(first.index("- scripts/a.py"), first.index("- scripts/z.py"))

        spec_path = self.root / "example.spec.md"
        out_path = self.root / "example.html"
        spec_path.write_text(first, encoding="utf-8")
        spec, parse_errors = parse_spec(spec_path)
        errors, _warnings, _preset = validate_spec(spec, ROOT / ".ai" / "templates" / "pr-blueprint" / "presets", parse_errors)
        self.assertEqual([], errors)
        self.assertEqual("Example Task Extractor", spec["metadata"]["title"])
        self.assertEqual("Codex", spec["execution_summary"].split("Tool: ", 1)[1].splitlines()[0])
        self.assertNotIn("ai:source", spec["overview"])
        result, sections, _warnings = build_report(spec_path, out_path)
        html = result.read_text(encoding="utf-8")
        self.assertIn("Execution Summary", html)
        self.assertIn("File Inventory", html)
        self.assertNotIn("ai:source", html)
        self.assertNotIn("ai:needs-judgment", html)
        self.assertIn("execution-summary", sections)
        self.assertIn("file-inventory", sections)

        for argv, kwargs in calls:
            self.assertIsInstance(argv, list)
            self.assertNotIn("shell", kwargs)
            self.assertEqual(self.root, kwargs["cwd"])
        self.assertEqual(["git", "diff", "--no-ext-diff", "--no-textconv", "--name-only", "-z", "HEAD", "--"], calls[0][0])
        self.assertEqual(["git", "ls-files", "--others", "--exclude-standard", "-z"], calls[1][0])

    def test_missing_receipts_base_and_fields_are_explicit_not_fabricated(self) -> None:
        minimal = {"id": "task_900_example", "title": "Example Task Extractor"}
        first = blueprint.task_blueprint_spec(
            self.task_dir, minimal, preset="api-only", kind="tool", base=None, root=self.root
        )
        second = blueprint.task_blueprint_spec(
            self.task_dir, minimal, preset="api-only", kind="tool", base=None, root=self.root
        )
        self.assertEqual(first, second)
        self.assertIn('<!-- ai:needs-judgment reason="receipt.executor.yaml is absent" -->', first)
        self.assertIn('<!-- ai:needs-judgment reason="receipt.qa.yaml is absent" -->', first)
        self.assertIn('<!-- ai:needs-judgment reason="--base is absent" -->', first)
        self.assertIn("No acceptance tests were present", first)
        self.assertIn("No known risks were present", first)
        self.assertNotIn("decision: accept", first.lower())

    def test_empty_diff_inventory_is_a_judgment_marker(self) -> None:
        task = parse_simple_yaml(self.task_dir / "task.yaml")
        run, _calls = self.fake_git(diff=b"", untracked=b"")
        spec = blueprint.task_blueprint_spec(
            self.task_dir, task, preset="tool", kind="tool", base="abc123", root=self.root, run=run
        )
        self.assertIn('reason="the base-to-working-tree inventory is empty"', spec)
        self.assertIn("The base-to-working-tree file inventory is empty.", spec)

    def test_invalid_git_base_fails_before_write(self) -> None:
        def failing_run(argv, **_kwargs):
            return subprocess.CompletedProcess(argv, 128, stdout=b"", stderr=b"bad revision")

        with self.assertRaisesRegex(ValueError, "bad revision"):
            blueprint.git_name_inventory("missing", root=self.root, run=failing_run)
        with self.assertRaisesRegex(ValueError, "not an option"):
            blueprint.git_name_inventory("--output=outside.txt", root=self.root, run=failing_run)

    def test_output_path_is_confined_to_repository(self) -> None:
        output = blueprint.blueprint_output_path("Example Task", self.root)
        self.assertEqual(self.root / "docs" / "blueprints" / "example_task.spec.md", output)
        self.assertTrue(output.resolve().is_relative_to(self.root.resolve()))

    def test_unknown_cli_task_returns_guidance_and_writes_nothing(self) -> None:
        args = argparse.Namespace(
            feature=None,
            from_task="task_900_missing",
            base=None,
            preset="backend",
            kind="tool",
            force=False,
        )
        output = io.StringIO()
        with mock.patch.object(blueprint.constants, "ROOT", self.root), redirect_stdout(output):
            blueprint.cmd_blueprint_init(args)
        self.assertIn("Task not found: task_900_missing", output.getvalue())
        self.assertIn("task_900_example", output.getvalue())
        self.assertFalse((self.root / "docs").exists())

    def test_marker_validation_and_semantic_stripping(self) -> None:
        valid = blueprint.blueprint_spec_template(
            "Marker Test",
            "api-only",
            "tool",
            section_bodies={"overview": "Machine fact."},
            section_markers={"overview": ["<!-- ai:source task.yaml#title -->"]},
        )
        valid_path = self.root / "valid.spec.md"
        valid_path.write_text(valid, encoding="utf-8")
        spec, errors = parse_spec(valid_path)
        self.assertEqual([], errors)
        self.assertEqual("Machine fact.", spec["overview"])
        self.assertEqual("source", spec["_section_markers"]["overview"][0]["kind"])

        invalid_path = self.root / "invalid.spec.md"
        invalid_path.write_text(valid.replace("task.yaml#title", "task yaml title"), encoding="utf-8")
        _spec, errors = parse_spec(invalid_path)
        self.assertTrue(any("invalid ai:source" in error for error in errors))

        late_path = self.root / "late.spec.md"
        late_path.write_text(valid.replace("Machine fact.", "Machine fact.\n<!-- ai:source task.yaml#title -->"), encoding="utf-8")
        _spec, errors = parse_spec(late_path)
        self.assertTrue(any("immediately follow" in error for error in errors))

    @staticmethod
    def folder_digest(folder: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                digest.update(path.relative_to(folder).as_posix().encode("utf-8"))
                digest.update(path.read_bytes())
        return digest.hexdigest()


if __name__ == "__main__":
    unittest.main()