from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import scripts.ai_plane.config as config_module
import scripts.ai_plane.tasks as tasks
from scripts.ai_plane.routing_profile import ToolProfile, ToolProfileError, load_profile


ROUTE_SCHEMA_VERSION = 1
CONFIGURE_GUIDANCE = "python scripts/ai_cli.py tools configure"


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def catalog_freshness(catalog: dict[str, Any]) -> dict[str, Any]:
    """Compute freshness only from normalized inputs; never read wall-clock time."""
    evaluation = _parse_time(catalog.get("evaluation_time"))
    basis_name = "fetched_at" if catalog.get("fetched_at") else "observed_at"
    basis = _parse_time(catalog.get(basis_name))
    ttl = catalog.get("ttl_seconds")
    if evaluation is None or basis is None or type(ttl) is not int or ttl < 0:
        return {
            "evaluation_time": catalog.get("evaluation_time"),
            "ttl_seconds": ttl,
            "basis": basis_name,
            "age_seconds": None,
            "stale": None,
            "reason": "freshness is unknown because normalized time or TTL evidence is incomplete",
        }
    age = int((evaluation - basis).total_seconds())
    stale = age < 0 or age > ttl
    return {
        "evaluation_time": catalog["evaluation_time"],
        "ttl_seconds": ttl,
        "basis": basis_name,
        "age_seconds": age,
        "stale": stale,
        "reason": (
            f"{basis_name} age {age}s exceeds TTL {ttl}s"
            if stale else f"{basis_name} age {age}s is within TTL {ttl}s"
        ),
    }


def _routing_vector(data: dict[str, Any], taxonomy: dict[str, Any]) -> tuple[dict[str, str], list[dict[str, Any]]]:
    keys = tasks.routing_metadata_keys(data)
    if not keys:
        return {}, [{
            "code": "routing-vector-required",
            "field": "routing_policy_version",
            "value": None,
            "allowed": [str(taxonomy["version"])],
            "reason": "legacy task declares no routing vector; no zone or task shape was guessed",
        }]
    violations = tasks.task_routing_violations(data)
    if violations:
        return {}, [{
            "code": "routing-vector-invalid",
            "field": field,
            "value": value,
            "allowed": list(allowed),
            "reason": f"{field} conflicts with the declared routing vocabulary",
        } for field, value, allowed in violations]
    vector, _ = tasks._axis_vector(
        data["routing_axes"], tuple(taxonomy["axes"]), tuple(taxonomy["axis_levels"])
    )
    return vector, []


def _profile_capabilities(tool: str, spec: dict[str, Any], taxonomy: dict[str, Any]) -> list[str]:
    effective = set(config_module.TOOL_CAPABILITIES.get(tool, ())) | set(spec.get("capabilities", ()))
    return [tag for tag in taxonomy["capability_tags"] if tag in effective]


def _minimum_reasoning(band: str, taxonomy: dict[str, Any]) -> str:
    return taxonomy["profile_preferences"]["complexity_reasoning"][band]


def _supported_reasoning(spec: dict[str, Any], minimum: str, taxonomy: dict[str, Any]) -> str | None:
    order = list(taxonomy["reasoning_levels"])
    floor = order.index(minimum)
    return next((level for level in order[floor:] if level in spec["reasoning_levels"]), None)


def _candidate_sort_key(
    candidate: dict[str, Any],
    taxonomy: dict[str, Any],
    tool_order: dict[str, int],
    profile_order: dict[tuple[str, str], int],
) -> tuple[int, ...]:
    values = {
        "preference_rank": candidate["preference_rank"],
        "tool_order": tool_order[candidate["tool"]],
        "profile_order": profile_order[(candidate["tool"], candidate["logical_profile"])],
    }
    return tuple(values[name] for name in taxonomy["profile_preferences"]["tie_breakers"])


def _catalog_for(profile: ToolProfile, tool: str, taxonomy: dict[str, Any]) -> dict[str, Any]:
    configured = profile.catalogs.get(tool)
    if configured is not None:
        return configured
    return {
        "provenance": "unknown",
        "source": "legacy-version-1-profile",
        "observed_at": None,
        "fetched_at": None,
        "evaluation_time": None,
        "ttl_seconds": None,
        "selector": taxonomy["resolution_selectors"][0],
        "exact_pin": None,
        "observations": [],
    }


def _resolve_model(
    tool: str,
    profile_name: str,
    logical: dict[str, Any],
    reasoning: str,
    required_capabilities: list[str],
    local: ToolProfile,
    taxonomy: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    catalog = _catalog_for(local, tool, taxonomy)
    freshness = catalog_freshness(catalog)
    provenance = catalog["provenance"]
    pin = catalog.get("exact_pin")
    selector = catalog["selector"]
    normalized = {
        "integration_source": catalog["source"],
        "provenance": provenance,
        "observed_at": catalog.get("observed_at"),
        "fetched_at": catalog.get("fetched_at"),
        "freshness": freshness,
        "selector": selector,
        "exact_pin": pin,
        "observation_count": len(catalog.get("observations", [])),
    }

    compatible: list[dict[str, Any]] = []
    for observation in catalog.get("observations", []):
        if observation["provenance"] != provenance:
            continue
        if profile_name not in observation["logical_profiles"]:
            continue
        if reasoning not in observation["reasoning_levels"]:
            continue
        if not set(required_capabilities).issubset(observation["capabilities"]):
            continue
        compatible.append(observation)

    trustworthy = provenance not in ("unknown", "manual") and freshness["stale"] is False
    conflicts: list[dict[str, Any]] = []
    if pin is not None:
        exact = [item for item in compatible if item["model"] == pin]
        if not trustworthy or not exact:
            conflicts.append({
                "code": "exact-pin-unproven",
                "field": f"catalogs.{tool}.exact_pin",
                "value": pin,
                "reason": (
                    "the normalized catalog cannot prove this exact identity fresh, available, "
                    "and compatible under the selected provenance; no fallback was attempted"
                ),
                "guidance": (
                    f"Refresh the integration-owned catalog or update the pin with {CONFIGURE_GUIDANCE}; "
                    "API-account evidence never proves desktop-app entitlement."
                ),
            })
            return {
                "kind": "conflict",
                "model": None,
                "selector": None,
                "reason": "explicit exact pin failed closed",
                "catalog": normalized,
                "receipt_requirement": "record the actual model reported by the execution surface",
            }, conflicts
        return {
            "kind": "exact",
            "model": pin,
            "selector": None,
            "reason": "fresh compatible normalized catalog observation proves the explicit exact pin",
            "catalog": normalized,
            "receipt_requirement": "record the actual model reported by the execution surface",
        }, conflicts

    if selector == "latest_compatible" and trustworthy and compatible:
        chosen = sorted(compatible, key=lambda item: (item["preference"], item["model"]))[0]
        return {
            "kind": "exact",
            "model": chosen["model"],
            "selector": selector,
            "reason": "fresh integration-owned catalog resolved latest_compatible deterministically",
            "catalog": normalized,
            "receipt_requirement": "record the actual model reported by the execution surface",
        }, conflicts

    if freshness["stale"] is not False:
        reason = "catalog freshness is stale or unknown; exact resolution is not trustworthy"
    elif not trustworthy:
        reason = f"catalog provenance {provenance!r} is policy-untrusted for exact resolution"
    else:
        reason = "the symbolic selector is owned by the execution surface"
    return {
        "kind": "symbolic",
        "model": None,
        "selector": selector,
        "reason": reason,
        "catalog": normalized,
        "receipt_requirement": "record the actual model reported by the execution surface",
    }, conflicts


def explain_route(
    task_id: str,
    data: dict[str, Any],
    *,
    local_profile: ToolProfile | None,
    detection: Any = None,
    respect_assignments: bool = True,
) -> dict[str, Any]:
    """Pure route evaluation. `detection` is accepted only to prove it is non-authoritative."""
    del detection
    taxonomy = config_module.ROUTING_TAXONOMY
    result: dict[str, Any] = {
        "schema_version": ROUTE_SCHEMA_VERSION,
        "task_id": task_id,
        "status": "conflict",
        "normalized_inputs": {
            "routing_policy_version": data.get("routing_policy_version"),
            "zone": data.get("routing_zone"),
            "axes": data.get("routing_axes"),
            "risk": data.get("risk"),
            "owner_constraints": {
                "tool": data.get("preferred_tool"),
                "profile": data.get("routing_profile"),
                "reasoning": data.get("routing_reasoning_level"),
                "reviewer": data.get("review_tool"),
            },
            "enabled_tools": list(local_profile.enabled_tools) if local_profile else [],
            "local_profile_version": local_profile.source_version if local_profile else None,
        },
        "complexity": None,
        "selected_candidate": None,
        "rejected_candidates": [],
        "reviewer_candidates": [],
        "resolution": None,
        "conflicts": [],
        "decision_reasons": [],
    }
    if taxonomy is None:
        result["conflicts"].append({
            "code": config_module.ROUTING_UNDECLARED,
            "reason": config_module.ROUTING_GUIDANCE,
        })
        return result

    vector, conflicts = _routing_vector(data, taxonomy)
    if conflicts:
        result["conflicts"].extend(conflicts)
        return result
    band, band_reasons = tasks.derive_complexity_band(vector)
    result["complexity"] = {"band": band, "reasons": band_reasons}
    result["normalized_inputs"]["axes"] = vector
    if local_profile is None:
        result["conflicts"].append({
            "code": "tool-profile-required",
            "reason": "no tool is explicitly enabled; advisory detection cannot authorize routing",
            "guidance": CONFIGURE_GUIDANCE,
        })
        return result

    zone = taxonomy["zones"][data["routing_zone"]]
    required = list(zone["required_capabilities"])
    minimum = _minimum_reasoning(band, taxonomy)
    tool_order = {tool: index for index, tool in enumerate(config_module.TOOLS)}
    profile_order = {
        (tool, name): profile_index
        for tool in config_module.TOOLS
        for profile_index, name in enumerate(config_module.TOOL_PROFILES.get(tool, {}))
    }
    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    enabled = set(local_profile.enabled_tools)
    constrained = respect_assignments
    owner_profile = data.get("routing_profile") if constrained else None
    owner_reasoning = data.get("routing_reasoning_level") if constrained else None
    owner_tool = (
        data.get("preferred_tool")
        if constrained and data.get("preferred_tool") in config_module.TOOLS else None
    )

    for tool in config_module.TOOLS:
        profiles = config_module.TOOL_PROFILES.get(tool, {})
        for name, spec in profiles.items():
            reasons: list[str] = []
            if tool not in enabled:
                reasons.append("tool is not explicitly enabled")
            if name not in local_profile.profiles.get(tool, tuple(profiles)):
                reasons.append("logical profile is not explicitly enabled")
            if owner_tool and tool != owner_tool:
                reasons.append(f"conflicts with explicit owner tool {owner_tool!r}")
            if owner_profile and name != owner_profile:
                reasons.append(f"conflicts with explicit owner profile {owner_profile!r}")
            if band not in spec["complexity_bands"]:
                reasons.append(f"logical profile does not support complexity band {band!r}")
            effective = _profile_capabilities(tool, spec, taxonomy)
            missing = [capability for capability in required if capability not in effective]
            if missing:
                reasons.append("missing required capabilities: " + ", ".join(missing))
            reasoning = owner_reasoning or _supported_reasoning(spec, minimum, taxonomy)
            if reasoning not in spec["reasoning_levels"]:
                reasons.append(f"reasoning level {reasoning!r} is unsupported")
            candidate = {
                "tool": tool,
                "family": config_module.TOOL_FAMILIES[tool],
                "logical_profile": name,
                "reasoning_level": reasoning,
                "capabilities": effective,
                "preference_rank": spec["preference_rank"],
                "reasons": reasons,
            }
            if reasons:
                rejected.append(candidate)
            else:
                candidates.append(candidate)

    result["rejected_candidates"] = rejected
    if not candidates:
        if owner_tool or owner_profile or owner_reasoning:
            result["conflicts"].append({
                "code": "explicit-owner-ineligible",
                "field": "preferred_tool",
                "value": owner_tool,
                "reason": (
                    "the explicit owner tool/profile/reasoning selection is unavailable or "
                    "ineligible for the declared zone and complexity; no fallback was attempted"
                ),
                "guidance": CONFIGURE_GUIDANCE,
            })
        else:
            result["conflicts"].append({
                "code": "no-eligible-executor",
                "reason": "no explicitly enabled logical profile satisfies every hard constraint",
                "guidance": CONFIGURE_GUIDANCE,
            })
        return result
    candidates.sort(
        key=lambda item: _candidate_sort_key(item, taxonomy, tool_order, profile_order)
    )
    selected = candidates[0]
    selected["reasons"] = [
        f"covers zone capabilities: {', '.join(required)}",
        f"supports complexity band {band!r}",
        f"supports reasoning level {selected['reasoning_level']!r}",
        "won declared preference rank and stable config-order tie-break",
    ]
    result["selected_candidate"] = selected
    result["decision_reasons"] = band_reasons + selected["reasons"]

    review_floor = taxonomy["review_depth_floor"].get(
        data.get("risk"), taxonomy["reasoning_levels"][0]
    )
    eligible_reviewers: list[dict[str, Any]] = []
    for tool in config_module.TOOLS:
        if tool not in enabled:
            continue
        for name, spec in config_module.TOOL_PROFILES.get(tool, {}).items():
            if name not in local_profile.profiles.get(tool, tuple()):
                continue
            family = config_module.TOOL_FAMILIES[tool]
            capabilities = _profile_capabilities(tool, spec, taxonomy)
            if family == selected["family"] or "independent_review" not in capabilities:
                continue
            review_reasoning = _supported_reasoning(spec, review_floor, taxonomy)
            if review_reasoning is None:
                continue
            eligible_reviewers.append({
                "tool": tool,
                "family": family,
                "logical_profile": name,
                "reasoning_level": review_reasoning,
                "capabilities": capabilities,
                "preference_rank": spec["preference_rank"],
                "reason": "eligible cross-family independent reviewer",
            })
    eligible_reviewers.sort(
        key=lambda item: _candidate_sort_key(item, taxonomy, tool_order, profile_order)
    )
    owner_reviewer = data.get("review_tool")
    if owner_reviewer not in config_module.TOOLS:
        owner_reviewer = None
    result["reviewer_candidates"] = [
        candidate for candidate in eligible_reviewers
        if not owner_reviewer or candidate["tool"] == owner_reviewer
    ]
    if owner_reviewer and not result["reviewer_candidates"]:
        result["conflicts"].append({
            "code": "explicit-reviewer-ineligible",
            "field": "review_tool",
            "value": owner_reviewer,
            "reason": (
                "the owner-selected reviewer has no explicitly enabled cross-family logical "
                "profile with independent-review capability at the required review depth"
            ),
            "guidance": CONFIGURE_GUIDANCE,
        })
    elif not result["reviewer_candidates"]:
        result["decision_reasons"].append(
            "no eligible cross-family independent reviewer; owner waiver or newly enabled "
            "independent reviewer is required"
        )
    resolution, resolution_conflicts = _resolve_model(
        selected["tool"], selected["logical_profile"],
        config_module.TOOL_PROFILES[selected["tool"]][selected["logical_profile"]],
        selected["reasoning_level"], required, local_profile, taxonomy,
    )
    result["resolution"] = resolution
    result["conflicts"].extend(resolution_conflicts)
    result["status"] = "conflict" if result["conflicts"] else "selected"
    return result


def render_human(result: dict[str, Any]) -> str:
    lines = [f"Route explanation: {result['task_id']}", f"Status: {result['status']}"]
    if result["complexity"]:
        lines.append(f"Complexity band: {result['complexity']['band']}")
    selected = result["selected_candidate"]
    if selected:
        lines.append(
            f"Executor: {selected['tool']} / {selected['logical_profile']} "
            f"(reasoning={selected['reasoning_level']})"
        )
    resolution = result["resolution"]
    if resolution:
        value = resolution["model"] or resolution["selector"]
        lines.append(
            f"Model resolution: {resolution['kind']} {value}; provenance="
            f"{resolution['catalog']['provenance']}; {resolution['reason']}"
        )
    lines.append(f"Reviewer candidates: {len(result['reviewer_candidates'])}")
    lines.append(f"Rejected candidates: {len(result['rejected_candidates'])}")
    if result["conflicts"]:
        lines.append("Conflicts:")
        lines.extend(f"- {item['code']}: {item['reason']}" for item in result["conflicts"])
    if result["decision_reasons"]:
        lines.append("Decision reasons:")
        lines.extend(f"- {reason}" for reason in result["decision_reasons"])
    lines.append("Read-only evaluation: no task edits, launch, detection, credential, or network access.")
    return "\n".join(lines)


def add_route_parser(subparsers: argparse._SubParsersAction) -> None:
    route = subparsers.add_parser("route", help="Explain deterministic availability-aware routing")
    route_sub = route.add_subparsers(dest="route_command", required=True)
    explain = route_sub.add_parser("explain", help="Explain a task route without dispatching or mutating")
    explain.add_argument("task_id")
    explain.add_argument("--json", action="store_true", help="Emit stable machine-readable JSON")
    apply_parser = route_sub.add_parser("apply", help="Atomically fill unset routing assignment fields")
    apply_parser.add_argument("task_id")
    apply_parser.add_argument("--replace", action="store_true", help="Replace assignments with reconciliation evidence")
    apply_parser.add_argument("--reconciliation", help="Auditable owner reconciliation text required by --replace")


def cmd_route_explain(args: argparse.Namespace) -> None:
    _task_dir, data = tasks.find_task(args.task_id)
    try:
        local = load_profile(
            config_module.TOOLS,
            declared_profiles=config_module.TOOL_PROFILES,
            taxonomy=config_module.ROUTING_TAXONOMY,
        )
    except ToolProfileError as error:
        result = {
            "schema_version": ROUTE_SCHEMA_VERSION,
            "task_id": args.task_id,
            "status": "conflict",
            "normalized_inputs": {},
            "complexity": None,
            "selected_candidate": None,
            "rejected_candidates": [],
            "reviewer_candidates": [],
            "resolution": None,
            "conflicts": [{"code": error.reason, "reason": error.detail, "guidance": CONFIGURE_GUIDANCE}],
            "decision_reasons": [],
        }
    else:
        result = explain_route(args.task_id, data, local_profile=local)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(render_human(result))
