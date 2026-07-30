"""task_192a: routing taxonomy and tool capability/profile schema validation.

Every fixture below uses SYNTHETIC vocabulary (``zone_one``, ``axis_two``, ``cap_alpha``, ``light``)
rather than the repository's real zones, axes, capabilities, or model names. A validator that only
accepts the real vocabulary would fail these tests, which is the point: the schema is generic and
the vocabulary is data.
"""
from __future__ import annotations

import ast
import copy
import re
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scripts.ai_plane.config as config_module  # noqa: E402

WHERE = ".ai/config.yaml"

BASE_TAXONOMY = {
    "version": 1,
    "axis_levels": ["small", "medium", "large"],
    "provenance": ["by_owner", "by_planner"],
    "capability_tags": {"cap_alpha": "Does alpha.", "cap_beta": "Does beta."},
    "zones": {
        "zone_one": {"summary": "First zone.", "required_capabilities": ["cap_alpha"]},
        "zone_two": {"summary": "Second zone.", "required_capabilities": ["cap_alpha", "cap_beta"]},
    },
    "axes": {
        "axis_one": {"summary": "First axis.", "small": "s", "medium": "m", "large": "l"},
        "axis_two": {"summary": "Second axis.", "small": "s", "medium": "m", "large": "l"},
        "axis_three": {"summary": "Third axis.", "small": "s", "medium": "m", "large": "l"},
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
    "catalog_provenance": ["api_account", "desktop_app", "bundled", "manual", "unknown"],
    "resolution_selectors": ["app_default", "latest_compatible"],
    "profile_preferences": {
        "complexity_reasoning": {"calm": "light", "busy": "deep", "heavy": "deep"},
        "tie_breakers": ["preference_rank", "tool_order", "profile_order"],
    },
}


def taxonomy(**overrides):
    data = copy.deepcopy(BASE_TAXONOMY)
    data.update(overrides)
    return {"routing_taxonomy": data}


def mutate(path: list[str], value):
    """A copy of the base taxonomy with one nested key replaced (or deleted when value is None)."""
    data = copy.deepcopy(BASE_TAXONOMY)
    cursor = data
    for key in path[:-1]:
        cursor = cursor[key]
    if value is _DELETE:
        del cursor[path[-1]]
    else:
        cursor[path[-1]] = value
    return {"routing_taxonomy": data}


class _Delete:
    pass


_DELETE = _Delete()


class RoutingTaxonomySchemaTests(unittest.TestCase):
    def validate(self, data):
        return config_module.validate_routing_taxonomy(data, WHERE)

    def assert_rejected(self, data, *expected_fragments: str) -> None:
        with self.assertRaises(config_module.ConfigError) as caught:
            self.validate(data)
        message = str(caught.exception)
        for fragment in expected_fragments:
            self.assertIn(fragment, message)

    def test_valid_synthetic_taxonomy_is_accepted(self) -> None:
        result = self.validate(taxonomy())
        self.assertEqual(BASE_TAXONOMY["zones"], result["zones"])

    def test_absent_block_is_not_an_error_and_substitutes_no_default(self) -> None:
        self.assertIsNone(self.validate({"tools": {}}))

    def test_unknown_and_missing_top_level_fields_fail_closed(self) -> None:
        self.assert_rejected(taxonomy(extra_field="x"), "unknown field(s): extra_field")
        self.assert_rejected(mutate(["zones"], _DELETE), "missing field(s): zones")

    def test_version_must_be_a_positive_integer(self) -> None:
        for bad in ("1", 0, -1, 1.5, True):
            with self.subTest(version=bad):
                self.assert_rejected(mutate(["version"], bad), "version must be a positive integer")

    def test_duplicate_axis_level_fails_closed(self) -> None:
        self.assert_rejected(mutate(["axis_levels"], ["small", "small", "large"]),
                             "declares duplicate axis level names: small")

    def test_capability_tag_name_and_description_are_validated(self) -> None:
        self.assert_rejected(mutate(["capability_tags"], {"Cap Alpha": "x"}), "lowercase identifier")
        self.assert_rejected(mutate(["capability_tags", "cap_alpha"], "  "), "must be a non-empty string")
        self.assert_rejected(mutate(["capability_tags"], {}), "non-empty mapping of capability tag")

    def test_zone_required_capability_must_be_declared(self) -> None:
        self.assert_rejected(mutate(["zones", "zone_one", "required_capabilities"], ["cap_missing"]),
                             "names unknown capability tags: cap_missing", "declared capability tags:")

    def test_zone_capabilities_must_be_unique_and_non_empty(self) -> None:
        self.assert_rejected(mutate(["zones", "zone_one", "required_capabilities"], ["cap_alpha", "cap_alpha"]),
                             "declares duplicate capability tags: cap_alpha")
        self.assert_rejected(mutate(["zones", "zone_one", "required_capabilities"], []),
                             "must be a non-empty list of capability tags")

    def test_every_declared_axis_level_needs_a_meaning_and_no_extra_key(self) -> None:
        self.assert_rejected(mutate(["axes", "axis_one", "medium"], _DELETE), "missing field(s): medium")
        self.assert_rejected(mutate(["axes", "axis_one", "enormous"], "x"), "unknown field(s): enormous")

    def test_band_rules_are_validated_against_the_declared_vocabulary(self) -> None:
        self.assert_rejected(mutate(["complexity_bands", "default"], "nowhere"),
                             "complexity_bands.default must name a declared band")
        self.assert_rejected(mutate(["complexity_bands", "rules", "any_large", "band"], "nowhere"),
                             "rules.any_large.band must name a declared band")
        self.assert_rejected(mutate(["complexity_bands", "rules", "any_large", "at_or_above"], "enormous"),
                             "at_or_above must name a declared axis level")
        self.assert_rejected(
            mutate(["complexity_bands", "rules", "focus_axis_large", "restrict_to_axes"], ["axis_nine"]),
            "names unknown axes: axis_nine")

    def test_a_rule_that_can_never_match_is_a_config_defect(self) -> None:
        self.assert_rejected(mutate(["complexity_bands", "rules", "focus_axis_large", "minimum_count"], 2),
                             "exceeds its 1 eligible axis/axes, so the rule can never match")
        self.assert_rejected(mutate(["complexity_bands", "rules", "two_large", "minimum_count"], 4),
                             "exceeds its 3 eligible axis/axes")

    def test_minimum_count_must_be_a_positive_integer(self) -> None:
        for bad in (0, -1, "2", 1.0):
            with self.subTest(minimum_count=bad):
                self.assert_rejected(mutate(["complexity_bands", "rules", "any_large", "minimum_count"], bad),
                                     "minimum_count must be a positive integer")

    def test_reasoning_threshold_and_review_floor_are_closed(self) -> None:
        self.assert_rejected(mutate(["reasoning_escalation_threshold"], "colossal"),
                             "reasoning_escalation_threshold must name a declared reasoning level")
        self.assert_rejected(mutate(["review_depth_floor"], {"critical": "deep"}),
                             "must name a declared risk tier", "low, medium, high")
        self.assert_rejected(mutate(["review_depth_floor"], {"high": "colossal"}),
                             "review_depth_floor.high must name a declared reasoning level")

    def test_risk_tiers_remain_exactly_the_canonical_three(self) -> None:
        self.assertEqual(("low", "medium", "high"), config_module.RISKS)


class ToolRoutingCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.taxonomy = config_module.validate_routing_taxonomy(taxonomy(), WHERE)

    def validate(self, entry, taxonomy_value="default", *, inject_family=True):
        if inject_family and "family" not in entry:
            entry = {"family": "fourth_family", **entry}
        config_module._validate_tool_routing(
            f"{WHERE} tools.fourth", entry,
            self.taxonomy if taxonomy_value == "default" else taxonomy_value,
        )

    def assert_rejected(self, entry, *fragments: str, taxonomy_value="default",
                        inject_family=True) -> None:
        with self.assertRaises(config_module.ConfigError) as caught:
            self.validate(entry, taxonomy_value, inject_family=inject_family)
        for fragment in fragments:
            self.assertIn(fragment, str(caught.exception))

    def profile(self, **overrides):
        value = {
            "reasoning_levels": ["light"],
            "complexity_bands": ["calm"],
            "preference_rank": 1,
        }
        value.update(overrides)
        return value

    def test_a_routed_tool_must_declare_a_family(self) -> None:
        self.assert_rejected({"profiles": {"p": self.profile()}}, "but no 'family'",
                             inject_family=False)

    def test_valid_logical_profile_is_accepted(self) -> None:
        self.validate({
            "capabilities": ["cap_alpha"],
            "profiles": {
                "balanced": self.profile(
                    reasoning_levels=["light", "deep"],
                    complexity_bands=["calm", "busy"],
                    capabilities=["cap_beta"],
                    requires_rationale=True,
                ),
            },
        })

    def test_unrouted_tool_still_needs_no_family(self) -> None:
        self.validate({"role": "r", "notes": ["n"]}, inject_family=False)

    def test_family_must_be_an_identifier(self) -> None:
        self.assert_rejected({"family": "Fourth Family"},
                             "family must be a lowercase identifier")

    def test_unknown_and_duplicate_capabilities_fail_closed(self) -> None:
        self.assert_rejected({"capabilities": ["cap_missing"]},
                             "names unknown capability tags: cap_missing")
        self.assert_rejected({"capabilities": ["cap_alpha", "cap_alpha"]},
                             "declares duplicate capability tags: cap_alpha")

    def test_profile_reasoning_levels_are_unique_and_declared(self) -> None:
        self.assert_rejected({"profiles": {"p": self.profile(
            reasoning_levels=["light", "light"]
        )}}, "declares duplicate reasoning levels: light")
        self.assert_rejected({"profiles": {"p": self.profile(
            reasoning_levels=["missing"]
        )}}, "names unknown reasoning levels: missing")

    def test_profile_rationale_flag_must_be_boolean(self) -> None:
        self.assert_rejected({"profiles": {"p": self.profile(
            requires_rationale="yes"
        )}}, "requires_rationale must be a boolean")

    def test_exact_model_inventory_is_not_permitted(self) -> None:
        self.assert_rejected({"profiles": {"p": self.profile(model="Monthly Model")}},
                             "unknown field(s): model")

    def test_profile_fields_are_exact_and_closed(self) -> None:
        self.assert_rejected({"profiles": {"p": {"reasoning_levels": ["light"]}}},
                             "missing field(s): complexity_bands, preference_rank")
        self.assert_rejected({"profiles": {"p": self.profile(complexity_bands=["missing"])}},
                             "unknown complexity bands")
        self.assert_rejected({"profiles": {"p": self.profile(preference_rank=-1)}},
                             "preference_rank must be a non-negative integer")
        self.assert_rejected({"profiles": {"p": self.profile(capabilities=["missing"])}},
                             "unknown capability tags")

    def test_profile_name_and_reasoning_are_validated(self) -> None:
        self.assert_rejected({"profiles": {"Big Profile": self.profile()}},
                             "lowercase identifier")
        self.assert_rejected({"profiles": {"p": self.profile(reasoning_levels=["missing"])}},
                             "unknown reasoning levels")

    def test_capabilities_or_profiles_without_taxonomy_fail_closed(self) -> None:
        self.assert_rejected({"capabilities": ["cap_alpha"]}, config_module.ROUTING_UNDECLARED,
                             taxonomy_value=None)
        self.assert_rejected({"profiles": {"p": self.profile()}}, config_module.ROUTING_UNDECLARED,
                             taxonomy_value=None)


class RepositoryCatalogTests(unittest.TestCase):
    """The repository's own declarations must satisfy the schema they introduce."""

    def setUp(self) -> None:
        self.registry = config_module.load_tool_registry(REPO_ROOT / ".ai" / "config.yaml")
        self.taxonomy = config_module.validate_routing_taxonomy(
            config_module.parse_config_yaml(REPO_ROOT / ".ai" / "config.yaml"), WHERE)

    def test_repository_declares_the_seven_zones_and_six_axes(self) -> None:
        self.assertEqual(7, len(self.taxonomy["zones"]))
        self.assertEqual(6, len(self.taxonomy["axes"]))
        self.assertEqual(["low", "moderate", "high"], self.taxonomy["axis_levels"])
        self.assertEqual(["explicit_owner", "planner", "router"], self.taxonomy["provenance"])

    def test_every_declared_capability_is_reachable_from_some_zone_or_tool(self) -> None:
        declared = set(self.taxonomy["capability_tags"])
        required = {tag for zone in self.taxonomy["zones"].values() for tag in zone["required_capabilities"]}
        self.assertEqual(declared, required, "every capability tag must be required by at least one zone")

    def test_no_tool_or_profile_claims_an_undeclared_capability(self) -> None:
        declared = set(self.taxonomy["capability_tags"])
        for name, entry in self.registry.items():
            self.assertLessEqual(set(entry.get("capabilities", ())), declared, name)
            for profile, spec in entry.get("profiles", {}).items():
                self.assertLessEqual(set(spec.get("capabilities", ())), declared, f"{name}.{profile}")

    def test_every_tool_declares_a_family_so_independence_stays_checkable(self) -> None:
        for name, entry in self.registry.items():
            self.assertIn("family", entry, f"tool {name} declares no family")

    def test_at_least_two_families_can_perform_independent_review(self) -> None:
        reviewers = {
            entry["family"]
            for entry in self.registry.values()
            for spec in entry.get("profiles", {}).values()
            if "independent_review" in set(entry.get("capabilities", ())) | set(spec.get("capabilities", ()))
        }
        self.assertGreaterEqual(len(reviewers), 2, "cross-family independent review must stay possible")

    def test_duplicate_capability_tag_keys_fail_closed_in_the_yaml_subset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text("routing_taxonomy:\n  capability_tags:\n    cap_a: \"x\"\n    cap_a: \"y\"\n",
                            encoding="utf-8")
            with self.assertRaises(config_module.ConfigError) as caught:
                config_module.parse_config_yaml(path)
            self.assertIn("duplicate key", str(caught.exception))


class RoutingLawConsistencyTests(unittest.TestCase):
    """One noncontradictory rule: risk governs impact gates, task shape governs the model profile."""

    SUPERSEDED = "only reasoning-tier selector"
    LAW_DOCUMENTS = (
        ".ai/config.yaml",
        ".ai/workflows/planning.md",
        ".ai/workflows/task-template.md",
        ".ai/agents/planner.md",
        ".ai/rules/task-contracts.md",
    )
    SUPERSESSION_RECORDS = (".ai/project/routing-taxonomy.md", ".ai/project/principles.md")

    def read(self, rel_path: str) -> str:
        """Whitespace-normalized so a line wrap cannot hide a phrase from the scan."""
        return " ".join((REPO_ROOT / rel_path).read_text(encoding="utf-8").split())

    def test_the_superseded_rule_survives_only_as_a_quoted_supersession(self) -> None:
        for rel_path in self.LAW_DOCUMENTS + self.SUPERSESSION_RECORDS:
            text = self.read(rel_path)
            for match in re.finditer(re.escape(self.SUPERSEDED), text):
                window = text[max(0, match.start() - 300):match.end() + 300].lower()
                with self.subTest(document=rel_path, at=match.start()):
                    self.assertIn("supersede", window,
                                  "the superseded rule may appear only inside a supersession clause")

    def test_the_supersession_is_recorded_rather_than_silently_dropped(self) -> None:
        for rel_path in self.SUPERSESSION_RECORDS:
            with self.subTest(document=rel_path):
                text = self.read(rel_path)
                self.assertIn(self.SUPERSEDED, text)
                self.assertIn("supersede", text.lower())

    def test_the_reconciled_law_is_stated_in_every_law_document(self) -> None:
        for rel_path in self.LAW_DOCUMENTS:
            with self.subTest(document=rel_path):
                text = self.read(rel_path).lower()
                self.assertIn("complexity band", text)
                self.assertIn("risk", text)

    # R192A-8: the round-1 guard was risk-tier shaped, so it missed a sentence that severed the
    # profile from TASK SHAPE instead. This detects the construction rather than one wording: a
    # sentence that names a profile selector AND separates it from a task-shape term. Separating the
    # profile from RISK is the law and must not be flagged.
    PROFILE_SELECTOR = re.compile(r"model|reasoning|execution profile|executor profile", re.IGNORECASE)
    SEVERED_FROM_SHAPE = re.compile(
        r"(separately from|independent(?:ly)? of|regardless of|apart from|without regard to)"
        r"\s+(?:the\s+|its\s+|a\s+)?[^.;]{0,40}?"
        r"(task shape|task zone|complexity band|complexity|\bzone\b)",
        re.IGNORECASE)
    REVIEWED_TASK_SHAPE_CONTRADICTION = "Model and reasoning depth are chosen separately from task shape."
    SHAPE_CONTRADICTION_VARIANTS = (
        REVIEWED_TASK_SHAPE_CONTRADICTION,
        "Reasoning depth is chosen independently of the derived complexity band.",
        "The execution profile is selected regardless of task zone.",
        "Model selection happens apart from the task's complexity.",
    )
    # The law itself, which must never be flagged.
    SHAPE_CONTRADICTION_NON_OFFENDERS = (
        "Task shape and the derived complexity band select the model and reasoning depth "
        "independently of risk.",
        "Risk selects impact evidence, approval, and review gates.",
    )

    def severs_profile_from_task_shape(self, sentence: str) -> bool:
        return bool(self.PROFILE_SELECTOR.search(sentence) and self.SEVERED_FROM_SHAPE.search(sentence))

    def test_the_reviewed_contradiction_and_its_paraphrases_bite_the_guard(self) -> None:
        for sentence in self.SHAPE_CONTRADICTION_VARIANTS:
            with self.subTest(sentence=sentence):
                self.assertTrue(self.severs_profile_from_task_shape(sentence))
        for sentence in self.SHAPE_CONTRADICTION_NON_OFFENDERS:
            with self.subTest(law=sentence):
                self.assertFalse(self.severs_profile_from_task_shape(sentence))

    def test_no_law_document_severs_the_profile_from_task_shape(self) -> None:
        offenders: list[str] = []
        for rel_path in self.LAW_DOCUMENTS:
            for sentence in re.split(r"(?<=[.;])\s+", self.read(rel_path)):
                if self.severs_profile_from_task_shape(sentence):
                    offenders.append(f"{rel_path}: {sentence.strip()[:160]}")
        self.assertEqual([], offenders,
                         "task shape and the derived complexity band must select the executor profile")

    # A risk tier may still set a REVIEW-depth floor, so a sentence that also talks about review is
    # legal law; an EXECUTOR profile default conditioned on a risk tier is exactly what this task
    # removed (R192A-1).
    RISK_TIER = re.compile(r"\b(low|medium|high)[- ]?(and [a-z]+-)?risk\b", re.IGNORECASE)
    EXECUTOR_PROFILE = re.compile(
        r"executor tier|executor model|executor profile|reasoning tier|reasoning profile|"
        r"model profile|model tier|least costly profile|balanced .{0,20}tier",
        re.IGNORECASE)
    REVIEW_OR_GATE = re.compile(r"review|approval|gate|evidence|supersede", re.IGNORECASE)

    def test_no_law_document_conditions_an_executor_profile_on_a_risk_tier(self) -> None:
        offenders: list[str] = []
        for rel_path in self.LAW_DOCUMENTS:
            for sentence in re.split(r"(?<=[.;])\s+", self.read(rel_path)):
                if (self.RISK_TIER.search(sentence) and self.EXECUTOR_PROFILE.search(sentence)
                        and not self.REVIEW_OR_GATE.search(sentence)):
                    offenders.append(f"{rel_path}: {sentence.strip()[:160]}")
        self.assertEqual([], offenders,
                         "risk may select gates and a review-depth floor, never an executor profile")

    def test_risk_gates_and_independence_language_are_preserved(self) -> None:
        config_text = self.read(".ai/config.yaml")
        for preserved in (
            "High-risk tasks require independent review and explicit owner merge approval.",
            "Canonical risk remains exactly low, medium, or high; critical impact is a routing "
            "annotation mapped to high, never a fourth risk token.",
            "Cross-family review is preferred for independence.",
        ):
            self.assertIn(preserved, config_text)

    def test_high_risk_gate_requirements_are_unchanged(self) -> None:
        gates = config_module.parse_config_yaml(REPO_ROOT / ".ai" / "config.yaml")["risk_gates"]
        self.assertEqual(
            ["research_digest", "implementation_receipt", "tests", "independent_review",
             "explicit_merge_approval"],
            gates["high"]["requires"],
        )


class GenericRoutingCodeTests(unittest.TestCase):
    """AC5: routing vocabulary stays config data. No Python conditional or dispatch table may branch
    on a declared zone, axis, capability, family, profile, or model identity."""

    # `"review"` is a prompt-purpose literal (prompts.py) and a CLI subcommand name (ai_cli.py),
    # both predating this taxonomy and unrelated to the `review` zone. These are the only pre-existing
    # collisions; nothing else is exempt.
    EXEMPT = {("prompts.py", "review"), ("ai_cli.py", "review")}

    def routing_names(self) -> set[str]:
        parsed = config_module.parse_config_yaml(REPO_ROOT / ".ai" / "config.yaml")
        tax = parsed["routing_taxonomy"]
        names = set(tax["zones"]) | set(tax["axes"]) | set(tax["capability_tags"])
        for tool, entry in parsed["tools"].items():
            names.add(tool)
            if "family" in entry:
                names.add(entry["family"])
            for profile, spec in entry.get("profiles", {}).items():
                names.add(profile)
        return names

    def literals(self, node: ast.AST):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value
        elif isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            for item in node.elts:
                yield from self.literals(item)

    def test_no_module_branches_on_a_routing_vocabulary_name(self) -> None:
        forbidden = self.routing_names()
        offenders: list[str] = []
        sources = sorted((REPO_ROOT / "scripts" / "ai_plane").glob("*.py"))
        sources.append(REPO_ROOT / "scripts" / "ai_cli.py")
        for source in sources:
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            for node in ast.walk(tree):
                found: list[str] = []
                if isinstance(node, ast.Compare):
                    for comparator in node.comparators:
                        found.extend(self.literals(comparator))
                elif isinstance(node, ast.Dict):
                    for key in node.keys:
                        if key is not None:
                            found.extend(self.literals(key))
                for value in found:
                    if value in forbidden and (source.name, value) not in self.EXEMPT:
                        offenders.append(f"{source.name}:{node.lineno} branches on {value!r}")
        self.assertEqual([], offenders, "routing vocabulary must stay in config, not in Python")


if __name__ == "__main__":
    unittest.main()
