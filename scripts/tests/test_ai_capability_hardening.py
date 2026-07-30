"""task_190b capability hardening: typed extension configuration + consumption path, gate
`cmd_verify` signature inspection, and command input/output/write-authority at manifest AND
dispatch time. design.md (task_190b) is normative for the public shapes and named failure
boundaries exercised here; every new failure mode fails closed with a stable reason.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
import scripts.ai_plane.config as config_module
import scripts.extension_registry as reg
import scripts.verify_core as verify_core


# ---------------------------------------------------------------------------
# Typed configuration: manifest-time value-type validation (AC1)
# ---------------------------------------------------------------------------


def pack(**over) -> dict:
    manifest = {"id": "p", "version": "1.0.0", "api_version": 1, "types": ["pack"]}
    manifest.update(over)
    return manifest


class ConfigSchemaTypingTests(unittest.TestCase):
    def validate(self, data: dict) -> dict:
        return reg.validate_manifest(data, source="ext.json")

    def assert_reason(self, reason: str, data: dict) -> None:
        with self.assertRaises(reg.RegistryError) as ctx:
            self.validate(data)
        self.assertEqual(reason, ctx.exception.reason)

    def test_all_supported_type_tokens_validate(self) -> None:
        self.validate(pack(
            config_schema={"profile": "string", "retries": "integer", "threshold": "number",
                           "strict": "boolean", "features": "list"},
            defaults={"profile": "ci", "retries": 3, "threshold": 0.5, "strict": True,
                      "features": ["a", "b"]},
        ))

    def test_unknown_type_token_fails_closed(self) -> None:
        self.assert_reason("invalid-config-schema", pack(config_schema={"x": "float"}))

    def test_non_string_schema_value_fails_closed(self) -> None:
        self.assert_reason("invalid-config-schema", pack(config_schema={"x": {"type": "string"}}))

    def test_empty_schema_key_fails_closed(self) -> None:
        self.assert_reason("invalid-config-schema", pack(config_schema={"": "string"}))

    def test_undeclared_default_key_fails_closed(self) -> None:
        self.assert_reason("invalid-defaults",
                           pack(config_schema={"known": "string"}, defaults={"unknown": "x"}))

    def test_default_value_type_matrix(self) -> None:
        # (token, value, is_valid) — every token gets a positive and a negative, plus the decisive
        # bool-vs-int/number edge (a boolean is NOT an integer or number, and vice versa).
        cases = [
            ("string", "text", True), ("string", 1, False), ("string", True, False),
            ("integer", 3, True), ("integer", "3", False), ("integer", True, False),
            ("integer", 3.0, False),
            ("number", 3, True), ("number", 3.5, True), ("number", True, False),
            ("number", "3", False),
            ("boolean", True, True), ("boolean", False, True), ("boolean", "true", False),
            ("boolean", 1, False),
            ("list", [], True), ("list", ["x"], True), ("list", "x", False), ("list", {}, False),
        ]
        for token, value, is_valid in cases:
            with self.subTest(token=token, value=value):
                data = pack(config_schema={"k": token}, defaults={"k": value})
                if is_valid:
                    self.validate(data)
                else:
                    self.assert_reason("invalid-defaults", data)


# ---------------------------------------------------------------------------
# Shared on-disk fixture for resolve()/load/dispatch tests
# ---------------------------------------------------------------------------


class RegistryDiskFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "scripts" / "extensions").mkdir(parents=True)

    def write_ext(self, ext_id: str, manifest: dict, code_files: dict[str, str] | None = None) -> None:
        ext_dir = self.root / "scripts" / "extensions" / ext_id
        ext_dir.mkdir(parents=True, exist_ok=True)
        (ext_dir / "extension.json").write_text(json.dumps(manifest), encoding="utf-8")
        for rel, content in (code_files or {}).items():
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    def cfg(self, enabled: list[str], config: dict | None = None) -> dict:
        block: dict = {"roots": ["scripts/extensions"], "enabled": enabled}
        if config is not None:
            block["config"] = config
        return {"extensions": block}

    def resolve(self, enabled: list[str], config: dict | None = None, **kw):
        return reg.resolve(self.cfg(enabled, config), self.root, **kw)

    def assert_reason(self, reason: str, enabled: list[str], config: dict | None = None, **kw):
        with self.assertRaises(reg.RegistryError) as ctx:
            self.resolve(enabled, config, **kw)
        self.assertEqual(reason, ctx.exception.reason)
        return ctx.exception


# ---------------------------------------------------------------------------
# Project overrides + effective configuration (AC1 / AC2)
# ---------------------------------------------------------------------------


class ProjectConfigOverrideTests(RegistryDiskFixture):
    def write_configurable_pack(self, ext_id: str = "cfgpack") -> None:
        self.write_ext(ext_id, pack(id=ext_id, config_schema={"strict": "boolean", "profile": "string"},
                                    defaults={"strict": False, "profile": "ci"}))

    def test_effective_config_overlays_defaults_without_mutating_manifest(self) -> None:
        self.write_configurable_pack()
        resolved = self.resolve(["cfgpack"], config={"cfgpack": {"strict": True}})
        self.assertEqual({"strict": True, "profile": "ci"}, resolved["config"]["cfgpack"])
        # manifest defaults are untouched (the resolver overlays onto a copy).
        self.assertEqual({"strict": False, "profile": "ci"},
                         resolved["extensions"]["cfgpack"]["defaults"])

    def test_zero_config_yields_pure_defaults(self) -> None:
        self.write_configurable_pack()
        resolved = self.resolve(["cfgpack"])
        self.assertEqual({"strict": False, "profile": "ci"}, resolved["config"]["cfgpack"])

    def test_unknown_id_in_extensions_config_fails_closed(self) -> None:
        self.write_configurable_pack()
        self.assert_reason("invalid-extensions-config", ["cfgpack"], config={"ghost": {"strict": True}})

    def test_disabled_but_discovered_id_in_config_fails_closed(self) -> None:
        self.write_configurable_pack()
        self.write_ext("other", pack(id="other", config_schema={"k": "string"}, defaults={"k": "x"}))
        # 'other' exists on disk but is not enabled -> configuring it fails closed.
        self.assert_reason("invalid-extensions-config", ["cfgpack"], config={"other": {"k": "y"}})

    def test_value_block_not_a_mapping_fails_closed(self) -> None:
        self.write_configurable_pack()
        self.assert_reason("invalid-extensions-config", ["cfgpack"], config={"cfgpack": ["not", "map"]})

    def test_extensions_config_not_a_mapping_fails_closed(self) -> None:
        self.write_configurable_pack()
        with self.assertRaises(reg.RegistryError) as ctx:
            reg.resolve({"extensions": {"roots": ["scripts/extensions"], "enabled": ["cfgpack"],
                                        "config": ["x"]}}, self.root)
        self.assertEqual("invalid-extensions-config", ctx.exception.reason)

    def test_undeclared_project_key_fails_closed(self) -> None:
        self.write_configurable_pack()
        self.assert_reason("invalid-extension-config-value", ["cfgpack"],
                           config={"cfgpack": {"nope": 1}})

    def test_mistyped_project_value_fails_closed(self) -> None:
        self.write_configurable_pack()
        # 'strict' is boolean; a string value is rejected.
        self.assert_reason("invalid-extension-config-value", ["cfgpack"],
                           config={"cfgpack": {"strict": "yes"}})


# ---------------------------------------------------------------------------
# Consumption path: {config:<key>} expansion reaches the runner argv (AC2)
# ---------------------------------------------------------------------------


def command_ext(**over) -> dict:
    manifest = {
        "id": "cmd", "version": "1.0.0", "api_version": 1, "types": ["command"],
        "root": "scripts/extensions/cmd", "executables": ["tool"],
        "config_schema": {"profile": "string"}, "defaults": {"profile": "ci"},
        "commands": {"run": {"executable": "tool", "args": ["--profile", "{config:profile}"]}},
    }
    manifest.update(over)
    return manifest


class ConfigConsumptionTests(RegistryDiskFixture):
    def test_placeholder_expands_from_default(self) -> None:
        self.write_ext("cmd", command_ext())
        resolved = self.resolve(["cmd"])
        argv, _ = reg.command_argv(resolved, "run", [])
        self.assertEqual(["tool", "--profile", "ci"], argv)

    def test_placeholder_expands_from_project_override_to_runner_argv(self) -> None:
        # The bounded proof (design.md "Required consumption path"): an effective PROJECT value
        # reaches the argv handed to the generic shell-disabled runner.
        self.write_ext("cmd", command_ext())
        resolved = self.resolve(["cmd"], config={"cmd": {"profile": "prod"}})
        captured: dict = {}

        def fake_run_argv(argv, cwd, *, timeout=None):
            captured["argv"] = argv
            captured["timeout"] = timeout
            return 0

        with mock.patch.object(verify_core, "run_argv", side_effect=fake_run_argv):
            code = reg.run_command_capability(resolved, "run", self.root)
        self.assertEqual(0, code)
        self.assertEqual(["tool", "--profile", "prod"], captured["argv"])

    def test_expansion_spellings_are_deterministic(self) -> None:
        manifest = command_ext(
            config_schema={"s": "string", "b": "boolean", "i": "integer", "n": "number",
                           "lst": "list"},
            defaults={"s": "raw text", "b": True, "i": 7, "n": 2.5, "lst": ["x", "y"]},
            commands={"run": {"executable": "tool", "args": [
                "{config:s}", "{config:b}", "{config:i}", "{config:n}", "{config:lst}"]}},
        )
        self.write_ext("cmd", manifest)
        resolved = self.resolve(["cmd"])
        argv, _ = reg.command_argv(resolved, "run", [])
        self.assertEqual(["tool", "raw text", "true", "7", "2.5", '["x","y"]'], argv)

    def test_embedded_placeholder_expands_in_place(self) -> None:
        manifest = command_ext(
            commands={"run": {"executable": "tool", "args": ["--profile={config:profile}"]}})
        self.write_ext("cmd", manifest)
        resolved = self.resolve(["cmd"], config={"cmd": {"profile": "prod"}})
        argv, _ = reg.command_argv(resolved, "run", [])
        self.assertEqual(["tool", "--profile=prod"], argv)

    def test_undeclared_config_placeholder_fails_closed_at_manifest(self) -> None:
        manifest = command_ext(
            commands={"run": {"executable": "tool", "args": ["{config:missing}"]}})
        self.write_ext("cmd", manifest)
        self.assert_reason("invalid-commands", ["cmd"])

    def test_full_production_path_config_yaml_to_argv(self) -> None:
        # Prove the WHOLE production path: the dependency-free `parse_config_yaml` parses a nested
        # `extensions.config` block, resolve() validates+overlays it, and the override reaches argv.
        self.write_ext("cmd", command_ext())
        (self.root / ".ai").mkdir()
        (self.root / ".ai" / "config.yaml").write_text(
            "version: 1\n"
            "extensions:\n"
            "  roots:\n"
            "    - scripts/extensions\n"
            "  enabled:\n"
            "    - cmd\n"
            "  config:\n"
            "    cmd:\n"
            "      profile: prod\n",
            encoding="utf-8",
        )
        parsed = config_module.parse_config_yaml(self.root / ".ai" / "config.yaml")
        resolved = reg.resolve(parsed, self.root)
        argv, _ = reg.command_argv(resolved, "run", [])
        self.assertEqual(["tool", "--profile", "prod"], argv)

    def test_override_only_declared_key_resolves_from_project_config(self) -> None:
        # R1-190B-4: a declared config key with NO default may be supplied solely through
        # extensions.config; it must resolve and reach argv (a default is not required, only a
        # declaration).
        manifest = command_ext(
            config_schema={"profile": "string", "region": "string"}, defaults={"profile": "ci"},
            commands={"run": {"executable": "tool", "args": ["--region", "{config:region}"]}})
        self.write_ext("cmd", manifest)
        resolved = self.resolve(["cmd"], config={"cmd": {"region": "eu"}})
        argv, _ = reg.command_argv(resolved, "run", [])
        self.assertEqual(["tool", "--region", "eu"], argv)

    def test_declared_key_without_default_or_override_fails_closed(self) -> None:
        # R1-190B-4: a declared key referenced by a command but with neither a default nor a project
        # override has no effective value and fails closed at resolve with a stable reason.
        manifest = command_ext(
            config_schema={"profile": "string", "region": "string"}, defaults={"profile": "ci"},
            commands={"run": {"executable": "tool", "args": ["{config:region}"]}})
        self.write_ext("cmd", manifest)
        self.assert_reason("missing-config-value", ["cmd"])

    def test_undeclared_config_placeholder_still_fails_at_manifest(self) -> None:
        # A placeholder referencing a key NOT in config_schema is still a manifest error.
        manifest = command_ext(
            commands={"run": {"executable": "tool", "args": ["{config:notdeclared}"]}})
        self.write_ext("cmd", manifest)
        self.assert_reason("invalid-commands", ["cmd"])


# ---------------------------------------------------------------------------
# Gate cmd_verify signature inspection (AC3)
# ---------------------------------------------------------------------------


class GateInterfaceTests(RegistryDiskFixture):
    def gate_reason(self, cmd_verify_src: str) -> str | None:
        """Write a gate whose entrypoint defines cmd_verify from `cmd_verify_src`, load it, and
        return the fail-closed reason (or None if it loaded)."""
        manifest = {"id": "gt", "version": "1.0.0", "api_version": 1, "types": ["gate"],
                    "root": "scripts/extensions/gt", "entrypoints": {"gate": "g.py"}}
        self.write_ext("gt", manifest, code_files={"scripts/extensions/gt/g.py": cmd_verify_src})
        resolved = self.resolve(["gt"])
        try:
            reg.load_gate_module(resolved, "gt", self.root)
            return None
        except reg.RegistryError as error:
            return error.reason

    def test_reference_shape_loads(self) -> None:
        self.assertIsNone(self.gate_reason("def cmd_verify(args, *, root, ai):\n    pass\n"))

    def test_positional_name_is_enforced(self) -> None:
        # R1-190B-3: the normative public shape names the positional parameter `args`; a wrong name
        # fails closed even though the argument is bound positionally at the call site.
        self.assertEqual("invalid-gate-interface",
                         self.gate_reason("def cmd_verify(a, *, root, ai):\n    pass\n"))

    def test_wrong_keyword_name_fails_closed(self) -> None:
        self.assertEqual("invalid-gate-interface",
                         self.gate_reason("def cmd_verify(args, *, base, ai):\n    pass\n"))

    def test_missing_required_keyword_fails_closed(self) -> None:
        self.assertEqual("invalid-gate-interface",
                         self.gate_reason("def cmd_verify(args, *, root):\n    pass\n"))

    def test_extra_positional_arity_fails_closed(self) -> None:
        self.assertEqual("invalid-gate-interface",
                         self.gate_reason("def cmd_verify(args, extra, *, root, ai):\n    pass\n"))

    def test_zero_positional_fails_closed(self) -> None:
        self.assertEqual("invalid-gate-interface",
                         self.gate_reason("def cmd_verify(*, root, ai):\n    pass\n"))

    def test_var_positional_fails_closed(self) -> None:
        self.assertEqual("invalid-gate-interface",
                         self.gate_reason("def cmd_verify(*args, root, ai):\n    pass\n"))

    def test_var_keyword_fails_closed(self) -> None:
        self.assertEqual("invalid-gate-interface",
                         self.gate_reason("def cmd_verify(args, *, root, ai, **kw):\n    pass\n"))

    def test_extra_keyword_only_fails_closed(self) -> None:
        self.assertEqual("invalid-gate-interface",
                         self.gate_reason("def cmd_verify(args, *, root, ai, extra):\n    pass\n"))

    def test_optional_positional_default_fails_closed(self) -> None:
        self.assertEqual("invalid-gate-interface",
                         self.gate_reason("def cmd_verify(args=None, *, root, ai):\n    pass\n"))

    def test_optional_keyword_default_fails_closed(self) -> None:
        self.assertEqual("invalid-gate-interface",
                         self.gate_reason("def cmd_verify(args, *, root, ai=None):\n    pass\n"))

    def test_reference_rust_gate_signature_conforms(self) -> None:
        # The reference gate must remain loadable/routable unchanged: its real cmd_verify passes the
        # same predicate load_gate_module applies (annotations are irrelevant).
        import scripts.rust_verify as rust_verify
        reg._validate_gate_interface("rust", rust_verify.cmd_verify)  # must not raise


# ---------------------------------------------------------------------------
# Command input/output/write-authority contract, manifest + dispatch (AC4)
# ---------------------------------------------------------------------------


def io_command(**spec_over) -> dict:
    spec = {"executable": "tool"}
    spec.update(spec_over)
    return {
        "id": "cmd", "version": "1.0.0", "api_version": 1, "types": ["command"],
        "root": "scripts/extensions/cmd", "executables": ["tool"],
        "read_roots": ["project"], "write_roots": [".ai/out"],
        "commands": {"run": spec},
    }


class CommandContractTests(RegistryDiskFixture):
    def assert_manifest_reason(self, reason: str, manifest: dict) -> None:
        with self.assertRaises(reg.RegistryError) as ctx:
            reg.validate_manifest(manifest, source="ext.json")
        self.assertEqual(reason, ctx.exception.reason)

    def test_input_within_read_roots_ok_and_stdin_stream_ok(self) -> None:
        reg.validate_manifest(io_command(inputs=["project/Cargo.toml", "stdin: piped"]),
                              source="ext.json")

    def test_input_outside_read_roots_fails_closed(self) -> None:
        self.assert_manifest_reason("out-of-authority-read-root",
                                    io_command(inputs=["secret/keys.txt"]))

    def test_output_stream_ok_and_file_within_command_write_roots_ok(self) -> None:
        reg.validate_manifest(
            io_command(write_roots=[".ai/out"], outputs=["stdout: json", "stderr: log", ".ai/out/index.json"]),
            source="ext.json")

    def test_output_file_outside_command_write_roots_fails_closed(self) -> None:
        # The command declares no write_roots, so a file output has no authority.
        self.assert_manifest_reason("out-of-authority-write-root",
                                    io_command(outputs=[".ai/out/index.json"]))

    def test_command_write_root_outside_manifest_authority_fails_closed(self) -> None:
        self.assert_manifest_reason("out-of-authority-write-root",
                                    io_command(write_roots=[".ai/elsewhere"]))

    # R1-190B-1: Windows drive-relative (`C:escape`) and rooted (`/escape`, `\escape`) paths are NOT
    # is_absolute() on Windows yet resolve outside the repository; they must fail closed under the
    # named authority reasons on any OS (evaluated under both path conventions).
    def test_contained_relative_rejects_drive_and_root_paths(self) -> None:
        for bad in ("C:escape", "C:/escape", "C:\\escape", "/escape", "\\escape", "../escape"):
            self.assertFalse(reg._contained_relative(bad), f"{bad!r} must be rejected")
        for ok in (".ai/out", "project/Cargo.toml", "scripts/x.py"):
            self.assertTrue(reg._contained_relative(ok), f"{ok!r} must be accepted")

    def test_windows_drive_relative_manifest_write_root_fails_closed(self) -> None:
        manifest = io_command()
        manifest["write_roots"] = ["C:escape"]
        self.assert_manifest_reason("out-of-authority-write-root", manifest)

    def test_windows_drive_relative_command_output_fails_closed(self) -> None:
        self.assert_manifest_reason("out-of-authority-write-root",
                                    io_command(outputs=["C:escape/file"]))

    def test_windows_drive_relative_command_input_fails_closed(self) -> None:
        self.assert_manifest_reason("out-of-authority-read-root",
                                    io_command(inputs=["C:escape/in"]))

    def test_rooted_command_output_fails_closed(self) -> None:
        self.assert_manifest_reason("out-of-authority-write-root",
                                    io_command(outputs=["/escape/file"]))

    def test_backslash_rooted_command_write_root_fails_closed(self) -> None:
        self.assert_manifest_reason("out-of-authority-write-root",
                                    io_command(write_roots=["\\escape"]))

    def test_dispatch_time_recheck_of_windows_drive_output_fails_closed(self) -> None:
        # The dispatch-time re-check must also reject a mutated drive-relative output.
        self.write_ext("cmd", io_command(write_roots=[".ai/out"], outputs=[".ai/out/ok.json"]))
        resolved = self.resolve(["cmd"])
        resolved["extensions"]["cmd"]["commands"]["run"]["outputs"] = ["C:escape/leak.json"]
        with self.assertRaises(reg.RegistryError) as ctx:
            reg.command_argv(resolved, "run", [])
        self.assertEqual("out-of-authority-write-root", ctx.exception.reason)

    def test_caller_argv_rejected_with_named_reason(self) -> None:
        self.write_ext("cmd", io_command(args=["--scan"]))
        resolved = self.resolve(["cmd"])
        with self.assertRaises(reg.RegistryError) as ctx:
            reg.command_argv(resolved, "run", ["surprise"])
        self.assertEqual("unexpected-command-arguments", ctx.exception.reason)

    def test_dispatch_time_recheck_of_mutated_output_fails_closed(self) -> None:
        # A previously RESOLVED command contract is mutated to name an out-of-authority output; the
        # dispatch-time re-check (design.md "Inputs, outputs, and declared authority") still fails
        # closed even though the manifest passed validation at load.
        self.write_ext("cmd", io_command(write_roots=[".ai/out"], outputs=[".ai/out/ok.json"]))
        resolved = self.resolve(["cmd"])
        # sanity: it dispatches cleanly before mutation
        reg.command_argv(resolved, "run", [])
        resolved["extensions"]["cmd"]["commands"]["run"]["outputs"] = ["../escape/leak.json"]
        with self.assertRaises(reg.RegistryError) as ctx:
            reg.command_argv(resolved, "run", [])
        self.assertEqual("out-of-authority-write-root", ctx.exception.reason)

    def test_dispatch_time_recheck_of_mutated_write_root_fails_closed(self) -> None:
        self.write_ext("cmd", io_command(write_roots=[".ai/out"]))
        resolved = self.resolve(["cmd"])
        resolved["extensions"]["cmd"]["commands"]["run"]["write_roots"] = [".ai/elsewhere"]
        with self.assertRaises(reg.RegistryError) as ctx:
            reg.command_argv(resolved, "run", [])
        self.assertEqual("out-of-authority-write-root", ctx.exception.reason)

    def test_reference_cargo_metadata_contract_passes_manifest_and_dispatch(self) -> None:
        # The reference cargo-metadata command (project/Cargo.toml input within read_roots, a
        # stdout stream output) must survive both the manifest-time and dispatch-time checks.
        rust_manifest = json.loads(
            (REPO_ROOT / "scripts" / "extensions" / "rust" / "extension.json").read_text(encoding="utf-8"))
        normalized = reg.validate_manifest(rust_manifest, source="rust")
        spec = normalized["commands"]["cargo-metadata"]
        reg._validate_command_authority("cargo-metadata", spec, normalized, "dispatch")


# ---------------------------------------------------------------------------
# End-to-end typed configuration through the production config.yaml parser (R1-190B-2)
# ---------------------------------------------------------------------------


class ProductionConfigYamlTypingTests(RegistryDiskFixture):
    """Every supported token round-trips through the dependency-free parse_config_yaml -> resolve ->
    argv path, including a FRACTIONAL number; type mismatches and non-finite literals fail closed."""

    def _write_typed_command_ext(self) -> None:
        self.write_ext("cmd", {
            "id": "cmd", "version": "1.0.0", "api_version": 1, "types": ["command"],
            "root": "scripts/extensions/cmd", "executables": ["tool"],
            "config_schema": {"s": "string", "i": "integer", "n": "number", "b": "boolean",
                              "lst": "list"},
            "defaults": {"s": "d", "i": 0, "n": 0, "b": False, "lst": []},
            "commands": {"run": {"executable": "tool", "args": [
                "{config:s}", "{config:i}", "{config:n}", "{config:b}", "{config:lst}"]}},
        })

    def _parse(self, overrides_block: str) -> dict:
        (self.root / ".ai").mkdir(parents=True, exist_ok=True)
        (self.root / ".ai" / "config.yaml").write_text(
            "version: 1\nextensions:\n  roots:\n    - scripts/extensions\n  enabled:\n    - cmd\n"
            + overrides_block, encoding="utf-8")
        return config_module.parse_config_yaml(self.root / ".ai" / "config.yaml")

    def test_all_tokens_including_fractional_number_round_trip_to_argv(self) -> None:
        self._write_typed_command_ext()
        parsed = self._parse(
            "  config:\n    cmd:\n"
            "      s: hello\n      i: 7\n      n: 0.5\n      b: true\n      lst: [\"a\", \"b\"]\n")
        resolved = reg.resolve(parsed, self.root)
        argv, _ = reg.command_argv(resolved, "run", [])
        self.assertEqual(["tool", "hello", "7", "0.5", "true", '["a","b"]'], argv)

    def test_fractional_number_parses_as_float_and_validates(self) -> None:
        self._write_typed_command_ext()
        parsed = self._parse("  config:\n    cmd:\n      n: 0.5\n")
        self.assertIsInstance(parsed["extensions"]["config"]["cmd"]["n"], float)
        resolved = reg.resolve(parsed, self.root)
        self.assertEqual(0.5, resolved["config"]["cmd"]["n"])

    def test_integer_valued_number_override_round_trips(self) -> None:
        self._write_typed_command_ext()
        parsed = self._parse("  config:\n    cmd:\n      n: 3\n")
        resolved = reg.resolve(parsed, self.root)
        self.assertEqual(3, resolved["config"]["cmd"]["n"])

    def test_bool_given_for_number_fails_closed(self) -> None:
        self._write_typed_command_ext()
        parsed = self._parse("  config:\n    cmd:\n      n: true\n")
        with self.assertRaises(reg.RegistryError) as ctx:
            reg.resolve(parsed, self.root)
        self.assertEqual("invalid-extension-config-value", ctx.exception.reason)

    def test_quoted_number_stays_string_and_fails_closed(self) -> None:
        self._write_typed_command_ext()
        parsed = self._parse('  config:\n    cmd:\n      n: "0.5"\n')  # quoted -> str, not number
        with self.assertRaises(reg.RegistryError) as ctx:
            reg.resolve(parsed, self.root)
        self.assertEqual("invalid-extension-config-value", ctx.exception.reason)

    def test_non_finite_number_literal_fails_closed_at_parse(self) -> None:
        self._write_typed_command_ext()
        with self.assertRaises(config_module.ConfigError):
            self._parse("  config:\n    cmd:\n      n: 1e400\n")


if __name__ == "__main__":
    unittest.main()
