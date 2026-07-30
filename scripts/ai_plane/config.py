from __future__ import annotations

import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any

import scripts.ai_plane.constants as constants
from scripts.ai_plane import routing_schema
import scripts.extension_registry as extension_registry
from scripts.ai_plane.utils import rel

RISKS = ("low", "medium", "high")
PENDING_ASSIGNMENT = "pending"
MINIMUM_PYTHON = (3, 10)

_LOADED_CONFIG_DEFAULTS: dict[str, Any] = {}
TOOLS: tuple[str, ...] = ()
TOOL_NOTES: dict[str, str] = {}
TOOL_DEFAULTS: dict[str, str] = {}
TOOL_ROLES: dict[str, str] = {}
COMMAND_TOOL_DEFAULTS: dict[str, str] = {}
GENERATED_DISPATCH_ROOT: str = ".ai/adapters"
ADAPTERS: dict[str, dict[str, Any]] = {}
TASK_CONTRACT_VOCABULARY: dict[str, tuple[str, ...]] = {
    "risk": RISKS,
    "preferred_tool": (*TOOLS, PENDING_ASSIGNMENT),
    "review_tool": (*TOOLS, PENDING_ASSIGNMENT),
}

# Automatic dispatch lane (task_185): optional per-tool exec/deeplink launch descriptors.
# Off by default; a project enables it via defaults.auto_dispatch: true in .ai/config.yaml.
DISPATCH_PLACEHOLDERS: tuple[str, ...] = (
    "task_id",
    "tool",
    "prompt_path",
    "prompt_text",
    "prompt_encoded",
    "prompt_path_encoded",
)
PLACEHOLDER_PATTERN = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
AUTO_DISPATCH_ENABLED: bool = False
DISPATCH_DESCRIPTORS: dict[str, dict[str, Any]] = {}

# Routing taxonomy (task_192a). The routing vocabulary is DATA: this module validates its SHAPE
# generically and names no zone, axis, capability, provider, or model (its semantics live in
# tasks.py). The block is optional so a minimal configuration stays loadable, but no default
# vocabulary is ever substituted — an absent block leaves ROUTING_TAXONOMY None and fails consumers closed.
ROUTING_TAXONOMY: dict[str, Any] | None = None
TOOL_FAMILIES: dict[str, str] = {}
TOOL_CAPABILITIES: dict[str, tuple[str, ...]] = {}
TOOL_PROFILES: dict[str, dict[str, dict[str, Any]]] = {}
ROUTING_UNDECLARED = "routing-taxonomy-undeclared"
ROUTING_GUIDANCE = ("declare a 'routing_taxonomy' block in .ai/config.yaml (see "
                    ".ai/project/routing-taxonomy.md); no default routing vocabulary is ever substituted")
_LOADED_ROUTING_TAXONOMY: dict[str, Any] | None = None


class ConfigError(ValueError):
    """Actionable `.ai/config.yaml` schema failure."""


def strip_yaml_inline_comment(line: str) -> str:
    in_single = False
    in_double = False
    for i, char in enumerate(line):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            return line[:i].rstrip()
    return line.rstrip()


def parse_config_yaml(path: Path) -> dict[str, Any]:
    """Parse the dependency-free YAML subset used by `.ai/config.yaml`."""
    if not path.exists():
        raise ConfigError(f"{rel(path)} is missing; no default roster is available")

    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ConfigError(f"{rel(path)} is unreadable: {error}") from error

    lines: list[tuple[int, str, int]] = []
    for number, raw in enumerate(raw_lines, 1):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise ConfigError(f"{rel(path)}:{number} uses a tab for indentation")
        content = strip_yaml_inline_comment(raw.strip())
        if not content or content.startswith("#"):
            continue
        lines.append((len(raw) - len(raw.lstrip(" ")), content, number))

    def scalar(value: str) -> Any:
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            return value[1:-1]
        if value in ("true", "false"):
            return value == "true"
        if value in ("null", "~"):
            return None
        if value.startswith("[") and value.endswith("]"):
            try:
                return json.loads(value)
            except json.JSONDecodeError as error:
                raise ConfigError(f"{rel(path)} contains an invalid inline list: {value}") from error
        if re.fullmatch(r"-?\d+", value):
            return int(value)
        # Finite floats: a decimal (with optional exponent) or an integer mantissa WITH an exponent,
        # so a `number`-typed project override such as `threshold: 0.5` round-trips as a float. The
        # grammar excludes inf/nan spellings by construction; an overflowing literal (e.g. 1e400)
        # fails closed rather than silently becoming inf. Quoted values are handled above and are
        # never coerced.
        if re.fullmatch(r"-?\d+\.\d+([eE][+-]?\d+)?", value) or re.fullmatch(r"-?\d+[eE][+-]?\d+", value):
            parsed = float(value)
            if not math.isfinite(parsed):
                raise ConfigError(f"{rel(path)} contains a non-finite number: {value}")
            return parsed
        return value

    def block(index: int, indent: int) -> tuple[Any, int]:
        if index >= len(lines) or lines[index][0] < indent:
            return {}, index
        is_list = lines[index][1].startswith("- ")
        result: Any = [] if is_list else {}
        while index < len(lines):
            current_indent, content, number = lines[index]
            if current_indent < indent:
                break
            if current_indent > indent:
                raise ConfigError(f"{rel(path)}:{number} has unexpected indentation")
            if is_list:
                if not content.startswith("- "):
                    raise ConfigError(f"{rel(path)}:{number} mixes a mapping into a list")
                result.append(scalar(content[2:].strip()))
                index += 1
                continue
            if content.startswith("- ") or ":" not in content:
                raise ConfigError(f"{rel(path)}:{number} expected a mapping entry")
            key, value = content.split(":", 1)
            key = key.strip()
            if not key or key in result:
                raise ConfigError(f"{rel(path)}:{number} has an empty or duplicate key '{key}'")
            value = value.strip()
            index += 1
            if value:
                result[key] = scalar(value)
            elif index < len(lines) and lines[index][0] > indent:
                result[key], index = block(index, lines[index][0])
            else:
                result[key] = {}
        return result, index

    if not lines:
        raise ConfigError(f"{rel(path)} is empty")
    parsed, end = block(0, lines[0][0])
    if end != len(lines) or not isinstance(parsed, dict):
        raise ConfigError(f"{rel(path)} must contain one top-level mapping")
    return parsed


def validate_dispatch_template(where: str, template: str) -> None:
    """Fail closed on an unknown `{placeholder}` or unbalanced brace in a dispatch template."""
    if template.count("{") != template.count("}"):
        raise ConfigError(f"{where} has unbalanced template braces")
    for name in PLACEHOLDER_PATTERN.findall(template):
        if name not in DISPATCH_PLACEHOLDERS:
            raise ConfigError(
                f"{where} uses unknown placeholder '{{{name}}}'; known placeholders: "
                f"{', '.join(DISPATCH_PLACEHOLDERS)}"
            )


def validate_dispatch_descriptor(where: str, descriptor: Any) -> None:
    """Fail closed on a malformed optional per-tool automatic-dispatch descriptor."""
    if not isinstance(descriptor, dict) or not descriptor:
        raise ConfigError(f"{where}.dispatch must be a non-empty mapping")
    allowed_forms = {"exec", "deeplink"}
    unknown_forms = set(descriptor) - allowed_forms
    if unknown_forms:
        raise ConfigError(f"{where}.dispatch has unknown form(s): {', '.join(sorted(unknown_forms))}")
    if not (set(descriptor) & allowed_forms):
        raise ConfigError(f"{where}.dispatch must declare an 'exec' and/or 'deeplink' form")

    exec_form = descriptor.get("exec")
    if exec_form is not None:
        if not isinstance(exec_form, dict) or set(exec_form) != {"argv"}:
            raise ConfigError(f"{where}.dispatch.exec must be a mapping with exactly one key: 'argv'")
        argv = exec_form["argv"]
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
            raise ConfigError(f"{where}.dispatch.exec.argv must be a non-empty list of non-empty strings")
        for index, item in enumerate(argv):
            validate_dispatch_template(f"{where}.dispatch.exec.argv[{index}]", item)

    deeplink_form = descriptor.get("deeplink")
    if deeplink_form is not None:
        if not isinstance(deeplink_form, dict) or set(deeplink_form) != {"url_template"}:
            raise ConfigError(f"{where}.dispatch.deeplink must be a mapping with exactly one key: 'url_template'")
        url_template = deeplink_form["url_template"]
        if not isinstance(url_template, str) or not url_template:
            raise ConfigError(f"{where}.dispatch.deeplink.url_template must be a non-empty string")
        validate_dispatch_template(f"{where}.dispatch.deeplink.url_template", url_template)


def validate_routing_taxonomy(data: dict[str, Any], where: str) -> dict[str, Any] | None:
    """Validate the optional canonical routing vocabulary."""
    try:
        return routing_schema.validate_routing_taxonomy(data, where, RISKS)
    except routing_schema.RoutingSchemaError as error:
        raise ConfigError(str(error)) from error


def _validate_tool_routing(where: str, entry: dict[str, Any], taxonomy: dict[str, Any] | None) -> None:
    """Validate one tool's optional declarative routing catalog."""
    try:
        routing_schema.validate_tool_routing(
            where, entry, taxonomy, ROUTING_UNDECLARED, ROUTING_GUIDANCE
        )
    except routing_schema.RoutingSchemaError as error:
        raise ConfigError(str(error)) from error

def load_tool_registry(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load and validate the authoritative roster and adapter registry."""
    global _LOADED_CONFIG_DEFAULTS, _LOADED_ROUTING_TAXONOMY
    config_path = path or (constants.AI / "config.yaml")
    data = parse_config_yaml(config_path)
    taxonomy = validate_routing_taxonomy(data, rel(config_path))
    tools = data.get("tools")
    if not isinstance(tools, dict) or not tools:
        raise ConfigError(f"{rel(config_path)} field 'tools' must be a non-empty mapping")

    registry: dict[str, dict[str, Any]] = {}
    for name, entry in tools.items():
        where = f"{rel(config_path)} tools.{name}"
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", name):
            raise ConfigError(f"{where} has an invalid tool name")
        if not isinstance(entry, dict):
            raise ConfigError(f"{where} must be a mapping")
        for field in ("role", "default_isolation"):
            if not isinstance(entry.get(field), str) or not entry[field]:
                raise ConfigError(f"{where}.{field} must be a non-empty string")
        notes = entry.get("notes")
        if not isinstance(notes, list) or not notes or not all(isinstance(note, str) and note for note in notes):
            raise ConfigError(f"{where}.notes must be a non-empty string list")
        # task_190a: the adapter block SELECTS a render source only; the render descriptor (format,
        # detect marker, command tokens, catalogs, artifacts, templates) lives in the selected
        # `integration` extension. An omitted/null render_source selects the vendor-free core
        # neutral descriptor; a non-empty value must resolve to an enabled integration (checked when
        # the runtime is built). The core owns no per-vendor adapter-format branch.
        adapter = entry.get("adapter", {})
        if not isinstance(adapter, dict):
            raise ConfigError(f"{where}.adapter must be a mapping")
        unknown_adapter = sorted(set(adapter) - {"render_source"})
        if unknown_adapter:
            raise ConfigError(f"{where}.adapter declares unknown field(s): {', '.join(unknown_adapter)}; "
                              "only 'render_source' is permitted (the render descriptor lives in the integration)")
        render_source = adapter.get("render_source")
        if render_source is not None and (not isinstance(render_source, str) or not render_source):
            raise ConfigError(f"{where}.adapter.render_source must be a non-empty string or null")
        dispatch_descriptor = entry.get("dispatch")
        if dispatch_descriptor is not None:
            validate_dispatch_descriptor(where, dispatch_descriptor)
        _validate_tool_routing(where, entry, taxonomy)
        registry[name] = entry
    defaults = data.get("defaults")
    if not isinstance(defaults, dict):
        raise ConfigError(f"{rel(config_path)} field 'defaults' must be a mapping")
    default_fields = ("research_tool", "planning_tool", "implementation_tool", "review_tool")
    for field in default_fields:
        if defaults.get(field) not in registry:
            raise ConfigError(
                f"{rel(config_path)} defaults.{field} must name a declared tool; "
                f"got {defaults.get(field)!r}"
            )
    dispatch_root = defaults.get("generated_dispatch_root")
    if not isinstance(dispatch_root, str) or not dispatch_root:
        raise ConfigError(f"{rel(config_path)} defaults.generated_dispatch_root must be a non-empty string")
    dispatch_path = Path(dispatch_root)
    if dispatch_path.is_absolute() or ".." in dispatch_path.parts:
        raise ConfigError(f"{rel(config_path)} defaults.generated_dispatch_root must be repository-relative and contained")
    auto_dispatch_default = defaults.get("auto_dispatch", False)
    if not isinstance(auto_dispatch_default, bool):
        raise ConfigError(f"{rel(config_path)} defaults.auto_dispatch must be a boolean")
    _LOADED_CONFIG_DEFAULTS = defaults
    _LOADED_ROUTING_TAXONOMY = taxonomy
    return registry


def _derive_support_dir(descriptor: dict[str, Any]) -> str:
    """The single top-level support directory an integration writes under (e.g. a dot-prefixed
    support dir, or ``""`` for a root-only adapter), derived from its artifact output paths — used
    by scaffold-protection and reporting, not for rendering."""
    tops: set[str] = set()
    for artifact in descriptor["render"]["artifacts"]:
        candidates = []
        if "path" in artifact:
            candidates.append(artifact["path"])
        if "destination" in artifact:
            candidates.append(artifact["destination"])
        if artifact.get("index"):
            candidates.append(artifact["index"]["path"])
        if artifact.get("blueprint_destination"):
            candidates.append(artifact["blueprint_destination"])
        for candidate in candidates:
            parts = candidate.split("/")
            if len(parts) > 1:
                tops.add(parts[0])
    return sorted(tops)[0] if tops else ""


def _adapter_from_descriptor(descriptor: dict[str, Any], render_source: str | None,
                             ext_root: Path | None) -> dict[str, Any]:
    """Project one resolved render descriptor into the ADAPTERS shape the rest of the plane
    consumes (commands/detect_marker/output_dir/support_dir/format/…), plus the descriptor and its
    extension root for the generic renderer."""
    return {
        "output_dir": ".",
        "support_dir": _derive_support_dir(descriptor),
        "format": descriptor["format"],
        "argument_placeholder": descriptor["argument_placeholder"],
        "generated_file_extension": descriptor["generated_file_extension"],
        "invoke_separator": descriptor["invoke_separator"],
        "detect_marker": descriptor["detect_marker"],
        "commands": dict(descriptor["commands"]),
        "render_source": render_source,
        "descriptor": descriptor,
        "ext_root": str(ext_root) if ext_root is not None else None,
    }


def build_tool_adapters(registry: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Resolve every tool's render descriptor from its ``adapter.render_source`` selector and the
    enabled `integration` extensions. Omitted/null render_source selects the vendor-free core
    neutral descriptor; a non-empty value must resolve uniquely to an enabled integration or fail
    closed (`missing-render-source`) — it never falls back to neutral."""
    # The enabled-extension composition is only needed to build adapters when at least one tool
    # actually selects an integration render_source. A fully neutral roster needs no integrations,
    # so it does not force the extensions block here (sync still resolves + requires it separately).
    needs_integrations = any(
        entry.get("adapter", {}).get("render_source") is not None for entry in registry.values()
    )
    descriptors: dict[str, dict[str, Any]] = {}
    resolved: dict[str, Any] = {"extensions": {}}
    if needs_integrations:
        parsed = parse_config_yaml(constants.AI / "config.yaml")
        try:
            resolved = extension_registry.resolve(parsed, constants.ROOT, platform_name=os.name)
        except extension_registry.RegistryError as error:
            raise ConfigError(f"extension roster is invalid; cannot build adapters: {error}") from error
        descriptors = resolved["integration_descriptors"]
    adapters: dict[str, dict[str, Any]] = {}
    for name, entry in registry.items():
        render_source = entry.get("adapter", {}).get("render_source")
        if render_source is None:
            adapters[name] = _adapter_from_descriptor(extension_registry.neutral_descriptor(), None, None)
            continue
        if render_source not in descriptors:
            raise ConfigError(
                f".ai/config.yaml tools.{name}.adapter.render_source {render_source!r} does not resolve "
                f"to an enabled integration (missing-render-source); enabled integrations: "
                f"{sorted(descriptors)}"
            )
        ext_root = constants.ROOT / resolved["extensions"][render_source]["root"]
        adapters[name] = _adapter_from_descriptor(descriptors[render_source], render_source, ext_root)
    return adapters


def initialize_runtime_config(path: Path | None = None) -> None:
    """Parse config once for a CLI invocation and derive every roster consumer."""
    global TOOLS, TOOL_NOTES, TOOL_DEFAULTS, TOOL_ROLES, COMMAND_TOOL_DEFAULTS, GENERATED_DISPATCH_ROOT, ADAPTERS, TASK_CONTRACT_VOCABULARY, AUTO_DISPATCH_ENABLED, DISPATCH_DESCRIPTORS
    global ROUTING_TAXONOMY, TOOL_FAMILIES, TOOL_CAPABILITIES, TOOL_PROFILES
    registry = load_tool_registry(path)
    TOOLS = tuple(registry)
    TOOL_NOTES = {name: " ".join(entry["notes"]) for name, entry in registry.items()}
    TOOL_DEFAULTS = {name: entry["default_isolation"] for name, entry in registry.items()}
    TOOL_ROLES = {name: entry["role"] for name, entry in registry.items()}
    COMMAND_TOOL_DEFAULTS = {
        field: _LOADED_CONFIG_DEFAULTS[field]
        for field in ("research_tool", "planning_tool", "implementation_tool", "review_tool")
    }
    GENERATED_DISPATCH_ROOT = _LOADED_CONFIG_DEFAULTS["generated_dispatch_root"]
    ADAPTERS = build_tool_adapters(registry)
    TASK_CONTRACT_VOCABULARY = {
        "risk": RISKS,
        "preferred_tool": (*TOOLS, PENDING_ASSIGNMENT),
        "review_tool": (*TOOLS, PENDING_ASSIGNMENT),
    }
    ROUTING_TAXONOMY = _LOADED_ROUTING_TAXONOMY
    TOOL_FAMILIES = {name: entry["family"] for name, entry in registry.items() if "family" in entry}
    TOOL_CAPABILITIES = {name: tuple(entry.get("capabilities", ())) for name, entry in registry.items()}
    TOOL_PROFILES = {name: dict(entry.get("profiles", {})) for name, entry in registry.items()}
    AUTO_DISPATCH_ENABLED = bool(_LOADED_CONFIG_DEFAULTS.get("auto_dispatch", False))
    # task_185 per-tool automatic-dispatch substrate (unchanged, off by default). task_190a is
    # renderer-only (owner commit 48f9ae0): an integration declares NO dispatch descriptor.
    DISPATCH_DESCRIPTORS = {name: entry["dispatch"] for name, entry in registry.items() if entry.get("dispatch") is not None}
    for mod_name, mod in list(sys.modules.items()):
        if "ai_cli" in mod_name:
            setattr(mod, "TOOLS", TOOLS)
            setattr(mod, "TOOL_NOTES", TOOL_NOTES)
            setattr(mod, "TOOL_DEFAULTS", TOOL_DEFAULTS)
            setattr(mod, "TOOL_ROLES", TOOL_ROLES)
            setattr(mod, "COMMAND_TOOL_DEFAULTS", COMMAND_TOOL_DEFAULTS)
            setattr(mod, "GENERATED_DISPATCH_ROOT", GENERATED_DISPATCH_ROOT)
            setattr(mod, "ADAPTERS", ADAPTERS)
            setattr(mod, "TASK_CONTRACT_VOCABULARY", TASK_CONTRACT_VOCABULARY)
            setattr(mod, "AUTO_DISPATCH_ENABLED", AUTO_DISPATCH_ENABLED)
            setattr(mod, "DISPATCH_DESCRIPTORS", DISPATCH_DESCRIPTORS)


def generated_dispatch_root() -> Path:
    return constants.ROOT / GENERATED_DISPATCH_ROOT


def ensure_dirs() -> None:
    for path in [
        constants.AI,
        constants.AI / "tasks",
        constants.AI / "agents",
        constants.AI / "rules",
        constants.AI / "workflows",
        constants.AI / "skills",
        constants.AI / "memory",
        *(constants.ROOT / GENERATED_DISPATCH_ROOT / tool for tool in TOOLS),
        constants.AI / "migration",
    ]:
        path.mkdir(parents=True, exist_ok=True)
    for state in ("queue", "active", "done", "archive"):
        path = constants.AI / "tasks" / state
        path.mkdir(parents=True, exist_ok=True)
        gitkeep = path / ".gitkeep"
        if not gitkeep.exists():
            gitkeep.write_text("\n", encoding="utf-8")


def adapter_contract_paths() -> list[str]:
    """Return every configured generated-adapter path for scaffold protection."""
    paths = {rel(generated_dispatch_root()) + "/"}
    for adapter in ADAPTERS.values():
        output_root = constants.ROOT / adapter["output_dir"]
        paths.add(rel(output_root / adapter["detect_marker"]))
        if adapter["support_dir"]:
            paths.add(rel(constants.ROOT / adapter["support_dir"]) + "/")
    return sorted(paths)


def configured_command(tool: str, token: str) -> str:
    return ADAPTERS[tool]["commands"][token]


try:
    if (constants.AI / "config.yaml").exists():
        initialize_runtime_config()
except Exception:
    pass
