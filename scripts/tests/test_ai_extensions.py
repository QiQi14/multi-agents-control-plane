from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
import scripts.extension_registry as reg


def base_manifest(ext_id: str, **overrides) -> dict:
    manifest = {
        "id": ext_id,
        "version": "1.0.0",
        "api_version": 1,
        "types": ["pack"],
    }
    manifest.update(overrides)
    return manifest


class RegistryFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / ".ai" / "extensions").mkdir(parents=True)

    def write_ext(self, ext_id: str, manifest: dict | None = None, *, folder: str | None = None,
                  code_files: dict[str, str] | None = None) -> Path:
        folder = folder or ext_id
        ext_dir = self.root / ".ai" / "extensions" / folder
        ext_dir.mkdir(parents=True, exist_ok=True)
        payload = manifest if manifest is not None else base_manifest(ext_id)
        (ext_dir / "extension.json").write_text(json.dumps(payload), encoding="utf-8")
        for rel, content in (code_files or {}).items():
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        return ext_dir

    def cfg(self, enabled: list[str], roots: list[str] | None = None) -> dict:
        block = {"enabled": enabled}
        if roots is not None:
            block["roots"] = roots
        return {"extensions": block}

    def resolve(self, enabled: list[str], **kw):
        return reg.resolve(self.cfg(enabled), self.root, **kw)

    def assert_reason(self, reason: str, enabled: list[str], **kw):
        with self.assertRaises(reg.RegistryError) as ctx:
            self.resolve(enabled, **kw)
        self.assertEqual(reason, ctx.exception.reason)
        return ctx.exception


class ZeroAndBaselineTests(RegistryFixture):
    def test_absent_extensions_block_fails_closed_no_default_roster(self) -> None:
        with self.assertRaises(reg.RegistryError) as ctx:
            reg.resolve({}, self.root)
        self.assertEqual("missing-extensions-config", ctx.exception.reason)

    def test_present_block_missing_enabled_key_fails_closed_no_default_roster(self) -> None:
        # R5-Q181-1: a block that omits the enabled key (a partial-config typo) is NOT a
        # zero-extension declaration; it must fail closed with the same named reason, never be
        # defaulted to enabled: []. Only an EXPLICIT enabled: [] is the zero-extension form.
        with self.assertRaises(reg.RegistryError) as ctx:
            reg.resolve({"extensions": {"roots": [".ai/extensions"]}}, self.root)
        self.assertEqual("missing-extensions-config", ctx.exception.reason)

    def test_explicit_empty_roster_is_the_zero_extension_form(self) -> None:
        resolved = self.resolve([])
        self.assertEqual([], resolved["order"])
        self.assertEqual(list(reg.CORE_SCOPES), resolved["scopes"])
        self.assertEqual([], resolved["gate_ids"])

    def test_empty_enabled_list_is_valid_zero_extension(self) -> None:
        self.write_ext("rust", base_manifest("rust", types=["gate"], root="scripts",
                                              entrypoints={"gate": "rust_verify.py"}),
                       code_files={"scripts/rust_verify.py": "X=1\n"})
        resolved = self.resolve([])
        self.assertEqual([], resolved["order"])  # present on disk but not enabled

    def test_each_capability_type_registers_and_exposes_only_its_declared_surface(self) -> None:
        self.write_ext("intg", base_manifest("intg", types=["integration"]))
        self.write_ext("pk", base_manifest("pk", types=["pack"], scopes=["docs-lint"]))
        self.write_ext("gt", base_manifest("gt", types=["gate"], root="scripts",
                                            entrypoints={"gate": "g.py"}, scopes=["gpu"]),
                       code_files={"scripts/g.py": "X=1\n"})
        self.write_ext("cmd", base_manifest("cmd", types=["command"], root="scripts",
                                            entrypoints={"command": "c.py"},
                                            executables=["idx"], commands={"index": {"executable": "idx"}}),
                       code_files={"scripts/c.py": "X=1\n"})
        resolved = self.resolve(["intg", "pk", "gt", "cmd"])
        caps = resolved["contributions"]
        self.assertEqual(["intg"], [e["id"] for e in caps["integration"]])
        self.assertEqual(["pk"], [e["id"] for e in caps["pack"]])
        self.assertEqual(["gt"], [e["id"] for e in caps["gate"]])
        self.assertEqual(["cmd"], [e["id"] for e in caps["command"]])
        self.assertEqual(["gt"], resolved["gate_ids"])
        self.assertIn("docs-lint", resolved["scopes"])
        self.assertIn("gpu", resolved["scopes"])
        self.assertEqual({"index": "cmd"}, resolved["commands"])


class FailClosedMatrixTests(RegistryFixture):
    def test_duplicate_id_across_roots(self) -> None:
        self.write_ext("dup", base_manifest("dup"))
        (self.root / "other").mkdir()
        second = self.root / "other" / "dup"
        second.mkdir(parents=True)
        (second / "extension.json").write_text(json.dumps(base_manifest("dup")), encoding="utf-8")
        with self.assertRaises(reg.RegistryError) as ctx:
            reg.resolve(self.cfg(["dup"], roots=[".ai/extensions", "other"]), self.root)
        self.assertEqual("duplicate-id", ctx.exception.reason)

    def test_unsupported_api_version(self) -> None:
        self.write_ext("x", base_manifest("x", api_version=2))
        self.assert_reason("unsupported-api-version", ["x"])

    def test_missing_dependency(self) -> None:
        self.write_ext("a", base_manifest("a", dependencies=["b"]))
        self.assert_reason("missing-dependency", ["a"])

    def test_conflict_between_enabled(self) -> None:
        self.write_ext("a", base_manifest("a", conflicts=["b"]))
        self.write_ext("b", base_manifest("b"))
        self.assert_reason("conflict", ["a", "b"])

    def test_ordering_cycle(self) -> None:
        self.write_ext("a", base_manifest("a", before=["b"], after=["b"]))
        self.write_ext("b", base_manifest("b"))
        self.assert_reason("composition-cycle", ["a", "b"])

    def test_dependency_cycle_fails_closed(self) -> None:
        self.write_ext("a", base_manifest("a", dependencies=["b"]))
        self.write_ext("b", base_manifest("b", dependencies=["a"]))
        self.assert_reason("composition-cycle", ["a", "b"])

    def test_unresolved_ordering_constraint_fails_closed(self) -> None:
        self.write_ext("a", base_manifest("a", after=["ghost"]))
        self.assert_reason("unresolved-constraint", ["a"])

    def test_pack_only_manifest_cannot_declare_a_command(self) -> None:
        self.write_ext("a", base_manifest("a", types=["pack"], executables=["z"],
                                          commands={"idx": {"executable": "z"}}))
        self.assert_reason("capability-field-mismatch", ["a"])

    def test_integration_cannot_declare_scopes(self) -> None:
        self.write_ext("a", base_manifest("a", types=["integration"], scopes=["x"]))
        self.assert_reason("capability-field-mismatch", ["a"])

    def test_command_without_executable_fails_closed(self) -> None:
        self.write_ext("a", base_manifest("a", types=["command"], root="scripts",
                                          entrypoints={"command": "c.py"},
                                          commands={"idx": {}}),
                       code_files={"scripts/c.py": "X=1\n"})
        self.assert_reason("undeclared-command", ["a"])

    def test_command_string_write_roots_fails_closed_not_char_iterated(self) -> None:
        self.write_ext("a", base_manifest("a", types=["command"], root="scripts",
                                          entrypoints={"command": "c.py"}, executables=["z"],
                                          write_roots=["out"],
                                          commands={"idx": {"executable": "z", "write_roots": "out"}}),
                       code_files={"scripts/c.py": "X=1\n"})
        self.assert_reason("invalid-commands", ["a"])

    def test_command_malformed_args_type_fails_closed(self) -> None:
        self.write_ext("a", base_manifest("a", types=["command"], root="scripts",
                                          entrypoints={"command": "c.py"}, executables=["z"],
                                          commands={"idx": {"executable": "z", "args": "notalist"}}),
                       code_files={"scripts/c.py": "X=1\n"})
        self.assert_reason("invalid-commands", ["a"])

    def test_command_write_root_outside_manifest_authority_fails_closed(self) -> None:
        self.write_ext("a", base_manifest("a", types=["command"], root="scripts",
                                          entrypoints={"command": "c.py"}, executables=["z"],
                                          write_roots=["out"],
                                          commands={"idx": {"executable": "z", "write_roots": ["elsewhere"]}}),
                       code_files={"scripts/c.py": "X=1\n"})
        self.assert_reason("out-of-authority-write-root", ["a"])

    def test_entrypoint_for_capability_not_in_types_fails_closed(self) -> None:
        # types=[gate] but declares a 'command' entrypoint -> capability not in types.
        self.write_ext("a", base_manifest("a", types=["gate"], root="scripts",
                                          entrypoints={"gate": "g.py", "command": "c.py"}),
                       code_files={"scripts/g.py": "X=1\n", "scripts/c.py": "X=1\n"})
        self.assert_reason("invalid-entrypoints", ["a"])

    def test_contribution_destination_outside_write_roots_fails_closed(self) -> None:
        # R3-Q181-2: a pack authorized for .ai/adapters/p must not contribute to project/generated.md.
        self.write_ext("a", base_manifest("a", types=["pack"], root="scripts/extensions/a",
                                          write_roots=[".ai/adapters/p"],
                                          contributes={"files": [{"from": "c.md", "to": "project/generated.md"}]}),
                       code_files={"scripts/extensions/a/c.md": "x\n"})
        self.assert_reason("out-of-authority-write-root", ["a"])

    def test_command_entrypoint_is_optional(self) -> None:
        # R3-Q181-2: a command runs a declared executable and needs no Python entrypoint.
        self.write_ext("a", base_manifest("a", types=["command"], root="scripts/extensions/a",
                                          executables=["z"], commands={"idx": {"executable": "z"}}))
        resolved = self.resolve(["a"])
        self.assertEqual({"idx": "a"}, resolved["commands"])

    def test_config_schema_wrong_type_fails_closed(self) -> None:
        self.write_ext("a", base_manifest("a", types=["pack"], config_schema="notanobject"))
        self.assert_reason("invalid-config-schema", ["a"])

    def test_default_not_declared_in_config_schema_fails_closed(self) -> None:
        # task_190b: config_schema values are now type tokens, so the declared key carries a token
        # ("string") rather than a placeholder object; the assertion (an UNDECLARED default key fails
        # closed with invalid-defaults) is unchanged.
        self.write_ext("a", base_manifest("a", types=["pack"], config_schema={"known": "string"},
                                          defaults={"unknown": 1}))
        self.assert_reason("invalid-defaults", ["a"])

    def test_gate_still_requires_an_entrypoint(self) -> None:
        self.write_ext("a", base_manifest("a", types=["gate"], root="scripts/extensions/a"))
        self.assert_reason("missing-entrypoint", ["a"])

    def test_entrypoint_symlink_or_dotdot_escape_fails_closed(self) -> None:
        # entrypoint that resolves outside the declared root via `..`
        self.write_ext("a", base_manifest("a", types=["gate"], root="scripts/extensions/a",
                                          entrypoints={"gate": "../../rogue.py"}))
        (self.root / "scripts" / "rogue.py").parent.mkdir(parents=True, exist_ok=True)
        (self.root / "scripts" / "rogue.py").write_text("X=1\n", encoding="utf-8")
        self.assert_reason("entrypoint-out-of-root", ["a"])

    def test_platform_enforced_in_production_style_call(self) -> None:
        self.write_ext("a", base_manifest("a", platforms=["posix"]))
        with self.assertRaises(reg.RegistryError) as ctx:
            reg.resolve(self.cfg(["a"]), self.root, platform_name="nt")
        self.assertEqual("unsupported-platform", ctx.exception.reason)

    def test_invalid_scope_value(self) -> None:
        self.write_ext("a", base_manifest("a", scopes=[""]))
        self.assert_reason("invalid-scope", ["a"])

    def test_undeclared_command_executable(self) -> None:
        self.write_ext("a", base_manifest("a", types=["command"], root="scripts",
                                          entrypoints={"command": "c.py"},
                                          commands={"idx": {"executable": "notdeclared"}}),
                       code_files={"scripts/c.py": "X=1\n"})
        self.assert_reason("undeclared-command", ["a"])

    def test_out_of_authority_write_root(self) -> None:
        self.write_ext("a", base_manifest("a", write_roots=["../escape"]))
        self.assert_reason("out-of-authority-write-root", ["a"])

    def test_entrypoint_out_of_root(self) -> None:
        self.write_ext("a", base_manifest("a", types=["gate"], root=".ai/extensions/a",
                                          entrypoints={"gate": "../../../scripts/rust_verify.py"}))
        self.assert_reason("entrypoint-out-of-root", ["a"])

    def test_unknown_manifest_field(self) -> None:
        self.write_ext("a", {**base_manifest("a"), "surprise": 1})
        self.assert_reason("unknown-field", ["a"])

    def test_invalid_id_and_folder_mismatch(self) -> None:
        self.write_ext("a", base_manifest("bad-different"), folder="a")
        self.assert_reason("id-folder-mismatch", ["a"])

    def test_invalid_version(self) -> None:
        self.write_ext("a", base_manifest("a", version="1.0"))
        self.assert_reason("invalid-version", ["a"])

    def test_unknown_enabled_id_fails_closed(self) -> None:
        self.assert_reason("unknown-enabled-id", ["ghost"])

    def test_unsupported_platform(self) -> None:
        self.write_ext("a", base_manifest("a", platforms=["posix"]))
        self.assert_reason("unsupported-platform", ["a"], platform_name="nt")

    def test_duplicate_command_across_extensions(self) -> None:
        self.write_ext("a", base_manifest("a", types=["command"], root="scripts",
                                          entrypoints={"command": "a.py"},
                                          executables=["z"], commands={"idx": {"executable": "z"}}),
                       code_files={"scripts/a.py": "X=1\n"})
        self.write_ext("b", base_manifest("b", types=["command"], root="scripts",
                                          entrypoints={"command": "b.py"},
                                          executables=["z"], commands={"idx": {"executable": "z"}}),
                       code_files={"scripts/b.py": "X=1\n"})
        self.assert_reason("duplicate-command", ["a", "b"])


class ExtensionsConfigTests(RegistryFixture):
    def test_malformed_extensions_block_fails_closed(self) -> None:
        with self.assertRaises(reg.RegistryError) as ctx:
            reg.resolve({"extensions": ["not", "a", "map"]}, self.root)
        self.assertEqual("invalid-extensions-config", ctx.exception.reason)

    def test_unknown_extensions_field_fails_closed(self) -> None:
        with self.assertRaises(reg.RegistryError) as ctx:
            reg.resolve({"extensions": {"enabled": [], "surprise": True}}, self.root)
        self.assertEqual("invalid-extensions-config", ctx.exception.reason)

    def test_duplicate_enabled_id_fails_closed(self) -> None:
        self.write_ext("a", base_manifest("a"))
        with self.assertRaises(reg.RegistryError) as ctx:
            reg.resolve(self.cfg(["a", "a"]), self.root)
        self.assertEqual("duplicate-enabled-id", ctx.exception.reason)

    def test_non_contained_root_fails_closed(self) -> None:
        with self.assertRaises(reg.RegistryError) as ctx:
            reg.resolve(self.cfg(["a"], roots=["../outside"]), self.root)
        self.assertEqual("invalid-extensions-config", ctx.exception.reason)


class CompositionTests(RegistryFixture):
    def test_priority_then_id_tiebreak(self) -> None:
        self.write_ext("b", base_manifest("b", priority=10))
        self.write_ext("a", base_manifest("a", priority=10))
        self.write_ext("c", base_manifest("c", priority=5))
        resolved = self.resolve(["a", "b", "c"])
        self.assertEqual(["c", "a", "b"], resolved["order"])  # c (prio5) first, then a,b by id

    def test_before_after_constraints(self) -> None:
        self.write_ext("late", base_manifest("late", after=["early"]))
        self.write_ext("early", base_manifest("early"))
        resolved = self.resolve(["late", "early"])
        self.assertEqual(["early", "late"], resolved["order"])

    def test_enable_disable_changes_effective_composition(self) -> None:
        self.write_ext("a", base_manifest("a", scopes=["a-scope"]))
        self.write_ext("b", base_manifest("b", scopes=["b-scope"]))
        with_both = self.resolve(["a", "b"])
        self.assertIn("a-scope", with_both["scopes"])
        self.assertIn("b-scope", with_both["scopes"])
        only_a = self.resolve(["a"])
        self.assertIn("a-scope", only_a["scopes"])
        self.assertNotIn("b-scope", only_a["scopes"])

    def test_resolver_report_records_origin(self) -> None:
        self.write_ext("pk", base_manifest("pk", scopes=["custom"]))
        report = reg.resolver_report(self.resolve(["pk"]))
        origins = {row["scope"]: row["origin"] for row in report["scopes"]}
        self.assertEqual("core", origins["control-plane"])
        self.assertEqual("pk", origins["custom"])


class GateLoadingTests(RegistryFixture):
    def test_load_gate_module_only_for_enabled_gate(self) -> None:
        self.write_ext("gt", base_manifest("gt", types=["gate"], root="scripts",
                                           entrypoints={"gate": "mygate.py"}),
                       code_files={"scripts/mygate.py": "MARKER = 'loaded'\ndef cmd_verify(args, *, root, ai):\n    pass\n"})
        resolved = self.resolve(["gt"])
        module = reg.load_gate_module(resolved, "gt", self.root)
        self.assertEqual("loaded", module.MARKER)

    def test_gate_missing_cmd_verify_fails_closed_before_routing(self) -> None:
        # R2-Q181-3-4: a structurally valid gate without the protocol must fail with a named
        # reason at load, not a later AttributeError.
        self.write_ext("gt", base_manifest("gt", types=["gate"], root="scripts",
                                           entrypoints={"gate": "nogate.py"}),
                       code_files={"scripts/nogate.py": "X = 1\n"})
        resolved = self.resolve(["gt"])
        with self.assertRaises(reg.RegistryError) as ctx:
            reg.load_gate_module(resolved, "gt", self.root)
        self.assertEqual("invalid-gate-interface", ctx.exception.reason)

    def test_load_unknown_gate_fails_closed(self) -> None:
        resolved = self.resolve([])
        with self.assertRaises(reg.RegistryError) as ctx:
            reg.load_gate_module(resolved, "ghost", self.root)
        self.assertEqual("unknown-gate", ctx.exception.reason)


if __name__ == "__main__":
    unittest.main()
