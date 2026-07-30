from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"

# The dispatch base for task_181 (the pre-split monolith lives here). Pinned so the A/B evidence
# comparison stays non-vacuous after landing (Q181-7): comparing against HEAD would become a
# post-vs-post no-op once this task is committed.
DISPATCH_BASE = "1e75aba"

# maw_core modules: the reusable coordination substrate + the contract/degradation layer. They
# must never import an extension NOR the ai_cli facade / any tool/vendor/language module (Q181-5).
CORE_MODULES = ("verify_core.py", "verify_contract.py", "extension_registry.py")
# Names that only an extension or the CLI facade (never the core) may reference as an import target.
EXTENSION_IMPORT_NAMES = {"rust_verify", "ai_cli"}
# Substrings that would betray a contextual (stack/vendor/language/tool) dependency in the core.
CONTEXTUAL_IMPORT_SUBSTRINGS = ("rust", "cargo", "codex", "claude", "antigravity", "gemini", "vite", "electron")


def collect_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
    return names


class ImportGraphTests(unittest.TestCase):
    """Acceptance: the generic core has no reverse import or enabled-by-default dependency on
    official extensions. Extraction (task_182) can only lift maw_core cleanly if this holds."""

    def test_core_modules_do_not_import_any_extension_or_facade(self) -> None:
        for module in CORE_MODULES:
            imports = collect_imports(SCRIPTS / module)
            for imported in imports:
                leaf = imported.split(".")[-1]
                self.assertNotIn(
                    leaf, EXTENSION_IMPORT_NAMES,
                    f"{module} imports {imported!r}; maw_core must not depend on an extension or the ai_cli facade",
                )
                self.assertFalse(
                    imported.startswith("scripts.extensions") or "extensions." in imported,
                    f"{module} imports from an extensions package ({imported!r})",
                )
                lowered = imported.lower()
                for banned in CONTEXTUAL_IMPORT_SUBSTRINGS:
                    self.assertNotIn(
                        banned, lowered,
                        f"{module} imports a contextual module ({imported!r}) containing {banned!r}",
                    )

    def test_registry_module_names_no_stack_policy(self) -> None:
        # The registry must be stack-agnostic: it may not hard-code a language/vendor identity.
        text = (SCRIPTS / "extension_registry.py").read_text(encoding="utf-8").lower()
        for banned in ("import cargo", "rust_verify", "def cargo", "clippy"):
            self.assertNotIn(banned, text, f"extension_registry.py references stack policy {banned!r}")

    def test_verify_core_does_not_reference_cargo_selection_policy(self) -> None:
        text = (SCRIPTS / "verify_core.py").read_text(encoding="utf-8")
        for banned in ("select_packages", "build_run_commands", "load_workspace_metadata", "clippy"):
            self.assertNotIn(banned, text, f"verify_core.py contains cargo policy {banned!r}")

    def test_core_modules_carry_no_transitive_tool_or_vendor_graph(self) -> None:
        # Q181-5 (round 2): a MODULE-LEVEL transitive import check in a fresh interpreter. Importing
        # the maw_core modules must not pull the config/tool roster, prompts/dispatch, the ai_cli
        # facade, or any extension. This is what makes the core cleanly extractable.
        code = (
            "import sys; "
            "import scripts.verify_core, scripts.verify_contract, scripts.extension_registry; "
            "banned = ['scripts.ai_plane.config','scripts.ai_plane.prompts','scripts.ai_plane.tasks',"
            "'scripts.ai_plane.manifest','scripts.ai_plane.auto_dispatch','scripts.ai_cli','ai_cli','rust_verify']; "
            "pulled = [m for m in banned if m in sys.modules]; "
            "sys.exit('PULLED:' + ','.join(pulled) if pulled else 0)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=REPO_ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        self.assertEqual(0, result.returncode, f"core pulled a contextual module: {result.stdout.strip()}")

    def test_core_runtime_contract_path_loads_no_tool_graph(self) -> None:
        # Q181-5 (round 3): not just IMPORT, but EXECUTING the normal contract path with no injected
        # callback must load no config/prompts/dispatch. The tool-aware prompt-pair exemption is now
        # injected, not imported, so verify_contract's runtime graph stays context-agnostic.
        code = (
            "import sys, json, tempfile, os; "
            "from pathlib import Path; "
            "import scripts.verify_contract as vc; "
            "d = tempfile.mkdtemp(); root = Path(d); ai = root / '.ai'; ai.mkdir(); "
            "(ai / '_manifest.json').write_text(json.dumps({'schema_version':1,'entries':[]}), encoding='utf-8'); "
            "vc.manifest_exemptions(root, ai, ai, {}, ['x'], 'base', (lambda r,a: ''), prompt_pair_fn=None); "
            "banned = ['scripts.ai_plane.config','scripts.ai_plane.prompts','scripts.ai_plane.auto_dispatch']; "
            "pulled = [m for m in banned if m in sys.modules]; "
            "sys.exit('PULLED:' + ','.join(pulled) if pulled else 0)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=REPO_ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        self.assertEqual(0, result.returncode, f"core runtime path pulled a tool module: {result.stdout.strip()}")


def load_base_module(rel_path: str, module_name: str, base: str):
    """Load the pre-split version of a script from the pinned DISPATCH BASE commit into a temp
    module, cleaning the temp file up afterward (N181-1). Returns None if the base commit is not
    available in this checkout (the test then skips rather than silently passing)."""
    import os
    result = subprocess.run(
        ["git", "show", f"{base}:{rel_path}"], cwd=REPO_ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    if result.returncode != 0 or not result.stdout:
        return None
    import sys
    fd, tmp_path = tempfile.mkstemp(suffix=".py")
    scripts_dir = str(SCRIPTS)
    added = scripts_dir not in sys.path
    if added:
        sys.path.insert(0, scripts_dir)  # the pre-split monolith does a bare `import ai_cli`
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(result.stdout)
        spec = importlib.util.spec_from_file_location(module_name, tmp_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if added and scripts_dir in sys.path:
            sys.path.remove(scripts_dir)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


class EvidenceRecordABIdentityTests(unittest.TestCase):
    """Acceptance: fixture A/B before and after the split produce semantically identical evidence
    records (same schema version, keys, and values apart from timestamps/duration/lock identity).

    The evidence-record writer moved verbatim from rust_verify.py into verify_core.py. This drives
    the SAME invocation payload through the PRE-SPLIT writer (loaded from the pinned dispatch base
    commit, NOT HEAD, so the comparison stays real after this task lands) and the post-split writer
    and asserts the persisted bytes are identical once the sole volatile field is pinned."""

    def _write_with(self, writer_module, task_dir: Path, config: dict, invocation: dict) -> str:
        with mock.patch.object(writer_module, "now_iso", lambda: "2026-07-24T00:00:00+00:00"):
            path = writer_module.append_invocation(task_dir, config, invocation)
        return path.read_text(encoding="utf-8")

    def test_evidence_writer_is_byte_identical_across_the_split(self) -> None:
        import scripts.verify_core as post_split
        pre_split = load_base_module("scripts/rust_verify.py", "task_181_base_rust_verify", DISPATCH_BASE)
        if pre_split is None:
            self.skipTest(f"dispatch base {DISPATCH_BASE} not available in this checkout")

        config = {"evidence": {"file_name": "ev.json", "schema_version": 1, "kind": "rust-verification-evidence"}}
        invocation = {
            "task_id": "task_x", "mode": "run", "label": None,
            "repository_key_sha256": "a" * 64, "base_commit": "base", "head_commit": "head",
            "diff_fingerprint": "f" * 64, "change_set": [{"path": "p", "status": "M", "origin": "committed", "old_path": None}],
            "commands": [], "platform": {"os": "X"}, "toolchain": {}, "lock": {}, "timing": {},
            "result": {"exit_status": 0},
        }

        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            pre_text = self._write_with(pre_split, Path(a), config, dict(invocation))
            post_text = self._write_with(post_split, Path(b), config, dict(invocation))

        pre = json.loads(pre_text)
        post = json.loads(post_text)
        self.assertEqual(pre["schema_version"], post["schema_version"])
        self.assertEqual(pre["kind"], post["kind"])
        self.assertEqual(set(pre), set(post))
        self.assertEqual(pre_text, post_text)  # byte-identical with the timestamp pinned


class OrchestrationABTests(unittest.TestCase):
    """R2-Q181-7: a durable pre/post A/B that drives the FULL verification orchestration —
    change-set discovery, path classification, package selection, and escalation — through the
    pinned pre-split monolith and the post-split rust gate with the SAME deterministic fake git and
    cargo-metadata runner, and asserts the produced selection + contract check are identical. This
    guards behavior far beyond the evidence-serialization helper."""

    def setUp(self) -> None:
        from scripts.tests import test_rust_verify as harness
        self.harness = harness
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ai = self.root / ".ai"
        (self.ai / "project").mkdir(parents=True)
        (self.ai / "project" / "rust-verification.json").write_text(harness.LIVE_CONFIG_TEXT, encoding="utf-8")
        (self.ai / "config.yaml").write_text(harness.LIVE_AI_CONFIG_TEXT, encoding="utf-8")
        (self.root / "project").mkdir(exist_ok=True)
        (self.root / "project" / "Cargo.toml").write_text(
            '[workspace]\nresolver = "3"\nmembers = ["crates/*"]\n', encoding="utf-8")
        task_dir = self.ai / "tasks" / "queue" / "task_ab"
        task_dir.mkdir(parents=True)
        (task_dir / "task.yaml").write_text(
            'id: "task_ab"\ntitle: "t"\nfeature: "f"\nstatus: "queued"\nrisk: "medium"\n'
            'preferred_tool: "codex"\nreview_tool: "codex"\nisolation_strategy: "patch"\n'
            'verification_scope: "affected-plus-neighbors"\n'
            'target_files:\n  - "project/**"\nforbidden_files:\n  - "secret/**"\n'
            'input_contract: "i"\noutput_contract: "o"\nacceptance_tests:\n  - "a"\n'
            'commands:\n  - "c"\nknown_risks: "none"\n', encoding="utf-8")

    def _fakes(self):
        git = self.harness.FakeGit()
        git.committed = [("M", "crates/runtime/src/lib.rs")]
        runner = self.harness.FakeRunner(metadata=self.harness.fixture_graph(self.root))
        return git, runner

    def _prepare(self, module):
        git, runner = self._fakes()
        return module.prepare_verification(
            self.root, self.ai, "task_ab", git.base, git_fn=git, runner=runner)

    def test_selection_and_contract_are_identical_across_the_split(self) -> None:
        import scripts.rust_verify as post_gate
        pre = load_base_module("scripts/rust_verify.py", "task_181_base_orch", DISPATCH_BASE)
        if pre is None:
            self.skipTest(f"dispatch base {DISPATCH_BASE} not available")
        pre_prepared = self._prepare(pre)
        post_prepared = self._prepare(post_gate)
        # The selection (owners, reverse-dependents, packages, escalations, schedule) must match.
        self.assertEqual(pre_prepared["selection"], post_prepared["selection"])
        self.assertTrue(post_prepared["selection"]["schedule_cargo"])
        self.assertIn("runtime", post_prepared["selection"]["owners"])
        # The contract check (target/forbidden/exemptions) must match.
        self.assertEqual(pre_prepared["contract_check"], post_prepared["contract_check"])
        # The change set must match.
        self.assertEqual(pre_prepared["change_set"]["entries"], post_prepared["change_set"]["entries"])


class ZeroExtensionCoreTests(unittest.TestCase):
    """Acceptance: a zero-extension repository is a complete control plane. The canonical
    workflows reference the registered evidence gate / degradation rather than naming a stack
    command inline, so an extracted zero-extension workflow can never point at a command that
    does not exist."""

    def _normalized(self, rel: str) -> str:
        return " ".join((REPO_ROOT / ".ai" / rel).read_text(encoding="utf-8").lower().split())

    def test_workflows_reference_registered_gate_not_only_inline_cargo(self) -> None:
        execution = self._normalized("workflows/execution.md")
        self.assertIn("registered evidence gate", execution)
        self.assertIn("no gate registered", execution)
        qa = self._normalized("workflows/qa.md")
        self.assertIn("evidence-gate result", qa)
        self.assertIn("degrad", qa)

    def test_zero_extension_resolution_is_rust_free(self) -> None:
        import scripts.extension_registry as reg
        with tempfile.TemporaryDirectory() as d:
            resolved = reg.resolve({"extensions": {"enabled": []}}, Path(d))
        self.assertEqual([], resolved["gate_ids"])
        self.assertEqual(list(reg.CORE_SCOPES), resolved["scopes"])
        self.assertEqual([], resolved["order"])

    def test_missing_extensions_block_fails_closed(self) -> None:
        import scripts.extension_registry as reg
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(reg.RegistryError) as ctx:
                reg.resolve({}, Path(d))
        self.assertEqual("missing-extensions-config", ctx.exception.reason)


if __name__ == "__main__":
    unittest.main()
