"""Guards for product topology, stack detection, and index-adapter selection.

Every case here is a real adoption failure. A control plane installed over a product's own worktree
coupled two Git checkouts and put two `AGENTS.md` files in conflict. A hard-coded singular
`project/` left documentation in two stale places. A Python indexer rooted at `scripts` described
the control plane as if it were the product. And rebuild guidance written at the presentation layer
told a Node workspace to run Cargo.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.ai_plane import products
from scripts.ai_plane.knowledge_projection import index_adapters


class Fixture(unittest.TestCase):
    def workspace(self, files: dict[str, str]) -> Path:
        root = Path(tempfile.mkdtemp(prefix="topology-"))
        self.addCleanup(shutil.rmtree, root, True)
        for relative, body in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        return root


class TopologyContractTests(Fixture):
    def test_the_projects_root_is_plural(self) -> None:
        """The singular `project/` is recognised as legacy; it is never the contract."""
        self.assertEqual("projects", products.PROJECTS_ROOT)
        self.assertEqual("project", products.LEGACY_PROJECT_ROOT)

    def test_a_nested_product_is_discovered_by_its_manifest(self) -> None:
        root = self.workspace({"projects/app/package.json": '{"name":"app"}'})
        found = products.discover_products(root)
        self.assertEqual(["app"], [item.product_id for item in found])
        self.assertEqual(("node",), found[0].stacks)
        self.assertTrue(found[0].nested)

    def test_a_directory_without_a_manifest_is_not_a_product(self) -> None:
        """A stack is proven by an authoritative manifest. Guessing from a name or a stray source
        file is how a Rust evidence gate got installed on a React product."""
        root = self.workspace({"projects/notes/README.md": "# notes\n",
                               "projects/notes/main.rs": "fn main() {}\n"})
        self.assertEqual([], products.discover_products(root))

    def test_npm_workspace_members_are_discovered(self) -> None:
        root = self.workspace({
            "projects/app/package.json": json.dumps({"name": "app", "workspaces": ["client", "server"]}),
            "projects/app/client/package.json": '{"name":"client"}',
            "projects/app/server/package.json": '{"name":"server"}',
        })
        found = products.discover_products(root)
        self.assertEqual(
            ["projects/app", "projects/app/client", "projects/app/server"],
            [package.relative_path for package in found[0].packages],
        )

    def test_a_broken_product_manifest_does_not_break_discovery(self) -> None:
        """A product's own file must never be able to stop the control plane from running."""
        root = self.workspace({"projects/app/package.json": "{ this is not json"})
        found = products.discover_products(root)
        self.assertEqual(["app"], [item.product_id for item in found])

    def test_the_legacy_singular_layout_still_works(self) -> None:
        root = self.workspace({"project/Cargo.toml": "[workspace]\n"})
        found = products.discover_products(root)
        self.assertEqual(["project"], [item.product_id for item in found])
        self.assertEqual(("rust",), found[0].stacks)

    def test_legacy_is_never_scanned(self) -> None:
        """Superseded material that is still discoverable has not been superseded. `legacy/` is
        excluded by contract, not by each scanner remembering to skip it."""
        self.assertIn(products.ARCHIVE_ROOT, products.EXCLUDED_DIR_NAMES)
        self.assertTrue(products.is_excluded("legacy/tasks/queue/task_1/task.yaml"))
        root = self.workspace({"legacy/old-product/package.json": '{"name":"old"}'})
        self.assertEqual([], products.discover_products(root))

    def test_generated_and_vendored_trees_are_never_scanned(self) -> None:
        for name in ("node_modules", "dist", "target", "__pycache__", ".git"):
            self.assertTrue(products.is_excluded(f"projects/app/{name}/thing.ts"), name)


class MixedInstallTests(Fixture):
    def test_a_product_worktree_is_reported_as_a_conflict(self) -> None:
        root = self.workspace({"package.json": '{"name":"app"}', "src/index.ts": "export {};\n"})
        conflicts = products.mixed_install_conflicts(root)
        self.assertTrue(conflicts)
        self.assertTrue(any("node" in reason for reason in conflicts))

    def test_a_clean_workspace_reports_no_conflict(self) -> None:
        root = self.workspace({"projects/app/package.json": '{"name":"app"}'})
        self.assertEqual([], products.mixed_install_conflicts(root))


class DocumentRootTests(Fixture):
    def test_product_doc_roots_are_discovered_not_assumed(self) -> None:
        root = self.workspace({
            "projects/app/package.json": '{"name":"app"}',
            "projects/app/docs/overview.md": "# overview\n",
        })
        self.assertEqual(["projects/app/docs"], products.product_document_roots(root))

    def test_a_product_without_docs_contributes_no_root(self) -> None:
        root = self.workspace({"projects/app/package.json": '{"name":"app"}'})
        self.assertEqual([], products.product_document_roots(root))


class WorkspaceModeTests(Fixture):
    """A team sharing the plane and a developer wrapping a product they do not own want opposite
    things from git. Guessing means telling someone to commit files they deliberately excluded."""

    def test_a_workspace_that_is_not_a_checkout_is_a_local_wrapper(self) -> None:
        root = self.workspace({"projects/app/package.json": '{"name":"app"}'})
        self.assertEqual(products.LOCAL_WRAPPER, products.workspace_git_mode(root))
        self.assertEqual((), products.workspace_ignore_entries(products.LOCAL_WRAPPER))

    def test_a_versioned_workspace_shares_the_plane(self) -> None:
        root = self.workspace({"projects/app/package.json": '{"name":"app"}'})
        (root / ".git").mkdir()
        self.assertEqual(products.SHARED_PLANE, products.workspace_git_mode(root))

    def test_a_versioned_workspace_that_excludes_the_plane_is_a_wrapper(self) -> None:
        root = self.workspace({"projects/app/package.json": '{"name":"app"}',
                               ".gitignore": "/.ai/\n/scripts/\n"})
        (root / ".git").mkdir()
        self.assertEqual(products.IGNORED_PLANE, products.workspace_git_mode(root))

    def test_a_shared_workspace_must_not_track_the_products(self) -> None:
        """A product carries its own .git; tracking it here records a broken gitlink or swallows
        the whole checkout."""
        entries = products.workspace_ignore_entries(products.SHARED_PLANE)
        self.assertIn("/projects/", entries)
        self.assertNotIn("/.ai/", entries)

    def test_an_ignored_plane_excludes_its_own_surface_too(self) -> None:
        entries = products.workspace_ignore_entries(products.IGNORED_PLANE)
        self.assertIn("/projects/", entries)
        for surface in ("/.ai/", "/scripts/", "/AGENTS.md"):
            self.assertIn(surface, entries)

    def test_an_unreadable_gitignore_does_not_decide_the_mode_by_accident(self) -> None:
        root = self.workspace({"projects/app/package.json": '{"name":"app"}', ".gitignore": ""})
        (root / ".git").mkdir()
        self.assertEqual(products.SHARED_PLANE, products.workspace_git_mode(root))

class AdapterSelectionTests(Fixture):
    def test_a_nested_product_outranks_the_control_plane(self) -> None:
        """This ordering IS the fix: the fallback used to be the default, so an adopting repository
        opened the graph and found the plane's own `scripts/` described as its product."""
        root = self.workspace({
            "scripts/__init__.py": "",
            "scripts/thing.py": "def f():\n    pass\n",
            "projects/app/package.json": '{"name":"app"}',
            "projects/app/src/index.ts": "export function go() {}\n",
        })
        chosen = index_adapters.select(root)
        self.assertIsNotNone(chosen)
        self.assertEqual("ts-structural", chosen.adapter_id)
        self.assertEqual("app", chosen.product_id)
        # Absent, not merely outranked: appending it still leaves the plane's own scripts one
        # ordering change away from describing somebody else's product.
        self.assertEqual([], [item for item in index_adapters.candidates(root)
                              if item.product_id == "(control plane)"])

    def test_the_control_plane_is_indexed_only_when_no_product_exists(self) -> None:
        root = self.workspace({"scripts/__init__.py": "", "scripts/thing.py": "def f():\n    pass\n"})
        chosen = index_adapters.select(root)
        self.assertEqual("(control plane)", chosen.product_id)
        self.assertIn("projects/<product-id>", chosen.rebuild_guidance)

    def test_rebuild_guidance_comes_from_the_selected_adapter(self) -> None:
        """Written at the presentation layer, this told a Node workspace to run Cargo."""
        root = self.workspace({
            "projects/app/package.json": '{"name":"app"}',
            "projects/app/src/index.ts": "export function go() {}\n",
        })
        chosen = index_adapters.select(root)
        for foreign in ("Cargo", "cargo", "ai-impact", "crates/"):
            self.assertNotIn(foreign, chosen.rebuild_guidance)
            self.assertNotIn(foreign, " ".join(chosen.exclude_rules))
        self.assertIn("no Node toolchain", chosen.rebuild_guidance)
        self.assertEqual(["projects/app/"], list(chosen.indexed_roots))

    def test_every_adapter_speaks_only_its_own_stack(self) -> None:
        """One shared vocabulary is how the presentation layer came to own all of it."""
        node_root = self.workspace({"projects/app/package.json": '{"name":"app"}',
                                    "projects/app/src/a.ts": "export function go() {}\n"})
        plane_root = self.workspace({"scripts/__init__.py": "",
                                     "scripts/a.py": "def f():\n    pass\n"})
        node = index_adapters.select(node_root)
        plane = index_adapters.select(plane_root)
        self.assertNotEqual(node.rebuild_guidance, plane.rebuild_guidance)
        self.assertNotEqual(node.exclude_rules, plane.exclude_rules)
        self.assertIn("call and import edges", node.exclude_rules)

    def test_a_rust_product_without_the_exporter_is_told_what_is_missing(self) -> None:
        """"No adapter supports that stack" sends the reader to write one that already exists."""
        root = self.workspace({"projects/app/Cargo.toml": "[workspace]\n"})
        guidance = index_adapters.unavailable_boundary_fields(root)["rebuild_guidance"]
        self.assertIn("tools/ai-impact", guidance)

    def test_no_product_at_all_is_stated_plainly(self) -> None:
        root = self.workspace({"README.md": "# nothing here\n"})
        fields = index_adapters.unavailable_boundary_fields(root)
        self.assertEqual([], fields["indexed_roots"])
        self.assertIn("projects/<product-id>", fields["rebuild_guidance"])


if __name__ == "__main__":
    unittest.main()
