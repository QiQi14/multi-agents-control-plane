"""Guards for the structural TypeScript/JavaScript indexer.

The honesty rule matters more than the coverage here. A regex cannot follow imports, aliases,
overloads, or dynamic dispatch, so this indexer must never emit a relation -- a graph people
navigate by is worse for one invented edge than for a stated gap. The gap is stated in
`omissions.call_edges`, not left to be inferred from an empty list.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.ai_plane.knowledge_projection import ts_index


class Fixture(unittest.TestCase):
    def repo(self, files: dict[str, str]) -> Path:
        root = Path(tempfile.mkdtemp(prefix="ts-index-"))
        self.addCleanup(shutil.rmtree, root, True)
        for relative, body in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        return root

    def build(self, files: dict[str, str], packages: list[dict[str, str]] | None = None) -> dict:
        root = self.repo(files)
        return ts_index.build_export(
            root, packages or [{"name": "app", "relative_path": "."}])


class ContractTests(Fixture):
    def test_it_emits_the_neutral_contract(self) -> None:
        export = self.build({"package.json": '{"name":"app"}',
                             "src/thing.ts": "export function go() {}\n"})
        self.assertEqual(2, export["contract_version"])
        for neutral in ("display_name", "symbol_namespace", "package_id"):
            self.assertIn(neutral, export["packages"][0])
        for rust in ("cargo_display_name", "rust_crate_name", "rust_semantic_target_name"):
            self.assertNotIn(rust, export["packages"][0])

    def test_nodes_key_on_the_symbol_form_packages_expose(self) -> None:
        """Clusters match a node's `unit_name` to a package's `symbol_namespace`; two spellings
        collapse every cluster into one unnamed bucket while every count still looks plausible."""
        export = self.build({"package.json": '{"name":"my-app"}',
                             "src/thing.ts": "export function go() {}\n"},
                            [{"name": "my-app", "relative_path": "."}])
        namespaces = {package["symbol_namespace"] for package in export["packages"]}
        for node in export["semantic_nodes"]:
            self.assertIn(node["unit_name"], namespaces)

    def test_the_module_tier_is_the_containing_directory(self) -> None:
        export = self.build({"package.json": '{"name":"app"}',
                             "src/widgets/chart/index.ts": "export function a() {}\n",
                             "src/widgets/chart/util.ts": "export function b() {}\n"})
        modules = {item["module_path"] for item in export["files"]}
        self.assertEqual({"src/widgets/chart"}, modules)

    def test_node_ids_are_stable_across_runs(self) -> None:
        files = {"package.json": '{"name":"app"}', "src/a.ts": "export function go() {}\n"}
        first = self.build(files)["semantic_nodes"]
        second = self.build(files)["semantic_nodes"]
        self.assertEqual([item["id"] for item in first], [item["id"] for item in second])


class HonestyTests(Fixture):
    def test_no_relation_is_ever_invented(self) -> None:
        export = self.build({
            "package.json": '{"name":"app"}',
            "src/a.ts": "export function go() {}\n",
            "src/b.ts": "import { go } from './a';\nexport function run() { go(); }\n",
        })
        self.assertEqual([], export["relations"])

    def test_the_call_edge_gap_is_stated_not_implied(self) -> None:
        export = self.build({"package.json": '{"name":"app"}',
                             "src/a.ts": "export function go() {}\n"})
        self.assertIn("not attempted", export["omissions"]["call_edges"])

    def test_every_import_becomes_a_pending_boundary(self) -> None:
        export = self.build({
            "package.json": '{"name":"app"}',
            "src/b.ts": "import { go } from './a';\nconst x = require('lodash');\n",
        })
        spellings = {item["spelling"] for item in export["pending_boundaries"]}
        self.assertIn("./a", spellings)
        self.assertIn("lodash", spellings)


class DeclarationTests(Fixture):
    def test_the_common_declaration_kinds_are_found(self) -> None:
        export = self.build({"package.json": '{"name":"app"}', "src/a.ts": (
            "export class Widget {}\n"
            "export interface Shape { x: number }\n"
            "export type Alias = string;\n"
            "export enum Colour { Red }\n"
            "export function make() {}\n"
            "export const Panel = () => null;\n"
        )})
        found = {node["identity_name"]: node["kind"] for node in export["semantic_nodes"]}
        self.assertEqual(
            {"Widget": "class", "Shape": "interface", "Alias": "type", "Colour": "enum",
             "make": "function", "Panel": "function"}, found)

    def test_a_plain_value_is_not_a_declaration(self) -> None:
        """`const MAX = 5` is a value. Listing it makes the graph a variable dump."""
        export = self.build({"package.json": '{"name":"app"}',
                             "src/a.ts": "export const MAX = 5;\nexport const NAMES = ['a'];\n"})
        self.assertEqual([], export["semantic_nodes"])

    def test_ambient_declaration_files_are_skipped(self) -> None:
        """A `.d.ts` describes a shape that lives elsewhere; indexing it lists every symbol twice."""
        export = self.build({"package.json": '{"name":"app"}',
                             "src/a.d.ts": "export declare function go(): void;\n"})
        self.assertEqual([], export["files"])

    def test_one_line_is_never_counted_twice(self) -> None:
        export = self.build({"package.json": '{"name":"app"}',
                             "src/a.ts": "export default function go() {}\n"})
        self.assertEqual(1, len(export["semantic_nodes"]))

    def test_a_doc_comment_becomes_the_authored_purpose(self) -> None:
        export = self.build({"package.json": '{"name":"app"}', "src/a.ts": (
            "/**\n * Renders the chart panel.\n * @param x anything\n */\n"
            "export function render() {}\n"
        )})
        self.assertEqual("Renders the chart panel.",
                         export["semantic_nodes"][0]["purpose"]["value"])


class WorkspaceTests(Fixture):
    def test_a_member_owns_its_files_not_the_root_that_lists_it(self) -> None:
        """Otherwise every workspace file is counted twice and the root swallows the members."""
        export = self.build({
            "package.json": json.dumps({"name": "app", "workspaces": ["client"]}),
            "client/package.json": '{"name":"client"}',
            "client/src/a.ts": "export function go() {}\n",
            "src/root.ts": "export function top() {}\n",
        }, [{"name": "app", "relative_path": "."}, {"name": "client", "relative_path": "client"}])
        owners = {item["path"]: item["unit_name"] for item in export["files"]}
        self.assertEqual("client", owners["client/src/a.ts"])
        self.assertEqual("app", owners["src/root.ts"])
        self.assertEqual(2, len(export["files"]))

    def test_generated_and_vendored_trees_are_excluded(self) -> None:
        export = self.build({
            "package.json": '{"name":"app"}',
            "node_modules/dep/index.js": "module.exports = {};\n",
            "dist/bundle.js": "console.log(1);\n",
            "src/a.ts": "export function go() {}\n",
        })
        self.assertEqual(["src/a.ts"], [item["path"] for item in export["files"]])

    def test_a_package_manifest_description_becomes_the_package_purpose(self) -> None:
        export = self.build({"package.json": '{"name":"app","description":"The trading client."}',
                             "src/a.ts": "export function go() {}\n"})
        self.assertEqual("The trading client.", export["packages"][0]["purpose"]["value"])


if __name__ == "__main__":
    unittest.main()
