"""task_192a: optional task routing metadata and the derived complexity band.

Like the config schema tests, every fixture uses SYNTHETIC vocabulary and synthetic tool names, so
nothing here can pass by knowing the repository's real zones, axes, capabilities, or models.
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scripts.ai_plane.config as config_module  # noqa: E402
import scripts.ai_plane.tasks as tasks  # noqa: E402
from scripts.ai_plane.primitives import parse_simple_yaml  # noqa: E402

TAXONOMY = {
    "version": 1,
    "axis_levels": ["small", "medium", "large"],
    "provenance": ["by_owner", "by_planner"],
    "capability_tags": {"cap_alpha": "Does alpha.", "cap_beta": "Does beta."},
    "zones": {
        "zone_one": {"summary": "First zone.", "required_capabilities": ["cap_alpha"]},
        "zone_two": {"summary": "Second zone.", "required_capabilities": ["cap_beta"]},
    },
    "axes": {
        "axis_one": {"summary": "A.", "small": "s", "medium": "m", "large": "l"},
        "axis_two": {"summary": "B.", "small": "s", "medium": "m", "large": "l"},
        "axis_three": {"summary": "C.", "small": "s", "medium": "m", "large": "l"},
    },
    "complexity_bands": {
        "order": ["calm", "busy", "heavy"],
        "default": "calm",
        "rules": {
            "focus_axis_large": {"band": "heavy", "at_or_above": "large", "minimum_count": 1,
                                 "restrict_to_axes": ["axis_two"]},
            "two_large": {"band": "heavy", "at_or_above": "large", "minimum_count": 2},
            "any_large": {"band": "busy", "at_or_above": "large", "minimum_count": 1},
            "two_medium": {"band": "busy", "at_or_above": "medium", "minimum_count": 2},
        },
    },
    "reasoning_levels": ["light", "deep"],
    "reasoning_escalation_threshold": "light",
    "review_depth_floor": {"high": "deep"},
}

TOOLS = ("alpha_tool", "beta_tool")
TOOL_CAPABILITIES = {"alpha_tool": ("cap_alpha",), "beta_tool": ("cap_beta",)}
TOOL_PROFILES = {
    "alpha_tool": {
        "fast": {"model": "Fast One", "reasoning_levels": ["light"]},
        "slow": {"model": "Slow One", "reasoning_levels": ["light", "deep"], "capabilities": ["cap_beta"]},
    },
    "beta_tool": {},
}

VALID_TASK = {
    "id": "task_synthetic",
    "risk": "medium",
    "preferred_tool": "alpha_tool",
    "review_tool": "beta_tool",
    "routing_policy_version": "1",
    "routing_zone": "zone_one",
    "routing_axes": ["axis_one=small", "axis_two=large", "axis_three=medium"],
    "routing_complexity_band": "heavy",
    "routing_profile_tool": "alpha_tool",
    "routing_profile": "slow",
    "routing_reasoning_level": "deep",
    "routing_provenance": "by_owner",
    "routing_rationale": "The vector, not the risk tier, chose this profile.",
}


def task(**overrides):
    data = copy.deepcopy(VALID_TASK)
    for key, value in overrides.items():
        if value is _DROP:
            data.pop(key, None)
        else:
            data[key] = value
    return data


class _Drop:
    pass


_DROP = _Drop()


class RoutingFixture(unittest.TestCase):
    """Bind the synthetic vocabulary for the duration of one test."""

    def setUp(self) -> None:
        vocabulary = {"risk": config_module.RISKS, "preferred_tool": TOOLS, "review_tool": TOOLS}
        for name, value in (("ROUTING_TAXONOMY", TAXONOMY), ("TOOLS", TOOLS),
                            ("TOOL_CAPABILITIES", TOOL_CAPABILITIES), ("TOOL_PROFILES", TOOL_PROFILES),
                            ("TASK_CONTRACT_VOCABULARY", vocabulary)):
            patch = mock.patch.object(config_module, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def fields(self, data) -> list[str]:
        return [field for field, _value, _allowed in tasks.task_routing_violations(data)]


class ComplexityBandTests(RoutingFixture):
    def band(self, **axes) -> str:
        vector = {axis: "small" for axis in TAXONOMY["axes"]}
        vector.update(axes)
        return tasks.derive_complexity_band(vector)[0]

    def test_default_band_applies_when_no_rule_matches(self) -> None:
        self.assertEqual("calm", self.band())
        self.assertEqual("calm", self.band(axis_one="medium"))

    def test_highest_matching_band_wins_regardless_of_rule_order(self) -> None:
        self.assertEqual("heavy", self.band(axis_two="large"))
        self.assertEqual("heavy", self.band(axis_one="large", axis_three="large"))
        self.assertEqual("busy", self.band(axis_one="large"))
        self.assertEqual("busy", self.band(axis_one="medium", axis_three="medium"))

    def test_rule_declaration_order_does_not_change_the_result(self) -> None:
        reversed_rules = dict(reversed(list(TAXONOMY["complexity_bands"]["rules"].items())))
        shuffled = copy.deepcopy(TAXONOMY)
        shuffled["complexity_bands"]["rules"] = reversed_rules
        with mock.patch.object(config_module, "ROUTING_TAXONOMY", shuffled):
            self.assertEqual("heavy", self.band(axis_two="large"))
            self.assertEqual("busy", self.band(axis_one="large"))

    def test_every_band_names_the_rule_and_axes_that_produced_it(self) -> None:
        vector = {"axis_one": "small", "axis_two": "large", "axis_three": "medium"}
        band, reasons = tasks.derive_complexity_band(vector)
        self.assertEqual("heavy", band)
        self.assertTrue(any("focus_axis_large" in reason and "axis_two" in reason for reason in reasons))
        for reason in reasons:
            self.assertNotRegex(reason, r"score|points|weight")

    def test_band_derivation_fails_closed_without_a_declared_taxonomy(self) -> None:
        with mock.patch.object(config_module, "ROUTING_TAXONOMY", None):
            with self.assertRaises(SystemExit):
                tasks.derive_complexity_band({"axis_one": "small"})


class EffectiveCapabilityTests(RoutingFixture):
    def test_effective_set_is_the_union_of_surface_and_model_capabilities(self) -> None:
        self.assertEqual(("cap_alpha",), tasks.profile_capabilities("alpha_tool", "fast"))
        self.assertEqual(("cap_alpha", "cap_beta"), tasks.profile_capabilities("alpha_tool", "slow"))

    def test_an_undeclared_profile_fails_closed(self) -> None:
        with self.assertRaises(SystemExit):
            tasks.profile_capabilities("beta_tool", "slow")


class RoutingMetadataCompatibilityTests(RoutingFixture):
    def test_a_contract_with_no_routing_field_is_legacy_and_readable(self) -> None:
        legacy = {"id": "task_legacy", "risk": "high", "preferred_tool": "alpha_tool",
                  "review_tool": "beta_tool"}
        self.assertEqual([], tasks.task_routing_violations(legacy))

    def test_nothing_is_guessed_for_a_legacy_contract(self) -> None:
        legacy = {"id": "task_legacy", "risk": "high"}
        tasks.task_routing_violations(legacy)
        for field in tasks.ROUTING_FIELDS:
            self.assertNotIn(field, legacy)

    def test_a_misspelled_routing_key_is_not_treated_as_legacy(self) -> None:
        """R192A-2: the whole `routing_` prefix is reserved, so a typo cannot pass as silence."""
        fields = self.fields({"routing_zome": "zone_one"})
        self.assertIn("routing_zome", fields)
        self.assertIn("routing_policy_version", fields)
        self.assertIn("routing_zone", fields)
        self.assertIn("routing_axes", fields)

    def test_a_blank_known_routing_key_is_not_treated_as_legacy(self) -> None:
        """R192A-2: presence is key membership, not a non-empty value."""
        for blank in ("", [], {}):
            with self.subTest(blank=blank):
                fields = self.fields({"routing_zone": blank})
                self.assertIn("routing_zone", fields)
                self.assertIn("routing_axes", fields)

    def test_blank_values_inside_a_full_vector_fail_closed(self) -> None:
        self.assertIn("routing_policy_version", self.fields(task(routing_policy_version="")))
        self.assertIn("routing_axes", self.fields(task(routing_axes="", routing_complexity_band=_DROP)))

    def test_an_unknown_routing_key_beside_a_valid_vector_still_fails_closed(self) -> None:
        self.assertEqual(["routing_zome"], self.fields(task(routing_zome="anything")))

    def test_a_complete_vector_is_accepted(self) -> None:
        self.assertEqual([], tasks.task_routing_violations(task()))

    def test_the_optional_band_may_be_omitted(self) -> None:
        self.assertEqual([], tasks.task_routing_violations(task(routing_complexity_band=_DROP)))

    def test_declaring_one_field_makes_the_core_vector_required(self) -> None:
        partial = {"routing_zone": "zone_one"}
        fields = self.fields(partial)
        self.assertIn("routing_policy_version", fields)
        self.assertIn("routing_axes", fields)

    def test_declaring_one_profile_field_makes_that_group_required(self) -> None:
        fields = self.fields(task(routing_profile=_DROP, routing_reasoning_level=_DROP,
                                  routing_provenance=_DROP, routing_rationale=_DROP))
        self.assertIn("routing_profile", fields)
        self.assertIn("routing_provenance", fields)
        self.assertIn("routing_rationale", fields)

    def test_routing_metadata_without_a_declared_taxonomy_fails_closed(self) -> None:
        with mock.patch.object(config_module, "ROUTING_TAXONOMY", None):
            violations = tasks.task_routing_violations(task())
            self.assertTrue(violations)
            for _field, _value, allowed in violations:
                self.assertIn("routing_taxonomy", allowed[0])


class RoutingMetadataValidationTests(RoutingFixture):
    def test_policy_version_must_match_the_declared_taxonomy(self) -> None:
        self.assertEqual(["routing_policy_version"], self.fields(task(routing_policy_version="2")))

    def test_unknown_zone_reports_the_declared_zones(self) -> None:
        violations = tasks.task_routing_violations(task(routing_zone="zone_nine"))
        self.assertEqual(1, len(violations))
        field, value, allowed = violations[0]
        self.assertEqual(("routing_zone", "zone_nine"), (field, value))
        self.assertEqual(("zone_one", "zone_two"), allowed)

    def test_unknown_axis_level_fails_closed(self) -> None:
        self.assertIn("routing_axes.axis_two",
                      self.fields(task(routing_axes=["axis_one=small", "axis_two=enormous",
                                                     "axis_three=medium"])))

    def test_unknown_axis_name_fails_closed(self) -> None:
        self.assertIn("routing_axes",
                      self.fields(task(routing_axes=["axis_nine=small", "axis_two=large",
                                                     "axis_three=medium"])))

    def test_a_missing_axis_is_never_defaulted(self) -> None:
        fields = self.fields(task(routing_axes=["axis_one=small", "axis_two=large"],
                                  routing_complexity_band=_DROP))
        self.assertIn("routing_axes.axis_three", fields)

    def test_a_duplicated_axis_fails_closed(self) -> None:
        fields = self.fields(task(routing_axes=["axis_one=small", "axis_one=large", "axis_two=large",
                                                "axis_three=medium"]))
        self.assertIn("routing_axes.axis_one", fields)

    def test_a_malformed_axis_entry_fails_closed(self) -> None:
        self.assertIn("routing_axes",
                      self.fields(task(routing_axes=["axis_one", "axis_two=large", "axis_three=medium"])))

    def test_empty_axis_list_reports_the_expected_shape(self) -> None:
        violations = tasks.task_routing_violations(task(routing_axes=[], routing_complexity_band=_DROP))
        _field, _value, allowed = violations[0]
        self.assertIn("axis_one=<small|medium|large>", allowed)

    def test_a_recorded_band_that_disagrees_with_its_vector_fails_closed(self) -> None:
        violations = tasks.task_routing_violations(task(routing_complexity_band="calm"))
        self.assertEqual([("routing_complexity_band", "calm", ("heavy",))], violations)

    def test_the_band_is_not_checked_while_the_vector_is_still_invalid(self) -> None:
        fields = self.fields(task(routing_axes=["axis_one=small"], routing_complexity_band="calm"))
        self.assertNotIn("routing_complexity_band", fields)

    def test_selected_profile_must_belong_to_a_declared_tool(self) -> None:
        fields = self.fields(task(routing_profile_tool="gamma_tool"))
        self.assertIn("routing_profile_tool", fields)
        self.assertIn("routing_profile", fields)

    def test_selected_profile_must_be_declared_by_that_tool(self) -> None:
        violations = tasks.task_routing_violations(task(routing_profile_tool="beta_tool"))
        allowed = dict((field, allowed) for field, _value, allowed in violations)["routing_profile"]
        self.assertEqual(("<this tool declares no execution profile>",), allowed)

    def test_reasoning_level_must_be_supported_by_the_selected_profile(self) -> None:
        violations = tasks.task_routing_violations(task(routing_profile="fast"))
        self.assertEqual([("routing_reasoning_level", "deep", ("light",))], violations)

    def test_provenance_is_a_closed_vocabulary(self) -> None:
        violations = tasks.task_routing_violations(task(routing_provenance="a_hunch"))
        self.assertEqual([("routing_provenance", "a_hunch", ("by_owner", "by_planner"))], violations)

    def test_rationale_must_be_present_and_non_empty(self) -> None:
        self.assertIn("routing_rationale", self.fields(task(routing_rationale="")))
        self.assertIn("routing_rationale", self.fields(task(routing_rationale=_DROP)))

    def test_selected_profile_tool_must_equal_the_canonical_assignment(self) -> None:
        """R192A-4: routing metadata explains an assignment; it never silently reassigns the work."""
        violations = tasks.task_routing_violations(
            task(preferred_tool="beta_tool", routing_profile_tool="alpha_tool"))
        self.assertIn(("routing_profile_tool", "alpha_tool", ("beta_tool",)), violations)

    def test_a_matching_assignment_is_accepted(self) -> None:
        self.assertEqual([], tasks.task_routing_violations(task(preferred_tool="alpha_tool")))

    def test_selected_profile_must_offer_every_zone_required_capability(self) -> None:
        """R192A-5: a zone's required capabilities are a hard filter on the SELECTED profile."""
        violations = tasks.task_routing_violations(
            task(routing_zone="zone_two", routing_profile="fast", routing_reasoning_level="light"))
        self.assertIn(("routing_profile", "alpha_tool/fast", ("<a profile declaring cap_beta>",)),
                      violations)

    def test_a_profile_whose_union_covers_the_zone_is_accepted(self) -> None:
        # cap_beta comes from the profile, cap_alpha from the tool surface: the union satisfies it.
        self.assertEqual([], tasks.task_routing_violations(task(routing_zone="zone_two")))


class ContractIntegrationTests(RoutingFixture):
    def test_invalid_routing_metadata_blocks_the_contract_vocabulary_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                tasks.task_contract_vocabulary_violations(Path(tmp), task(routing_zone="zone_nine"))

    def test_valid_routing_metadata_passes_the_contract_vocabulary_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual([], tasks.task_contract_vocabulary_violations(Path(tmp), task()))

    def test_legacy_contract_still_passes_the_contract_vocabulary_gate(self) -> None:
        legacy = {"id": "task_legacy", "risk": "low", "preferred_tool": "alpha_tool",
                  "review_tool": "beta_tool"}
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual([], tasks.task_contract_vocabulary_violations(Path(tmp), legacy))

    def write_contract(self, directory: Path, *extra_lines: str) -> None:
        (directory / "task.yaml").write_text(
            'id: "task_synthetic"\n'
            'risk: "medium"\n'
            'preferred_tool: "alpha_tool"\n'
            'review_tool: "beta_tool"\n'
            'routing_policy_version: 1\n'
            'routing_zone: "zone_one"\n'
            "routing_axes:\n"
            '  - "axis_one=small"\n'
            '  - "axis_two=large"\n'
            '  - "axis_three=medium"\n'
            + "".join(line + "\n" for line in extra_lines),
            encoding="utf-8",
        )

    def test_versioned_merge_runs_complete_task_evidence_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.write_contract(directory)
            (directory / "receipt.qa.yaml").write_text(
                '{"schema_version":1,"decision":{"status":"accept"}}\n', encoding="utf-8"
            )
            (directory / "task-closeout.yaml").write_text("{}\n", encoding="utf-8")
            args = type("Args", (), {"task_id": "task_synthetic", "approved": True})()
            with (
                mock.patch.object(tasks, "find_task", return_value=(directory, task(risk="low"))),
                mock.patch.object(tasks, "validate_closeout", return_value=[]),
                mock.patch.object(
                    tasks,
                    "repository_task_artifact_violations",
                    return_value=["receipt.qa.yaml: unknown evidence reference 'missing-evidence'"],
                ) as audit,
                self.assertRaises(SystemExit),
            ):
                tasks.cmd_merge(args)
            audit.assert_called_once_with(tasks.constants.ROOT)

    def test_duplicate_top_level_contract_keys_fail_closed(self) -> None:
        """R192A-3: the contract parser keeps the LAST occurrence, so a duplicate could override a
        reviewed value invisibly. Core, optional-band, and selected-profile keys are all covered."""
        cases = {
            "routing_zone": ['routing_zone: "deep_logic_typo"'],
            "routing_complexity_band": ['routing_complexity_band: "heavy"',
                                        'routing_complexity_band: "calm"'],
            "routing_profile": ['routing_profile: "slow"', 'routing_profile: "fast"'],
            "risk": ['risk: "low"'],
        }
        for key, extra in cases.items():
            with self.subTest(duplicate=key), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp)
                self.write_contract(directory, *extra)
                reported = [field for field, _value, _allowed in tasks.duplicate_contract_keys(directory)]
                self.assertIn(key, reported)
                with self.assertRaises(SystemExit):
                    tasks.task_contract_vocabulary_violations(directory, parse_simple_yaml(directory / "task.yaml"))

    def test_a_contract_without_duplicates_reports_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.write_contract(directory)
            self.assertEqual([], tasks.duplicate_contract_keys(directory))

    def test_indented_block_content_is_never_mistaken_for_a_duplicate_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "task.yaml").write_text(
                'id: "task_synthetic"\n'
                "input_contract: >\n"
                "  routing_zone: not a key, just prose\n"
                "  routing_zone: still prose\n"
                'risk: "medium"\n',
                encoding="utf-8",
            )
            self.assertEqual([], tasks.duplicate_contract_keys(directory))

    def test_the_flat_key_shape_round_trips_through_the_task_yaml_subset(self) -> None:
        """The routing vector must survive the real top-level-only contract parser."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "task.yaml"
            path.write_text(
                'id: "task_synthetic"\n'
                'risk: "medium"\n'
                'preferred_tool: "alpha_tool"\n'
                'review_tool: "beta_tool"\n'
                "routing_policy_version: 1\n"
                'routing_zone: "zone_one"\n'
                "routing_axes:\n"
                '  - "axis_one=small"\n'
                '  - "axis_two=large"\n'
                '  - "axis_three=medium"\n'
                'routing_complexity_band: "heavy"\n'
                'routing_profile_tool: "alpha_tool"\n'
                'routing_profile: "slow"\n'
                'routing_reasoning_level: "deep"\n'
                'routing_provenance: "by_owner"\n'
                'routing_rationale: "The vector, not the risk tier, chose this profile."\n',
                encoding="utf-8",
            )
            data = parse_simple_yaml(path)
            self.assertEqual(["axis_one=small", "axis_two=large", "axis_three=medium"],
                             data["routing_axes"])
            self.assertEqual([], tasks.task_routing_violations(data))


class PresentationContractTests(RoutingFixture):
    def presentation(self, **overrides):
        data = {
            "presentation_schema_version": "1",
            "presentation_purpose": "Make task delivery understandable to stakeholders.",
            "presentation_outcome": "The reader explains the observable product change.",
            "presentation_scope": ["Task reading for developers, product owners, and QA."],
            "presentation_out_of_scope": [],
            "presentation_acceptance": [
                "A stakeholder can verify the outcome without repository layout knowledge."
            ],
        }
        data.update(overrides)
        return data

    def fields(self, data) -> list[str]:
        return [
            field
            for field, _value, _allowed
            in tasks.presentation_contract_violations(data)
        ]

    def test_no_presentation_namespace_is_valid_legacy(self) -> None:
        self.assertEqual([], tasks.presentation_contract_violations({"id": "task_legacy"}))

    def test_complete_human_presentation_contract_is_accepted(self) -> None:
        self.assertEqual([], tasks.presentation_contract_violations(self.presentation()))

    def test_partial_and_unknown_namespaces_fail_closed(self) -> None:
        fields = self.fields({"presentation_purpose": "Explain the stakeholder effect."})
        self.assertIn("presentation_schema_version", fields)
        self.assertIn("presentation_outcome", fields)
        self.assertIn("presentation_scope", fields)
        self.assertIn("presentation_acceptance", fields)
        self.assertIn(
            "presentation_scpoe",
            self.fields({**self.presentation(), "presentation_scpoe": ["Typo"]}),
        )

    def test_repository_locators_commands_and_revisions_are_rejected(self) -> None:
        unsafe = (
            "Change scripts/ai_cli.py for this feature.",
            "Inspect app.js:42 before acceptance.",
            "Open C:\\workspace\\project\\task.yaml.",
            "Run git diff HEAD~1.",
            "The accepted revision is deadbeef.",
            "Open file:///workspace/task.yaml.",
            "Run cargo +nightly test.",
            "Run git -C repo status.",
            "Run python -c print(1).",
            "Execute powershell Get-Date.",
            "Run ai audit-framework.",
        )
        for value in unsafe:
            with self.subTest(value=value):
                self.assertIn(
                    "presentation_purpose",
                    self.fields(self.presentation(presentation_purpose=value)),
                )

    def test_reader_language_false_positives_remain_allowed(self) -> None:
        safe = (
            "Support image/png evidence in the reader.",
            "Explain the input/output boundary for Windows/Linux users.",
            "Keep render::Graph and task_199 understandable.",
            "Use Node.js terminology where it describes the product.",
            "A product link may use https://example.com/docs for reader navigation.",
            "Run unit tests before stakeholder review.",
            "The theme uses CSS color #123456.",
            "The theme uses CSS color #12345678.",
            "UI /UX behavior remains consistent.",
            "The /settings route remains available.",
            "Correlation ID 123e4567-e89b-12d3-a456-426614174000 remains visible.",
            "cargo verification remains understandable.",
            "python integration stays optional.",
        )
        for value in safe:
            with self.subTest(value=value):
                self.assertNotIn(
                    "presentation_purpose",
                    self.fields(self.presentation(presentation_purpose=value)),
                )

    def test_empty_optional_out_of_scope_is_valid(self) -> None:
        self.assertEqual(
            [],
            tasks.presentation_contract_violations(
                self.presentation(presentation_out_of_scope=[])
            ),
        )

    def test_scaffold_keeps_reader_semantics_unavailable_until_planner_authors_them(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(tasks.constants, "AI", Path(tmp) / ".ai"):
                task_dir = tasks.create_task(
                    title="Plan reader semantics",
                    feature="task reader",
                    risk="medium",
                    preferred_tool="alpha_tool",
                    review_tool="beta_tool",
                    isolation_strategy="readonly-research",
                    brief="brief",
                    context="context",
                )
                data = parse_simple_yaml(task_dir / "task.yaml")
        self.assertEqual([], tasks.presentation_contract_violations(data))
        self.assertFalse(any(
            str(key).startswith("presentation_")
            for key in data
        ))
        serialized = json.dumps(data, sort_keys=True)
        self.assertNotIn("Coordinate task reader as an explicit stakeholder task.", serialized)
        self.assertNotIn("The task reader feature or workflow.", serialized)
        self.assertNotIn("Stakeholders can verify the agreed task reader outcome", serialized)

    def test_scaffold_rejects_a_locator_bearing_feature_without_leaving_a_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_root = Path(tmp) / ".ai"
            with (
                mock.patch.object(tasks.constants, "AI", task_root),
                self.assertRaises(SystemExit),
            ):
                tasks.create_task(
                    title="Unsafe scaffold",
                    feature="scripts/ai_cli.py",
                    risk="medium",
                    preferred_tool="alpha_tool",
                    review_tool="beta_tool",
                    isolation_strategy="patch",
                    brief="brief",
                    context="context",
                )
            self.assertFalse((task_root / "tasks" / "queue").exists())


class RepositoryContractTests(unittest.TestCase):
    def test_no_live_contract_declares_partial_routing_metadata(self) -> None:
        """Every existing contract is either fully valid or legacy-silent under the real taxonomy."""
        config_module.initialize_runtime_config()
        for state in ("queue", "active"):
            for task_file in sorted((REPO_ROOT / ".ai" / "tasks" / state).glob("*/task.yaml")):
                data = parse_simple_yaml(task_file)
                with self.subTest(task=task_file.parent.name):
                    self.assertEqual([], tasks.task_routing_violations(data))


if __name__ == "__main__":
    unittest.main()
