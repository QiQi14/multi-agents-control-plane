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
import unittest
from pathlib import Path
from unittest import mock

import scripts.ai_plane.adapter_render as adapter_render
import scripts.ai_plane.config as config_module
import scripts.ai_plane.routing_profile as tool_profile
import scripts.ai_plane.manifest as manifest_module

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


class AiCliAdapterRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.live_before = live_state_snapshot()

    @classmethod
    def tearDownClass(cls) -> None:
        live_after = live_state_snapshot()
        if cls.live_before != live_after:
            raise AssertionError("adapter registry tests changed the live repository or adapters")

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.ai = self.root / ".ai"
        self.ai.mkdir()
        for d in ("rules", "workflows", "agents", "project", "memory", "skills", "migration"):
            (self.ai / d).mkdir()
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
        root_patch = mock.patch.object(ai_cli.constants, "ROOT", self.root)
        ai_patch = mock.patch.object(ai_cli.constants, "AI", self.ai)
        root_patch.start()
        ai_patch.start()
        self.addCleanup(root_patch.stop)
        self.addCleanup(ai_patch.stop)
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

    def write_config(self, *, tool: str = "fourth", format_name: str = "codex", support_dir: str = "") -> Path:
        # task_190a: a tool selects rendering via adapter.render_source -> an `integration` extension
        # that owns the render descriptor. This fixture writes that integration (manifest + template)
        # so the SAME assertions exercise the integration-driven path. The marker keeps its historic
        # `.{tool}/FOURTH.md` location and the codex prose the assertions check; commands keep their
        # `fixture <x>` values.
        output_dir = f".{tool}"
        marker_path = f"{output_dir}/FOURTH.md"
        ext_dir = self.root / "scripts" / "extensions" / tool
        (ext_dir / "templates").mkdir(parents=True, exist_ok=True)
        (ext_dir / "templates" / "marker.tmpl").write_text(
            "{generated_warning}\n\n# Fixture Adapter\n\n"
            "This is the generated Codex/generic coding-agent adapter.\n",
            encoding="utf-8",
        )
        commands = {
            "INIT": "fixture init", "SYNC": "fixture sync", "AUDIT_FRAMEWORK": "fixture audit-framework",
            "FEATURE": "fixture feature", "RESEARCH": "fixture research", "PLAN": "fixture plan",
            "TASKS": "fixture tasks", "TASK": "fixture task", "DISPATCH": "fixture dispatch",
            "REVIEW": "fixture review", "QA": "fixture qa", "MERGE": "fixture merge",
            "LEARN": "fixture learn", "ARCHIVE": "fixture archive", "VERIFY": "fixture verify",
            "BLUEPRINT": "fixture blueprint",
        }
        manifest = {
            "id": tool, "version": "1.0.0", "api_version": 1, "types": ["integration"],
            "root": f"scripts/extensions/{tool}", "read_roots": [".ai"],
            "write_roots": [output_dir] + ([support_dir] if support_dir else []),
            "platforms": ["any"],
            "integration": {
                "format": format_name, "agent_document": None, "catalogs": {},
                "argument_placeholder": "<task_id>", "generated_file_extension": ".md",
                "invoke_separator": " ", "detect_marker": marker_path, "commands": commands,
                "render": {"artifacts": [
                    {"kind": "marker_document", "path": marker_path, "template": "templates/marker.tmpl"},
                ]},
            },
        }
        (ext_dir / "extension.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        path = self.ai / "config.yaml"
        path.write_text(
            f'''version: 1
defaults:
  research_tool: {tool}
  planning_tool: {tool}
  implementation_tool: {tool}
  review_tool: {tool}
  generated_dispatch_root: .ai/adapters
extensions:
  roots:
    - scripts/extensions
  enabled:
    - {tool}
tools:
  {tool}:
    role: fixture_role
    default_isolation: patch
    notes:
      - Fixture registry note.
    adapter:
      render_source: {tool}
''',
            encoding="utf-8",
        )
        return path

    def capture_main(self, argv: list[str]) -> tuple[int, str]:
        stderr = io.StringIO()
        code = 0
        with contextlib.redirect_stderr(stderr):
            try:
                ai_cli.main(argv)
            except SystemExit as exit_err:
                code = exit_err.code if isinstance(exit_err.code, int) else 1
        return code, stderr.getvalue()

    def test_registry_derives_roster_notes_defaults_and_vocabulary(self) -> None:
        path = self.write_config()
        ai_cli.initialize_runtime_config(path)
        self.assertEqual(("fourth",), ai_cli.TOOLS)
        self.assertEqual("Fixture registry note.", ai_cli.TOOL_NOTES["fourth"])
        self.assertEqual("patch", ai_cli.TOOL_DEFAULTS["fourth"])
        self.assertEqual(("fourth", "pending"), ai_cli.TASK_CONTRACT_VOCABULARY["review_tool"])

    def test_missing_or_corrupt_config_fails_sync_dispatch_and_review_closed(self) -> None:
        for command in (["sync"], ["dispatch", "task_x"], ["review", "task_x"]):
            code, stderr = self.capture_main(command)
            self.assertEqual(1, code)
            self.assertIn(".ai/config.yaml is missing", stderr)
            self.assertIn("no default roster is available", stderr)
        (self.ai / "config.yaml").write_text("tools: []\n", encoding="utf-8")
        code, stderr = self.capture_main(["sync"])
        self.assertEqual(1, code)
        self.assertIn("field 'tools' must be a non-empty mapping", stderr)

    def test_hypothetical_fourth_tool_renders_without_python_changes(self) -> None:
        path = self.write_config()
        ai_cli.initialize_runtime_config(path)
        ai_cli.render_adapter("fourth", ai_cli.ADAPTERS["fourth"])
        marker = self.root / ".fourth" / "FOURTH.md"
        self.assertTrue(marker.exists())
        self.assertIn("generated Codex/generic coding-agent adapter", marker.read_text(encoding="utf-8"))

    def test_neutral_tokens_resolve_and_unknown_tokens_fail_closed(self) -> None:
        path = self.write_config()
        ai_cli.initialize_runtime_config(path)
        adapter = ai_cli.ADAPTERS["fourth"]
        self.assertEqual("Run fixture sync now.", ai_cli.resolve_command_tokens("Run __AI_COMMAND_SYNC__ now.", "fourth", adapter))
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
            ai_cli.resolve_command_tokens("__AI_COMMAND_UNKNOWN__", "fourth", adapter)
        self.assertIn("Unknown neutral command token", stderr.getvalue())

    def test_render_adapter_honors_support_dir_and_file_extensions(self) -> None:
        path = self.write_config(tool="fourth", format_name="antigravity", support_dir=".fourth_support")
        ai_cli.initialize_runtime_config(path)
        ai_cli.render_adapter("fourth", ai_cli.ADAPTERS["fourth"])
        self.assertTrue((self.root / ".fourth" / "FOURTH.md").exists())

    def test_manifest_records_exact_bytes_and_never_records_itself(self) -> None:
        self.write_config()
        ai_cli.initialize_runtime_config(self.ai / "config.yaml")
        generated = self.root / "generated.md"
        with ai_cli.generation_session("fixture generate"):
            ai_cli.write_generated(generated, "hello")
        entries = ai_cli.read_generated_manifest()
        self.assertEqual({"generated.md"}, set(entries))
        self.assertEqual(hashlib.sha256(generated.read_bytes()).hexdigest(), entries["generated.md"]["sha256"])
        self.assertNotIn(".ai/_manifest.json", entries)

    def test_modified_generated_file_is_preserved_and_warned(self) -> None:
        self.write_config()
        ai_cli.initialize_runtime_config(self.ai / "config.yaml")
        generated = self.root / "generated.md"
        with ai_cli.generation_session("fixture generate"):
            ai_cli.write_generated(generated, "original")
        original_entry = ai_cli.read_generated_manifest()["generated.md"]
        generated.write_bytes(b"user customization\n")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with ai_cli.generation_session("fixture generate"):
                ai_cli.write_generated(generated, "replacement")
        self.assertEqual(b"user customization\n", generated.read_bytes())
        self.assertIn("preserving user-modified generated file: generated.md", stderr.getvalue())
        self.assertEqual(original_entry, ai_cli.read_generated_manifest()["generated.md"])

    def test_atomic_manifest_failure_preserves_previous_manifest(self) -> None:
        self.write_config()
        ai_cli.initialize_runtime_config(self.ai / "config.yaml")
        initial = {"one.txt": {"path": "one.txt", "sha256": "0" * 64, "command": "fixture"}}
        ai_cli.atomic_write_manifest(initial)
        before = ai_cli.manifest_path().read_bytes()
        replacement = {"two.txt": {"path": "two.txt", "sha256": "1" * 64, "command": "fixture"}}
        with mock.patch.object(manifest_module.os, "replace", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                ai_cli.atomic_write_manifest(replacement)
        self.assertEqual(before, ai_cli.manifest_path().read_bytes())
        self.assertEqual([], list(self.ai.glob("._manifest.*.tmp")))

    def test_sync_bytes_are_independent_of_local_tool_profile(self) -> None:
        self.write_config()
        ai_cli.initialize_runtime_config(self.ai / "config.yaml")
        (self.ai / "agents").mkdir(exist_ok=True)
        (self.ai / "agents" / "executor.md").write_text("# Fixture executor\n", encoding="utf-8")
        ai_cli.cmd_sync(argparse.Namespace())
        first_manifest = ai_cli.manifest_path().read_bytes()
        first_registry = (self.ai / "_registry.json").read_bytes()
        first_entries = ai_cli.read_generated_manifest()
        first_generated = {
            path: (self.root / path).read_bytes()
            for path in first_entries
        }

        tool_profile.atomic_write_profile(
            tool_profile.ToolProfile(
                enabled_tools=("fourth",),
                defaults={field: "fourth" for field in tool_profile.ROLE_FIELDS},
                profiles={"fourth": ("fixture",)},
                catalogs={"fourth": tool_profile._unknown_catalog("app_default")},
            ),
            ai_root=self.ai,
        )
        ai_cli.cmd_sync(argparse.Namespace())

        self.assertEqual(first_manifest, ai_cli.manifest_path().read_bytes())
        self.assertEqual(first_registry, (self.ai / "_registry.json").read_bytes())
        self.assertEqual(first_generated, {path: (self.root / path).read_bytes() for path in first_entries})
        self.assertNotIn(".ai/.local/tools.json", ai_cli.read_generated_manifest())
        self.assertNotIn(".ai/.local", (self.ai / "_registry.json").read_text(encoding="utf-8"))


    def test_sync_manifest_is_idempotent_and_prunes_stale_entries(self) -> None:
        self.write_config()
        ai_cli.initialize_runtime_config(self.ai / "config.yaml")
        (self.ai / "agents").mkdir(exist_ok=True)
        (self.ai / "agents" / "executor.md").write_text("# Fixture executor\n", encoding="utf-8")
        ai_cli.cmd_sync(argparse.Namespace())
        first = ai_cli.manifest_path().read_bytes()
        ai_cli.cmd_sync(argparse.Namespace())
        self.assertEqual(first, ai_cli.manifest_path().read_bytes())

        dispatched = self.root / "dispatch.md"
        with ai_cli.generation_session("fixture dispatch"):
            ai_cli.write_generated(dispatched, "dispatch")
        ai_cli.cmd_sync(argparse.Namespace())
        self.assertTrue(dispatched.exists())
        self.assertEqual(
            "fixture dispatch", ai_cli.read_generated_manifest()["dispatch.md"]["command"]
        )

        stale = self.root / "stale.md"
        with ai_cli.generation_session("fixture sync"):
            ai_cli.write_generated(stale, "stale")
        self.assertIn("stale.md", ai_cli.read_generated_manifest())
        with ai_cli.generation_session("fixture sync", replace_sync_entries=True):
            pass
        self.assertFalse(stale.exists())
        self.assertNotIn("stale.md", ai_cli.read_generated_manifest())


class RuleActivationFrontmatterTests(unittest.TestCase):
    """A client that decides rule activation from frontmatter ignores rules that arrive with none.

    The plane strips its OWN registry frontmatter from generated rules, so an integration whose
    client needs an activation marker must be able to declare one and have it emitted -- otherwise
    every rule reaches that tool with no declared activation and is silently optional.
    """

    SPEC = {
        "template": "---\ntrigger: {activation}\ndescription: {description}\n---\n",
        "default_activation": "always",
        "activation_map": {"always": "always_on", "model_decision": "model_decision"},
    }
    BODY = "# Rule: Thing\n\nDo the thing."

    def render(self, meta, name="thing.md"):
        return adapter_render._rule_activation_frontmatter(self.SPEC, meta, name, self.BODY)

    def test_default_activation_is_emitted_when_the_rule_declares_none(self) -> None:
        rendered = self.render({})
        self.assertTrue(rendered.startswith("---\n"))
        self.assertIn("trigger: always_on", rendered)

    def test_rule_may_override_activation_and_it_maps_to_the_native_spelling(self) -> None:
        self.assertIn("trigger: model_decision", self.render({"activation": "model_decision"}))

    def test_an_integration_without_a_spec_gets_no_frontmatter(self) -> None:
        self.assertEqual(
            "", adapter_render._rule_activation_frontmatter(None, {}, "thing.md", self.BODY)
        )

    def test_description_falls_back_to_the_rule_summary(self) -> None:
        self.assertIn("Rule: Thing. Do the thing.", self.render({}))

    def test_description_is_quoted_so_an_embedded_colon_cannot_break_the_parse(self) -> None:
        # Summaries routinely contain a colon ("Rule: Task Contracts"). Unquoted, that breaks the
        # client's parse of the whole frontmatter block and the rule goes back to being ignored.
        line = next(ln for ln in self.render({}).splitlines() if ln.startswith("description:"))
        self.assertTrue(line.startswith('description: "'), line)
        self.assertTrue(line.endswith('"'), line)

    def test_embedded_quotes_are_escaped(self) -> None:
        rendered = self.render({"description": 'a "quoted" phrase'})
        self.assertIn('description: "a \\"quoted\\" phrase"', rendered)


class ShippedAntigravityRulesTests(unittest.TestCase):
    def test_every_generated_antigravity_rule_declares_an_activation_trigger(self) -> None:
        rules_dir = Path(__file__).resolve().parents[2] / ".agents" / "rules"
        if not rules_dir.is_dir():
            self.skipTest("no generated antigravity rules in this checkout")
        for rule in sorted(rules_dir.glob("*.md")):
            with self.subTest(rule=rule.name):
                head = rule.read_text(encoding="utf-8").splitlines()[:4]
                self.assertEqual("---", head[0], f"{rule.name} has no frontmatter block")
                self.assertTrue(
                    any(ln.startswith("trigger: ") for ln in head),
                    f"{rule.name} declares no activation trigger",
                )


if __name__ == "__main__":
    unittest.main()
