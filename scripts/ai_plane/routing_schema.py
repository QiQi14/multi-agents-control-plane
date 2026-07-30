"""Validation for the declarative routing taxonomy and logical tool profiles."""

from __future__ import annotations

import re
from typing import Any


class RoutingSchemaError(ValueError):
    """A routing taxonomy or logical-profile declaration is invalid."""


TAXONOMY_FIELDS = (
    "version",
    "axis_levels",
    "provenance",
    "capability_tags",
    "zones",
    "axes",
    "complexity_bands",
    "reasoning_levels",
    "reasoning_escalation_threshold",
    "review_depth_floor",
    "catalog_provenance",
    "resolution_selectors",
    "profile_preferences",
)


def _fields(
    where: str,
    value: Any,
    required: tuple[str, ...],
    optional: tuple[str, ...] = (),
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RoutingSchemaError(f"{where} must be a mapping")
    unknown = sorted(set(value) - set(required) - set(optional))
    missing = [field for field in required if field not in value]
    if missing:
        raise RoutingSchemaError(f"{where} is missing field(s): {', '.join(missing)}")
    if unknown:
        raise RoutingSchemaError(f"{where} has unknown field(s): {', '.join(unknown)}")
    return value


def _text(where: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RoutingSchemaError(f"{where} must be a non-empty string")
    return value


def _identifier(where: str, value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", value):
        raise RoutingSchemaError(
            f"{where} must be a lowercase identifier matching [a-z][a-z0-9_-]*; got {value!r}"
        )
    return value


def _member(where: str, value: Any, allowed: Any, label: str) -> str:
    if value not in allowed:
        raise RoutingSchemaError(
            f"{where} must name a declared {label}; got {value!r}; declared: {', '.join(allowed)}"
        )
    return value


def _closed_list(where: str, value: Any, allowed: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item for item in value
    ):
        raise RoutingSchemaError(f"{where} must be a non-empty list of {label}")
    if duplicates := sorted({item for item in value if value.count(item) > 1}):
        raise RoutingSchemaError(f"{where} declares duplicate {label}: {', '.join(duplicates)}")
    if allowed is not None:
        unknown = [item for item in value if item not in allowed]
        if unknown:
            raise RoutingSchemaError(
                f"{where} names unknown {label}: {', '.join(unknown)}; "
                f"declared {label}: {', '.join(allowed)}"
            )
    return tuple(value)


def _named_mapping(where: str, value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        raise RoutingSchemaError(f"{where} must be a non-empty mapping of {label}")
    for name in value:
        _identifier(f"{where} {label} name", name)
    return value


def validate_routing_taxonomy(
    data: dict[str, Any],
    where: str,
    risks: tuple[str, ...],
) -> dict[str, Any] | None:
    """Validate the optional canonical routing vocabulary."""
    if "routing_taxonomy" not in data:
        return None
    root = f"{where} routing_taxonomy"
    taxonomy = _fields(root, data["routing_taxonomy"], TAXONOMY_FIELDS)
    if type(taxonomy["version"]) is not int or taxonomy["version"] < 1:
        raise RoutingSchemaError(
            f"{root}.version must be a positive integer; got {taxonomy['version']!r}"
        )
    levels = _closed_list(f"{root}.axis_levels", taxonomy["axis_levels"], None, "axis level names")
    _closed_list(f"{root}.provenance", taxonomy["provenance"], None, "provenance values")
    reasoning = _closed_list(
        f"{root}.reasoning_levels", taxonomy["reasoning_levels"], None, "reasoning levels"
    )
    _closed_list(
        f"{root}.catalog_provenance",
        taxonomy["catalog_provenance"],
        None,
        "catalog provenance values",
    )
    _closed_list(
        f"{root}.resolution_selectors",
        taxonomy["resolution_selectors"],
        None,
        "resolution selectors",
    )
    tags = _named_mapping(f"{root}.capability_tags", taxonomy["capability_tags"], "capability tag")
    for tag, description in tags.items():
        _text(f"{root}.capability_tags.{tag}", description)
    for zone, entry in _named_mapping(f"{root}.zones", taxonomy["zones"], "zone").items():
        entry = _fields(f"{root}.zones.{zone}", entry, ("summary", "required_capabilities"))
        _text(f"{root}.zones.{zone}.summary", entry["summary"])
        _closed_list(
            f"{root}.zones.{zone}.required_capabilities",
            entry["required_capabilities"],
            tags,
            "capability tags",
        )
    for axis, entry in _named_mapping(f"{root}.axes", taxonomy["axes"], "axis").items():
        entry = _fields(f"{root}.axes.{axis}", entry, ("summary", *levels))
        for field in ("summary", *levels):
            _text(f"{root}.axes.{axis}.{field}", entry[field])
    _validate_bands(
        f"{root}.complexity_bands",
        taxonomy["complexity_bands"],
        levels,
        tuple(taxonomy["axes"]),
    )
    _member(
        f"{root}.reasoning_escalation_threshold",
        taxonomy["reasoning_escalation_threshold"],
        reasoning,
        "reasoning level",
    )
    floors = taxonomy["review_depth_floor"]
    if not isinstance(floors, dict) or not floors:
        raise RoutingSchemaError(
            f"{root}.review_depth_floor must be a non-empty mapping of risk tier to reasoning level"
        )
    for tier, level in floors.items():
        _member(f"{root}.review_depth_floor risk tier", tier, risks, "risk tier")
        _member(f"{root}.review_depth_floor.{tier}", level, reasoning, "reasoning level")
    preferences = _fields(
        f"{root}.profile_preferences",
        taxonomy["profile_preferences"],
        ("complexity_reasoning", "tie_breakers"),
    )
    band_names = tuple(taxonomy["complexity_bands"]["order"])
    mapping = preferences["complexity_reasoning"]
    if not isinstance(mapping, dict) or set(mapping) != set(band_names):
        raise RoutingSchemaError(
            f"{root}.profile_preferences.complexity_reasoning must map every complexity band"
        )
    for band, level in mapping.items():
        _member(
            f"{root}.profile_preferences.complexity_reasoning.{band}",
            level,
            reasoning,
            "reasoning level",
        )
    _closed_list(
        f"{root}.profile_preferences.tie_breakers",
        preferences["tie_breakers"],
        ("preference_rank", "tool_order", "profile_order"),
        "tie breakers",
    )
    return taxonomy


def _validate_bands(
    where: str,
    bands: Any,
    levels: tuple[str, ...],
    axes: tuple[str, ...],
) -> None:
    bands = _fields(where, bands, ("order", "default", "rules"))
    order = _closed_list(f"{where}.order", bands["order"], None, "band names")
    _member(f"{where}.default", bands["default"], order, "band")
    for name, rule in _named_mapping(f"{where}.rules", bands["rules"], "band rule").items():
        rule_where = f"{where}.rules.{name}"
        rule = _fields(
            rule_where,
            rule,
            ("band", "at_or_above", "minimum_count"),
            ("restrict_to_axes",),
        )
        _member(f"{rule_where}.band", rule["band"], order, "band")
        _member(f"{rule_where}.at_or_above", rule["at_or_above"], levels, "axis level")
        eligible = (
            _closed_list(
                f"{rule_where}.restrict_to_axes",
                rule["restrict_to_axes"],
                axes,
                "axes",
            )
            if "restrict_to_axes" in rule
            else axes
        )
        count = rule["minimum_count"]
        if type(count) is not int or count < 1:
            raise RoutingSchemaError(
                f"{rule_where}.minimum_count must be a positive integer; got {count!r}"
            )
        if count > len(eligible):
            raise RoutingSchemaError(
                f"{rule_where}.minimum_count {count} exceeds its {len(eligible)} eligible "
                "axis/axes, so the rule can never match"
            )


def validate_tool_routing(
    where: str,
    entry: dict[str, Any],
    taxonomy: dict[str, Any] | None,
    undeclared_code: str,
    guidance: str,
) -> None:
    """Validate one tool's optional logical routing catalog."""
    declared = [field for field in ("capabilities", "profiles") if field in entry]
    if declared and taxonomy is None:
        raise RoutingSchemaError(
            f"{where} declares {', '.join(declared)} but no routing vocabulary exists "
            f"({undeclared_code}): {guidance}"
        )
    if "family" in entry:
        _identifier(f"{where}.family", entry["family"])
    if taxonomy is None:
        return
    if declared and "family" not in entry:
        raise RoutingSchemaError(
            f"{where} declares {', '.join(declared)} but no 'family'; cross-family "
            "review independence cannot be checked without it"
        )
    tags = taxonomy["capability_tags"]
    if "capabilities" in entry:
        _closed_list(f"{where}.capabilities", entry["capabilities"], tags, "capability tags")
    if "profiles" not in entry:
        return
    bands = tuple(taxonomy["complexity_bands"]["order"])
    for name, profile in _named_mapping(
        f"{where}.profiles", entry["profiles"], "logical profile"
    ).items():
        at = f"{where}.profiles.{name}"
        profile = _fields(
            at,
            profile,
            ("reasoning_levels", "complexity_bands", "preference_rank"),
            ("capabilities", "requires_rationale"),
        )
        _closed_list(
            f"{at}.reasoning_levels",
            profile["reasoning_levels"],
            taxonomy["reasoning_levels"],
            "reasoning levels",
        )
        _closed_list(
            f"{at}.complexity_bands",
            profile["complexity_bands"],
            bands,
            "complexity bands",
        )
        if type(profile["preference_rank"]) is not int or profile["preference_rank"] < 0:
            raise RoutingSchemaError(f"{at}.preference_rank must be a non-negative integer")
        if "capabilities" in profile:
            _closed_list(
                f"{at}.capabilities",
                profile["capabilities"],
                tags,
                "capability tags",
            )
        if "requires_rationale" in profile and not isinstance(
            profile["requires_rationale"], bool
        ):
            raise RoutingSchemaError(f"{at}.requires_rationale must be a boolean")
