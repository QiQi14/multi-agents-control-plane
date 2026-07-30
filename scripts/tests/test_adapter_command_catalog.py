from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import scripts.extension_registry as extension_registry
from scripts.ai_plane import adapter_render


ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / ".ai" / "skills" / "pr-blueprint" / "command-catalog.json"


class AdapterCommandCatalogTests(unittest.TestCase):
    def descriptor(self, source: str) -> dict:
        return {
            "command_catalog_source": source,
            "commands": {
                "BLUEPRINT": "tool blueprint",
                "DOCS": "tool docs",
            },
        }

    def write_catalog(self, root: Path, payload: dict) -> str:
        relative = ".ai/catalog.json"
        path = root / relative
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return relative

    def test_generic_catalog_renders_descriptor_commands(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = self.write_catalog(
                root,
                {
                    "schema_version": 1,
                    "title": "Commands",
                    "commands": [
                        {
                            "token": "BLUEPRINT",
                            "label": "Blueprint",
                            "summary": "Build a report.",
                            "invocations": ["init <task>", "build <spec>"],
                        },
                        {
                            "token": "DOCS",
                            "label": "Docs",
                            "summary": "Inspect documentation.",
                            "invocations": ["build", "lint"],
                        },
                    ],
                },
            )
            rendered = adapter_render._command_catalog_text(self.descriptor(source), root)
        self.assertIn("## Commands", rendered)
        self.assertIn("`tool blueprint init <task>`", rendered)
        self.assertIn("`tool docs lint`", rendered)

    def test_malformed_duplicate_and_undeclared_tokens_fail_closed(self) -> None:
        base_row = {
            "token": "BLUEPRINT",
            "label": "Blueprint",
            "summary": "Build a report.",
            "invocations": ["build <spec>"],
        }
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = self.write_catalog(
                root,
                {
                    "schema_version": 1,
                    "title": "Commands",
                    "commands": [base_row, dict(base_row)],
                },
            )
            with self.assertRaisesRegex(adapter_render.RenderError, "duplicate command token"):
                adapter_render._command_catalog_text(self.descriptor(source), root)

            payload = json.loads((root / source).read_text(encoding="utf-8"))
            payload["commands"] = [
                {
                    **base_row,
                    "token": "UNKNOWN",
                }
            ]
            (root / source).write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(adapter_render.RenderError, "absent from integration.commands"):
                adapter_render._command_catalog_text(self.descriptor(source), root)

    def test_catalog_source_must_be_contained_and_exist(self) -> None:
        integration = {
            "format": "fixture",
            "agent_document": None,
            "command_catalog_source": "../outside.json",
            "catalogs": {},
            "argument_placeholder": "<task_id>",
            "generated_file_extension": ".md",
            "invoke_separator": " ",
            "detect_marker": "AGENTS.md",
            "commands": {"BLUEPRINT": "tool blueprint", "DOCS": "tool docs"},
            "render": {
                "artifacts": [
                    {
                        "kind": "marker_document",
                        "path": "AGENTS.md",
                        "template": "templates/AGENTS.md.tmpl",
                    }
                ]
            },
        }
        with self.assertRaisesRegex(extension_registry.RegistryError, "contained path"):
            extension_registry._validate_integration_descriptor(integration, source="fixture")

        integration["command_catalog_source"] = ".ai/missing.json"
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(adapter_render.RenderError, "missing-source"):
                adapter_render.validate_descriptor_sources(integration, Path(folder))

    def test_live_catalog_and_generated_adapter_exposure(self) -> None:
        payload = json.loads(CATALOG.read_text(encoding="utf-8"))
        self.assertEqual(["BLUEPRINT", "DOCS"], [row["token"] for row in payload["commands"]])

        surfaces = (
            ROOT / "AGENTS.md",
            ROOT / "CLAUDE.md",
            ROOT / ".claude" / "README.md",
            ROOT / "GEMINI.md",
            ROOT / ".agents" / "skills" / "index.md",
        )
        for surface in surfaces:
            with self.subTest(surface=surface.relative_to(ROOT)):
                text = surface.read_text(encoding="utf-8")
                self.assertIn("## Command Families", text)
                self.assertIn("ai blueprint init --from-task <task_id> [--base <commit>]", text)
                self.assertIn("ai docs build", text)
                self.assertNotIn("{command_catalog}", text)

        index = (ROOT / ".agents" / "skills" / "index.md").read_text(encoding="utf-8")
        self.assertEqual(1, index.count("- `pr-blueprint`:"))
        self.assertIn("`.ai/skills/pr-blueprint/SKILL.md`", index)

        skill = (ROOT / ".ai" / "skills" / "pr-blueprint" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("`.ai/templates/pr-blueprint/`", skill)
        self.assertIn("`.agents/skills/pr-blueprint/template/`", skill)
        self.assertTrue((ROOT / ".ai" / "templates" / "pr-blueprint").is_dir())
        self.assertTrue((ROOT / ".agents" / "skills" / "pr-blueprint" / "template").is_dir())
        self.assertTrue(
            (ROOT / ".agents" / "skills" / "pr-blueprint" / "command-catalog.json").is_file()
        )


if __name__ == "__main__":
    unittest.main()
