from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
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


class AiCliFrontmatterRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.live_before = live_state_snapshot()

    @classmethod
    def tearDownClass(cls) -> None:
        live_after = live_state_snapshot()
        if cls.live_before != live_after:
            raise AssertionError("frontmatter/registry tests changed the live repository")

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.ai = self.root / ".ai"
        self.ai.mkdir()
        for d in ("rules", "workflows", "agents", "project", "memory", "skills", "migration"):
            (self.ai / d).mkdir()
        # task_190a: the live roster selects rendering through the reference codex/claude/antigravity
        # `integration` extensions, so this temp repo installs those manifests + templates (fixture
        # setup may add the integrations now required to exercise the same assertions) and enables
        # exactly them (the `rust` gate is dropped — its entrypoint is not present here). Minimal
        # agent documents satisfy each integration's agent_document/agent_roles catalog.
        src_lines = live_config_text().splitlines(keepends=True)
        out_lines: list[str] = []
        in_enabled = False
        for line in src_lines:
            if in_enabled:
                if line.strip().startswith("- "):
                    continue
                in_enabled = False
            if line.rstrip() == "  enabled:" and any(l.rstrip() == "extensions:" for l in out_lines):
                out_lines.append("  enabled:\n    - codex\n    - claude\n    - antigravity\n")
                in_enabled = True
                continue
            out_lines.append(line)
        (self.ai / "config.yaml").write_text("".join(out_lines), encoding="utf-8")
        for ext_id in ("codex", "claude", "antigravity"):
            shutil.copytree(REPO_ROOT / "scripts" / "extensions" / ext_id,
                            self.root / "scripts" / "extensions" / ext_id)
        root_patch = mock.patch.object(ai_cli.constants, "ROOT", self.root)
        ai_patch = mock.patch.object(ai_cli.constants, "AI", self.ai)
        root_patch.start()
        ai_patch.start()
        self.addCleanup(root_patch.stop)
        self.addCleanup(ai_patch.stop)
        ai_cli.initialize_runtime_config(self.ai / "config.yaml")
        self.original_runtime = (
            config_module.TOOLS,
            config_module.TOOL_NOTES,
            config_module.TOOL_DEFAULTS,
            config_module.TOOL_ROLES,
            config_module.COMMAND_TOOL_DEFAULTS,
            config_module.GENERATED_DISPATCH_ROOT,
            config_module.ADAPTERS,
            config_module.TASK_CONTRACT_VOCABULARY,
        )
        self.addCleanup(self.restore_runtime)

    def restore_runtime(self) -> None:
        (
            config_module.TOOLS,
            config_module.TOOL_NOTES,
            config_module.TOOL_DEFAULTS,
            config_module.TOOL_ROLES,
            config_module.COMMAND_TOOL_DEFAULTS,
            config_module.GENERATED_DISPATCH_ROOT,
            config_module.ADAPTERS,
            config_module.TASK_CONTRACT_VOCABULARY,
        ) = self.original_runtime
        (
            ai_cli.TOOLS,
            ai_cli.TOOL_NOTES,
            ai_cli.TOOL_DEFAULTS,
            ai_cli.TOOL_ROLES,
            ai_cli.COMMAND_TOOL_DEFAULTS,
            ai_cli.GENERATED_DISPATCH_ROOT,
            ai_cli.ADAPTERS,
            ai_cli.TASK_CONTRACT_VOCABULARY,
        ) = self.original_runtime

    def test_frontmatter_parsing_and_body_extraction(self) -> None:
        text = (
            "---\n"
            "id: test-doc\n"
            "type: rule\n"
            "tags:\n"
            "  - tag1\n"
            "  - tag2\n"
            "relations:\n"
            "- type: relates_to\n"
            "  target: rule-a\n"
            "---\n"
            "# Test Heading\n"
            "Body text."
        )
        meta, body = ai_cli.parse_frontmatter(text)
        self.assertEqual("test-doc", meta.get("id"))
        self.assertEqual("rule", meta.get("type"))
        self.assertEqual(["tag1", "tag2"], meta.get("tags"))
        self.assertEqual(
            [{"type": "relates_to", "target": "rule-a"}],
            meta.get("relations"),
        )
        self.assertEqual("# Test Heading\nBody text.", body.strip())

    def test_frontmatter_inline_lists_indented_relations_and_quoted_scalars(self) -> None:
        text = (
            "---\n"
            "id: test-doc\n"
            "type: rule\n"
            "owner: system # stripped comment\n"
            "description: First folded line\n"
            "  and its continuation.\n"
            "tags: [schema, \"doc, metadata\", 'owner''s']\n"
            "relations:\n"
            "  - type: depends_on\n"
            "    target: workflow-execution\n"
            "    note: \"Keeps: colon # hash\"\n"
            "---\n"
            "# Test Heading\n"
        )
        meta, _ = ai_cli.parse_frontmatter(text)
        self.assertEqual("system", meta.get("owner"))
        self.assertEqual("First folded line and its continuation.", meta.get("description"))
        self.assertEqual(["schema", "doc, metadata", "owner's"], meta.get("tags"))
        self.assertEqual(
            [
                {
                    "type": "depends_on",
                    "target": "workflow-execution",
                    "note": "Keeps: colon # hash",
                }
            ],
            meta.get("relations"),
        )

    def test_frontmatter_rejects_unsupported_nesting_without_partial_metadata(self) -> None:
        text = (
            "---\n"
            "id: test-doc\n"
            "type: rule\n"
            "relations:\n"
            "  mapping:\n"
            "    target: rule-a\n"
            "---\n"
            "# Body survives\n"
        )
        meta, body = ai_cli.parse_frontmatter(text)
        self.assertEqual({}, meta)
        self.assertEqual("# Body survives", body.strip())
    def test_registry_generation_includes_valid_documents(self) -> None:
        (self.ai / "rules" / "rule-a.md").write_text(
            "---\nid: rule-a\ntype: rule\ndomain: control-plane\nstatus: active\nowner: system\n---\n# Rule A\nBody A.",
            encoding="utf-8",
        )
        registry = ai_cli.generate_registry(self.ai)
        self.assertEqual(2, registry["schema_version"])
        self.assertEqual("ai sync", registry["generator"])
        self.assertEqual(1, len(registry["documents"]))
        doc = registry["documents"][0]
        self.assertEqual("rule-a", doc["id"])
        self.assertEqual(".ai/rules/rule-a.md", doc["path"])
        self.assertEqual("control-plane", doc["corpus"])
        self.assertEqual("rule", doc["type"])
        self.assertEqual("Rule A", doc["title"])

    def test_graceful_exclusion_of_files_without_frontmatter(self) -> None:
        (self.ai / "rules" / "no-fm.md").write_text("# Plain File\nNo frontmatter here.", encoding="utf-8")
        registry = ai_cli.generate_registry(self.ai)
        self.assertEqual(0, len(registry["documents"]))

    def _write_product_doc(self, name: str, metadata: dict[str, str] | None = None) -> Path:
        product_dir = self.root / "project" / "docs"
        product_dir.mkdir(parents=True, exist_ok=True)
        path = product_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if metadata is None:
            path.write_text("# Legacy Product Document\nBody.\n", encoding="utf-8")
            return path
        lines = [
            "---",
            f"id: {metadata.get('id', 'product-example')}",
            f"corpus: {metadata.get('corpus', 'product')}",
            f"type: {metadata.get('type', 'architecture')}",
            f"domain: {metadata.get('domain', 'rendering')}",
            f"audiences: [{metadata.get('audiences', 'engineering')}]",
            f"authority: {metadata.get('authority', 'canonical')}",
            f"status: {metadata.get('status', 'active')}",
            f"maturity: {metadata.get('maturity', 'implemented')}",
            f"visibility: {metadata.get('visibility', 'internal')}",
            f"summary: {metadata.get('summary', 'Governed product summary.')}",
            "navigation: []",
            "relations: []",
            "subjects: []",
            "---",
            "# Product Example",
            "Body.",
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def _write_legacy_baseline(self, path: Path) -> None:
        relative = path.relative_to(self.root).as_posix()
        payload = {
            "schema_version": 1,
            "source_commit": "fixture",
            "corpus": "product",
            "documents": [{"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}],
        }
        (self.ai / "project" / "product-doc-legacy-baseline.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def test_product_document_schema_v2_uses_repository_relative_paths(self) -> None:
        self._write_product_doc("architecture.md", {})
        registry = ai_cli.generate_registry(self.ai)
        self.assertEqual([], registry["errors"])
        doc = next(item for item in registry["documents"] if item["id"] == "product-example")
        self.assertEqual("project/docs/architecture.md", doc["path"])
        self.assertEqual("product", doc["corpus"])
        self.assertEqual(2, doc["source_metadata_version"])

    def test_unchanged_baseline_product_doc_is_explicit_legacy_untyped(self) -> None:
        path = self._write_product_doc("legacy.md")
        self._write_legacy_baseline(path)
        registry = ai_cli.generate_registry(self.ai)
        self.assertEqual([], registry["errors"])
        doc = next(item for item in registry["documents"] if item["path"] == "project/docs/legacy.md")
        self.assertEqual("legacy-untyped", doc["type"])
        self.assertEqual("unclassified", doc["authority"])
        self.assertEqual("internal", doc["visibility"])

    def test_nested_legacy_product_document_is_included(self) -> None:
        path = self._write_product_doc("research/legacy.md")
        self._write_legacy_baseline(path)
        registry = ai_cli.generate_registry(self.ai)
        self.assertEqual([], registry["errors"])
        doc = next(item for item in registry["documents"] if item["path"] == "project/docs/research/legacy.md")
        self.assertEqual("legacy-product-research-legacy", doc["id"])
        self.assertEqual("legacy-untyped", doc["type"])
        self.assertIn("no authored authority", doc["warning"])

    def test_changed_legacy_product_doc_loses_exemption(self) -> None:
        path = self._write_product_doc("legacy.md")
        self._write_legacy_baseline(path)
        path.write_text("# Legacy Product Document\nChanged.\n", encoding="utf-8")
        registry = ai_cli.generate_registry(self.ai)
        self.assertTrue(any("content hash changed" in error for error in registry["errors"]))

    def test_product_metadata_unknown_enums_fail_closed(self) -> None:
        cases = {
            "corpus": "unknown-corpus",
            "type": "unknown-type",
            "audiences": "unknown-audience",
            "authority": "unknown-authority",
            "status": "unknown-status",
            "maturity": "unknown-maturity",
            "visibility": "unknown-visibility",
        }
        for field, value in cases.items():
            with self.subTest(field=field):
                product_dir = self.root / "project" / "docs"
                if product_dir.exists():
                    shutil.rmtree(product_dir)
                self._write_product_doc("invalid.md", {field: value})
                registry = ai_cli.generate_registry(self.ai)
                self.assertTrue(any(field in error and "unknown" in error for error in registry["errors"]))

    def test_product_domain_requires_topical_kebab_case_slug(self) -> None:
        self._write_product_doc("valid.md", {"domain": "rendering-core"})
        valid_registry = ai_cli.generate_registry(self.ai)
        self.assertEqual([], valid_registry["errors"])

        for invalid_domain in ("rendering core", "Rendering", ""):
            with self.subTest(domain=invalid_domain):
                shutil.rmtree(self.root / "project" / "docs")
                self._write_product_doc("invalid.md", {"domain": invalid_domain})
                registry = ai_cli.generate_registry(self.ai)
                self.assertTrue(
                    any("field 'domain' must be a topical kebab-case slug" in error for error in registry["errors"])
                )
    def test_duplicate_cross_corpus_id_and_unresolved_relation_fail_closed(self) -> None:
        (self.ai / "rules" / "rule-a.md").write_text(
            "---\nid: product-example\ntype: rule\ndomain: control-plane\nstatus: active\nowner: system\n---\n# Rule A",
            encoding="utf-8",
        )
        self._write_product_doc("duplicate.md", {})
        registry = ai_cli.generate_registry(self.ai)
        self.assertTrue(any("Duplicate document id" in error for error in registry["errors"]))


    def test_catalog_rendering(self) -> None:
        (self.ai / "rules" / "rule-a.md").write_text(
            "---\nid: rule-a\ntype: rule\ndomain: control-plane\nstatus: active\nowner: system\nsummary: First rule summary.\n---\n# Rule A\nBody A.",
            encoding="utf-8",
        )
        rendered = ai_cli.render_catalog("Rules", self.ai / "rules")
        self.assertIn("## Rules", rendered)
        self.assertIn("- **Rule A** — Body A. — `.ai/rules/rule-a.md`", rendered)




    def _write_reference_agent_docs(self) -> None:
        # The codex/claude integrations expand an agent_document at render time; create the minimal
        # ones so a real sync renders. (Only the two sync tests need these; creating them in setUp
        # would pollute the registry-count assertions of the non-sync tests.)
        for agent in ("executor", "reviewer"):
            (self.ai / "agents" / f"{agent}.md").write_text(
                f"---\nid: {agent}\ntype: agent\ndomain: control-plane\nstatus: active\nowner: system\n---\n"
                f"# {agent.title()}\nReference {agent} role body.\n",
                encoding="utf-8",
            )
        # the antigravity integration declares a blueprint_source; its existence is checked pre-mutation.
        blueprint = self.ai / "templates" / "pr-blueprint"
        blueprint.mkdir(parents=True, exist_ok=True)
        (blueprint / "README.md").write_text("# PR Blueprint\nFixture blueprint.\n", encoding="utf-8")
        command_catalog = self.ai / "skills" / "pr-blueprint" / "command-catalog.json"
        command_catalog.parent.mkdir(parents=True, exist_ok=True)
        command_catalog.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "title": "Command Families",
                    "commands": [
                        {
                            "token": "BLUEPRINT",
                            "label": "PR Blueprint",
                            "summary": "Build a report.",
                            "invocations": ["build <spec>"],
                        },
                        {
                            "token": "DOCS",
                            "label": "Documentation",
                            "summary": "Build documentation.",
                            "invocations": ["build"],
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

    def test_generated_adapters_contain_no_frontmatter(self) -> None:
        self._write_reference_agent_docs()
        (self.ai / "rules" / "rule-a.md").write_text(
            "---\nid: rule-a\ntype: rule\ndomain: control-plane\nstatus: active\nowner: system\n---\n# Rule A\nBody A.",
            encoding="utf-8",
        )
        ai_cli.cmd_sync(argparse.Namespace())
        agents_md = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        gemini_md = (self.root / "GEMINI.md").read_text(encoding="utf-8")
        self.assertFalse(agents_md.startswith("---"))
        self.assertFalse(gemini_md.startswith("---"))

    def test_sync_registry_and_manifest_are_idempotent(self) -> None:
        self._write_reference_agent_docs()
        (self.ai / "rules" / "rule-a.md").write_text(
            "---\nid: rule-a\ntype: rule\ndomain: control-plane\nstatus: active\nowner: system\n---\n# Rule A\nBody A.",
            encoding="utf-8",
        )
        ai_cli.cmd_sync(argparse.Namespace())
        registry_file = self.ai / "_registry.json"
        manifest_file = self.ai / "_manifest.json"
        self.assertTrue(registry_file.exists())
        self.assertTrue(manifest_file.exists())
        reg_bytes = registry_file.read_bytes()
        man_bytes = manifest_file.read_bytes()
        ai_cli.cmd_sync(argparse.Namespace())
        self.assertEqual(reg_bytes, registry_file.read_bytes())
        self.assertEqual(man_bytes, manifest_file.read_bytes())

    def test_registry_indexing_includes_blueprint_specs(self) -> None:
        spec_dir = self.ai / "templates" / "pr-blueprint" / "examples"
        spec_dir.mkdir(parents=True, exist_ok=True)
        (spec_dir / "test.spec.md").write_text(
            "---\nid: spec-test\ntype: spec\ndomain: frontend\nstatus: draft\nowner: system\n---\n# Spec Test\nBody.",
            encoding="utf-8",
        )
        registry = ai_cli.generate_registry(self.ai)
        doc_ids = [d["id"] for d in registry["documents"]]
        self.assertIn("spec-test", doc_ids)

    def test_target_reference_resolution_for_task_and_decision_ids(self) -> None:
        tasks_queue = self.ai / "tasks" / "queue" / "task_175_blueprint_gen3_core"
        tasks_queue.mkdir(parents=True, exist_ok=True)
        (self.ai / "rules" / "rule-a.md").write_text(
            "---\nid: rule-a\ntype: rule\ndomain: control-plane\nstatus: active\nowner: system\nrelations:\n  - type: references\n    target: task_175\n---\n# Rule A",
            encoding="utf-8",
        )
        registry = ai_cli.generate_registry(self.ai)
        self.assertNotIn("task_175", registry["unresolved_references"])

    def test_dangling_relation_targets_produce_unresolved_references(self) -> None:
        (self.ai / "rules" / "rule-a.md").write_text(
            "---\nid: rule-a\ntype: rule\ndomain: control-plane\nstatus: active\nowner: system\nrelations:\n  - type: references\n    target: non-existent-target-999\n---\n# Rule A",
            encoding="utf-8",
        )
        registry = ai_cli.generate_registry(self.ai)
        self.assertIn("non-existent-target-999", registry["unresolved_references"])

    def test_decision_target_resolution_from_decisions_file(self) -> None:
        """Decision IDs extracted from decisions.md resolve relation targets (P1-4)."""
        (self.ai / "project" / "decisions.md").write_text(
            "---\nid: project-decisions\ntype: decision\ndomain: control-plane\nstatus: active\nowner: system\n---\n"
            "# Decisions\n\n## D1 First Decision\nText.\n\n## Decision-42 Second\nMore text.\n",
            encoding="utf-8",
        )
        (self.ai / "rules" / "rule-ref-decision.md").write_text(
            "---\nid: rule-ref-decision\ntype: rule\ndomain: control-plane\nstatus: active\nowner: system\nrelations:\n  - type: references\n    target: D1\n  - type: references\n    target: Decision-42\n---\n# Rule Ref Decision\nBody.",
            encoding="utf-8",
        )
        registry = ai_cli.generate_registry(self.ai)
        self.assertNotIn("D1", registry["unresolved_references"])
        self.assertNotIn("Decision-42", registry["unresolved_references"])

    def test_project_document_templates_cover_required_variants(self) -> None:
        expected = {
            "product-requirements.md.tmpl": "product-requirements",
            "user-tutorial.md.tmpl": "tutorial",
            "user-how-to.md.tmpl": "how-to",
            "user-reference.md.tmpl": "reference",
            "user-explanation.md.tmpl": "explanation",
            "engineering-architecture.md.tmpl": "architecture",
            "engineering-spec.md.tmpl": "spec",
            "engineering-reference.md.tmpl": "reference",
            "engineering-runbook.md.tmpl": "runbook",
            "engineering-decision.md.tmpl": "decision",
            "research.md.tmpl": "research",
            "proposal.md.tmpl": "proposal",
        }
        template_root = REPO_ROOT / ".ai" / "templates" / "project-doc"
        self.assertEqual(set(expected), {path.name for path in template_root.glob("*.md.tmpl")})
        required = {
            "id", "corpus", "type", "domain", "audiences", "authority", "status", "maturity",
            "visibility", "summary", "navigation", "relations", "subjects",
        }
        for filename, doc_type in expected.items():
            with self.subTest(filename=filename):
                meta, _ = ai_cli.parse_frontmatter(
                    (template_root / filename).read_text(encoding="utf-8")
                )
                self.assertTrue(required.issubset(meta))
                self.assertEqual("product", meta["corpus"])
                self.assertEqual(doc_type, meta["type"])
                self.assertEqual("internal", meta["visibility"])


if __name__ == "__main__":
    unittest.main()
