"""One-step task report export.

`ai docs export` collapses "author a spec, then build it" into one command whose input is the task
itself. These tests pin the parts that make that safe: it resolves a real task, it never persists a
spec that could drift from its source, it fails success-shaped on an unknown id, and its defaults
match `ai blueprint init` so the two entry points cannot produce differently-shaped reports.
"""

from __future__ import annotations

import argparse
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from scripts.ai_plane import docs_export


def die(message: str) -> None:
    raise AssertionError(message)


def args(**overrides) -> argparse.Namespace:
    base = {
        "task_id": "task_01_thing", "out": None, "base": None,
        "preset": docs_export.DEFAULT_PRESET, "kind": docs_export.DEFAULT_KIND,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


class ExportContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_unknown_task_is_success_shaped_and_writes_nothing(self) -> None:
        # An unknown id is an ordinary mistake. Exiting nonzero here trains agents to abandon the
        # toolset, so it reports and returns instead.
        buffer = io.StringIO()
        with mock.patch.object(docs_export, "resolve_task_source", return_value=(None, {}, ["task_a"])):
            with redirect_stdout(buffer):
                docs_export.cmd_docs_export(args(task_id="task_nope"), die=die)
        output = buffer.getvalue()
        self.assertIn("Task not found: task_nope", output)
        self.assertIn("No report was written", output)
        self.assertIn("task_a", output)

    def test_spec_is_never_persisted_next_to_the_report(self) -> None:
        # A task report is DERIVED from the contract and receipts. Leaving a spec copy behind would
        # immediately drift from its source and become a second, lying truth.
        seen: dict[str, Path] = {}

        def fake_build(spec_path: Path, out_path: Path) -> int:
            seen["spec"] = spec_path
            seen["out"] = out_path
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text("<html></html>", encoding="utf-8")
            return 0

        task_dir = self.root / "task_01_thing"
        task_dir.mkdir()
        with mock.patch.object(docs_export, "resolve_task_source",
                               return_value=(task_dir, {"id": "task_01_thing"}, [])), \
             mock.patch.object(docs_export, "task_blueprint_spec", return_value="# spec\n"), \
             mock.patch.object(docs_export, "build_report", side_effect=fake_build), \
             mock.patch.object(docs_export.constants, "ROOT", self.root), \
             mock.patch.object(docs_export.constants, "AI", self.root / ".ai"):
            with redirect_stdout(io.StringIO()):
                docs_export.cmd_docs_export(args(), die=die)

        self.assertTrue(seen["out"].is_file())
        self.assertFalse(seen["spec"].exists(), "the intermediate spec outlived the export")

    def test_report_defaults_into_the_site_so_the_reader_can_link_it(self) -> None:
        with mock.patch.object(docs_export.constants, "AI", self.root / ".ai"):
            path = docs_export.report_path("task_07_payment_retry")
        self.assertEqual(("_site", "reports"), path.parent.parts[-2:])
        self.assertTrue(path.name.endswith(".html"))

    def test_defaults_match_the_hand_authored_blueprint_entry_point(self) -> None:
        # If these drift, the same task renders differently depending on which command produced it.
        source = (Path(__file__).resolve().parents[1] / "ai_cli.py").read_text(encoding="utf-8")
        self.assertIn(f'"--preset", default="{docs_export.DEFAULT_PRESET}"', source)
        self.assertIn(f'"--kind", default="{docs_export.DEFAULT_KIND}"', source)
        for preset in docs_export.PRESETS:
            self.assertIn(f'"{preset}"', source)


class ExportWiringTests(unittest.TestCase):
    def test_export_is_registered_on_the_docs_command(self) -> None:
        import scripts.ai_cli as ai_cli
        parser = ai_cli.build_parser() if hasattr(ai_cli, "build_parser") else None
        if parser is None:
            self.skipTest("no parser factory exposed")
        actions = [a for a in parser._subparsers._group_actions[0].choices.items()]  # noqa: SLF001
        docs = dict(actions).get("docs")
        self.assertIsNotNone(docs, "docs command is missing")
        sub = docs._subparsers._group_actions[0].choices  # noqa: SLF001
        self.assertIn("export", sub)


if __name__ == "__main__":
    unittest.main()
