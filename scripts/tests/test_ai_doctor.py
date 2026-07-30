from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import scripts.ai_plane.doctor as doctor
import scripts.ai_plane.tool_detection as tool_detection
import scripts.ai_plane.routing_profile as tool_profile


CONFIG = """version: 1
defaults:
  research_tool: codex
  planning_tool: codex
  implementation_tool: codex
  review_tool: codex
  generated_dispatch_root: .ai/adapters
  auto_dispatch: false
tools:
  codex:
    role: fixture
    default_isolation: patch
    notes:
      - Fixture tool.
    adapter:
      render_source: null
"""


class ToolProfileDoctorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.ai = self.root / ".ai"
        self.ai.mkdir()
        (self.ai / "config.yaml").write_text(CONFIG, encoding="utf-8")
        for state in ("queue", "active", "done", "archive"):
            (self.ai / "tasks" / state).mkdir(parents=True)

    def collect_profile_check(self) -> dict[str, str]:
        checks = doctor.collect_doctor_checks(
            root=self.root,
            ai=self.ai,
            version_info=(3, 12, 0),
            which=lambda _name: "fixture",
            lock_inspector=lambda _root, _ai: doctor.doctor_check("PASS", "Verification lock", "fixture"),
        )
        return next(check for check in checks if check["name"] == "Tool profile")

    def write_profile_json(self, data: object) -> Path:
        path = self.ai / ".local" / "tools.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_missing_profile_warns_without_mutating(self) -> None:
        check = self.collect_profile_check()
        self.assertEqual("WARN", check["status"])
        self.assertIn("zero providers", check["detail"])
        self.assertEqual(tool_profile.CONFIGURE_GUIDANCE, check["guidance"])
        self.assertFalse((self.ai / ".local").exists())

    def test_malformed_profile_warns_without_mutating(self) -> None:
        path = self.ai / ".local" / "tools.json"
        path.parent.mkdir(parents=True)
        path.write_text("{", encoding="utf-8")
        before = path.read_bytes()
        check = self.collect_profile_check()
        self.assertEqual("WARN", check["status"])
        self.assertIn("tool-profile-invalid", check["detail"])
        self.assertEqual(before, path.read_bytes())

    def test_stale_profile_warns_without_mutating(self) -> None:
        path = self.write_profile_json(
            {
                "version": 1,
                "enabled_tools": ["claude"],
                "defaults": {field: "claude" for field in tool_profile.ROLE_FIELDS},
            }
        )
        before = path.read_bytes()
        check = self.collect_profile_check()
        self.assertEqual("WARN", check["status"])
        self.assertIn("tool-profile-stale", check["detail"])
        self.assertEqual(before, path.read_bytes())

    def test_valid_profile_passes_without_mutating(self) -> None:
        path = self.write_profile_json(
            {
                "version": 1,
                "enabled_tools": ["codex"],
                "defaults": {field: "codex" for field in tool_profile.ROLE_FIELDS},
            }
        )
        before = path.read_bytes()
        check = self.collect_profile_check()
        self.assertEqual("PASS", check["status"])
        self.assertIn("1 tool(s)", check["detail"])
        self.assertEqual(before, path.read_bytes())

    def test_detection_check_distinguishes_all_evidence_layers(self) -> None:
        detections = {
            "codex": (
                tool_detection.DetectionResult(
                    "present",
                    "deeplink-registry",
                    "fixture registration exists",
                ),
            )
        }
        checks = doctor.collect_doctor_checks(
            root=self.root,
            ai=self.ai,
            version_info=(3, 12, 0),
            which=lambda _name: "fixture",
            lock_inspector=lambda _root, _ai: doctor.doctor_check(
                "PASS", "Verification lock", "fixture"
            ),
            transport_detector=lambda *_args, **_kwargs: detections,
            platform_name="fixture-platform",
        )
        check = next(
            item
            for item in checks
            if item["name"] == "Tool detection (codex/deeplink-registry)"
        )
        self.assertEqual("PASS", check["status"])
        self.assertIn("supported=yes", check["detail"])
        self.assertIn("enabled=no", check["detail"])
        self.assertIn("detected=present", check["detail"])
        self.assertIn("launch-attempted=no", check["detail"])
        self.assertIn("submitted/running=unknown", check["detail"])
        self.assertFalse((self.ai / ".local").exists())

    def test_absent_or_error_detection_warns_without_authorizing_changes(self) -> None:
        for detected_status in ("absent", "error"):
            with self.subTest(detected_status=detected_status):
                detections = {
                    "codex": (
                        tool_detection.DetectionResult(
                            detected_status,
                            "exec-path",
                            "fixture advisory result",
                        ),
                    )
                }
                checks = doctor.collect_doctor_checks(
                    root=self.root,
                    ai=self.ai,
                    version_info=(3, 12, 0),
                    which=lambda _name: "fixture",
                    lock_inspector=lambda _root, _ai: doctor.doctor_check(
                        "PASS", "Verification lock", "fixture"
                    ),
                    transport_detector=lambda *_args, **_kwargs: detections,
                )
                check = next(item for item in checks if item["name"].startswith("Tool detection"))
                self.assertEqual("WARN", check["status"])
                self.assertIn("advisory only", check["guidance"])
                self.assertFalse((self.ai / ".local").exists())


if __name__ == "__main__":
    unittest.main()
