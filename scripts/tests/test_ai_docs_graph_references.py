from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scripts.ai_cli as ai_cli
import scripts.ai_docs as ai_docs
import scripts.ai_plane.constants as constants


def _write_doc(ai: Path, subdir: str, filename: str, content: str) -> None:
    target = ai / subdir / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


class AiDocsGraphReferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.ai = self.root / ".ai"
        for directory in (
            "rules", "workflows", "agents", "project", "memory",
            "skills", "migration", "templates", "tasks",
        ):
            (self.ai / directory).mkdir(parents=True)

        root_patch = mock.patch.object(constants, "ROOT", self.root)
        ai_patch = mock.patch.object(constants, "AI", self.ai)
        root_patch.start()
        ai_patch.start()
        self.addCleanup(root_patch.stop)
        self.addCleanup(ai_patch.stop)

    def test_registered_relative_and_explicit_paths_infer_unique_edges(self) -> None:
        _write_doc(
            self.ai, "skills/rust-code-standards", "SKILL.md",
            "---\nid: skill-rust\ntype: skill\ndomain: rust\nstatus: active\nowner: system\n---\n"
            "# Rust Standards\n"
            "See [module design](module_architecture.md), then `module_architecture.md` again.\n"
            "Verification follows `.ai/rules/rust-verification.md`; "
            "see `.ai/rules/rust-verification.md` twice.",
        )
        _write_doc(
            self.ai, "skills/rust-code-standards", "module_architecture.md",
            "---\nid: skill-rust-module\ntype: skill\ndomain: rust\nstatus: active\nowner: system\n---\n"
            "# Module Architecture\nBody.",
        )
        _write_doc(
            self.ai, "rules", "rust-verification.md",
            "---\nid: rule-rust-verification\ntype: rule\ndomain: rust\nstatus: active\nowner: system\n---\n"
            "# Rust Verification\nBody.",
        )

        documents = ai_cli.generate_registry(self.ai)["documents"]
        inferred = [
            edge for edge in ai_docs._collect_edges(documents, self.ai)
            if edge["provenance"] == "inferred"
        ]
        self.assertEqual(
            [
                ("skill-rust", "rule-rust-verification"),
                ("skill-rust", "skill-rust-module"),
            ],
            sorted((edge["source"], edge["target"]) for edge in inferred),
        )


if __name__ == "__main__":
    unittest.main()
