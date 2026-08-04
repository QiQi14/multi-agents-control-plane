"""Guards for the stdlib Python indexer and the neutral export contract.

Two failure modes matter here. The first is a wrong EDGE: a graph people navigate by is worse for
having an invented link than for missing one, so a call only resolves when its name matches exactly
one indexed definition. The second is the contract quietly renaming itself out from under an
existing Rust indexer, which is what the version-1 normalisation exists to prevent.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.ai_plane.knowledge_projection import project as projection
from scripts.ai_plane.knowledge_projection import py_index


class Fixture(unittest.TestCase):
    def repo(self, files: dict[str, str]) -> Path:
        root = Path(tempfile.mkdtemp(prefix="py-index-"))
        self.addCleanup(__import__("shutil").rmtree, root, True)
        for rel, body in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        return root


class IndexShapeTests(Fixture):
    def test_it_emits_the_neutral_contract(self) -> None:
        root = self.repo({"scripts/__init__.py": '"""Package purpose."""\n',
                          "scripts/a.py": '"""Module purpose."""\ndef alpha():\n    pass\n'})
        export = py_index.build_export(root, ["scripts"])
        self.assertEqual(2, export["contract_version"])
        package = export["packages"][0]
        for neutral in ("display_name", "symbol_namespace", "package_id"):
            self.assertIn(neutral, package)
        for rust in ("cargo_display_name", "rust_semantic_target_name", "rust_crate_name"):
            self.assertNotIn(rust, package, "the neutral contract must not reintroduce Rust names")

    def test_nodes_key_on_the_same_symbol_form_packages_expose(self) -> None:
        """Clusters are built by matching a node's `unit_name` to a package's `symbol_namespace`.

        Emitting the dotted display form on nodes and the underscored form on packages collapsed
        every cluster into one unnamed bucket while every count still looked plausible.
        """
        root = self.repo({"scripts/__init__.py": "", "scripts/pkg/__init__.py": "",
                          "scripts/pkg/m.py": "def f():\n    pass\n"})
        export = py_index.build_export(root, ["scripts"])
        namespaces = {p["symbol_namespace"] for p in export["packages"]}
        for node in export["semantic_nodes"]:
            self.assertIn(node["unit_name"], namespaces)

    def test_a_method_is_distinguished_from_a_function(self) -> None:
        root = self.repo({"scripts/__init__.py": "",
                          "scripts/a.py": "class C:\n    def m(self):\n        pass\ndef g():\n    pass\n"})
        kinds = {n["identity_name"]: n["kind"] for n in
                 py_index.build_export(root, ["scripts"])["semantic_nodes"]}
        self.assertEqual({"C": "class", "m": "method", "g": "function"}, kinds)

    def test_node_ids_are_stable_across_runs(self) -> None:
        root = self.repo({"scripts/__init__.py": "", "scripts/a.py": "def f():\n    pass\n"})
        first = py_index.build_export(root, ["scripts"])["semantic_nodes"]
        second = py_index.build_export(root, ["scripts"])["semantic_nodes"]
        self.assertEqual([n["id"] for n in first], [n["id"] for n in second])


class EdgeHonestyTests(Fixture):
    def test_a_unique_name_resolves_to_a_call_edge(self) -> None:
        root = self.repo({"scripts/__init__.py": "",
                          "scripts/a.py": "def only_one():\n    pass\ndef caller():\n    only_one()\n"})
        export = py_index.build_export(root, ["scripts"])
        self.assertEqual(1, len(export["relations"]))
        self.assertEqual("calls", export["relations"][0]["kind"])

    def test_an_ambiguous_name_becomes_a_boundary_not_an_edge(self) -> None:
        """Python dispatch is dynamic. Two same-named definitions cannot be told apart lexically,
        so picking one would invent an edge that reads as a fact."""
        root = self.repo({
            "scripts/__init__.py": "",
            "scripts/a.py": "class A:\n    def run(self):\n        pass\n",
            "scripts/b.py": "class B:\n    def run(self):\n        pass\n",
            "scripts/c.py": "def caller(x):\n    x.run()\n",
        })
        export = py_index.build_export(root, ["scripts"])
        self.assertEqual([], export["relations"])
        self.assertTrue(any(p["reason"] == "ambiguous" and p["spelling"] == "run"
                            for p in export["pending_boundaries"]))

    def test_a_builtin_call_is_not_an_unresolved_boundary(self) -> None:
        """`len` is resolved, just not to indexed code. Counting it buried the real boundaries."""
        root = self.repo({"scripts/__init__.py": "",
                          "scripts/a.py": "def f(x):\n    return len(x)\n"})
        export = py_index.build_export(root, ["scripts"])
        self.assertFalse(any(p["spelling"] == "len" for p in export["pending_boundaries"]))

    def test_unparseable_source_is_recorded_not_dropped(self) -> None:
        root = self.repo({"scripts/__init__.py": "", "scripts/bad.py": "def (:\n"})
        export = py_index.build_export(root, ["scripts"])
        self.assertTrue(any(p["reason"] == "unparsed" for p in export["pending_boundaries"]))

    def test_a_call_never_links_a_definition_to_itself(self) -> None:
        root = self.repo({"scripts/__init__.py": "",
                          "scripts/a.py": "def recur(n):\n    return recur(n - 1)\n"})
        export = py_index.build_export(root, ["scripts"])
        for relation in export["relations"]:
            self.assertNotEqual(relation["source_id"], relation["target_id"])


class ContractCompatibilityTests(unittest.TestCase):
    """A version-1 export from the Rust indexer must keep working with no change to it."""

    def v1(self) -> dict:
        return {
            "contract_version": 1,
            "packages": [{"package_id": "p", "cargo_display_name": "aios-core",
                          "rust_semantic_target_name": "aios_core"}],
            "semantic_nodes": [{"id": "n1", "rust_crate_name": "aios_core"}],
        }

    def test_version_one_is_renamed_to_the_neutral_fields(self) -> None:
        normalized = projection.normalize_contract(self.v1())
        self.assertEqual("aios-core", normalized["packages"][0]["display_name"])
        self.assertEqual("aios_core", normalized["packages"][0]["symbol_namespace"])
        self.assertEqual("aios_core", normalized["semantic_nodes"][0]["unit_name"])
        self.assertNotIn("rust_crate_name", normalized["semantic_nodes"][0])

    def test_version_two_is_left_alone(self) -> None:
        payload = {"contract_version": 2, "packages": [{"display_name": "scripts"}]}
        self.assertEqual(payload, projection.normalize_contract(payload))

    def test_both_versions_are_accepted(self) -> None:
        self.assertIn(1, projection.SUPPORTED_CONTRACT_VERSIONS)
        self.assertIn(2, projection.SUPPORTED_CONTRACT_VERSIONS)


if __name__ == "__main__":
    unittest.main()
