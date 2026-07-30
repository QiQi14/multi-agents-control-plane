"""task_190a behaviors beyond the raw A/B: core vendor-neutrality, the synthetic non-vendor
integration, the neutral zero-extension path, exact render-source semantics, and the pre-mutation
transaction guarantee. The byte-identity of the reference adapters is proven separately by
scripts/tests/test_ai_adapter_ab.py."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scripts.ai_plane.config as config_module
import scripts.ai_cli as ai_cli

REPO_ROOT = Path(__file__).resolve().parents[2]
_CORE_MODULES = ("scripts/ai_plane/config.py", "scripts/ai_plane/sync.py",
                 "scripts/ai_plane/adapter_render.py")
_BANNED = ("codex", "claude", "antigravity", "gemini", "cargo")


class StaticVendorNeutralityTests(unittest.TestCase):
    def test_core_modules_carry_no_vendor_identifier_or_format_branch(self) -> None:
        for rel in _CORE_MODULES:
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
            lowered = text.lower()
            for banned in _BANNED:
                self.assertNotIn(banned, lowered, f"{rel} contains vendor identifier {banned!r}")
            self.assertIsNone(re.search(r"\brust\b", lowered), f"{rel} references rust")
            self.assertNotIn("if format ==", lowered, f"{rel} branches on a vendor format")


def _integration_manifest(ext_id: str, integration: dict) -> dict:
    return {
        "id": ext_id, "version": "1.0.0", "api_version": 1, "types": ["integration"],
        "root": f"scripts/extensions/{ext_id}", "read_roots": [".ai"],
        "write_roots": [integration["detect_marker"].split("/")[0] if "/" in integration["detect_marker"]
                        else integration["detect_marker"]],
        "platforms": ["any"], "integration": integration,
    }


def _base_integration(ext_id: str, marker: str, template_ref: str, fmt: str = "generic") -> dict:
    return {
        "format": fmt, "agent_document": None, "catalogs": {},
        "argument_placeholder": "<task_id>", "generated_file_extension": ".md",
        "invoke_separator": " ", "detect_marker": marker,
        "commands": {"INIT": "ai init", "SYNC": "ai sync", "DISPATCH": "ai dispatch",
                     "REVIEW": "ai review", "QA": "ai qa"},
        "render": {"artifacts": [{"kind": "marker_document", "path": marker, "template": template_ref}]},
    }


class _RepoFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.ai = self.root / ".ai"
        for d in ("rules", "workflows", "agents", "project", "memory", "skills", "migration"):
            (self.ai / d).mkdir(parents=True)
        self._saved = (config_module.TOOLS, config_module.TOOL_NOTES, config_module.TOOL_DEFAULTS,
                       config_module.TOOL_ROLES, config_module.COMMAND_TOOL_DEFAULTS,
                       config_module.GENERATED_DISPATCH_ROOT, config_module.ADAPTERS,
                       config_module.TASK_CONTRACT_VOCABULARY)
        for patch in (mock.patch.object(ai_cli.constants, "ROOT", self.root),
                      mock.patch.object(ai_cli.constants, "AI", self.ai)):
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        (config_module.TOOLS, config_module.TOOL_NOTES, config_module.TOOL_DEFAULTS,
         config_module.TOOL_ROLES, config_module.COMMAND_TOOL_DEFAULTS,
         config_module.GENERATED_DISPATCH_ROOT, config_module.ADAPTERS,
         config_module.TASK_CONTRACT_VOCABULARY) = self._saved

    def write_integration(self, ext_id: str, integration: dict, templates: dict[str, str]) -> None:
        ext_dir = self.root / "scripts" / "extensions" / ext_id
        ext_dir.mkdir(parents=True, exist_ok=True)
        (ext_dir / "extension.json").write_text(
            json.dumps(_integration_manifest(ext_id, integration), indent=2), encoding="utf-8")
        for ref, body in templates.items():
            target = ext_dir / ref
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")

    def write_config(self, tools_yaml: str, enabled: list[str], default_tool: str | None = None) -> None:
        enabled_yaml = "\n".join(f"    - {e}" for e in enabled) if enabled else ""
        enabled_block = f"  enabled:\n{enabled_yaml}" if enabled else "  enabled: []"
        dt = default_tool or re.match(r"\s*([a-z][a-z0-9_-]*):", tools_yaml).group(1)
        (self.ai / "config.yaml").write_text(
            f"""version: 1
defaults:
  research_tool: {dt}
  planning_tool: {dt}
  implementation_tool: {dt}
  review_tool: {dt}
  generated_dispatch_root: .ai/adapters
extensions:
  roots:
    - scripts/extensions
{enabled_block}
tools:
{tools_yaml}
""", encoding="utf-8")
        ai_cli.initialize_runtime_config(self.ai / "config.yaml")

    def tool(self, name: str, render_source: str | None) -> str:
        rs = "null" if render_source is None else render_source
        return (f"  {name}:\n    role: r\n    default_isolation: patch\n    notes:\n      - n\n"
                f"    adapter:\n      render_source: {rs}\n")

    def sync(self) -> None:
        ai_cli.cmd_sync(argparse.Namespace())

    def sync_exit(self) -> tuple[int, str]:
        stderr = io.StringIO()
        code = 0
        with contextlib.redirect_stderr(stderr):
            try:
                ai_cli.cmd_sync(argparse.Namespace())
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
        return code, stderr.getvalue()

    def write_raw_manifest(self, ext_id: str, manifest: dict, templates: dict[str, str]) -> None:
        ext_dir = self.root / "scripts" / "extensions" / ext_id
        ext_dir.mkdir(parents=True, exist_ok=True)
        (ext_dir / "extension.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        for ref, body in templates.items():
            target = ext_dir / ref
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")

    def config_exit(self, tools_yaml: str, enabled: list[str]) -> tuple[int, str]:
        stderr = io.StringIO()
        code = 0
        with contextlib.redirect_stderr(stderr):
            try:
                self.write_config(tools_yaml, enabled)
            except (SystemExit, config_module.ConfigError) as exc:
                code = 1
                stderr.write(str(exc))
        return code, stderr.getvalue()


class IntegrationSchemaAuthorityTests(_RepoFixture):
    # R1-190A-1: complete schema + pre-mutation authority containment, all fail-closed before write.
    def test_output_outside_declared_write_roots_fails_closed(self) -> None:
        integration = _base_integration("w", "OUT.md", "templates/m.tmpl")
        manifest = {
            "id": "w", "version": "1.0.0", "api_version": 1, "types": ["integration"],
            "root": "scripts/extensions/w", "read_roots": [".ai"], "write_roots": [".elsewhere"],
            "platforms": ["any"], "integration": integration,
        }
        self.write_raw_manifest("w", manifest, {"templates/m.tmpl": "{generated_warning}\n"})
        code, err = self.config_exit(self.tool("w", "w"), ["w"])
        self.assertEqual(1, code)
        self.assertIn("out-of-authority-write-root", err)

    def test_catalog_source_outside_read_roots_fails_closed(self) -> None:
        integration = _base_integration("r", "R.md", "templates/m.tmpl")
        integration["catalogs"] = {"docs": {"title": "D", "source_root": "elsewhere", "include": None, "exclude": []}}
        manifest = {
            "id": "r", "version": "1.0.0", "api_version": 1, "types": ["integration"],
            "root": "scripts/extensions/r", "read_roots": [".ai"], "write_roots": ["R.md"],
            "platforms": ["any"], "integration": integration,
        }
        self.write_raw_manifest("r", manifest, {"templates/m.tmpl": "{generated_warning}\n"})
        code, err = self.config_exit(self.tool("r", "r"), ["r"])
        self.assertEqual(1, code)
        self.assertIn("out-of-authority-read-root", err)

    def test_template_escaping_extension_root_fails_closed(self) -> None:
        integration = _base_integration("e", "E.md", "../escape.tmpl")
        self.write_integration("e", integration, {})
        code, err = self.config_exit(self.tool("e", "e"), ["e"])
        self.assertEqual(1, code)
        self.assertIn("invalid-render-descriptor", err)  # a non-contained template ref is rejected at schema time

    def test_command_document_missing_invocation_fails_closed(self) -> None:
        integration = _base_integration("c", "C.md", "templates/m.tmpl")
        integration["render"]["artifacts"].append(
            {"kind": "command_document", "path": "cmd.md", "template": "templates/m.tmpl"})
        integration["write_roots"] = None  # ignored; manifest write_roots below
        manifest = {
            "id": "c", "version": "1.0.0", "api_version": 1, "types": ["integration"],
            "root": "scripts/extensions/c", "read_roots": [".ai"], "write_roots": ["C.md", "cmd.md"],
            "platforms": ["any"], "integration": {k: v for k, v in integration.items() if k != "write_roots"},
        }
        self.write_raw_manifest("c", manifest, {"templates/m.tmpl": "{generated_warning}\n"})
        code, err = self.config_exit(self.tool("c", "c"), ["c"])
        self.assertEqual(1, code)
        self.assertIn("invalid-render-descriptor", err)

    def test_unresolved_placeholder_leaves_tree_and_manifest_byte_identical(self) -> None:
        # a valid schema whose TEMPLATE CONTENT references an unknown placeholder fails at render
        # (pre-mutation plan), before any write — the prior tree + manifest are untouched.
        self.write_integration("p", _base_integration("p", "P.md", "templates/m.tmpl"),
                               {"templates/m.tmpl": "{generated_warning}\n\n{totally_unknown}\n"})
        self.write_config(self.tool("p", "p"), enabled=["p"])
        snapshot = {path.relative_to(self.root).as_posix(): path.read_bytes()
                    for path in sorted(self.root.rglob("*"))
                    if path.is_file() and "extensions" not in path.parts and "__pycache__" not in path.parts}
        code, err = self.sync_exit()
        self.assertEqual(1, code)
        self.assertIn("unresolved-render-placeholder", err)
        after = {path.relative_to(self.root).as_posix(): path.read_bytes()
                 for path in sorted(self.root.rglob("*"))
                 if path.is_file() and "extensions" not in path.parts and "__pycache__" not in path.parts}
        self.assertEqual(snapshot, after, "a failed render mutated the tree/manifest")


class SyntheticIntegrationTests(_RepoFixture):
    def test_non_vendor_integration_renders_with_zero_core_changes(self) -> None:
        self.write_integration("acme", _base_integration("acme", "ACME.md", "templates/m.tmpl"),
                               {"templates/m.tmpl": "{generated_warning}\n\n# Acme\n\nAdapter for `{tool}`.\n"})
        self.write_config(self.tool("acme", "acme"), enabled=["acme"])
        self.sync()
        marker = (self.root / "ACME.md").read_text(encoding="utf-8")
        self.assertIn("# Acme", marker)
        self.assertIn("Adapter for `acme`.", marker)

    def test_unknown_kind_missing_template_and_duplicate_output_fail_closed(self) -> None:
        # unknown artifact kind -> unknown-artifact-kind at manifest validation
        bad = _base_integration("k", "K.md", "templates/m.tmpl")
        bad["render"]["artifacts"] = [{"kind": "not_a_kind", "path": "K.md", "template": "templates/m.tmpl"}]
        self.write_integration("k", bad, {"templates/m.tmpl": "{generated_warning}\n"})
        code, err = self._config_exit(self.tool("k", "k"), ["k"])
        self.assertEqual(1, code)
        self.assertIn("unknown-artifact-kind", err)

    def test_missing_template_fails_closed_before_mutation(self) -> None:
        # R1-190A-1: a referenced template that does not exist under the extension root fails closed
        # at discovery (config resolution) — before any sync/mutation — with the named reason.
        self.write_integration("m", _base_integration("m", "M.md", "templates/absent.tmpl"), {})
        code, err = self._config_exit(self.tool("m", "m"), ["m"])
        self.assertEqual(1, code)
        self.assertIn("missing-template", err)

    def _config_exit(self, tools_yaml: str, enabled: list[str]) -> tuple[int, str]:
        stderr = io.StringIO()
        code = 0
        with contextlib.redirect_stderr(stderr):
            try:
                self.write_config(tools_yaml, enabled)
            except (SystemExit, config_module.ConfigError) as exc:
                code = 1
                stderr.write(str(exc))
        return code, stderr.getvalue()


class NeutralAndSelectionTests(_RepoFixture):
    def test_neutral_single_tool_zero_extension_has_no_vendor_reference(self) -> None:
        self.write_config(self.tool("solo", None), enabled=[])
        task_dir = self.ai / "tasks" / "queue" / "task_neutral"
        task_dir.mkdir(parents=True)
        (task_dir / "task.yaml").write_text(
            """id: "task_neutral"
title: "Neutral lifecycle"
feature: "neutral"
status: "queued"
risk: "low"
preferred_tool: "solo"
review_tool: "solo"
isolation_strategy: "patch"
verification_scope: "control-plane"
target_files:
  - ".ai/**"
  - "AGENTS.md"
forbidden_files: []
input_contract: "Exercise the neutral lifecycle."
output_contract: "Produce neutral generated outputs."
acceptance_tests:
  - "Neutral lifecycle passes."
commands: []
known_risks: "none"
""",
            encoding="utf-8",
        )
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Fixture"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture baseline"], cwd=self.root, check=True)

        for argv in (
            ["init"],
            ["sync"],
            ["dispatch", "task_neutral", "--tool", "solo"],
            ["review", "task_neutral", "--tool", "solo"],
            ["verify", "task_neutral", "--base", "HEAD", "--plan"],
        ):
            ai_cli.main(argv)

        manifest = json.loads((self.ai / "_manifest.json").read_text(encoding="utf-8"))
        generated_paths = [self.root / entry["path"] for entry in manifest["entries"]]
        self.assertTrue(generated_paths, "neutral lifecycle produced no generated files")
        for path in generated_paths:
            self.assertTrue(path.is_file(), f"manifest-listed generated file is missing: {path}")
            generated = path.read_bytes().decode("utf-8").lower()
            for banned in (*_BANNED, "rust"):
                self.assertNotIn(banned, generated, f"{path} contains banned identifier {banned!r}")
        self.assertIn("agent control plane", (self.root / "AGENTS.md").read_text(encoding="utf-8").lower())

    def test_missing_render_source_fails_closed_and_never_falls_back(self) -> None:
        # a non-empty render_source that resolves to no enabled integration fails closed; it does
        # NOT silently fall back to the neutral descriptor.
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(config_module.ConfigError) as ctx:
            self.write_config(self.tool("t", "ghost"), enabled=[])
        self.assertIn("missing-render-source", str(ctx.exception))


class PreMutationTransactionTests(_RepoFixture):
    def _snapshot(self) -> dict[str, bytes]:
        snap = {}
        for path in sorted(self.root.rglob("*")):
            if path.is_file() and "extensions" not in path.parts and "__pycache__" not in path.parts:
                snap[path.relative_to(self.root).as_posix()] = path.read_bytes()
        return snap

    def test_duplicate_render_output_across_tools_leaves_files_and_manifest_byte_identical(self) -> None:
        # two neutral tools both target AGENTS.md -> duplicate-render-output; the first sync must
        # fail closed BEFORE any write, leaving the tree + manifest untouched.
        self.write_integration("a", _base_integration("a", "SHARED.md", "templates/m.tmpl"),
                               {"templates/m.tmpl": "{generated_warning}\n\n# A\n"})
        self.write_integration("b", _base_integration("b", "SHARED.md", "templates/m.tmpl"),
                               {"templates/m.tmpl": "{generated_warning}\n\n# B\n"})
        self.write_config(self.tool("a", "a") + self.tool("b", "b"), enabled=["a", "b"])
        before = self._snapshot()
        code, err = self.sync_exit()
        self.assertEqual(1, code)
        self.assertIn("duplicate-render-output", err)
        self.assertFalse((self.root / "SHARED.md").exists())
        self.assertEqual(before, self._snapshot(), "a failed sync mutated the tree")


class IntegrationRemovalPruningTests(_RepoFixture):
    # R2-190A-2/3: a valid transition that disables an integration prunes ONLY its former outputs
    # through the hash manifest and is idempotent.
    def test_removing_integration_tool_prunes_only_its_output_and_is_idempotent(self) -> None:
        self.write_integration("acme", _base_integration("acme", "ACME.md", "templates/m.tmpl"),
                               {"templates/m.tmpl": "{generated_warning}\n\n# Acme\n"})
        self.write_config(self.tool("a", "acme") + self.tool("z", None), enabled=["acme"])
        self.sync()
        self.assertTrue((self.root / "ACME.md").is_file())
        self.assertTrue((self.root / "AGENTS.md").is_file())  # neutral tool z

        # valid transition: drop tool "a" and disable the integration.
        self.write_config(self.tool("z", None), enabled=[])
        self.sync()
        self.assertFalse((self.root / "ACME.md").exists(), "former integration output was not pruned")
        self.assertTrue((self.root / "AGENTS.md").is_file())
        tracked = {e["path"] for e in json.loads((self.ai / "_manifest.json").read_text(encoding="utf-8"))["entries"]}
        self.assertNotIn("ACME.md", tracked)

        manifest_bytes = (self.ai / "_manifest.json").read_bytes()
        self.sync()
        self.assertEqual(manifest_bytes, (self.ai / "_manifest.json").read_bytes(), "post-removal sync is not idempotent")


class SchemaCompletenessTests(_RepoFixture):
    # R2-190A-2: complete nested schema + canonical source existence, each fail-closed.
    def _manifest(self, ext_id: str, integration: dict, write_roots: list[str]) -> dict:
        return {
            "id": ext_id, "version": "1.0.0", "api_version": 1, "types": ["integration"],
            "root": f"scripts/extensions/{ext_id}", "read_roots": [".ai"], "write_roots": write_roots,
            "platforms": ["any"], "integration": integration,
        }

    def _with_catalog(self, ext_id: str, include: list | None) -> dict:
        integration = _base_integration(ext_id, f"{ext_id.upper()}.md", "templates/m.tmpl")
        integration["catalogs"] = {"docs": {"title": "D", "source_root": ".ai/project", "include": include, "exclude": []}}
        return integration

    def test_catalog_include_must_be_sorted(self) -> None:
        self.write_raw_manifest("s", self._manifest("s", self._with_catalog("s", ["b.md", "a.md"]), ["S.md"]),
                                {"templates/m.tmpl": "{generated_warning}\n"})
        code, err = self.config_exit(self.tool("s", "s"), ["s"])
        self.assertEqual(1, code)
        self.assertIn("must be sorted", err)

    def test_catalog_include_must_be_duplicate_free(self) -> None:
        self.write_raw_manifest("s", self._manifest("s", self._with_catalog("s", ["a.md", "a.md"]), ["S.md"]),
                                {"templates/m.tmpl": "{generated_warning}\n"})
        code, err = self.config_exit(self.tool("s", "s"), ["s"])
        self.assertEqual(1, code)
        self.assertIn("duplicate-free", err)

    def test_legacy_alias_path_escaping_fails_closed(self) -> None:
        integration = _base_integration("l", "L.md", "templates/m.tmpl")
        integration["render"]["artifacts"].append({
            "kind": "rules_tree", "source_root": ".ai/rules", "destination": ".agents/rules",
            "strip_frontmatter": True,
            "legacy_aliases": [{"path": "../../escape.md", "title": "T", "replacement": ".ai/x", "reason": "r"}],
        })
        self.write_raw_manifest("l", self._manifest("l", integration, ["L.md", ".agents"]),
                                {"templates/m.tmpl": "{generated_warning}\n"})
        code, err = self.config_exit(self.tool("l", "l"), ["l"])
        self.assertEqual(1, code)
        self.assertIn("bare contained filename", err)

    def test_skills_entry_glob_must_be_exact(self) -> None:
        integration = _base_integration("g", "G.md", "templates/m.tmpl")
        integration["render"]["artifacts"].append({
            "kind": "skills_tree", "source_root": ".ai/skills", "destination": ".agents/skills",
            "transform": "generated_tree",
            "index": {"path": ".agents/skills/index.md", "template": "templates/idx.tmpl",
                      "entry_glob": "*.md", "entry_template": "- {skill_name} {skill_source}",
                      "blueprint_entry_template": None},
            "blueprint_source": None, "blueprint_destination": None,
        })
        self.write_raw_manifest("g", self._manifest("g", integration, ["G.md", ".agents"]),
                                {"templates/m.tmpl": "{generated_warning}\n", "templates/idx.tmpl": "{generated_warning}\n"})
        code, err = self.config_exit(self.tool("g", "g"), ["g"])
        self.assertEqual(1, code)
        self.assertIn("*/SKILL.md", err)

    def test_entry_template_missing_required_placeholder_fails_closed(self) -> None:
        integration = _base_integration("t", "T.md", "templates/m.tmpl")
        integration["render"]["artifacts"].append({
            "kind": "skills_tree", "source_root": ".ai/skills", "destination": ".agents/skills",
            "transform": "generated_tree",
            "index": {"path": ".agents/skills/index.md", "template": "templates/idx.tmpl",
                      "entry_glob": "*/SKILL.md", "entry_template": "- {skill_name}",
                      "blueprint_entry_template": None},
            "blueprint_source": None, "blueprint_destination": None,
        })
        self.write_raw_manifest("t", self._manifest("t", integration, ["T.md", ".agents"]),
                                {"templates/m.tmpl": "{generated_warning}\n", "templates/idx.tmpl": "{generated_warning}\n"})
        code, err = self.config_exit(self.tool("t", "t"), ["t"])
        self.assertEqual(1, code)
        self.assertIn("{skill_source}", err)

    def test_missing_catalog_source_leaves_tree_byte_identical(self) -> None:
        integration = _base_integration("m", "M.md", "templates/m.tmpl")
        integration["catalogs"] = {"docs": {"title": "D", "source_root": ".ai/nonexistent", "include": None, "exclude": []}}
        self.write_raw_manifest("m", self._manifest("m", integration, ["M.md"]),
                                {"templates/m.tmpl": "{generated_warning}\n\n{catalog:docs}\n"})
        self.write_config(self.tool("m", "m"), enabled=["m"])  # passes discovery (source is contained, just absent)
        snapshot = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in sorted(self.root.rglob("*"))
                    if p.is_file() and "extensions" not in p.parts and "__pycache__" not in p.parts}
        code, err = self.sync_exit()
        self.assertEqual(1, code)
        self.assertIn("missing-source", err)
        after = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in sorted(self.root.rglob("*"))
                 if p.is_file() and "extensions" not in p.parts and "__pycache__" not in p.parts}
        self.assertEqual(snapshot, after, "a missing source mutated the tree")


if __name__ == "__main__":
    unittest.main()
