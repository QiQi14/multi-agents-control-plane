from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scripts.ai_plane.config as config_module
import scripts.ai_plane.constants as constants
import scripts.ai_plane.tool_detection as tool_detection
import scripts.ai_plane.routing_profile as tool_profile


class FakeWinreg:
    HKEY_CURRENT_USER = "user"
    HKEY_LOCAL_MACHINE = "machine"
    KEY_READ = 1

    def __init__(self, registrations: tuple[str, ...] = (), *, fail: bool = False):
        self.registrations = set(registrations)
        self.fail = fail
        self.closed: list[tuple[str, str]] = []

    def OpenKey(self, root, path, _reserved, _access):
        if self.fail:
            raise PermissionError("fixture registry failure")
        if root not in self.registrations:
            raise FileNotFoundError(path)
        return root, path

    def QueryValueEx(self, _handle, name):
        if name != "URL Protocol":
            raise AssertionError(f"unexpected registry value: {name}")
        return "", 1

    def CloseKey(self, handle):
        self.closed.append(handle)


class ToolProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.ai = self.root / ".ai"
        self.ai.mkdir()
        self.original = (
            constants.ROOT,
            constants.AI,
            config_module.TOOLS,
            config_module.DISPATCH_DESCRIPTORS,
            config_module.TOOL_PROFILES,
            config_module.ROUTING_TAXONOMY,
        )
        constants.ROOT = self.root
        constants.AI = self.ai
        config_module.TOOLS = ("codex", "claude")
        config_module.DISPATCH_DESCRIPTORS = {
            "codex": {"deeplink": {"url_template": "fixture://open"}},
        }
        config_module.TOOL_PROFILES = {
            "codex": {"balanced": {"reasoning_levels": ["standard"]}},
            "claude": {"balanced": {"reasoning_levels": ["standard"]}},
        }
        config_module.ROUTING_TAXONOMY = {
            "catalog_provenance": ["unknown", "manual_assertion"],
            "resolution_selectors": ["app_default"],
            "capability_tags": {},
            "reasoning_levels": ["standard"],
        }
        self.addCleanup(self.restore)

    def restore(self) -> None:
        (
            constants.ROOT,
            constants.AI,
            config_module.TOOLS,
            config_module.DISPATCH_DESCRIPTORS,
            config_module.TOOL_PROFILES,
            config_module.ROUTING_TAXONOMY,
        ) = self.original

    def args(self, **overrides):
        values = {
            "enable": None,
            "default_tool": None,
            "research_tool": None,
            "planning_tool": None,
            "implementation_tool": None,
            "review_tool": None,
            "profile": None,
            "catalog_provenance": None,
            "catalog_source": None,
            "selector": None,
            "exact_pin": None,
            "observed_at": None,
            "fetched_at": None,
            "evaluation_time": None,
            "ttl_seconds": None,
            "reset": False,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def good_profile(self) -> tool_profile.ToolProfile:
        return tool_profile.ToolProfile(
            enabled_tools=("codex",),
            defaults={field: "codex" for field in tool_profile.ROLE_FIELDS},
            profiles={"codex": ("balanced",)},
            catalogs={"codex": tool_profile._unknown_catalog("app_default")},
        )

    def test_committed_public_default_disables_auto_dispatch(self) -> None:
        config_path = Path(__file__).resolve().parents[2] / ".ai" / "config.yaml"
        parsed = config_module.parse_config_yaml(config_path)
        self.assertIs(False, parsed["defaults"]["auto_dispatch"])


    def test_missing_profile_lists_zero_enabled_without_probing(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            tool_profile.cmd_tools_list(argparse.Namespace())
        rendered = out.getvalue()
        self.assertIn("Enabled tools: 0", rendered)
        self.assertIn("codex: enabled=no; transport=deeplink", rendered)
        self.assertIn("claude: enabled=no; transport=manual-only", rendered)
        self.assertIn("No provider, process, network, URI, registry, or PATH probes", rendered)
        self.assertFalse(tool_profile.profile_path().exists())

    def test_noninteractive_configuration_is_deterministic_and_catalog_ordered(self) -> None:
        args = self.args(enable=["claude", "codex"], default_tool="codex")
        tool_profile.cmd_tools_configure(args)
        first = tool_profile.profile_path().read_bytes()
        tool_profile.cmd_tools_configure(args)
        self.assertEqual(first, tool_profile.profile_path().read_bytes())
        parsed = json.loads(first)
        self.assertEqual(2, parsed["version"])
        self.assertEqual(["codex", "claude"], parsed["enabled_tools"])
        self.assertEqual(["balanced"], parsed["routing"]["profiles"]["codex"])
        self.assertEqual("unknown", parsed["routing"]["catalogs"]["codex"]["provenance"])

    def test_interactive_configuration_is_still_noninteractive(self) -> None:
        fake_stdin = mock.Mock()
        fake_stdin.isatty.return_value = True
        with mock.patch.object(tool_profile.sys, "stdin", fake_stdin), mock.patch(
            "builtins.input", side_effect=AssertionError("input must not be called")
        ), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                tool_profile.cmd_tools_configure(self.args())
        self.assertFalse(tool_profile.profile_path().exists())

    def test_non_tty_without_flags_fails_with_deterministic_example(self) -> None:
        fake_stdin = mock.Mock()
        fake_stdin.isatty.return_value = False
        err = io.StringIO()
        with contextlib.redirect_stderr(err), mock.patch.object(tool_profile.sys, "stdin", fake_stdin):
            with self.assertRaises(SystemExit):
                tool_profile.cmd_tools_configure(self.args())
        self.assertIn("deterministic configuration flags are required", err.getvalue())
        self.assertFalse(tool_profile.profile_path().exists())

    def test_invalid_selection_never_corrupts_prior_profile(self) -> None:
        path = tool_profile.atomic_write_profile(self.good_profile())
        before = path.read_bytes()
        invalid = [
            self.args(enable=[], default_tool="codex"),
            self.args(enable=["codex", "codex"], default_tool="codex"),
            self.args(enable=["codex"], default_tool="claude"),
            self.args(enable=["codex"], default_tool="codex", review_tool="codex"),
            self.args(enable=["codex"], reset=True),
        ]
        for args in invalid:
            with self.subTest(args=args):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        tool_profile.cmd_tools_configure(args)
                self.assertEqual(before, path.read_bytes())

    def test_atomic_replace_failure_preserves_prior_profile(self) -> None:
        path = tool_profile.atomic_write_profile(self.good_profile())
        before = path.read_bytes()
        replacement = tool_profile.ToolProfile(
            enabled_tools=("claude",),
            defaults={field: "claude" for field in tool_profile.ROLE_FIELDS},
            profiles={"claude": ("balanced",)},
            catalogs={"claude": tool_profile._unknown_catalog("app_default")},
        )
        with mock.patch.object(tool_profile.os, "replace", side_effect=OSError("fixture failure")):
            with self.assertRaises(OSError):
                tool_profile.atomic_write_profile(replacement)
        self.assertEqual(before, path.read_bytes())
        self.assertEqual([], list(path.parent.glob(".tools.*.tmp")))

    def test_reset_is_deliberate_and_removes_only_profile(self) -> None:
        path = tool_profile.atomic_write_profile(self.good_profile())
        tool_profile.cmd_tools_configure(self.args(reset=True))
        self.assertFalse(path.exists())
        self.assertTrue(path.parent.exists())

    def test_schema_rejects_secret_path_account_billing_and_executable_fields(self) -> None:
        base = self.good_profile().as_json()
        for hostile_key in (
            "secret",
            "token",
            "credential",
            "home_path",
            "executable_args",
            "account_id",
            "billing",
        ):
            with self.subTest(hostile_key=hostile_key):
                candidate = dict(base)
                candidate[hostile_key] = "forbidden"
                with self.assertRaises(tool_profile.ToolProfileError) as ctx:
                    tool_profile.validate_profile_data(candidate, config_module.TOOLS)
                self.assertEqual("tool-profile-invalid", ctx.exception.reason)

    def test_malformed_duplicate_unknown_empty_and_disabled_default_fail_closed(self) -> None:
        path = tool_profile.profile_path()
        path.parent.mkdir(parents=True)
        malformed_values = [
            "{",
            '{"version":1,"version":1,"enabled_tools":["codex"],"defaults":{}}',
            json.dumps({"version": 1, "enabled_tools": [], "defaults": {}}),
            json.dumps(
                {
                    "version": 1,
                    "enabled_tools": ["unknown"],
                    "defaults": {field: "unknown" for field in tool_profile.ROLE_FIELDS},
                }
            ),
            json.dumps(
                {
                    "version": 1,
                    "enabled_tools": ["codex"],
                    "defaults": {
                        **{field: "codex" for field in tool_profile.ROLE_FIELDS},
                        "review_tool": "claude",
                    },
                }
            ),
        ]
        for content in malformed_values:
            with self.subTest(content=content):
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(tool_profile.ToolProfileError):
                    tool_profile.load_profile(config_module.TOOLS, required=True)

    def test_implicit_and_auto_routing_fail_with_stable_reasons(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit):
                tool_profile.resolve_implicit_tool("planning_tool", config_module.TOOLS)
        self.assertIn("tool-profile-required", err.getvalue())
        self.assertIn(tool_profile.CONFIGURE_COMMAND, err.getvalue())

        tool_profile.atomic_write_profile(self.good_profile())
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit):
                tool_profile.require_enabled_tool("claude", config_module.TOOLS)
        self.assertIn("tool-not-enabled", err.getvalue())
        self.assertIn(tool_profile.CONFIGURE_COMMAND, err.getvalue())

    def test_exec_detection_inspects_only_fixed_argv0(self) -> None:
        seen: list[str] = []
        descriptor = {
            "exec": {
                "argv": [
                    "fixture-cli",
                    "$(do-not-run)",
                    "{prompt_text}",
                    "--token=do-not-read",
                ]
            }
        }
        result = tool_detection.detect_exec(
            descriptor,
            which=lambda token: seen.append(token) or "resolved-without-reporting-path",
        )
        self.assertEqual("present", result.status)
        self.assertEqual("exec-path", result.detector)
        self.assertEqual(["fixture-cli"], seen)
        self.assertNotIn("resolved-without-reporting-path", result.reason)
        self.assertFalse(hasattr(tool_detection, "subprocess"))
        self.assertFalse(hasattr(tool_detection, "webbrowser"))

    def test_exec_detection_reports_absent_error_and_nonfixed_unknown(self) -> None:
        descriptor = {"exec": {"argv": ["fixture-cli", "--ignored"]}}
        absent = tool_detection.detect_exec(descriptor, which=lambda _token: None)
        self.assertEqual("absent", absent.status)

        error = tool_detection.detect_exec(
            descriptor,
            which=lambda _token: (_ for _ in ()).throw(OSError("fixture lookup error")),
        )
        self.assertEqual("error", error.status)

        for token in ("{tool}", "../fixture-cli", r"folder\fixture-cli", "C:fixture-cli", "a:b"):
            with self.subTest(token=token):
                detector = mock.Mock(side_effect=AssertionError("PATH lookup must not run"))
                unknown = tool_detection.detect_exec(
                    {"exec": {"argv": [token]}},
                    which=detector,
                )
                self.assertEqual("unknown", unknown.status)
                detector.assert_not_called()

    def test_windows_deeplink_detection_covers_registry_outcomes(self) -> None:
        cases = (
            (("user",), False, "present"),
            ((), False, "absent"),
            (("user", "machine"), False, "unknown"),
            ((), True, "error"),
        )
        for registrations, fail, expected in cases:
            with self.subTest(expected=expected):
                registry = FakeWinreg(registrations, fail=fail)
                result = tool_detection._windows_uri_registration(
                    "fixture",
                    winreg_module=registry,
                )
                self.assertEqual(expected, result.status)
                self.assertEqual("deeplink-registry", result.detector)

    def test_deeplink_detection_is_unknown_when_unsupported_or_unfixed(self) -> None:
        unsupported = tool_detection.detect_uri_scheme(
            "fixture",
            platform_name="unsupported-fixture",
        )
        self.assertEqual("unknown", unsupported.status)
        self.assertIn("unsupported", unsupported.reason)

        detector = mock.Mock(side_effect=AssertionError("URI registry probe must not run"))
        unfixed = tool_detection.detect_deeplink(
            {"deeplink": {"url_template": "{tool}://open"}},
            platform_name="win32",
            uri_detector=detector,
        )
        self.assertEqual("unknown", unfixed.status)
        detector.assert_not_called()

    def test_no_descriptor_returns_unknown_with_exact_reason(self) -> None:
        results = tool_detection.detect_tool_transports(
            ("fixture",),
            {},
            which=mock.Mock(side_effect=AssertionError("PATH probe must not run")),
            uri_detector=mock.Mock(side_effect=AssertionError("URI probe must not run")),
        )
        self.assertEqual(
            tool_detection.DetectionResult("unknown", "none", "no configured transport"),
            results["fixture"][0],
        )

    def test_status_detection_is_structured_and_never_mutates_profile(self) -> None:
        path = tool_profile.atomic_write_profile(self.good_profile())
        before = path.read_bytes()
        detected = {
            "codex": (
                tool_detection.DetectionResult(
                    "present",
                    "deeplink-registry",
                    "fixture registration exists",
                ),
            ),
            "claude": (
                tool_detection.DetectionResult("unknown", "none", "no configured transport"),
            ),
        }
        out = io.StringIO()
        with mock.patch.object(
            tool_detection,
            "detect_tool_transports",
            return_value=detected,
        ), contextlib.redirect_stdout(out):
            tool_profile.cmd_tools_status(argparse.Namespace(detect=True))
        rendered = out.getvalue()
        self.assertIn("codex: enabled=yes", rendered)
        self.assertIn("status=present; detector=deeplink-registry", rendered)
        self.assertIn("Launch attempted: no", rendered)
        self.assertEqual(before, path.read_bytes())

    def test_present_but_disabled_detection_does_not_authorize_routing(self) -> None:
        tool_profile.atomic_write_profile(self.good_profile())
        result = tool_detection.DetectionResult("present", "none", "fixture hint")
        self.assertEqual("present", result.status)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit):
                tool_profile.require_enabled_tool("claude", config_module.TOOLS)
        self.assertIn("tool-not-enabled", err.getvalue())


if __name__ == "__main__":
    unittest.main()
