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


class AiDocsTests(unittest.TestCase):
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

    def test_name_segment_extraction(self) -> None:
        segments = ai_docs.extract_name_segments("task_175_blueprint_gen3_core ProductCardGrid")
        self.assertIn("task", segments)
        self.assertIn("175", segments)
        self.assertIn("blueprint", segments)
        self.assertIn("product", segments)
        self.assertIn("card", segments)
        self.assertIn("grid", segments)

    def test_ai_docs_build_and_search_index(self) -> None:
        _write_doc(
            self.ai, "rules", "rule-a.md",
            "---\nid: rule-a\ntype: rule\ndomain: control-plane\nstatus: active\nowner: system\nrelations:\n  - type: relates_to\n    target: workflow-b\n---\n# Rule A\nThis is rule A body.",
        )
        _write_doc(
            self.ai, "workflows", "workflow-b.md",
            "---\nid: workflow-b\ntype: workflow\ndomain: control-plane\nstatus: active\nowner: system\n---\n# Workflow B\nWorkflow B body.",
        )

        site_dir = ai_docs.cmd_docs_build(self.ai)
        self.assertTrue((site_dir / "index.html").exists())
        self.assertTrue((site_dir / "search_index.json").exists())
        self.assertTrue((site_dir / "reader-data.json").exists())
        self.assertTrue((site_dir / "assets" / "reader-data.js").exists())
        self.assertTrue((site_dir / "docs" / "rule-a.html").exists())
        self.assertTrue((site_dir / "docs" / "workflow-b.html").exists())
        reader_data = json.loads((site_dir / "reader-data.json").read_text(encoding="utf-8"))
        self.assertEqual(1, reader_data["schema_version"])
        self.assertEqual(
            {"project_intelligence", "documents", "tasks_features"},
            set(reader_data["truth_systems"]),
        )

        rule_a_html = (site_dir / "docs" / "rule-a.html").read_text(encoding="utf-8")
        self.assertIn("Staleness Honesty:", rule_a_html)
        self.assertIn("workflow-b.html", rule_a_html)

        workflow_b_html = (site_dir / "docs" / "workflow-b.html").read_text(encoding="utf-8")
        self.assertIn("rule-a.html", workflow_b_html)  # backlink from rule-a

        search_res = ai_docs.cmd_docs_search(self.ai, query="Rule")
        matches = search_res.get("query_matches", [])
        self.assertTrue(any(m["id"] == "rule-a" for m in matches))

    def test_ai_docs_lint_reports_pending_relations(self) -> None:
        _write_doc(
            self.ai, "rules", "rule-a.md",
            "---\nid: rule-a\ntype: rule\ndomain: control-plane\nstatus: active\nowner: system\nrelations:\n  - type: relates_to\n    target: dangling-target-999\n---\n# Rule A\nBody.",
        )
        res = ai_docs.cmd_docs_lint(self.ai)
        self.assertEqual(1, res)

    def test_ai_docs_lint_fails_for_new_untyped_product_document(self) -> None:
        product_dir = self.root / "project" / "docs"
        product_dir.mkdir(parents=True)
        (product_dir / "new.md").write_text("# New Untyped Product Doc\n", encoding="utf-8")
        self.assertEqual(1, ai_docs.cmd_docs_lint(self.ai))

    def test_ai_docs_cli_propagates_product_lint_failure_nonzero(self) -> None:
        product_dir = self.root / "project" / "docs"
        product_dir.mkdir(parents=True)
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

    def test_ai_docs_stats(self) -> None:
        _write_doc(
            self.ai, "rules", "rule-a.md",
            "---\nid: rule-a\ntype: rule\ndomain: control-plane\nstatus: active\nowner: system\nrelations:\n  - type: relates_to\n    target: workflow-b\n---\n# Rule A\nBody.",
        )
        _write_doc(
            self.ai, "workflows", "workflow-b.md",
            "---\nid: workflow-b\ntype: workflow\ndomain: control-plane\nstatus: active\nowner: system\n---\n# Workflow B\nBody.",
        )
        _write_doc(
            self.ai, "memory", "orphan-c.md",
            "---\nid: memory-c\ntype: memory\ndomain: control-plane\nstatus: active\nowner: system\n---\n# Memory C\nBody.",
        )

        stats = ai_docs.cmd_docs_stats(self.ai)
        self.assertEqual(3, stats["total_documents"])
        self.assertEqual(1, stats["orphan_documents_count"])
        self.assertIn("memory-c", stats["orphan_documents"])

    def test_ai_docs_graph_svg_rendering(self) -> None:
        _write_doc(
            self.ai, "rules", "rule-a.md",
            "---\nid: rule-a\ntype: rule\ndomain: control-plane\nstatus: active\nowner: system\nrelations:\n  - type: relates_to\n    target: workflow-b\n---\n# Rule A\nBody.",
        )
        _write_doc(
            self.ai, "workflows", "workflow-b.md",
            "---\nid: workflow-b\ntype: workflow\ndomain: control-plane\nstatus: active\nowner: system\n---\n# Workflow B\nBody.",
        )

        svg = ai_docs.cmd_docs_graph(self.ai, doc_id="rule-a")
        self.assertIn("<svg", svg)
        self.assertIn("Rule A", svg)
        self.assertIn("Workflow B", svg)
        self.assertIn("edge-authored", svg)

    # --- P1-5 fix: real Git clean/dirty fixture assertions ---

    def test_staleness_clean_repo(self) -> None:
        """Clean Git repo reports zero changed files."""
        repo = Path(self.temp_dir.name) / "clean_repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True, capture_output=True)
        (repo / "README.md").write_text("# Clean\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

        info = ai_docs.compute_staleness_info(repo)
        self.assertNotEqual("0000000", info["commit"])
        self.assertEqual(0, info["changed_files_count"])
        self.assertIn("; 0 source files changed since", info["banner"])

    def test_staleness_dirty_repo(self) -> None:
        """Dirty Git repo reports nonzero changed files."""
        repo = Path(self.temp_dir.name) / "dirty_repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True, capture_output=True)
        (repo / "README.md").write_text("# Init\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

        # Dirty the working tree: modify an existing file and add an untracked file
        (repo / "README.md").write_text("# Modified\n", encoding="utf-8")
        (repo / "new_file.txt").write_text("new\n", encoding="utf-8")

        info = ai_docs.compute_staleness_info(repo)
        self.assertNotEqual("0000000", info["commit"])
        self.assertGreaterEqual(info["changed_files_count"], 2)
        self.assertNotIn("; 0 source files changed since", info["banner"])

    # --- P1-1 fix: search always writes index ---

    def test_search_writes_index_by_default(self) -> None:
        """cmd_docs_search writes search_index.json without explicit out_file."""
        _write_doc(
            self.ai, "rules", "rule-a.md",
            "---\nid: rule-a\ntype: rule\ndomain: control-plane\nstatus: active\nowner: system\n---\n# Rule A\nBody.",
        )
        ai_docs.cmd_docs_search(self.ai)
        index_path = self.ai / "_site" / "search_index.json"
        self.assertTrue(index_path.exists(), "search_index.json should be written by default")
        data = json.loads(index_path.read_text(encoding="utf-8"))
        self.assertIn("documents", data)

    # --- P1-2 fix: inferred edges and graph artifact emission ---

    def test_inferred_edges_from_markdown_links(self) -> None:
        """Markdown body links to known doc IDs produce inferred edges."""
        _write_doc(
            self.ai, "rules", "rule-a.md",
            "---\nid: rule-a\ntype: rule\ndomain: control-plane\nstatus: active\nowner: system\n---\n# Rule A\nSee [workflow-b](workflow-b) for details.",
        )
        _write_doc(
            self.ai, "workflows", "workflow-b.md",
            "---\nid: workflow-b\ntype: workflow\ndomain: control-plane\nstatus: active\nowner: system\n---\n# Workflow B\nBody.",
        )

        reg = ai_cli.generate_registry(self.ai)
        docs = reg["documents"]
        edges = ai_docs._collect_edges(docs, self.ai)
        inferred = [e for e in edges if e["provenance"] == "inferred"]
        self.assertTrue(len(inferred) > 0, "Should find at least one inferred edge from Markdown link")
        self.assertTrue(any(e["source"] == "rule-a" and e["target"] == "workflow-b" for e in inferred))

    def test_cross_corpus_graphs_keep_nodes_separate_and_label_authored_bridges(self) -> None:
        documents = [
            {
                "id": "rule-a",
                "title": "Rule A",
                "corpus": "control-plane",
                "type": "rule",
                "domain": "control-plane",
                "status": "active",
                "owner": "system",
                "path": ".ai/rules/rule-a.md",
                "relations": [{"type": "references", "target": "product-architecture"}],
            },
            {
                "id": "product-architecture",
                "title": "Product Architecture",
                "corpus": "product",
                "type": "architecture",
                "domain": "rendering-core",
                "status": "active",
                "owner": "product",
                "path": "project/docs/architecture.md",
                "relations": [],
            },
        ]
        edges = ai_docs._collect_edges(documents, self.ai)
        summaries = {document["id"]: f'Summary for {document["id"]}' for document in documents}

        control_payload = ai_docs._graph_payload(
            documents, edges, summaries, corpus="control-plane"
        )
        product_payload = ai_docs._graph_payload(
            documents, edges, summaries, corpus="product"
        )

        self.assertEqual("control-plane", control_payload["corpus"])
        self.assertEqual(["rule-a"], [node["id"] for node in control_payload["nodes"]])
        self.assertEqual(["control-plane"], [node["corpus"] for node in control_payload["nodes"]])
        self.assertEqual(["product-architecture"], [node["id"] for node in product_payload["nodes"]])
        self.assertEqual([], control_payload["edges"])
        self.assertEqual(1, len(control_payload["bridges"]))
        bridge = control_payload["bridges"][0]
        self.assertTrue(bridge["bridge"])
        self.assertEqual("authored", bridge["provenance"])
        self.assertEqual("control-plane", bridge["source_corpus"])
        self.assertEqual("product", bridge["target_corpus"])

        graphs_dir = self.ai / "_site" / "graphs"
        ai_docs.emit_graph_artifacts({"documents": documents}, graphs_dir, ai_root=self.ai)
        control_svg = (graphs_dir / "graph-global.svg").read_text(encoding="utf-8")
        product_svg = (graphs_dir / "graph-corpus-product.svg").read_text(encoding="utf-8")
        self.assertIn("Rule A", control_svg)
        self.assertNotIn("Product Architecture", control_svg)
        self.assertIn("Product Architecture", product_svg)
        self.assertNotIn("Rule A", product_svg)

    def test_inferred_cross_corpus_links_are_suppressed(self) -> None:
        control_path = _write_doc(
            self.ai,
            "rules",
            "rule-a.md",
            "---\nid: rule-a\ntype: rule\ndomain: control-plane\nstatus: active\nowner: system\n---\n"
            "# Rule A\nSee [product architecture](product-architecture).",
        )
        product_dir = self.root / "project" / "docs"
        product_dir.mkdir(parents=True)
        product_path = product_dir / "architecture.md"
        product_path.write_text("# Product Architecture\n", encoding="utf-8")
        documents = [
            {
                "id": "rule-a",
                "corpus": "control-plane",
                "path": control_path.relative_to(self.root).as_posix(),
            },
            {
                "id": "product-architecture",
                "corpus": "product",
                "path": product_path.relative_to(self.root).as_posix(),
            },
        ]

        edges = ai_docs._collect_edges(documents, self.ai)

        self.assertFalse(any(edge["provenance"] == "inferred" for edge in edges))
    def test_graph_artifact_emission(self) -> None:
        """emit_graph_artifacts writes global, domain, and local SVG files."""
        _write_doc(
            self.ai, "rules", "rule-a.md",
            "---\nid: rule-a\ntype: rule\ndomain: control-plane\nstatus: active\nowner: system\nrelations:\n  - type: relates_to\n    target: workflow-b\n---\n# Rule A\nBody.",
        )
        _write_doc(
            self.ai, "workflows", "workflow-b.md",
            "---\nid: workflow-b\ntype: workflow\ndomain: control-plane\nstatus: active\nowner: system\n---\n# Workflow B\nBody.",
        )

        reg = ai_cli.generate_registry(self.ai)
        graphs_dir = self.ai / "_site" / "graphs"
        written = ai_docs.emit_graph_artifacts(reg, graphs_dir, ai_root=self.ai)

        # Global
        self.assertTrue((graphs_dir / "graph-global.svg").exists())
        # Domain
        self.assertTrue((graphs_dir / "graph-domain-control-plane.svg").exists())
        # Local per-document
        self.assertTrue((graphs_dir / "graph-local-rule-a.svg").exists())
        self.assertTrue((graphs_dir / "graph-local-workflow-b.svg").exists())
        self.assertTrue(len(written) >= 4)

    # --- P1-3 fix: SVG edge endpoint within viewBox ---

    def test_svg_edge_endpoints_within_viewbox(self) -> None:
        """All SVG edge endpoints must be within the declared viewBox."""
        import re as re_mod
        _write_doc(
            self.ai, "rules", "rule-a.md",
            "---\nid: rule-a\ntype: rule\ndomain: control-plane\nstatus: active\nowner: system\nrelations:\n  - type: relates_to\n    target: rule-b\n  - type: relates_to\n    target: rule-c\n---\n# Rule A\nBody.",
        )
        _write_doc(
            self.ai, "rules", "rule-b.md",
            "---\nid: rule-b\ntype: rule\ndomain: control-plane\nstatus: active\nowner: system\n---\n# Rule B\nBody.",
        )
        _write_doc(
            self.ai, "rules", "rule-c.md",
            "---\nid: rule-c\ntype: rule\ndomain: control-plane\nstatus: active\nowner: system\n---\n# Rule C\nBody.",
        )

        reg = ai_cli.generate_registry(self.ai)
        svg = ai_docs.generate_svg_graph(None, reg, ai_root=self.ai)

        # Parse viewBox
        vb_match = re_mod.search(r'viewBox="0 0 (\d+) (\d+)"', svg)
        self.assertIsNotNone(vb_match, "SVG must have a viewBox")
        vb_w = int(vb_match.group(1))
        vb_h = int(vb_match.group(2))

        # Check all <line> endpoints
        for line_match in re_mod.finditer(r'<line\s[^>]*x1="(\d+)"[^>]*y1="(\d+)"[^>]*x2="(\d+)"[^>]*y2="(\d+)"', svg):
            x1, y1, x2, y2 = (int(line_match.group(i)) for i in range(1, 5))
            self.assertLessEqual(x2, vb_w, f"x2={x2} exceeds viewBox width={vb_w}")
            self.assertLessEqual(y2, vb_h, f"y2={y2} exceeds viewBox height={vb_h}")

        # Check all <path> Q control points and endpoints
        for path_match in re_mod.finditer(r'<path d="M (\d+) (\d+) Q (\d+) (\d+) (\d+) (\d+)"', svg):
            vals = [int(path_match.group(i)) for i in range(1, 7)]
            # Start point
            self.assertLessEqual(vals[1], vb_h, f"path start y={vals[1]} exceeds viewBox height={vb_h}")
            # Control point
            self.assertGreaterEqual(vals[3], 0, f"path control y={vals[3]} is negative")
            # End point
            self.assertLessEqual(vals[5], vb_h, f"path end y={vals[5]} exceeds viewBox height={vb_h}")

    def test_unregistered_targets_rendered_without_broken_hyperlinks(self) -> None:
        """Outgoing relations and Markdown links to unregistered targets (e.g. task_175) must not create broken .html links."""
        _write_doc(
            self.ai, "templates/pr-blueprint/examples", "fullstack.spec.md",
            "---\nid: spec-fullstack\ntype: spec\ndomain: fullstack\nstatus: active\nowner: system\nrelations:\n  - type: references\n    target: task_175\n---\n# Fullstack Spec\nSee [task_175](task_175) for context.",
        )
        site_dir = ai_docs.cmd_docs_build(self.ai)
        spec_html_path = site_dir / "docs" / "spec-fullstack.html"
        self.assertTrue(spec_html_path.exists())
        html_content = spec_html_path.read_text(encoding="utf-8")

        # Must NOT contain a hyperlink to non-existent task_175.html
        self.assertNotIn('href="task_175.html"', html_content, "Must not create a link to non-existent task_175.html")
        # Must render task_175 as code block instead
        self.assertIn('<code>task_175</code>', html_content)

    def test_blueprint_spec_template_frontmatter_schema(self) -> None:
        """blueprint_spec_template must include id, type: spec, domain, status, owner, relations."""
        from scripts.ai_plane.blueprint import blueprint_spec_template
        tmpl = blueprint_spec_template("checkout-flow", "fullstack", "app")
        meta, _ = parse_frontmatter(tmpl)
        self.assertEqual("spec-checkout_flow", meta.get("id"))
        self.assertEqual("spec", meta.get("type"))
        self.assertEqual("general", meta.get("domain"))
        self.assertEqual("draft", meta.get("status"))
        self.assertEqual("system", meta.get("owner"))
        self.assertIn("relations", meta)

    def test_built_site_relative_links_all_resolve(self) -> None:
        """Every relative href in the built site must resolve to a file that exists.

        A generated page that links to a page nobody generated is the failure mode round two and
        round three both reproduced by hand (task_175.html); this crawls the whole site instead.
        """
        _write_doc(
            self.ai, "rules", "crawl.md",
            "---\nid: rule-crawl\ntype: rule\ndomain: control-plane\nstatus: active\nowner: system\n"
            "relations:\n  - type: references\n    target: task_175\n  - type: depends_on\n    target: rule-a\n---\n"
            "# Rule: Crawl\nSee [rule-a](rule-a.md) and [task_175](task_175).",
        )
        site_dir = ai_docs.cmd_docs_build(self.ai)

        broken: list[str] = []
        for page in sorted(site_dir.rglob("*.html")):
            content = page.read_text(encoding="utf-8")
            for href in re.findall(r'href="([^"]+)"', content):
                if href.startswith(("http://", "https://", "#", "mailto:")):
                    continue
                target = (page.parent / href.split("#", 1)[0]).resolve()
                if not target.exists():
                    broken.append(f"{page.relative_to(site_dir).as_posix()} -> {href}")

        self.assertEqual([], broken, "built site contains relative links to files that do not exist")

    def test_hard_wrapped_paragraph_renders_as_one_paragraph(self) -> None:
        """Control-plane Markdown hard-wraps prose; a per-source-line <p> splits every sentence.

        Found by looking at the rendered rule page, not by any passing test (task_176 round 4).
        """
        body = (
            "# Heading\n"
            "Executors may edit only target_files across a wrapped\n"
            "sentence that continues here.\n"
            "\n"
            "A second block.\n"
            "- list item\n"
        )
        rendered = ai_docs.render_markdown_body_to_html(body)

        self.assertIn(
            "<p>Executors may edit only target_files across a wrapped sentence that continues here.</p>",
            rendered,
            "hard-wrapped source lines must join into one paragraph",
        )
        self.assertIn("<p>A second block.</p>", rendered)
        self.assertIn("<li>list item</li>", rendered)
        self.assertEqual(2, rendered.count("<p>"), "one paragraph per blank-line block, not per line")

    def test_headings_render_to_level_six(self) -> None:
        """Blueprint specs use #### for response codes; an unhandled level leaks its hash marks."""
        rendered = ai_docs.render_markdown_body_to_html(
            "# One\n## Two\n### Three\n#### 200 OK\n##### Five\n###### Six\n"
        )
        for level, text in ((1, "One"), (2, "Two"), (3, "Three"), (4, "200 OK"), (5, "Five"), (6, "Six")):
            self.assertIn(f"<h{level}>{text}</h{level}>", rendered)
        self.assertNotIn("#### 200 OK", rendered)

    def test_built_site_paragraphs_are_not_line_fragments(self) -> None:
        """The same guard at the real build boundary: no rendered page splits a wrapped sentence."""
        _write_doc(
            self.ai, "rules", "wrapped.md",
            "---\nid: rule-wrapped\ntype: rule\ndomain: control-plane\nstatus: active\nowner: system\n---\n"
            "# Rule: Wrapped\nThe task folder is the contract and this sentence wraps\n"
            "onto a second source line before it ends.\n",
        )
        site_dir = ai_docs.cmd_docs_build(self.ai)
        html_content = (site_dir / "docs" / "rule-wrapped.html").read_text(encoding="utf-8")
        self.assertIn(
            "<p>The task folder is the contract and this sentence wraps onto a second source line "
            "before it ends.</p>",
            html_content,
        )


    def test_d2_reader_ux_uses_shared_assets_and_semantic_views(self) -> None:
        _write_doc(
            self.ai, "project", "reader.md",
            "---\nid: project-reader\ntype: project-doc\ndomain: control-plane\nstatus: active\nowner: system\n"
            "relations:\n  - type: depends_on\n    target: rule-related\n---\n"
            "# Reader Guide\nA concise purpose summary that continues\n"
            "across a hard-wrapped source line.\n\n## Mission\nBody.\n\n"
            "## Required Inputs\n- A list item that continues\n"
            "  onto the next hard-wrapped line.\n- A second item.\n",
        )
        _write_doc(
            self.ai, "rules", "related.md",
            "---\nid: rule-related\ntype: rule\ndomain: control-plane\nstatus: active\nowner: system\n---\n"
            "# Related Rule\nRelated body.",
        )

        site_dir = ai_docs.cmd_docs_build(self.ai)
        index_html = (site_dir / "index.html").read_text(encoding="utf-8")
        doc_html = (site_dir / "docs" / "project-reader.html").read_text(encoding="utf-8")
        graph_html = (site_dir / "graphs" / "graph-local-project-reader.html").read_text(encoding="utf-8")
        css = (site_dir / "assets" / "docs.css").read_text(encoding="utf-8")
        app_css = (site_dir / "assets" / "app.css").read_text(encoding="utf-8")
        project_css = (site_dir / "assets" / "project.css").read_text(encoding="utf-8")
        delta_css = (site_dir / "assets" / "production-delta.css").read_text(encoding="utf-8")
        app_js = (site_dir / "assets" / "app.js").read_text(encoding="utf-8")
        project_js = (site_dir / "assets" / "project.js").read_text(encoding="utf-8")
        task_js = (site_dir / "assets" / "task-rich.js").read_text(encoding="utf-8")
        data_js = (site_dir / "assets" / "data.js").read_text(encoding="utf-8")
        project_data_js = (site_dir / "assets" / "project-data.js").read_text(encoding="utf-8")

        for stylesheet in ("app.css", "project.css", "production-delta.css"):
            self.assertIn(f'href="assets/{stylesheet}"', index_html)
        for script in (
            "data.js", "project-data.js", "markdown.js", "project.js",
            "task-rich.js", "app.js",
        ):
            self.assertIn(f'src="assets/{script}"', index_html)
        self.assertIn('href="#/project"', index_html)
        self.assertNotIn("<style>", index_html)
        self.assertNotRegex(index_html, r"\s(?:onclick|onsubmit|style)=")
        for control_id in ("nav", "theme-toggle", "main"):
            self.assertIn(f'id="{control_id}"', index_html)
        for screen in ("project", "home", "docs", "tasks"):
            self.assertIn(f'data-screen="{screen}"', index_html)
        self.assertNotIn("reader.css", index_html)
        self.assertNotIn("reader.js", index_html)
        self.assertIn("--canvas: hsl(40 22% 97%)", app_css)
        self.assertIn("--canvas: hsl(224 20% 9%)", app_css)
        self.assertIn("--font-read: ui-serif", app_css)
        self.assertIn(".graph-reader", app_css)
        self.assertIn("@media (max-width: 1024px)", app_css)
        self.assertIn(".project-screen", project_css)
        self.assertIn(".project-reader", project_css)
        self.assertIn("@media (max-width: 720px)", project_css)
        self.assertIn(".document-surface-bar", delta_css)
        self.assertIn(".documents-graph-shell", delta_css)
        self.assertIn(".evidence-media-grid", delta_css)
        self.assertIn(".source-artifacts", delta_css)
        for source in (app_js, project_js, task_js):
            self.assertNotRegex(source, r"https?://|fetch\(")
        for marker in (
            "function documentSurfaceBar", "data-doc-corpus",
            "corpusUrlValue", "control-plane", "product",
            "function screenGraph", "function screenDocs",
        ):
            self.assertIn(marker, app_js)
        screen_docs_default = (
            app_js.split("function screenDocs()", 1)[1]
            .split("var view =", 1)[0]
        )
        self.assertIn("state.query = selectedQuery;", screen_docs_default)
        self.assertNotRegex(screen_docs_default, r"go\([^\n]+replace: true[^\n]+;\s*return;")
        self.assertIn("data-doc-selection-state", app_js)
        self.assertIn("stats: graphStats", app_js)
        for marker in (
            "data-project-fit", "data-project-zoom", "pointerdown",
            "authoredCratePurpose", "authoredModuleSummary",
            "Missing authored summary.", "agentContext",
            "authoredText", "output.state === 'ready'", "Derived, non-fabricated context.",
        ):
            self.assertIn(marker, project_js)
        for marker in (
            "window.CPTaskRich", 'data-task-view="evidence"',
            'data-task-view="review"', 'data-task-view="source"',
            "Expected reference - not generated evidence",
            "Executor and QA notes", "Complete source projection",
        ):
            self.assertIn(marker, task_js)
        self.assertIn("window.CP_DATA = ", data_js)
        self.assertIn("window.CONTROL_PLANE_PROJECT = ", project_data_js)

        self.assertEqual(1, len(re.findall(r"<h1(?:\s|>)", doc_html)))
        self.assertIn('aria-label="Breadcrumb"', doc_html)
        self.assertIn('<dl class="metadata">', doc_html)
        self.assertIn('aria-label="On this page"', doc_html)
        self.assertIn('<h2 id="mission">Mission</h2>', doc_html)
        self.assertIn('<h2 id="required-inputs">Required Inputs</h2>', doc_html)
        self.assertIn(
            "<li>A list item that continues onto the next hard-wrapped line.</li>",
            doc_html,
        )
        self.assertIn('href="../graphs/graph-local-project-reader.html"', doc_html)
        self.assertNotIn("graph-frame", doc_html)

        self.assertIn('class="graph-canvas"', graph_html)
        self.assertIn('id="forceGraphCanvas"', graph_html)
        self.assertIn('id="graph-data" type="application/json"', graph_html)
        self.assertIn("Relationship filters", graph_html)
        self.assertIn('aria-label="Selected document"', graph_html)
        self.assertIn('id="openSelectedDocument"', graph_html)
        self.assertIn('href="graph-local-project-reader.svg"', graph_html)
        self.assertIn('src="../assets/docs.js"', graph_html)
        self.assertNotIn("<svg", graph_html)
        self.assertNotIn("<style>", graph_html.split("</head>", 1)[0])
        self.assertNotRegex(graph_html, r"\s(?:onclick|onsubmit|style)=")
        self.assertIn(".force-canvas", css)
        self.assertIn("cursor:grab", css)
        self.assertIn("--cluster-agent", css)
        self.assertIn("@media (prefers-color-scheme: dark)", css)
        self.assertIn(":focus-visible", css)
        self.assertIn("@media (max-width: 430px)", css)

    def test_d2_force_graph_payload_is_deterministic_and_canonical(self) -> None:
        documents = [
            {"id": "agent-a", "title": "Agent A", "type": "agent", "domain": "control-plane", "status": "active", "owner": "system"},
            {"id": "workflow-b", "title": "Workflow B", "type": "workflow", "domain": "control-plane", "status": "active", "owner": "system"},
            {"id": "rule-c", "title": "Rule C", "type": "rule", "domain": "control-plane", "status": "active", "owner": "system"},
            {"id": "spec-d", "title": "Spec D", "type": "spec", "domain": "delivery", "status": "draft", "owner": "system"},
        ]
        edges = [
            {"source": "agent-a", "target": "workflow-b", "type": "depends_on", "provenance": "authored"},
            {"source": "workflow-b", "target": "rule-c", "type": "references", "provenance": "inferred"},
            {"source": "missing", "target": "agent-a", "type": "invalid", "provenance": "authored"},
        ]
        summaries = {doc["id"]: f"Summary for {doc['id']}" for doc in documents}
        first = ai_docs._graph_payload(documents, edges, summaries, focus_doc_id="agent-a")
        second = ai_docs._graph_payload(documents, edges, summaries, focus_doc_id="agent-a")
        self.assertEqual(first, second)
        self.assertEqual("agent-a", first["focus_id"])
        self.assertEqual(["agent-a", "workflow-b", "rule-c"], [node["id"] for node in first["nodes"]])
        self.assertEqual(["authored", "inferred"], [edge["provenance"] for edge in first["edges"]])
        self.assertEqual("../docs/agent-a.html", first["nodes"][0]["href"])
        self.assertNotIn("missing", json.dumps(first))

    def test_d2_generated_resource_links_are_local_and_resolve(self) -> None:
        _write_doc(
            self.ai, "rules", "local.md",
            "---\nid: rule-local\ntype: rule\ndomain: control-plane\nstatus: active\nowner: system\n---\n"
            "# Local Rule\nBody.\n\n## One\nA.\n\n## Two\nB.",
        )
        site_dir = ai_docs.cmd_docs_build(self.ai)
        broken: list[str] = []
        external: list[str] = []
        for page in sorted(site_dir.rglob("*.html")):
            content = page.read_text(encoding="utf-8")
            for attr, target in re.findall(r'(href|src)="([^"]+)"', content):
                if target.startswith(("http://", "https://", "//")):
                    external.append(f"{page.name}:{attr}={target}")
                    continue
                if target.startswith(("#", "mailto:")):
                    continue
                resolved = (page.parent / target.split("#", 1)[0]).resolve()
                if not resolved.exists():
                    broken.append(f"{page.relative_to(site_dir).as_posix()} -> {target}")
        self.assertEqual([], external)
        self.assertEqual([], broken)

    def test_d2_staleness_copy_distinguishes_current_and_stale(self) -> None:
        current = ai_docs._staleness_markup({"commit": "abc1234", "changed_files_count": 0})
        stale = ai_docs._staleness_markup({"commit": "abc1234", "changed_files_count": 2})
        self.assertIn("build-state--current", current)
        self.assertIn("no source changes", current)
        self.assertNotIn("Source changed.", current)
        self.assertIn("build-state--stale", stale)
        self.assertIn("2 source files changed since", stale)
        self.assertIn("docs build", stale)

if __name__ == "__main__":
    unittest.main()
