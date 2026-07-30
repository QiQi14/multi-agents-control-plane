"""task_190a raw adapter A/B (design.md "Raw A/B authority").

The sole authority is the final contract-amendment commit ``DISPATCH_BASE``. This harness
reconstructs that commit with ``git archive`` into an OS-temporary directory (never a worktree),
copies the current working tree into a separate disposable candidate root, runs ``ai sync`` only in
those disposable roots, and compares the raw path sets and bytes of the generated adapter tree with
NO normalization. It proves the integration-driven renderer reproduces the pre-migration adapters
(rendered by the old per-vendor code at the base) byte-for-byte except for Task 178's exact five
command-catalog exposure surfaces, which are separately required to differ and whose manifest
records may change only by their sync-derived hashes.
"""
from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# The final contract-amendment commit is the sole dispatch base (design.md). It is PRE-migration:
# the reference integrations do not exist there, so the baseline adapters are produced by the old
# per-vendor renderer — a genuine A/B, not a same-code comparison.
DISPATCH_BASE = "48f9ae022850198a24b5565d5b076a91d2bbaf9b"

# The mechanically discovered adapter surface: the three root markers plus everything sync writes
# under the two support trees. Counts are diagnostic, never hardcoded as a gate.
_ROOT_MARKERS = ("AGENTS.md", "CLAUDE.md", "GEMINI.md")
_SUPPORT_TREES = (".claude", ".agents")
_TASK_178_EXPOSURE_PATHS = frozenset({
    ".agents/skills/index.md",
    ".claude/README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
})
_WORKING_COPY = ("scripts", ".ai", ".claude", ".agents")
_TASK_ID = "task_190a_integration_driven_adapter_renderer"
# task_190b: the dispatch base predates every task_190 prompt, so each newly QUEUED task adds its own
# generated prompt pair(s) to the candidate tree. Authorizing them here (the harness's designed
# extension point) keeps the RENDERER-equivalence claim intact — the non-prompt adapter surface is
# still proven byte-identical — while allowing the queue to grow. task_190b has only a claude
# dispatch prompt (no codex review prompt), so it contributes a single pair.
_TASK_190B_ID = "task_190b_capability_schema_interface_hardening"
# task_190c, task_175a-c, and task_191a-b are accepted under done/.
_TASK_190C_ID = "task_190c_pack_content_default_relation_consumers"
_TASK_175A_ID = "task_175a_blueprint_grammar_validation_core"
_TASK_175B_ID = "task_175b_blueprint_static_report_and_evidence"
_TASK_175C_ID = "task_175c_blueprint_optional_mermaid_svg"
_TASK_191A_ID = "task_191a_local_tool_profile_public_defaults"
_TASK_191B_ID = "task_191b_advisory_detection_and_routing"
_TASK_192A_ID = "task_192a_routing_taxonomy_policy_reconciliation"
_TASK_192B_ID = "task_192b_deterministic_route_explain_engine"
_AUTHORIZED_PROMPT_PAIRS = (
    frozenset({
        f".ai/tasks/queue/{_TASK_ID}/prompt.claude.md",
        f".ai/adapters/claude/dispatch/{_TASK_ID}.prompt.md",
    }),
    frozenset({
        f".ai/tasks/queue/{_TASK_ID}/prompt.codex.review.md",
        f".ai/adapters/codex/review/{_TASK_ID}.prompt.md",
    }),
    # task_190b was accepted and moved queue/ -> done/ (fa41165), and its manifest prompt record was
    # reconciled to the done/ path; its authorized task-folder path follows to done/ so the moved record
    # is still recognized as one half of the complete pair.
    frozenset({
        f".ai/tasks/done/{_TASK_190B_ID}/prompt.claude.md",
        f".ai/adapters/claude/dispatch/{_TASK_190B_ID}.prompt.md",
    }),
    frozenset({
        f".ai/tasks/done/{_TASK_190C_ID}/prompt.claude.md",
        f".ai/adapters/claude/dispatch/{_TASK_190C_ID}.prompt.md",
    }),
    frozenset({
        f".ai/tasks/done/{_TASK_175A_ID}/prompt.codex.md",
        f".ai/adapters/codex/dispatch/{_TASK_175A_ID}.prompt.md",
    }),
    frozenset({
        f".ai/tasks/done/{_TASK_175B_ID}/prompt.codex.md",
        f".ai/adapters/codex/dispatch/{_TASK_175B_ID}.prompt.md",
    }),
    frozenset({
        f".ai/tasks/done/{_TASK_175B_ID}/prompt.claude.review.md",
        f".ai/adapters/claude/review/{_TASK_175B_ID}.prompt.md",
    }),
    frozenset({
        f".ai/tasks/done/{_TASK_175C_ID}/prompt.codex.md",
        f".ai/adapters/codex/dispatch/{_TASK_175C_ID}.prompt.md",
    }),
    frozenset({
        f".ai/tasks/done/{_TASK_175C_ID}/prompt.claude.review.md",
        f".ai/adapters/claude/review/{_TASK_175C_ID}.prompt.md",
    }),
    frozenset({
        f".ai/tasks/done/{_TASK_191A_ID}/prompt.codex.md",
        f".ai/adapters/codex/dispatch/{_TASK_191A_ID}.prompt.md",
    }),
    frozenset({
        f".ai/tasks/done/{_TASK_191A_ID}/prompt.claude.review.md",
        f".ai/adapters/claude/review/{_TASK_191A_ID}.prompt.md",
    }),
    frozenset({
        f".ai/tasks/done/{_TASK_191B_ID}/prompt.claude.review.md",
        f".ai/adapters/claude/review/{_TASK_191B_ID}.prompt.md",
    }),
    frozenset({
        f".ai/tasks/done/{_TASK_192A_ID}/prompt.claude.md",
        f".ai/adapters/claude/dispatch/{_TASK_192A_ID}.prompt.md",
    }),
    frozenset({
        f".ai/tasks/done/{_TASK_192A_ID}/prompt.codex.review.md",
        f".ai/adapters/codex/review/{_TASK_192A_ID}.prompt.md",
    }),
    frozenset({
        f".ai/tasks/done/{_TASK_192B_ID}/prompt.claude.review.md",
        f".ai/adapters/claude/review/{_TASK_192B_ID}.prompt.md",
    }),
)

# Task 180 intentionally adds generated canonical workflow mirrors to both integration trees.
_TASK_180_WORKFLOW_NAMES = (
    "brief-intake.md", "dispatch.md", "execution.md", "learn.md", "planning.md", "qa.md",
    "research.md", "review.md",
    "task-template.md",
)
_TASK_180_ADAPTER_ADDITIONS = frozenset(
    f"{root}/{name}"
    for root in (".agents/workflows", ".claude/workflows")
    for name in _TASK_180_WORKFLOW_NAMES
)


# task_190b (R1-190B-6): a later task may legitimately edit a canonical doc that the rules_tree
# transform copies into `.agents/rules/`, which would otherwise DRIFT the generated copy from the
# pre-migration base. Rather than WEAKEN the byte-identity invariant by excluding that copy, the
# harness overlays each such SOURCE's CURRENT bytes into the base tree BEFORE the base sync, so the
# old (pre-migration) renderer processes the exact same input as the new renderer. The comparison
# then remains a true renderer A/B on the actual current content: identical bytes prove the new
# renderer reproduces the old renderer's transform of the edited doc, and a real renderer regression
# still differs. task_190b reconciles rust-verification.md; task_175a owns the blueprint tree.
_CONTENT_DRIFT_SOURCES = (
    ".ai/rules/documentation-currency.md",
    ".ai/rules/knowledge-capture.md",
    ".ai/rules/rust-verification.md",
    ".ai/rules/visual-evidence.md",
    ".ai/templates/pr-blueprint",
    ".ai/skills/pr-blueprint",
    ".ai/project/commands.md",
    ".ai/memory/lessons.md",
    # task_192a reconciles the routing law: `rules/task-contracts.md` is copied by the rules_tree
    # transform, and both `project/` documents (one edited, one NEW) feed the generated project-doc
    # catalog and the registry index. Overlaying their current bytes keeps this a true renderer A/B.
    ".ai/rules/task-contracts.md",
    ".ai/rules/qa-gates.md",
    ".ai/agents/reviewer.md",
    ".ai/project/principles.md",
    ".ai/project/routing-taxonomy.md",
)

# This generated file was already a protected local customization at the current Task 195D base:
# its bytes intentionally differ from the last generated hash recorded in the manifest. The raw
# base carries the same bytes, so only the stale historical generated hash differs after sync.
# Preserve customization safety by comparing the candidate record to the current repository record
# instead of blessing the customized bytes as newly generated.
_PRESERVED_GENERATED_CUSTOMIZATIONS = frozenset({
    ".agents/skills/pr-blueprint/command-catalog.json",
})


def base_commit_available() -> bool:
    """The pinned baseline lives in the repository that produced it. A checkout without that
    commit (a fresh adopter, or a repository the plane was transplanted into) has nothing to
    compare against, so the A/B skips instead of erroring on a missing object."""
    return subprocess.run(
        ["git", "cat-file", "-e", f"{DISPATCH_BASE}^{{commit}}"],
        cwd=REPO_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0


def _extract_base(dest: Path) -> None:
    archive = subprocess.run(
        ["git", "archive", "--format=tar", DISPATCH_BASE],
        cwd=REPO_ROOT, check=True, stdout=subprocess.PIPE,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(dest)


def _copy_working_tree(dest: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    for name in _WORKING_COPY:
        src = REPO_ROOT / name
        if src.is_dir():
            shutil.copytree(src, dest / name, ignore=ignore)
    for marker in _ROOT_MARKERS:
        if (REPO_ROOT / marker).is_file():
            shutil.copy2(REPO_ROOT / marker, dest / marker)


def _run_sync(root: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(root / "scripts" / "ai_cli.py"), "sync"],
        cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"sync failed in {root}:\n{result.stdout}")


def _capture_adapter_tree(root: Path) -> dict[str, bytes]:
    """Enumerate the SYNC-GENERATED adapter surface from the authoritative hash manifest (so a
    non-generated local file such as .claude/settings.local.json is never mistaken for output),
    then add the generated registry index."""
    manifest = json.loads((root / ".ai" / "_manifest.json").read_text(encoding="utf-8"))
    tree: dict[str, bytes] = {}
    for entry in manifest["entries"]:
        path_value = entry["path"]
        top = path_value.split("/")[0]
        if top in _ROOT_MARKERS or top in _SUPPORT_TREES:
            file_path = root / path_value
            if file_path.is_file():
                tree[path_value] = file_path.read_bytes()
    for index in (".ai/_registry.json",):
        path = root / index
        if path.is_file():
            tree[index] = path.read_bytes()
    adapter_root = root / ".ai" / "adapters"
    if adapter_root.is_dir():
        for path in sorted(adapter_root.rglob("*")):
            if path.is_file():
                tree[path.relative_to(root).as_posix()] = path.read_bytes()
    return tree


def _manifest(root: Path) -> dict:
    return json.loads((root / ".ai" / "_manifest.json").read_text(encoding="utf-8"))


def _assert_manifest_delta_is_only_task_prompt_pairs(
    case: unittest.TestCase, base_manifest: dict, candidate_manifest: dict
) -> None:
    case.assertEqual(
        {key: value for key, value in base_manifest.items() if key != "entries"},
        {key: value for key, value in candidate_manifest.items() if key != "entries"},
        "manifest metadata changed",
    )
    base_entries = {entry["path"]: entry for entry in base_manifest["entries"]}
    candidate_entries = {entry["path"]: entry for entry in candidate_manifest["entries"]}
    current_entries = {
        entry["path"]: entry
        for entry in json.loads(
            (REPO_ROOT / ".ai" / "_manifest.json").read_text(encoding="utf-8")
        )["entries"]
    }
    for path, entry in base_entries.items():
        if path == ".ai/_registry.json":
            continue
        if path in _PRESERVED_GENERATED_CUSTOMIZATIONS:
            case.assertEqual(
                current_entries.get(path),
                candidate_entries.get(path),
                f"protected generated customization record changed: {path}",
            )
            continue
        if path in _TASK_178_EXPOSURE_PATHS:
            candidate = candidate_entries.get(path)
            case.assertIsNotNone(candidate, f"Task 178 exposure record missing: {path}")
            case.assertEqual(entry["path"], candidate["path"])
            case.assertEqual(entry["command"], candidate["command"])
            continue
        case.assertEqual(entry, candidate_entries.get(path), f"pre-existing manifest record changed: {path}")
    additions = set(candidate_entries) - set(base_entries)
    remaining = set(additions)
    for pair in _AUTHORIZED_PROMPT_PAIRS:
        overlap = remaining & pair
        case.assertIn(
            len(overlap), (0, len(pair)),
            f"Task 190A prompt records must be added as a complete pair, got {sorted(overlap)}",
        )
        remaining -= pair
    workflow_overlap = remaining & _TASK_180_ADAPTER_ADDITIONS
    case.assertIn(
        len(workflow_overlap), (0, len(_TASK_180_ADAPTER_ADDITIONS)),
        f"Task 180 workflow records must be added as a complete set, got {sorted(workflow_overlap)}",
    )
    remaining -= _TASK_180_ADAPTER_ADDITIONS
    case.assertEqual(set(), remaining, f"unauthorized manifest additions: {sorted(remaining)}")


class AdapterRawAbTests(unittest.TestCase):
    def test_generated_adapter_tree_matches_base_except_task_178_exposure(self) -> None:
        if not base_commit_available():
            self.skipTest(f"baseline commit {DISPATCH_BASE} is not in this repository")
        with tempfile.TemporaryDirectory() as base_tmp, tempfile.TemporaryDirectory() as cand_tmp:
            base_dir, cand_dir = Path(base_tmp), Path(cand_tmp)
            _extract_base(base_dir)
            # R1-190B-6: overlay each canonical-source doc a later task edited (that the rules_tree
            # transform copies) with its CURRENT bytes, so the base (old) renderer processes the exact
            # same input as the new renderer — keeping this a true byte-identity renderer A/B on the
            # actual current content instead of excluding the edited copy.
            for src_path in _CONTENT_DRIFT_SOURCES:
                current = REPO_ROOT / src_path
                overlaid = base_dir / src_path
                if current.is_dir():
                    if overlaid.exists():
                        shutil.rmtree(overlaid)
                    shutil.copytree(current, overlaid)
                elif current.is_file():
                    overlaid.parent.mkdir(parents=True, exist_ok=True)
                    overlaid.write_bytes(current.read_bytes())
            _run_sync(base_dir)
            base_tree = _capture_adapter_tree(base_dir)
            base_manifest = _manifest(base_dir)

            _copy_working_tree(cand_dir)
            _run_sync(cand_dir)
            cand_tree = _capture_adapter_tree(cand_dir)
            cand_manifest = _manifest(cand_dir)

            print(f"[adapter A/B] baseline commit {DISPATCH_BASE}")
            print(f"[adapter A/B] compared {len(base_tree)} generated paths")

            missing = sorted(set(base_tree) - set(cand_tree))
            additions = set(cand_tree) - set(base_tree)
            authorized_additions = set().union(
                *_AUTHORIZED_PROMPT_PAIRS, _TASK_180_ADAPTER_ADDITIONS
            )
            added = sorted(additions - authorized_additions)
            self.assertEqual([], missing, f"paths present at base but not in candidate: {missing}")
            self.assertEqual([], added, f"unauthorized paths generated by candidate: {added}")

            differing = []
            task_178_differing = []
            for path in sorted(base_tree):
                if base_tree[path] != cand_tree[path]:
                    if path == ".ai/_registry.json":
                        # task_176: _registry.json indexes new spec documents; adapter files remain gated below.
                        continue
                    if path in _TASK_178_EXPOSURE_PATHS:
                        task_178_differing.append(path)
                        print(f"[adapter A/B] TASK178 expected exposure delta {path}")
                        continue
                    differing.append(path)
                else:
                    import hashlib
                    print(f"[adapter A/B] OK {path} {hashlib.sha256(base_tree[path]).hexdigest()[:12]}")
            self.assertEqual(sorted(_TASK_178_EXPOSURE_PATHS), task_178_differing)
            self.assertEqual([], differing, f"raw byte drift vs base in: {differing}")
            _assert_manifest_delta_is_only_task_prompt_pairs(self, base_manifest, cand_manifest)


if __name__ == "__main__":
    unittest.main()
