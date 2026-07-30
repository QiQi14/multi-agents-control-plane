"""task_190c pack content/default/relation composition (id-based, per the normative design.md).

End-to-end coverage through the real sync path: a pack's declared CONTENT (rules/workflows/skills) is
identified by its frontmatter id, indexed into the generated registry, and materialized manifest-safe;
a `replace` relation deterministically resolves a duplicate content id; declared kind must agree with
frontmatter type; cross-pack DEFAULTS compose with before/after precedence and are consumed through a
template placeholder; every conflict/ambiguity fails closed with its named reason BEFORE any generation
transaction, leaving files and the manifest byte-identical.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import unittest
from pathlib import Path

import scripts.ai_cli as ai_cli
import scripts.ai_plane.config as config_module
import scripts.extension_registry as extension_registry
from scripts.ai_plane.sync import compose_pack_content
from scripts.tests.test_ai_ext_consumers import _ConsumerFixture


def _doc(doc_id: str, doc_type: str, body: str) -> str:
    return f"---\nid: {doc_id}\ntype: {doc_type}\n---\n{body}\n"


def _digest(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


class _PackFixture(_ConsumerFixture):
    def write_content_pack(self, ext_id, *, items=(), defaults=None, config_schema=None,
                           relations=(), before=(), after=(), write_root=None):
        """Write a pack contributing CONTENT (public entry {kind, from}) and optional defaults/relations.
        Each item is {kind, name, body}: its source is <name> under the pack root and it materializes to
        <write_root>/<name>. rules/workflows/skills carry frontmatter identifying the content id."""
        wr = write_root or f".ai/adapters/packs/{ext_id}"
        pack = self.root / "scripts" / "extensions" / ext_id
        pack.mkdir(parents=True, exist_ok=True)
        content_entries = []
        for item in items:
            (pack / item["name"]).write_text(item["body"], encoding="utf-8")
            content_entries.append({"kind": item["kind"], "from": item["name"]})
        manifest = {
            "id": ext_id, "version": "1.0.0", "api_version": 1, "types": ["pack"],
            "root": f"scripts/extensions/{ext_id}", "write_roots": [wr],
        }
        if content_entries:
            manifest["contributes"] = {"content": content_entries}
        if config_schema is not None:
            manifest["config_schema"] = config_schema
        if defaults is not None:
            manifest["defaults"] = defaults
        if relations:
            manifest["relations"] = [dict(r) for r in relations]
        if before:
            manifest["before"] = list(before)
        if after:
            manifest["after"] = list(after)
        (pack / "extension.json").write_text(json.dumps(manifest), encoding="utf-8")
        return wr

    def sync_capture(self) -> tuple[int, str]:
        err = io.StringIO()
        code = 0
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            try:
                ai_cli.cmd_sync(argparse.Namespace())
            except SystemExit as exit_err:
                code = exit_err.code if isinstance(exit_err.code, int) else 1
        return code, err.getvalue()

    def read_registry(self) -> dict:
        return json.loads((self.ai / "_registry.json").read_text(encoding="utf-8"))

    def registry_docs(self) -> dict[str, dict]:
        return {doc["id"]: doc for doc in self.read_registry()["documents"]}

    def report(self) -> dict:
        parsed = config_module.parse_config_yaml(self.ai / "config.yaml")
        resolved = extension_registry.resolve(parsed, self.root, platform_name=os.name)
        _files, _docs, content_report, _superseded = compose_pack_content(resolved)
        return extension_registry.resolver_report(resolved, content_report=content_report)

    def write_core_doc(self, name: str, doc_id: str, doc_type: str, body: str) -> None:
        (self.ai / "rules" / name).write_text(_doc(doc_id, doc_type, body), encoding="utf-8")


class ContentIdentityTests(_PackFixture):
    def test_content_participates_in_registry_and_materializes(self) -> None:
        self.write_content_pack("cpack", items=[
            {"kind": "rules", "name": "r.md", "body": _doc("cpack-rule", "rule", "BODY")}])
        self.write_config(["cpack"])
        self.sync()
        doc = self.registry_docs().get("cpack-rule")
        self.assertIsNotNone(doc, "content participates in the registry indexed by frontmatter id")
        self.assertEqual("cpack", doc["origin"])
        self.assertEqual("rule", doc["type"])
        self.assertTrue((self.root / ".ai/adapters/packs/cpack/r.md").is_file())

    def test_content_pruned_on_disable(self) -> None:
        self.write_content_pack("cpack", items=[
            {"kind": "rules", "name": "r.md", "body": _doc("cpack-rule", "rule", "BODY")}])
        self.write_config(["cpack"])
        self.sync()
        dest = self.root / ".ai/adapters/packs/cpack/r.md"
        self.assertTrue(dest.is_file())
        self.write_config([])
        self.sync()
        self.assertNotIn("cpack-rule", self.registry_docs())
        self.assertFalse(dest.exists())

    def test_declared_kind_must_match_frontmatter_type(self) -> None:
        # R1-190C-2: kind=rules but type=workflow must fail closed.
        self.write_content_pack("bad", items=[
            {"kind": "rules", "name": "r.md", "body": _doc("mismatch", "workflow", "B")}])
        self.write_config(["bad"])
        code, err = self.sync_capture()
        self.assertNotEqual(0, code)
        self.assertIn("invalid-content", err)

    def test_each_kind_type_pairing(self) -> None:
        for kind, dtype in (("rules", "rule"), ("workflows", "workflow"), ("skills", "skill")):
            with self.subTest(kind=kind):
                self.write_content_pack("k" + kind, items=[
                    {"kind": kind, "name": "d.md", "body": _doc(f"{kind}-id", dtype, "B")}])
                self.write_config(["k" + kind])
                code, err = self.sync_capture()
                self.assertEqual(0, code, err)
                self.assertIn(f"{kind}-id", self.registry_docs())

    def test_missing_frontmatter_fails_closed(self) -> None:
        self.write_content_pack("nofm", items=[{"kind": "rules", "name": "r.md", "body": "no frontmatter\n"}])
        self.write_config(["nofm"])
        code, err = self.sync_capture()
        self.assertNotEqual(0, code)
        self.assertIn("invalid-content", err)

    def test_duplicate_content_id_without_relation_fails_closed(self) -> None:
        self.write_content_pack("i1", items=[{"kind": "rules", "name": "r.md", "body": _doc("dup", "rule", "A")}])
        self.write_content_pack("i2", items=[{"kind": "rules", "name": "r.md", "body": _doc("dup", "rule", "B")}])
        self.write_config(["i1", "i2"])
        code, err = self.sync_capture()
        self.assertNotEqual(0, code)
        self.assertIn("content-id-conflict", err)

    def test_replace_resolves_duplicate_content_id(self) -> None:
        # R1-190C-1: a replace relation on a duplicate id makes the replacing pack the effective origin.
        self.write_content_pack("abase", items=[{"kind": "rules", "name": "r.md", "body": _doc("dup", "rule", "BASE")}])
        self.write_content_pack("zwin", items=[{"kind": "rules", "name": "r.md", "body": _doc("dup", "rule", "WIN")}],
                                relations=[{"op": "replace", "kind": "content", "target": "dup"}])
        self.write_config(["abase", "zwin"])
        code, err = self.sync_capture()
        self.assertEqual(0, code, err)
        doc = self.registry_docs()["dup"]
        self.assertEqual("zwin", doc["origin"], "the replacing pack is the effective registry origin")
        self.assertIn("WIN", (self.root / ".ai/adapters/packs/zwin/r.md").read_text(encoding="utf-8"))


class DefaultsCompositionTests(_PackFixture):
    def test_cross_pack_defaults_template_consumption(self) -> None:
        self.write_content_pack("adef", defaults={"greeting": "hi"}, config_schema={"greeting": "string"})
        self.write_content_pack("btmpl", items=[
            {"kind": "templates", "name": "t.txt", "body": "Say: {default:greeting}!"}])
        self.write_config(["adef", "btmpl"])
        self.sync()
        self.assertEqual("Say: hi!",
                         (self.root / ".ai/adapters/packs/btmpl/t.txt").read_text(encoding="utf-8"))

    def test_unordered_differing_defaults_conflict(self) -> None:
        self.write_content_pack("d1", defaults={"k": "a"}, config_schema={"k": "string"})
        self.write_content_pack("d2", defaults={"k": "b"}, config_schema={"k": "string"})
        self.write_config(["d1", "d2"])
        code, err = self.sync_capture()
        self.assertNotEqual(0, code)
        self.assertIn("pack-defaults-conflict", err)

    def test_before_precedence_resolves_differing_defaults(self) -> None:
        # R1-190C-3: d1 before=[d2] makes d2 higher precedence; d2's value wins, no conflict.
        self.write_content_pack("d1", defaults={"k": "a"}, config_schema={"k": "string"}, before=["d2"])
        self.write_content_pack("d2", defaults={"k": "b"}, config_schema={"k": "string"})
        self.write_config(["d1", "d2"])
        code, err = self.sync_capture()
        self.assertEqual(0, code, err)
        rep = {d["key"]: d for d in self.report()["defaults"]}
        self.assertEqual("b", rep["k"]["value"])
        self.assertEqual("d2", rep["k"]["origin"])
        self.assertIn("d1", rep["k"]["overridden"])

    def test_after_precedence_resolves_differing_defaults(self) -> None:
        self.write_content_pack("d1", defaults={"k": "a"}, config_schema={"k": "string"})
        self.write_content_pack("d2", defaults={"k": "b"}, config_schema={"k": "string"}, after=["d1"])
        self.write_config(["d1", "d2"])
        code, err = self.sync_capture()
        self.assertEqual(0, code, err)
        self.assertEqual("b", {d["key"]: d["value"] for d in self.report()["defaults"]}["k"])

    def test_identical_defaults_idempotent(self) -> None:
        self.write_content_pack("e1", defaults={"k": "same"}, config_schema={"k": "string"})
        self.write_content_pack("e2", defaults={"k": "same"}, config_schema={"k": "string"})
        self.write_config(["e1", "e2"])
        code, err = self.sync_capture()
        self.assertEqual(0, code, err)

    def test_missing_default_value_fails_closed(self) -> None:
        self.write_content_pack("btmpl", items=[{"kind": "templates", "name": "t.txt", "body": "{default:absent}"}])
        self.write_config(["btmpl"])
        code, err = self.sync_capture()
        self.assertNotEqual(0, code)
        self.assertIn("missing-default-value", err)


class RelationTests(_PackFixture):
    def _compose(self, op: str, *, base_before_mod: bool = True) -> str:
        self.write_content_pack("abase", items=[{"kind": "rules", "name": "r.md", "body": _doc("shared", "rule", "BASE")}])
        self.write_content_pack("zmod", items=[{"kind": "rules", "name": "r.md", "body": _doc("shared", "rule", "MOD")}],
                                relations=[{"op": op, "kind": "content", "target": "shared"}])
        self.write_config(["abase", "zmod"])
        code, err = self.sync_capture()
        self.assertEqual(0, code, err)
        # append/wrap keep the base as effective origin; a single modifier is always unambiguous.
        eff = "zmod" if op == "replace" else "abase"
        return (self.root / f".ai/adapters/packs/{eff}/r.md").read_text(encoding="utf-8")

    def test_append(self) -> None:
        text = self._compose("append")
        self.assertLess(text.index("BASE"), text.index("MOD"))

    def test_prepend(self) -> None:
        text = self._compose("prepend")
        self.assertLess(text.index("MOD"), text.index("BASE"))

    def test_wrap(self) -> None:
        text = self._compose("wrap")
        self.assertEqual(2, text.count("MOD"))

    def test_replace(self) -> None:
        text = self._compose("replace")
        self.assertIn("MOD", text)
        self.assertNotIn("BASE", text)

    def test_unordered_prepend_replace_is_ambiguous(self) -> None:
        # R1-190C-4: a prepend and a replace racing on one id with no precedence must fail closed.
        self.write_content_pack("abase", items=[{"kind": "rules", "name": "r.md", "body": _doc("shared", "rule", "BASE")}])
        self.write_content_pack("mpre", items=[{"kind": "rules", "name": "r.md", "body": _doc("shared", "rule", "PRE")}],
                                relations=[{"op": "prepend", "kind": "content", "target": "shared"}])
        self.write_content_pack("mrep", items=[{"kind": "rules", "name": "r.md", "body": _doc("shared", "rule", "REP")}],
                                relations=[{"op": "replace", "kind": "content", "target": "shared"}])
        self.write_config(["abase", "mpre", "mrep"])
        code, err = self.sync_capture()
        self.assertNotEqual(0, code)
        self.assertIn("ambiguous-relation", err)

    def test_ordered_prepend_replace_is_deterministic(self) -> None:
        # Control: the same two modifiers WITH an explicit order compose deterministically.
        self.write_content_pack("abase", items=[{"kind": "rules", "name": "r.md", "body": _doc("shared", "rule", "BASE")}])
        self.write_content_pack("mpre", items=[{"kind": "rules", "name": "r.md", "body": _doc("shared", "rule", "PRE")}],
                                relations=[{"op": "prepend", "kind": "content", "target": "shared"}], before=["mrep"])
        self.write_content_pack("mrep", items=[{"kind": "rules", "name": "r.md", "body": _doc("shared", "rule", "REP")}],
                                relations=[{"op": "replace", "kind": "content", "target": "shared"}])
        self.write_config(["abase", "mpre", "mrep"])
        code, err = self.sync_capture()
        self.assertEqual(0, code, err)

    def test_two_replace_is_ambiguous(self) -> None:
        self.write_content_pack("abase", items=[{"kind": "rules", "name": "r.md", "body": _doc("shared", "rule", "BASE")}])
        self.write_content_pack("m1", items=[{"kind": "rules", "name": "r.md", "body": _doc("shared", "rule", "R1")}],
                                relations=[{"op": "replace", "kind": "content", "target": "shared"}])
        self.write_content_pack("m2", items=[{"kind": "rules", "name": "r.md", "body": _doc("shared", "rule", "R2")}],
                                relations=[{"op": "replace", "kind": "content", "target": "shared"}])
        self.write_config(["abase", "m1", "m2"])
        code, err = self.sync_capture()
        self.assertNotEqual(0, code)
        self.assertIn("ambiguous-relation", err)

    def test_unresolved_relation_target(self) -> None:
        self.write_content_pack("orphan", items=[{"kind": "rules", "name": "r.md", "body": _doc("has-id", "rule", "B")}],
                                relations=[{"op": "append", "kind": "content", "target": "no-such-id"}])
        self.write_config(["orphan"])
        code, err = self.sync_capture()
        self.assertNotEqual(0, code)
        self.assertIn("unresolved-relation-target", err)

    def test_invalid_relation_shape(self) -> None:
        pack = self.root / "scripts" / "extensions" / "badrel"
        pack.mkdir(parents=True, exist_ok=True)
        (pack / "extension.json").write_text(json.dumps({
            "id": "badrel", "version": "1.0.0", "api_version": 1, "types": ["pack"],
            "root": "scripts/extensions/badrel",
            "relations": [{"op": "frobnicate", "kind": "content", "target": "x"}],
        }), encoding="utf-8")
        self.write_config(["badrel"])
        code, err = self.sync_capture()
        self.assertNotEqual(0, code)
        self.assertIn("invalid-relation", err)


class DeterminismReportAndSafetyTests(_PackFixture):
    def test_composition_independent_of_enable_order(self) -> None:
        self.write_content_pack("abase", items=[{"kind": "rules", "name": "r.md", "body": _doc("shared", "rule", "BASE")}])
        self.write_content_pack("zmod", items=[{"kind": "rules", "name": "r.md", "body": _doc("shared", "rule", "MOD")}],
                                relations=[{"op": "append", "kind": "content", "target": "shared"}])
        self.write_config(["abase", "zmod"])
        self.sync()
        first = (self.root / ".ai/adapters/packs/abase/r.md").read_text(encoding="utf-8")
        self.write_config(["zmod", "abase"])
        self.sync()
        second = (self.root / ".ai/adapters/packs/abase/r.md").read_text(encoding="utf-8")
        self.assertEqual(first, second)

    def test_resolver_report_shapes(self) -> None:
        # R1-190C-5: content {kind, id, origin, precedence}; defaults {key, value, origin, precedence};
        # a relations section; a replacing contribution reports the replacing origin.
        self.write_content_pack("adef", defaults={"greeting": "hi"}, config_schema={"greeting": "string"})
        self.write_content_pack("abase", items=[{"kind": "rules", "name": "r.md", "body": _doc("dup", "rule", "BASE")}])
        self.write_content_pack("zwin", items=[{"kind": "rules", "name": "r.md", "body": _doc("dup", "rule", "WIN")}],
                                relations=[{"op": "replace", "kind": "content", "target": "dup"}])
        self.write_config(["adef", "abase", "zwin"])
        report = self.report()
        content = {c["id"]: c for c in report["content"]}
        self.assertEqual({"kind", "id", "origin", "precedence"}, set(content["dup"]))
        self.assertEqual("zwin", content["dup"]["origin"])
        defaults = {d["key"]: d for d in report["defaults"]}
        self.assertEqual("adef", defaults["greeting"]["origin"])
        rels = {(r["kind"], r["target"], r["effective_origin"]) for r in report["relations"]}
        self.assertIn(("content", "dup", "zwin"), rels)

    def test_failed_composition_is_byte_identical(self) -> None:
        self.write_content_pack("good", items=[{"kind": "rules", "name": "r.md", "body": _doc("good-id", "rule", "G")}])
        self.write_config(["good"])
        self.sync()
        self.write_content_pack("dupe", items=[{"kind": "rules", "name": "r.md", "body": _doc("good-id", "rule", "D")}])
        self.write_config(["good", "dupe"])
        before = _digest(self.root)
        code, err = self.sync_capture()
        self.assertNotEqual(0, code)
        self.assertIn("content-id-conflict", err)
        self.assertEqual(before, _digest(self.root),
                         "a failed composition leaves every file and the manifest byte-identical")


class CoreIdReplaceTests(_PackFixture):
    def test_replace_supersedes_a_core_document(self) -> None:
        # R2-190C-1: a pack may replace a canonical/core registry document by id.
        self.write_core_doc("core.md", "core-id", "rule", "CORE")
        self.write_content_pack("packr", items=[{"kind": "rules", "name": "r.md", "body": _doc("core-id", "rule", "PACK")}],
                                relations=[{"op": "replace", "kind": "content", "target": "core-id"}])
        self.write_config(["packr"])
        code, err = self.sync_capture()
        self.assertEqual(0, code, err)
        doc = self.registry_docs()["core-id"]
        self.assertEqual("packr", doc["origin"], "the pack replacement supersedes the core registry entry")
        self.assertIn("PACK", (self.root / ".ai/adapters/packs/packr/r.md").read_text(encoding="utf-8"))
        # Disable restores the canonical document (no pack origin), manifest-safe.
        self.write_config([])
        self.sync()
        restored = self.registry_docs().get("core-id")
        self.assertIsNotNone(restored)
        self.assertNotIn("origin", restored)

    def test_core_id_collision_without_replace_fails(self) -> None:
        self.write_core_doc("core.md", "core-id", "rule", "CORE")
        self.write_content_pack("packr", items=[{"kind": "rules", "name": "r.md", "body": _doc("core-id", "rule", "PACK")}])
        self.write_config(["packr"])
        code, err = self.sync_capture()
        self.assertNotEqual(0, code)
        self.assertIn("content-id-conflict", err)

    def test_non_replace_relation_on_core_id_fails(self) -> None:
        self.write_core_doc("core.md", "core-id", "rule", "CORE")
        self.write_content_pack("packr", items=[{"kind": "rules", "name": "r.md", "body": _doc("core-id", "rule", "PACK")}],
                                relations=[{"op": "append", "kind": "content", "target": "core-id"}])
        self.write_config(["packr"])
        code, err = self.sync_capture()
        self.assertNotEqual(0, code)
        self.assertIn("invalid-relation", err)


class DuplicateSamePackRelationTests(_PackFixture):
    def test_duplicate_same_pack_content_relations_fail(self) -> None:
        # R2-190C-2: one pack declaring two operations for the same content target must fail closed.
        self.write_content_pack("abase", items=[{"kind": "rules", "name": "r.md", "body": _doc("dup", "rule", "BASE")}])
        self.write_content_pack("zmod", items=[{"kind": "rules", "name": "r.md", "body": _doc("dup", "rule", "MOD")}],
                                relations=[{"op": "prepend", "kind": "content", "target": "dup"},
                                           {"op": "replace", "kind": "content", "target": "dup"}])
        self.write_config(["abase", "zmod"])
        code, err = self.sync_capture()
        self.assertNotEqual(0, code)
        self.assertIn("ambiguous-relation", err)

    def test_duplicate_same_pack_defaults_relations_fail(self) -> None:
        self.write_content_pack("d1", defaults={"k": "a"}, config_schema={"k": "string"})
        self.write_content_pack("d2", defaults={"k": "b"}, config_schema={"k": "string"},
                                relations=[{"op": "replace", "kind": "defaults", "target": "k"},
                                           {"op": "prepend", "kind": "defaults", "target": "k"}])
        self.write_config(["d1", "d2"])
        code, err = self.sync_capture()
        self.assertNotEqual(0, code)
        self.assertIn("ambiguous-relation", err)


class ReportCompletenessTests(_PackFixture):
    def test_content_precedence_records_superseded_base_after_replace(self) -> None:
        # R2-190C-3: the replaced base origin must remain visible in the precedence chain.
        self.write_content_pack("abase", items=[{"kind": "rules", "name": "r.md", "body": _doc("dup", "rule", "BASE")}])
        self.write_content_pack("zwin", items=[{"kind": "rules", "name": "r.md", "body": _doc("dup", "rule", "WIN")}],
                                relations=[{"op": "replace", "kind": "content", "target": "dup"}])
        self.write_config(["abase", "zwin"])
        content = {c["id"]: c for c in self.report()["content"]}
        prec = content["dup"]["precedence"]
        self.assertEqual(("abase", "base"), (prec[0]["origin"], prec[0]["op"]), "superseded base is first in the chain")
        self.assertEqual(("zwin", "replace"), (prec[1]["origin"], prec[1]["op"]))
        self.assertEqual("zwin", content["dup"]["origin"])

    def test_before_after_defaults_winner_has_nonempty_precedence(self) -> None:
        self.write_content_pack("d1", defaults={"k": "a"}, config_schema={"k": "string"}, before=["d2"])
        self.write_content_pack("d2", defaults={"k": "b"}, config_schema={"k": "string"})
        self.write_config(["d1", "d2"])
        entry = {d["key"]: d for d in self.report()["defaults"]}["k"]
        prec_origins = [p["origin"] for p in entry["precedence"]]
        self.assertTrue(entry["precedence"], "a before/after defaults winner must expose non-empty precedence")
        self.assertIn("d1", prec_origins, "the superseded base is auditable in the chain")
        self.assertIn("d2", prec_origins)
        self.assertEqual("d2", entry["origin"])


class MultiSourceReplaceTests(_PackFixture):
    def test_pack_pack_replace_resolves_multiple_bases(self) -> None:
        # R3-190C-1: two unrelated pack bases + a replacing pack — the replace supersedes both.
        self.write_content_pack("abase", items=[{"kind": "rules", "name": "r.md", "body": _doc("dup", "rule", "A")}])
        self.write_content_pack("bbase", items=[{"kind": "rules", "name": "r.md", "body": _doc("dup", "rule", "B")}])
        self.write_content_pack("zwin", items=[{"kind": "rules", "name": "r.md", "body": _doc("dup", "rule", "WIN")}],
                                relations=[{"op": "replace", "kind": "content", "target": "dup"}])
        self.write_config(["abase", "bbase", "zwin"])
        code, err = self.sync_capture()
        self.assertEqual(0, code, err)
        self.assertEqual("zwin", self.registry_docs()["dup"]["origin"])
        content = {c["id"]: c for c in self.report()["content"]}
        origins = {p["origin"] for p in content["dup"]["precedence"]}
        self.assertEqual({"abase", "bbase", "zwin"}, origins, "every superseded base is reported")

    def test_core_plus_pack_base_plus_replace(self) -> None:
        # R3-190C-1: a core document AND an unrelated pack base, both superseded by a replacing pack.
        self.write_core_doc("core.md", "dup", "rule", "CORE")
        self.write_content_pack("abase", items=[{"kind": "rules", "name": "r.md", "body": _doc("dup", "rule", "A")}])
        self.write_content_pack("zwin", items=[{"kind": "rules", "name": "r.md", "body": _doc("dup", "rule", "WIN")}],
                                relations=[{"op": "replace", "kind": "content", "target": "dup"}])
        self.write_config(["abase", "zwin"])
        code, err = self.sync_capture()
        self.assertEqual(0, code, err)
        self.assertEqual("zwin", self.registry_docs()["dup"]["origin"])
        self.assertIn("WIN", (self.root / ".ai/adapters/packs/zwin/r.md").read_text(encoding="utf-8"))
        self.write_config([])
        self.sync()
        restored = self.registry_docs().get("dup")
        self.assertIsNotNone(restored)
        self.assertNotIn("origin", restored)

    def test_multiple_bases_without_replace_still_conflicts(self) -> None:
        # append cannot resolve two unordered bases — ordering would affect output.
        self.write_content_pack("abase", items=[{"kind": "rules", "name": "r.md", "body": _doc("dup", "rule", "A")}])
        self.write_content_pack("bbase", items=[{"kind": "rules", "name": "r.md", "body": _doc("dup", "rule", "B")}])
        self.write_content_pack("zmod", items=[{"kind": "rules", "name": "r.md", "body": _doc("dup", "rule", "M")}],
                                relations=[{"op": "append", "kind": "content", "target": "dup"}])
        self.write_config(["abase", "bbase", "zmod"])
        code, err = self.sync_capture()
        self.assertNotEqual(0, code)
        self.assertIn("content-id-conflict", err)

    def test_relations_report_exposes_resolved_order(self) -> None:
        # R3-190C-2: m1 prepend BEFORE m2 replace — the relations section shows the resolved order.
        self.write_content_pack("abase", items=[{"kind": "rules", "name": "r.md", "body": _doc("shared", "rule", "BASE")}])
        self.write_content_pack("m1", items=[{"kind": "rules", "name": "r.md", "body": _doc("shared", "rule", "P")}],
                                relations=[{"op": "prepend", "kind": "content", "target": "shared"}], before=["m2"])
        self.write_content_pack("m2", items=[{"kind": "rules", "name": "r.md", "body": _doc("shared", "rule", "R")}],
                                relations=[{"op": "replace", "kind": "content", "target": "shared"}])
        self.write_config(["abase", "m1", "m2"])
        rels = [r for r in self.report()["relations"] if r["target"] == "shared"]
        self.assertEqual(1, len(rels))
        chain = [(p["origin"], p["op"]) for p in rels[0]["resolved"]]
        self.assertEqual([("m1", "prepend"), ("m2", "replace")], chain, "resolved order is explicit")
        self.assertEqual("m2", rels[0]["effective_origin"])


if __name__ == "__main__":
    unittest.main()
