from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import scripts.ai_plane.config as config_module
from scripts.ai_plane.knowledge_projection.task_presentation import (
    contains_source_locator,
    presentation_contract_violations,
)
import scripts.ai_plane.constants as constants
from scripts.ai_plane.config import ensure_dirs
from scripts.ai_plane.utils import die, now_iso, rel, slugify
from scripts.ai_plane.task_evidence import (
    SCHEMA_VERSION,
    TaskEvidenceError,
    load_yaml,
    validate_closeout,
)
from scripts.ai_plane.task_evidence_legacy import repository_task_artifact_violations
from scripts.ai_plane.primitives import (  # config-free primitives (task_181 Q181-5)
    TASK_STATES,
    parse_simple_yaml,
    strip_yaml_inline_comment,
    task_list,
    yaml_scalar,
)


# Optional task routing metadata (task_192a). The task-contract YAML subset is top-level only, so the
# routing vector is expressed as flat `routing_*` keys with `<axis>=<level>` axis entries rather than
# a nested block. Semantics and the legacy compatibility rule: `.ai/project/routing-taxonomy.md`.
ROUTING_CORE_FIELDS = ("routing_policy_version", "routing_zone", "routing_axes")
ROUTING_PROFILE_FIELDS = ("routing_profile_tool", "routing_profile", "routing_reasoning_level",
                          "routing_provenance", "routing_rationale")
ROUTING_FIELDS = ROUTING_CORE_FIELDS + ("routing_complexity_band",) + ROUTING_PROFILE_FIELDS
Violation = tuple[str, Any, tuple[str, ...]]


def add_creation_routing_arguments(*parsers: argparse.ArgumentParser) -> None:
    """Add explicit task-shape inputs; absence means assignment stays pending."""
    taxonomy = config_module.ROUTING_TAXONOMY or {"zones": {}, "axes": {}, "axis_levels": []}
    for parser in parsers:
        destinations = {action.dest for action in parser._actions}
        if "tool" not in destinations:
            parser.add_argument("--tool", choices=config_module.TOOLS)
        parser.add_argument("--review-tool", choices=config_module.TOOLS)
        parser.add_argument("--routing-zone", choices=tuple(taxonomy["zones"]))
        parser.add_argument("--routing-rationale")
        parser.add_argument(
            "--routing-axis", action="append", metavar="AXIS=LEVEL",
            help="Repeat once for every declared routing axis",
        )


def creation_routing(args: argparse.Namespace) -> tuple[str, str, dict[str, Any]]:
    zone = getattr(args, "routing_zone", None)
    axes = getattr(args, "routing_axis", None) or []
    tool = getattr(args, "tool", None)
    reviewer = getattr(args, "review_tool", None)
    rationale = getattr(args, "routing_rationale", None)
    if not any((zone, axes, tool, reviewer, rationale)):
        return config_module.PENDING_ASSIGNMENT, config_module.PENDING_ASSIGNMENT, {}
    if not (zone and axes and tool and reviewer and rationale):
        die(
            "Task assignment pending: explicit routing requires --tool, --review-tool, "
            "--routing-zone, --routing-rationale, and one --routing-axis for every declared axis; no keyword inference "
            "or partial assignment was written. Use route explain/apply after completing task.yaml."
        )
    taxonomy = _required_taxonomy()
    vector, violations = _axis_vector(
        axes, tuple(taxonomy["axes"]), tuple(taxonomy["axis_levels"])
    )
    if violations:
        die("Invalid explicit routing axes; declare every axis exactly once using AXIS=LEVEL.")
    return tool, reviewer, {
        "routing_policy_version": str(taxonomy["version"]),
        "routing_zone": zone,
        "routing_axes": [f"{axis}={vector[axis]}" for axis in taxonomy["axes"]],
        "assignment_rationale": rationale,
    }


def creation_commands(tool: str, *steps: tuple[str, str | None]) -> list[str]:
    if tool == config_module.PENDING_ASSIGNMENT:
        return [
            "python scripts/ai_cli.py route explain <task_id>",
            "python scripts/ai_cli.py route apply <task_id>",
        ]
    commands = ["python scripts/ai_cli.py route apply <task_id>"]
    for verb, argument in steps:
        command = config_module.configured_command(tool, verb)
        commands.append(command if argument is None else f"{command} {argument}")
    return commands


def write_simple_yaml(path: Path, data: dict[str, Any]) -> None:
    lines: list[str] = []
    for key, value in data.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {yaml_scalar(item)}")
        else:
            lines.append(f"{key}: {yaml_scalar(value)}")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def all_task_files() -> list[Path]:
    task_root = constants.AI / "tasks"
    files: list[Path] = []
    for state in TASK_STATES:
        files.extend(sorted((task_root / state).glob("*/task.yaml")))
    return files


def find_task(task_id: str) -> tuple[Path, dict[str, Any]]:
    for task_file in all_task_files():
        data = parse_simple_yaml(task_file)
        if data.get("id") == task_id or task_file.parent.name == task_id:
            return task_file.parent, data
    die(f"Task not found: {task_id}")


def profile_capabilities(tool: str, profile: str) -> tuple[str, ...]:
    """The effective capability set of one (tool, profile) pair: the tool's SURFACE capabilities plus
    the profile's MODEL capabilities, in declared catalog order."""
    taxonomy = _required_taxonomy()
    if profile not in config_module.TOOL_PROFILES.get(tool, {}):
        die(f"tool {tool!r} declares no execution profile {profile!r}")
    effective = (set(config_module.TOOL_CAPABILITIES.get(tool, ()))
                 | set(config_module.TOOL_PROFILES[tool][profile].get("capabilities", [])))
    return tuple(tag for tag in taxonomy["capability_tags"] if tag in effective)


def _required_taxonomy() -> dict[str, Any]:
    if config_module.ROUTING_TAXONOMY is None:
        die(f"{config_module.ROUTING_UNDECLARED}: {config_module.ROUTING_GUIDANCE}")
    return config_module.ROUTING_TAXONOMY


def derive_complexity_band(axis_levels: dict[str, str]) -> tuple[str, list[str]]:
    """Derive the named complexity band from a complete axis vector.

    Every rule is evaluated on its own — declaration order is irrelevant — and the highest matching
    band in the declared order wins. The result is an ORDERED LABEL with a stated derivation, never a
    scalar: no level is summed, averaged, weighted, or compared numerically across tasks.
    """
    taxonomy = _required_taxonomy()
    bands = taxonomy["complexity_bands"]
    order = list(bands["order"])
    levels = list(taxonomy["axis_levels"])
    band = bands["default"]
    reasons: list[str] = []
    for name, rule in bands["rules"].items():
        eligible = rule.get("restrict_to_axes", list(taxonomy["axes"]))
        threshold = levels.index(rule["at_or_above"])
        matched = sorted(axis for axis in eligible
                         if axis in axis_levels and levels.index(axis_levels[axis]) >= threshold)
        if len(matched) < rule["minimum_count"]:
            continue
        reasons.append(f"rule '{name}' matched: {', '.join(matched)} at or above "
                       f"'{rule['at_or_above']}' selects band '{rule['band']}'")
        if order.index(rule["band"]) > order.index(band):
            band = rule["band"]
    if not reasons:
        reasons.append(f"no rule matched; default band '{band}'")
    return band, reasons


def _declared(value: Any) -> bool:
    return value not in (None, "", [], {})


def _axis_vector(value: Any, axes: tuple[str, ...], levels: tuple[str, ...]) -> tuple[dict[str, str], list[Violation]]:
    """Parse `routing_axes` `<axis>=<level>` entries into a vector, reporting every closed-vocabulary
    violation. A missing, unknown, duplicated, or malformed axis fails closed; nothing is defaulted."""
    if not isinstance(value, list) or not value:
        return {}, [("routing_axes", value, tuple(f"{axis}=<{'|'.join(levels)}>" for axis in axes))]
    vector: dict[str, str] = {}
    violations: list[Violation] = []
    for entry in value:
        axis, separator, level = str(entry).partition("=")
        if not separator or axis not in axes:
            violations.append(("routing_axes", entry, axes))
        elif axis in vector:
            violations.append((f"routing_axes.{axis}", entry, ("<declared exactly once>",)))
        elif level not in levels:
            violations.append((f"routing_axes.{axis}", level, levels))
        else:
            vector[axis] = level
    violations.extend((f"routing_axes.{axis}", None, levels) for axis in axes if axis not in vector)
    return vector, violations


def routing_metadata_keys(data: dict[str, Any]) -> list[str]:
    """Every `routing_`-prefixed contract key, whether declared or misspelled. The whole prefix is
    reserved (R192A-2): presence is decided by key membership, never by a value being non-empty, so a
    typo or a blank value can never be mistaken for a legacy contract that declared nothing."""
    return sorted(key for key in data if isinstance(key, str) and key.startswith("routing_"))


def task_routing_violations(data: dict[str, Any]) -> list[Violation]:
    """Validate the OPTIONAL task routing metadata against the declared routing taxonomy.

    Compatibility rule: a contract declaring no `routing_`-prefixed key at all is legacy and stays
    fully readable — no zone, axis, band, or profile is ever guessed on its behalf. Declaring ANY
    such key (including an unknown one, or a known one left blank) makes the core vector required,
    and declaring any selected-profile key makes that group required, so partial, misspelled, or
    empty metadata fails closed instead of being silently completed.
    """
    present = routing_metadata_keys(data)
    if not present:
        return []
    violations: list[Violation] = [(key, data[key], ROUTING_FIELDS)
                                   for key in present if key not in ROUTING_FIELDS]
    taxonomy = config_module.ROUTING_TAXONOMY
    if taxonomy is None:
        return violations + [(key, data[key], (config_module.ROUTING_GUIDANCE,))
                             for key in present if key in ROUTING_FIELDS]

    version = str(taxonomy["version"])
    if str(data.get("routing_policy_version")) != version:
        violations.append(("routing_policy_version", data.get("routing_policy_version"), (version,)))
    zones = tuple(taxonomy["zones"])
    if data.get("routing_zone") not in zones:
        violations.append(("routing_zone", data.get("routing_zone"), zones))
    vector, axis_violations = _axis_vector(data.get("routing_axes"), tuple(taxonomy["axes"]),
                                           tuple(taxonomy["axis_levels"]))
    violations.extend(axis_violations)

    # A hand-written band is checked against the derivation, so a stale label can never drift past
    # review; it is only checkable once the vector itself is complete and valid.
    if "routing_complexity_band" in data and not axis_violations:
        derived, _reasons = derive_complexity_band(vector)
        if data["routing_complexity_band"] != derived:
            violations.append(("routing_complexity_band", data["routing_complexity_band"], (derived,)))

    if any(field in data for field in ROUTING_PROFILE_FIELDS):
        violations.extend(_selected_profile_violations(data, taxonomy))
    return violations


def _effective_capabilities(tool: str, profile_spec: dict[str, Any]) -> set[str]:
    return set(config_module.TOOL_CAPABILITIES.get(tool, ())) | set(profile_spec.get("capabilities", []))


def _selected_profile_violations(data: dict[str, Any], taxonomy: dict[str, Any]) -> list[Violation]:
    violations: list[Violation] = []
    tool = data.get("routing_profile_tool")
    if tool not in config_module.TOOLS:
        violations.append(("routing_profile_tool", tool, config_module.TOOLS))
    else:
        # The selected profile describes how the CANONICAL executor assignment will be run; it never
        # reassigns the work (R192A-4). A mismatch is a contradiction, not a second assignment.
        preferred = data.get("preferred_tool")
        if preferred in config_module.TOOLS and tool != preferred:
            violations.append(("routing_profile_tool", tool, (preferred,)))
    profiles = config_module.TOOL_PROFILES.get(tool, {})
    profile = data.get("routing_profile")
    if profile in profiles:
        supported = tuple(profiles[profile]["reasoning_levels"])
        if data.get("routing_reasoning_level") not in supported:
            violations.append(("routing_reasoning_level", data.get("routing_reasoning_level"), supported))
        # The zone's required capabilities are a HARD filter, so a selected profile that cannot offer
        # them fails closed (R192A-5). This checks the declared vector against the declared catalog;
        # it ranks nothing and prefers no tool.
        zone = taxonomy["zones"].get(data.get("routing_zone"))
        if zone is not None:
            effective = _effective_capabilities(tool, profiles[profile])
            missing = [tag for tag in zone["required_capabilities"] if tag not in effective]
            if missing:
                violations.append(("routing_profile", f"{tool}/{profile}",
                                   tuple(f"<a profile declaring {tag}>" for tag in missing)))
    else:
        violations.append(("routing_profile", profile,
                           tuple(profiles) or ("<this tool declares no execution profile>",)))
    provenance = tuple(taxonomy["provenance"])
    if data.get("routing_provenance") not in provenance:
        violations.append(("routing_provenance", data.get("routing_provenance"), provenance))
    if not _declared(data.get("routing_rationale")):
        violations.append(("routing_rationale", data.get("routing_rationale"),
                           ("<a human-readable reason for this profile>",)))
    return violations


def duplicate_contract_keys(task_dir: Path) -> list[Violation]:
    """Reject a task contract that declares one top-level key more than once (R192A-3).

    The contract parser keeps the LAST occurrence silently, so a duplicated key would let a second
    `routing_zone` (or any other field) override the reviewed one invisibly. This scans the raw
    contract before semantic validation and mirrors the parser's own top-level rule exactly — a line
    that starts at column zero, is not a comment, and contains a colon — so the two agree on what a
    top-level key is. Indented block-scalar and list content is skipped by both.
    """
    path = task_dir / "task.yaml"
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return []
    counts: dict[str, int] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or line.startswith((" ", "\t")) or ":" not in line:
            continue
        key = line.split(":", 1)[0].strip()
        counts[key] = counts.get(key, 0) + 1
    return [(key, f"<declared {count} times>", ("<declared exactly once>",))
            for key, count in sorted(counts.items()) if count > 1]


def task_contract_vocabulary_violations(
    task_dir: Path,
    data: dict[str, Any],
    *,
    fail: bool = True,
) -> list[tuple[str, Any, tuple[str, ...]]]:
    """Validate all required risk/tool fields, optionally returning violations for inventory."""
    task_id_value = data.get("id")
    task_id = task_id_value if isinstance(task_id_value, str) and task_id_value else task_dir.name
    violations: list[tuple[str, Any, tuple[str, ...]]] = []
    for field, allowed in config_module.TASK_CONTRACT_VOCABULARY.items():
        value = data[field] if field in data else None
        if not isinstance(value, str) or value not in allowed:
            violations.append((field, value, allowed))
    violations.extend(duplicate_contract_keys(task_dir))
    violations.extend(task_routing_violations(data))
    violations.extend(presentation_contract_violations(data))

    if fail and violations:
        details = "; ".join(
            f"field '{field}' rejected value {format_rejected_value(value)}; "
            f"allowed values: {', '.join(allowed)}"
            for field, value, allowed in violations
        )
        die(f"Task {task_id} has invalid task-contract vocabulary: {details}.")
    return violations


def format_rejected_value(value: Any) -> str:
    return "<missing>" if value is None else repr(value)


def next_task_id(label: str) -> str:
    max_num = 0
    for task_file in all_task_files():
        task_id = parse_simple_yaml(task_file).get("id", task_file.parent.name)
        match = re.match(r"task_(\d+)_", str(task_id))
        if match:
            max_num = max(max_num, int(match.group(1)))
    return f"task_{max_num + 1:02d}_{slugify(label)}"


def git_value(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=constants.ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=5,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def create_task(
    *,
    title: str,
    feature: str,
    risk: str,
    preferred_tool: str,
    review_tool: str,
    isolation_strategy: str,
    brief: str,
    context: str,
    target_files: list[str] | None = None,
    forbidden_files: list[str] | None = None,
    acceptance_tests: list[str] | None = None,
    commands: list[str] | None = None,
    known_risks: list[str] | None = None,
    routing_metadata: dict[str, Any] | None = None,
) -> Path:
    task_id = next_task_id(title)
    task_dir = constants.AI / "tasks" / "queue" / task_id
    data = {
        "id": task_id,
        "title": title,
        "feature": feature,
        "status": "queue",
        "risk": risk,
        "preferred_tool": preferred_tool,
        "review_tool": review_tool,
        "isolation_strategy": isolation_strategy,
        "depends_on": [],
        "target_files": target_files or [rel(task_dir)],
        "forbidden_files": forbidden_files or [],
        "input_contract": "See brief.md and context.md.",
        "output_contract": "Produce task artifacts and required receipt.",
        "acceptance_tests": acceptance_tests or ["Contract is explicit enough for dispatch."],
        "commands": commands or ["ai dispatch " + task_id + " --tool " + preferred_tool],
        "known_risks": known_risks or [],
    }
    data.update(routing_metadata or {})
    if not routing_metadata:
        data["assignment_guidance"] = (
            f"Assignment pending; add an explicit routing vector, then run ai route explain {task_id} "
            f"and ai route apply {task_id}. No keyword inference was used."
        )
    unsafe_identity = [
        field for field in ("title", "feature")
        if contains_source_locator(str(data.get(field) or ""))
    ]
    if unsafe_identity:
        die(
            f"Cannot scaffold {task_id}: human task identity fields must not expose repository "
            f"locators, exact commands, line anchors, file URLs, or revisions: "
            f"{', '.join(unsafe_identity)}."
        )
    task_dir.mkdir(parents=True, exist_ok=False)
    write_simple_yaml(task_dir / "task.yaml", data)
    (task_dir / "brief.md").write_text(brief.rstrip() + "\n", encoding="utf-8")
    (task_dir / "context.md").write_text(context.rstrip() + "\n", encoding="utf-8")
    (task_dir / "notes.md").write_text(f"# Notes\n\nCreated: {now_iso()}\n", encoding="utf-8")
    return task_dir


def cmd_tasks(_args: argparse.Namespace) -> None:
    rows: list[tuple[str, str, Any, Any, str, list[tuple[str, Any, tuple[str, ...]]]]] = []
    for task_file in all_task_files():
        data = parse_simple_yaml(task_file)
        state = task_file.parent.parent.name
        task_id_value = data.get("id")
        task_id = task_id_value if isinstance(task_id_value, str) and task_id_value else task_file.parent.name
        violations = task_contract_vocabulary_violations(task_file.parent, data, fail=False)
        rows.append(
            (
                state,
                task_id,
                data.get("risk"),
                data.get("preferred_tool"),
                str(data.get("title", "")),
                violations,
            )
        )
    if not rows:
        print("No task folders found.")
        return
    live_invalid_tasks = 0
    live_invalid_fields = 0
    for state, task_id, risk, tool, title, violations in rows:
        live = state in {"queue", "active"}
        violation_label = "INVALID" if live else "LEGACY INVALID"
        annotations = " ".join(
            f"[{violation_label} {field}={format_rejected_value(value)} allowed={'|'.join(allowed)}]"
            for field, value, allowed in violations
        )
        if live and violations:
            live_invalid_tasks += 1
            live_invalid_fields += len(violations)
        risk_text = format_inventory_value(risk)
        tool_text = format_inventory_value(tool)
        suffix = f" {annotations}" if annotations else ""
        print(f"{state:7} {task_id:32} risk={risk_text:6} tool={tool_text:12} {title}{suffix}")
    if live_invalid_fields:
        sys.stdout.flush()
        print(
            f"ERROR: inventory completed with {live_invalid_fields} invalid live field(s) across "
            f"{live_invalid_tasks} queue/active task(s); all {len(rows)} task(s) were listed.",
            file=sys.stderr,
        )
        raise SystemExit(1)


def format_inventory_value(value: Any) -> str:
    return "<missing>" if value is None else (value if isinstance(value, str) else repr(value))


def cmd_task_show(args: argparse.Namespace) -> None:
    task_dir, data = find_task(args.task_id)
    task_contract_vocabulary_violations(task_dir, data)
    print(f"Task: {data.get('id', task_dir.name)}")
    print(f"Path: {rel(task_dir)}")
    print(f"Title: {data.get('title', '')}")
    print(f"Risk: {data.get('risk', '')}")
    print(f"Preferred tool: {data.get('preferred_tool', '')}")
    print(f"Review tool: {data.get('review_tool', '')}")
    print(f"Isolation: {data.get('isolation_strategy', '')}")
    print("\nTarget files:")
    for item in task_list(data.get("target_files")):
        print(f"- {item}")
    print("\nForbidden files:")
    for item in task_list(data.get("forbidden_files")):
        print(f"- {item}")


def cmd_merge(args: argparse.Namespace) -> None:
    task_dir, data = find_task(args.task_id)
    task_contract_vocabulary_violations(task_dir, data)
    qa_path = task_dir / "receipt.qa.yaml"
    try:
        qa = load_yaml(qa_path)
    except TaskEvidenceError:
        qa = parse_simple_yaml(qa_path)
    versioned = qa.get("schema_version") == SCHEMA_VERSION
    decision = qa.get("decision", {}).get("status", "") if versioned else qa.get("decision", "")
    risk = data.get("risk", "")
    if decision != "accept":
        die("Task is not accepted by QA. Record an accepting QA receipt before merge.")
    if versioned:
        closeout_path = task_dir / "task-closeout.yaml"
        if not closeout_path.exists():
            die("Versioned task evidence requires task-closeout.yaml before merge; acceptance cannot erase context.")
        try:
            closeout_errors = validate_closeout(load_yaml(closeout_path), task_dir, constants.ROOT)
        except TaskEvidenceError as error:
            die(str(error))
        if closeout_errors:
            die("Task closeout failed: " + "; ".join(closeout_errors))
        artifact_errors = repository_task_artifact_violations(constants.ROOT)
        if artifact_errors:
            die("Task evidence audit failed: " + "; ".join(artifact_errors))
    if risk == "high" and not args.approved:
        die("High-risk task requires explicit merge approval. Re-run with --approved after approval.")
    print("Merge gate passed. Perform the repository merge using your normal git workflow.")
def cmd_feature_new(args: argparse.Namespace) -> None:
    ensure_dirs()
    title = args.title
    planning_tool, review_tool, routing_metadata = creation_routing(args)
    task_dir = create_task(
        title=f"Plan {title}",
        feature=title,
        risk="medium",
        preferred_tool=planning_tool,
        review_tool=review_tool,
        isolation_strategy="readonly-research",
        brief=(
            f"# Feature Planning Brief\n\n"
            f"Feature: {title}\n\n"
            "Treat this feature brief as sufficient intake even if the user did not provide a workflow.\n\n"
            "Create a task graph, dependencies, target files, forbidden files, risk tiers, "
            "acceptance criteria, preferred tools, review tools, and isolation strategies.\n\n"
            "Brainstorm likely workstreams and unknowns. If repository facts are missing, create a bounded "
            "research task first instead of guessing."
        ),
        context=(
            "# Planning Context\n\n"
            "Use .ai/project/, .ai/memory/, and any valid research artifacts. "
            "Do not assume legacy .agents workflows are authoritative.\n\n"
            "The user should not need to pre-assign agents. The planner is responsible for splitting the work "
            "into safe task contracts with non-overlapping writable scopes."
        ),
        target_files=[rel(constants.AI / "tasks")],
        forbidden_files=config_module.adapter_contract_paths(),
        acceptance_tests=[
            "Task graph is folder-based under .ai/tasks/.",
            "Every task has explicit risk and isolation strategy.",
            "No task assumes a git worktree by default.",
        ],
        commands=creation_commands(planning_tool, ("PLAN", slugify(title))),
        known_risks=["Planning may need additional repository research before implementation tasks are safe."],
        routing_metadata=routing_metadata,
    )
    print(f"Created feature planning task: {rel(task_dir)}")
