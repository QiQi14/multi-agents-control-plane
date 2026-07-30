"""Version-2 local routing profile with version-1 read compatibility.

Exact model observations are checkout-local integration data. They are never committed routing law.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import scripts.ai_plane.config as config_module
import scripts.ai_plane.constants as constants
import scripts.ai_plane.tool_detection as tool_detection
from scripts.ai_plane.utils import die, rel

PROFILE_VERSION = 2
PROFILE_RELATIVE_PATH = ".ai/.local/tools.json"
ROLE_FIELDS = ("research_tool", "planning_tool", "implementation_tool", "review_tool")
CONFIGURE_COMMAND = "python scripts/ai_cli.py tools configure"
CONFIGURE_GUIDANCE = f"Configure local tools with: {CONFIGURE_COMMAND}"


class ToolProfileError(ValueError):
    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason, self.detail = reason, detail


@dataclass(frozen=True)
class ToolProfile:
    enabled_tools: tuple[str, ...]
    defaults: dict[str, str]
    profiles: dict[str, tuple[str, ...]]
    catalogs: dict[str, dict[str, Any]]
    source_version: int = PROFILE_VERSION

    def as_json(self) -> dict[str, Any]:
        return {
            "version": PROFILE_VERSION,
            "enabled_tools": list(self.enabled_tools),
            "defaults": {field: self.defaults[field] for field in ROLE_FIELDS},
            "routing": {
                "profiles": {tool: list(self.profiles[tool]) for tool in self.enabled_tools},
                "catalogs": {tool: self.catalogs[tool] for tool in self.enabled_tools},
            },
        }


def profile_path(ai_root: Path | None = None) -> Path:
    return (ai_root or constants.AI) / ".local" / "tools.json"


def _duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ToolProfileError("tool-profile-invalid", f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _unknown_catalog(selector: str, *, legacy: bool = False) -> dict[str, Any]:
    return {
        "provenance": "unknown",
        "source": "legacy-version-1-profile" if legacy else "unconfigured",
        "observed_at": None,
        "fetched_at": None,
        "evaluation_time": None if legacy else "1970-01-01T00:00:00Z",
        "ttl_seconds": None if legacy else 0,
        "selector": selector,
        "exact_pin": None,
        "observations": [],
    }


def _ordered(values: list[str], supported: tuple[str, ...]) -> tuple[str, ...]:
    selected = set(values)
    return tuple(tool for tool in supported if tool in selected)


def _assignments(values: list[str] | None, label: str, *, multiple: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for raw in values or []:
        tool, separator, value = raw.partition("=")
        if not separator or not tool or not value:
            raise ToolProfileError("tool-profile-invalid", f"{label} must use TOOL=VALUE")
        if not multiple and tool in result:
            raise ToolProfileError("tool-profile-invalid", f"{label} repeats tool {tool!r}")
        if multiple:
            result.setdefault(tool, []).append(value)
        else:
            result[tool] = value
    return result


def _base(data: dict[str, Any], supported: tuple[str, ...]) -> tuple[tuple[str, ...], dict[str, str]]:
    enabled = data.get("enabled_tools")
    defaults = data.get("defaults")
    if (not isinstance(enabled, list) or not enabled or
            not all(isinstance(item, str) and item for item in enabled) or
            len(enabled) != len(set(enabled))):
        raise ToolProfileError("tool-profile-invalid", "enabled_tools must be a non-empty unique list")
    unknown = sorted(set(enabled) - set(supported))
    if unknown:
        raise ToolProfileError("tool-profile-stale", "unknown enabled tool(s): " + ", ".join(unknown))
    if not isinstance(defaults, dict) or set(defaults) != set(ROLE_FIELDS):
        raise ToolProfileError("tool-profile-invalid", "defaults must declare exactly the four role fields")
    for role, tool in defaults.items():
        if tool not in enabled:
            raise ToolProfileError("tool-profile-invalid", f"defaults.{role} must name an enabled tool")
    return _ordered(enabled, supported), {field: defaults[field] for field in ROLE_FIELDS}


def _catalog(tool: str, value: Any, profiles: tuple[str, ...], taxonomy: dict[str, Any]) -> dict[str, Any]:
    at = f"routing.catalogs.{tool}"
    fields = {"provenance", "source", "observed_at", "fetched_at", "evaluation_time",
              "ttl_seconds", "selector", "exact_pin", "observations"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ToolProfileError("tool-profile-invalid", f"{at} has unknown or missing fields")
    if value["provenance"] not in taxonomy["catalog_provenance"]:
        raise ToolProfileError("tool-profile-invalid", f"{at}.provenance is not declared")
    if not isinstance(value["source"], str) or not value["source"]:
        raise ToolProfileError("tool-profile-invalid", f"{at}.source must be non-empty")
    for field in ("observed_at", "fetched_at", "evaluation_time", "exact_pin"):
        if value[field] is not None and (not isinstance(value[field], str) or not value[field]):
            raise ToolProfileError("tool-profile-invalid", f"{at}.{field} must be string or null")
    if type(value["ttl_seconds"]) is not int or value["ttl_seconds"] < 0:
        raise ToolProfileError("tool-profile-invalid", f"{at}.ttl_seconds must be non-negative")
    if value["selector"] not in taxonomy["resolution_selectors"]:
        raise ToolProfileError("tool-profile-invalid", f"{at}.selector is not declared")
    if not isinstance(value["observations"], list):
        raise ToolProfileError("tool-profile-invalid", f"{at}.observations must be a list")
    observation_fields = {"model", "provenance", "capabilities", "reasoning_levels",
                          "logical_profiles", "preference"}
    for index, observation in enumerate(value["observations"]):
        where = f"{at}.observations[{index}]"
        if not isinstance(observation, dict) or set(observation) != observation_fields:
            raise ToolProfileError("tool-profile-invalid", f"{where} has unknown or missing fields")
        if not isinstance(observation["model"], str) or not observation["model"]:
            raise ToolProfileError("tool-profile-invalid", f"{where}.model must be non-empty")
        if observation["provenance"] not in taxonomy["catalog_provenance"]:
            raise ToolProfileError("tool-profile-invalid", f"{where}.provenance is not declared")
        for field, allowed in (
            ("capabilities", taxonomy["capability_tags"]),
            ("reasoning_levels", taxonomy["reasoning_levels"]),
            ("logical_profiles", profiles),
        ):
            items = observation[field]
            if (not isinstance(items, list) or not items or len(items) != len(set(items)) or
                    any(not isinstance(item, str) or item not in allowed for item in items)):
                raise ToolProfileError("tool-profile-invalid", f"{where}.{field} must be a unique declared list")
        if type(observation["preference"]) is not int or observation["preference"] < 0:
            raise ToolProfileError("tool-profile-invalid", f"{where}.preference must be non-negative")
    return value


def validate_profile_data(data: Any, supported_tools: tuple[str, ...], *,
                          declared_profiles: dict[str, dict[str, Any]] | None = None,
                          taxonomy: dict[str, Any] | None = None) -> ToolProfile:
    if not isinstance(data, dict):
        raise ToolProfileError("tool-profile-invalid", "top level must be a JSON object")
    version = data.get("version")
    expected = {"version", "enabled_tools", "defaults"} if version == 1 else {
        "version", "enabled_tools", "defaults", "routing"}
    if set(data) != expected or type(version) is not int or version not in (1, PROFILE_VERSION):
        raise ToolProfileError("tool-profile-invalid", "profile must be exact version 1 or version 2 schema")
    enabled, defaults = _base(data, supported_tools)
    declared_profiles = declared_profiles or {}
    if version == 1:
        selector = taxonomy["resolution_selectors"][0] if taxonomy else "app_default"
        return ToolProfile(
            enabled, defaults,
            {tool: tuple(declared_profiles.get(tool, {})) for tool in enabled},
            {tool: _unknown_catalog(selector, legacy=True) for tool in enabled},
            source_version=1,
        )
    if taxonomy is None:
        raise ToolProfileError("tool-profile-invalid", "version 2 requires routing_taxonomy")
    routing = data["routing"]
    if (not isinstance(routing, dict) or set(routing) != {"profiles", "catalogs"} or
            not isinstance(routing["profiles"], dict) or not isinstance(routing["catalogs"], dict) or
            set(routing["profiles"]) != set(enabled) or set(routing["catalogs"]) != set(enabled)):
        raise ToolProfileError("tool-profile-invalid", "routing profiles/catalogs must match enabled_tools")
    profiles: dict[str, tuple[str, ...]] = {}
    catalogs: dict[str, dict[str, Any]] = {}
    for tool in enabled:
        allowed = tuple(declared_profiles.get(tool, {}))
        selected = routing["profiles"][tool]
        if (not isinstance(selected, list) or not selected or len(selected) != len(set(selected)) or
                any(profile not in allowed for profile in selected)):
            raise ToolProfileError("tool-profile-stale",
                                   f"routing.profiles.{tool} must name declared logical profiles")
        profiles[tool] = tuple(profile for profile in allowed if profile in selected)
        catalogs[tool] = _catalog(tool, routing["catalogs"][tool], allowed, taxonomy)
    return ToolProfile(enabled, defaults, profiles, catalogs)


def load_profile(supported_tools: tuple[str, ...], *, ai_root: Path | None = None,
                 required: bool = False,
                 declared_profiles: dict[str, dict[str, Any]] | None = None,
                 taxonomy: dict[str, Any] | None = None) -> ToolProfile | None:
    declared_profiles = config_module.TOOL_PROFILES if declared_profiles is None else declared_profiles
    taxonomy = config_module.ROUTING_TAXONOMY if taxonomy is None else taxonomy
    path = profile_path(ai_root)
    if not path.exists():
        if required:
            raise ToolProfileError("tool-profile-required", f"{rel(path)} is missing; no tool is enabled")
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_duplicates)
    except ToolProfileError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ToolProfileError("tool-profile-invalid", f"{rel(path)} is malformed: {error}") from error
    return validate_profile_data(data, supported_tools, declared_profiles=declared_profiles,
                                 taxonomy=taxonomy)


def serialize_profile(profile: ToolProfile) -> bytes:
    return (json.dumps(profile.as_json(), indent=2) + "\n").encode()


def atomic_write_profile(profile: ToolProfile, *, ai_root: Path | None = None) -> Path:
    path = profile_path(ai_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".tools.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(serialize_profile(profile))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return path


def configured_transport_form(tool: str, descriptors: dict[str, dict[str, Any]]) -> str:
    descriptor = descriptors.get(tool)
    return "manual-only" if descriptor is None else "+".join(
        form for form in ("exec", "deeplink") if form in descriptor
    )


def add_tools_parser(subparsers: argparse._SubParsersAction) -> None:
    tools = subparsers.add_parser("tools", help="Inspect or configure the checkout-local tool profile")
    commands = tools.add_subparsers(dest="tools_command", required=True)
    commands.add_parser("list")
    status = commands.add_parser("status")
    status.add_argument("--detect", action="store_true", required=True)
    configure = commands.add_parser("configure")
    configure.add_argument("--enable", action="append", choices=config_module.TOOLS)
    configure.add_argument("--default-tool", choices=config_module.TOOLS)
    for role in ROLE_FIELDS:
        configure.add_argument("--" + role.replace("_", "-"), choices=config_module.TOOLS)
    configure.add_argument("--profile", action="append", metavar="TOOL=PROFILE")
    configure.add_argument("--catalog-provenance", action="append", metavar="TOOL=PROVENANCE")
    configure.add_argument("--catalog-source", action="append", metavar="TOOL=SOURCE")
    configure.add_argument("--selector", action="append", metavar="TOOL=SELECTOR")
    configure.add_argument("--exact-pin", action="append", metavar="TOOL=MODEL")
    configure.add_argument("--observed-at", action="append", metavar="TOOL=TIMESTAMP")
    configure.add_argument("--fetched-at", action="append", metavar="TOOL=TIMESTAMP")
    configure.add_argument("--evaluation-time", action="append", metavar="TOOL=TIMESTAMP")
    configure.add_argument("--ttl-seconds", action="append", metavar="TOOL=SECONDS")
    configure.add_argument("--reset", action="store_true")


def _load() -> ToolProfile | None:
    return load_profile(config_module.TOOLS, declared_profiles=config_module.TOOL_PROFILES,
                        taxonomy=config_module.ROUTING_TAXONOMY)


def cmd_tools_list(_args: argparse.Namespace) -> None:
    try:
        profile = _load()
    except ToolProfileError as error:
        die(f"{error.reason}: {error.detail}. {CONFIGURE_GUIDANCE}")
    enabled = set(profile.enabled_tools if profile else ())
    print(f"Local tool profile: {'configured' if profile else 'not configured'} ({PROFILE_RELATIVE_PATH})")
    print(f"Enabled tools: {len(enabled)}")
    print("Supported tools:")
    for tool in config_module.TOOLS:
        print(f"- {tool}: enabled={'yes' if tool in enabled else 'no'}; "
              f"transport={configured_transport_form(tool, config_module.DISPATCH_DESCRIPTORS)}")
    print("No provider, process, network, URI, registry, or PATH probes were performed.")


def cmd_tools_status(_args: argparse.Namespace) -> None:
    profile = _load()
    enabled = set(profile.enabled_tools if profile else ())
    detections = tool_detection.detect_tool_transports(config_module.TOOLS,
                                                        config_module.DISPATCH_DESCRIPTORS)
    print("Advisory detection (never routing authority):")
    for tool in config_module.TOOLS:
        print(f"- {tool}: enabled={'yes' if tool in enabled else 'no'}")
        for result in detections[tool]:
            print(f"  detection: status={result.status}; detector={result.detector}; reason={result.reason}")
    print("Launch attempted: no; detection never starts providers or opens URIs.")


def cmd_tools_configure(args: argparse.Namespace) -> None:
    path = profile_path()
    routing_flags = (
        "profile", "catalog_provenance", "catalog_source", "selector", "exact_pin",
        "observed_at", "fetched_at", "evaluation_time", "ttl_seconds",
    )
    flags = bool(args.enable or args.default_tool or any(getattr(args, field) for field in ROLE_FIELDS)
                 or any(getattr(args, field) for field in routing_flags))
    if args.reset:
        if flags:
            die("tool-profile-invalid: --reset cannot be combined with selections")
        path.unlink(missing_ok=True)
        print(f"Local tool profile reset: {PROFILE_RELATIVE_PATH}")
        return
    if not flags:
        die("tool-profile-required: deterministic configuration flags are required")
    if not args.enable:
        die("tool-profile-invalid: at least one --enable TOOL is required")
    if len(args.enable) != len(set(args.enable)):
        die("tool-profile-invalid: duplicate --enable TOOL selections are not allowed")
    roles = {field: getattr(args, field) for field in ROLE_FIELDS}
    if args.default_tool and any(roles.values()):
        die("tool-profile-invalid: --default-tool cannot be combined with role-specific flags")
    if args.default_tool:
        roles = {field: args.default_tool for field in ROLE_FIELDS}
    if any(value is None for value in roles.values()):
        die("tool-profile-invalid: all role defaults are required")
    enabled = _ordered(args.enable, config_module.TOOLS)
    taxonomy = config_module.ROUTING_TAXONOMY
    if taxonomy is None:
        die("routing-taxonomy-undeclared: cannot configure logical profiles")
    try:
        profile_choices = _assignments(args.profile, "--profile", multiple=True)
        fields = {
            "provenance": _assignments(args.catalog_provenance, "--catalog-provenance"),
            "source": _assignments(args.catalog_source, "--catalog-source"),
            "selector": _assignments(args.selector, "--selector"),
            "exact_pin": _assignments(args.exact_pin, "--exact-pin"),
            "observed_at": _assignments(args.observed_at, "--observed-at"),
            "fetched_at": _assignments(args.fetched_at, "--fetched-at"),
            "evaluation_time": _assignments(args.evaluation_time, "--evaluation-time"),
            "ttl_seconds": _assignments(args.ttl_seconds, "--ttl-seconds"),
        }
        mentioned = set(profile_choices)
        for values in fields.values():
            mentioned.update(values)
        unknown_tools = sorted(mentioned - set(enabled))
        if unknown_tools:
            raise ToolProfileError(
                "tool-profile-invalid",
                "routing selections name tool(s) that are not enabled: " + ", ".join(unknown_tools),
            )
        selected_profiles = {
            tool: tuple(profile_choices.get(tool, config_module.TOOL_PROFILES.get(tool, {})))
            for tool in enabled
        }
        catalogs = {
            tool: _unknown_catalog(taxonomy["resolution_selectors"][0])
            for tool in enabled
        }
        for field, values in fields.items():
            for tool, value in values.items():
                catalogs[tool][field] = int(value) if field == "ttl_seconds" and value.isdigit() else value
        profile = ToolProfile(enabled, roles, selected_profiles, catalogs)
    except ToolProfileError as error:
        die(f"{error.reason}: {error.detail}")
    try:
        validated = validate_profile_data(
            profile.as_json(), config_module.TOOLS,
            declared_profiles=config_module.TOOL_PROFILES, taxonomy=taxonomy,
        )
        written = atomic_write_profile(validated)
    except (ToolProfileError, OSError) as error:
        detail = error.detail if isinstance(error, ToolProfileError) else str(error)
        die(f"tool-profile-write-failed: {detail}")
    print(f"Local tool profile written: {rel(written)}")


def resolve_implicit_tool(role: str, supported_tools: tuple[str, ...]) -> str:
    try:
        profile = load_profile(supported_tools, required=True,
                               declared_profiles=config_module.TOOL_PROFILES,
                               taxonomy=config_module.ROUTING_TAXONOMY)
    except ToolProfileError as error:
        die(f"{error.reason}: {error.detail}. {CONFIGURE_GUIDANCE}")
    assert profile
    return profile.defaults[role]


def require_enabled_tool(tool: str, supported_tools: tuple[str, ...]) -> None:
    try:
        profile = load_profile(supported_tools, required=True,
                               declared_profiles=config_module.TOOL_PROFILES,
                               taxonomy=config_module.ROUTING_TAXONOMY)
    except ToolProfileError as error:
        die(f"{error.reason}: {error.detail}. {CONFIGURE_GUIDANCE}")
    assert profile
    if tool not in profile.enabled_tools:
        die(f"tool-not-enabled: {tool!r} is not explicitly enabled. {CONFIGURE_GUIDANCE}")


def require_enabled_review_tool(tool: str, supported_tools: tuple[str, ...]) -> None:
    try:
        profile = load_profile(supported_tools, required=True,
                               declared_profiles=config_module.TOOL_PROFILES,
                               taxonomy=config_module.ROUTING_TAXONOMY)
    except ToolProfileError as error:
        die(f"{error.reason}: {error.detail}. Independent review is unavailable. "
            f"Enabled alternatives: none. No reviewer was substituted. {CONFIGURE_GUIDANCE}")
    assert profile
    if tool in profile.enabled_tools:
        return
    alternatives = ", ".join(candidate for candidate in profile.enabled_tools if candidate != tool) or "none"
    die(f"tool-not-enabled: configured review tool {tool!r} is not enabled. Independent review is "
        f"unavailable. Enabled alternatives: {alternatives}. No reviewer was substituted. "
        f"{CONFIGURE_GUIDANCE} The owner may authorize a disclosed fresh substitute; a same-family substitute does not satisfy the independent merge gate unless the owner explicitly waives that gate.")