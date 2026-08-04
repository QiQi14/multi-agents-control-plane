"""Product-corpus documentation: authority, adoption, and drift.

Split out of test_ai_docs.py at the 700-line ceiling. These are one subject: a product's existing
Markdown carries no authority until the repository takes it, and only after that does an unknown
path or a changed hash mean drift rather than "not adopted yet".
"""
from __future__ import annotations
import argparse

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scripts.ai_cli as ai_cli
import scripts.ai_docs as ai_docs
import scripts.ai_plane.constants as constants
from scripts.ai_plane.frontmatter import parse_frontmatter


def _write_doc(ai: Path, subdir: str, filename: str, content: str) -> Path:
    d = ai / subdir
    d.mkdir(parents=True, exist_ok=True)
    p = d / filename
    p.write_text(content, encoding="utf-8")
    return p


class AiProductDocsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.ai = self.root / ".ai"
        self.ai.mkdir()
        for d in ("rules", "workflows", "agents", "project", "memory", "skills", "migration", "templates", "tasks"):
            (self.ai / d).mkdir()

        root_patch = mock.patch.object(constants, "ROOT", self.root)
        ai_patch = mock.patch.object(constants, "AI", self.ai)
        root_patch.start()
        ai_patch.start()
        self.addCleanup(root_patch.stop)
        self.addCleanup(ai_patch.stop)

    def declare_product_baseline(self, *relative: str) -> None:
        """Take authority over the named product documents at their current bytes.

        Once a repository has done this, the corpus is governed and anything unknown is drift. A
        repository that has NOT done it has simply not adopted its documentation yet.
        """
        documents = []
        for item in relative:
            raw = (self.root / item).read_bytes()
            documents.append({"path": item, "sha256": hashlib.sha256(raw).hexdigest()})
        baseline = self.ai / "project" / "product-doc-legacy-baseline.json"
        baseline.parent.mkdir(parents=True, exist_ok=True)
        baseline.write_text(
            json.dumps({"schema_version": 1, "documents": documents}, indent=2) + "\n",
            encoding="utf-8")

    def test_ai_docs_lint_fails_for_new_untyped_product_document(self) -> None:
        """Drift, in a repository that has taken authority over its product corpus."""
        product_dir = self.root / "project" / "docs"
        product_dir.mkdir(parents=True)
        (product_dir / "known.md").write_text("# Known\n", encoding="utf-8")
        self.declare_product_baseline("project/docs/known.md")
        (product_dir / "new.md").write_text("# New Untyped Product Doc\n", encoding="utf-8")
        self.assertEqual(1, ai_docs.cmd_docs_lint(self.ai))

    def test_ai_docs_lint_fails_when_a_frozen_document_changes(self) -> None:
        """The other half of drift: a baselined file whose bytes moved."""
        product_dir = self.root / "project" / "docs"
        product_dir.mkdir(parents=True)
        (product_dir / "known.md").write_text("# Known\n", encoding="utf-8")
        self.declare_product_baseline("project/docs/known.md")
        (product_dir / "known.md").write_text("# Known, edited\n", encoding="utf-8")
        self.assertEqual(1, ai_docs.cmd_docs_lint(self.ai))

    def test_ai_docs_lint_does_not_fail_a_repository_that_has_not_adopted_yet(self) -> None:
        """A fresh install onto a repository that already has documentation must not report its
        existing files as errors. They carry no authority, and that is reported, not enforced."""
        product_dir = self.root / "project" / "docs"
        product_dir.mkdir(parents=True)
        (product_dir / "old-notes.md").write_text("# Notes from before\n", encoding="utf-8")
        self.assertEqual(0, ai_docs.cmd_docs_lint(self.ai))

    def test_ai_docs_cli_propagates_product_lint_failure_nonzero(self) -> None:
        product_dir = self.root / "project" / "docs"
        product_dir.mkdir(parents=True)
        (product_dir / "known.md").write_text("# Known\n", encoding="utf-8")
        self.declare_product_baseline("project/docs/known.md")
        (product_dir / "new.md").write_text("# New Untyped Product Doc\n", encoding="utf-8")
        with self.assertRaises(SystemExit) as raised:
            ai_docs.cmd_docs(argparse.Namespace(docs_command="lint"))
        self.assertEqual(1, raised.exception.code)

    def test_ai_docs_lint_accepts_valid_product_document(self) -> None:
        product_dir = self.root / "project" / "docs"
        product_dir.mkdir(parents=True)
        (product_dir / "architecture.md").write_text(
            "---\n"
            "id: product-architecture\n"
            "corpus: product\n"
            "type: architecture\n"
            "domain: rendering\n"
            "audiences: [engineering]\n"
            "authority: canonical\n"
            "status: active\n"
            "maturity: implemented\n"
            "visibility: internal\n"
            "summary: Current rendering architecture.\n"
            "navigation: []\n"
            "relations: []\n"
            "subjects: []\n"
            "---\n"
            "# Rendering Architecture\n",
            encoding="utf-8",
        )
        self.assertEqual(0, ai_docs.cmd_docs_lint(self.ai))
        site_dir = ai_docs.cmd_docs_build(self.ai)
        self.assertTrue((site_dir / "docs" / "product-architecture.html").exists())

    def test_ai_docs_lint_fails_unresolved_authored_product_relation(self) -> None:
        product_dir = self.root / "project" / "docs"
        product_dir.mkdir(parents=True)
        content = (
            "---\nid: product-relation\ncorpus: product\ntype: reference\ndomain: rendering\n"
            "audiences: [engineering]\nauthority: informative\nstatus: active\nmaturity: partial\n"
            "visibility: internal\nsummary: Relation fixture.\nnavigation: []\n"
            "relations:\n  - type: references\n    target: missing-product-doc\nsubjects: []\n"
            "---\n# Product Relation\n"
        )
        (product_dir / "relation.md").write_text(content, encoding="utf-8")
        self.assertEqual(1, ai_docs.cmd_docs_lint(self.ai))

    def test_unadopted_documents_are_named_not_merely_tolerated(self) -> None:
        """Not failing is satisfied just as well by silence, and silence is the state that let a
        product's documentation sit unreachable while every gate reported success."""
        product_dir = self.root / "project" / "docs"
        product_dir.mkdir(parents=True)
        (product_dir / "old-notes.md").write_text("# Notes from before\n", encoding="utf-8")
        payload = ai_cli.generate_registry(self.ai)
        warnings = " ".join(payload.get("warnings", []))
        self.assertIn("project/docs/old-notes.md", warnings)
        self.assertIn("carry no authority", warnings)
        self.assertIn("docs adopt", warnings)
        self.assertEqual([], payload.get("errors", []))

    def test_the_adoption_report_lists_what_carries_no_authority(self) -> None:
        from scripts.ai_plane import docs_adopt

        product_dir = self.root / "project" / "docs"
        product_dir.mkdir(parents=True)
        (product_dir / "old-notes.md").write_text("# Notes from before\n", encoding="utf-8")
        (product_dir / "typed.md").write_text(
            "---\nid: typed-doc\ncorpus: product\ntype: reference\n---\n# Typed\n",
            encoding="utf-8")
        found = [item["path"] for item in
                 docs_adopt.unregistered_product_documents(self.root)]
        self.assertEqual(["project/docs/old-notes.md"], found)

    def test_writing_a_baseline_takes_authority_and_makes_drift_real(self) -> None:
        from scripts.ai_plane import docs_adopt

        product_dir = self.root / "project" / "docs"
        product_dir.mkdir(parents=True)
        (product_dir / "old-notes.md").write_text("# Notes from before\n", encoding="utf-8")
        records = docs_adopt.unregistered_product_documents(self.root)
        docs_adopt.write_baseline(self.root, records)
        self.assertEqual(0, ai_docs.cmd_docs_lint(self.ai))
        # Now that the repository has taken authority, an edit is drift.
        (product_dir / "old-notes.md").write_text("# Notes, edited\n", encoding="utf-8")
        self.assertEqual(1, ai_docs.cmd_docs_lint(self.ai))

    def test_a_second_adoption_does_not_drop_the_first(self) -> None:
        from scripts.ai_plane import docs_adopt

        product_dir = self.root / "project" / "docs"
        product_dir.mkdir(parents=True)
        (product_dir / "first.md").write_text("# First\n", encoding="utf-8")
        docs_adopt.write_baseline(self.root, docs_adopt.unregistered_product_documents(self.root))
        (product_dir / "second.md").write_text("# Second\n", encoding="utf-8")
        docs_adopt.write_baseline(self.root, docs_adopt.unregistered_product_documents(self.root))
        frozen = json.loads(
            (self.ai / "project" / "product-doc-legacy-baseline.json").read_text(encoding="utf-8"))
        self.assertEqual(["project/docs/first.md", "project/docs/second.md"],
                         [item["path"] for item in frozen["documents"]])


if __name__ == "__main__":
    unittest.main()
