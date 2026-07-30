from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import scripts.ai_plane.auto_dispatch as auto_dispatch
import scripts.ai_plane.config as config_module
import scripts.ai_plane.constants as constants
import scripts.ai_plane.routing_profile as tool_profile
from scripts.ai_plane.config import ensure_dirs
from scripts.ai_plane.manifest import generation_session, write_generated
from scripts.ai_plane.tasks import create_task, creation_commands, creation_routing, find_task, git_value, parse_simple_yaml, task_contract_vocabulary_violations, task_list, write_simple_yaml
from scripts.ai_plane.task_evidence import SCHEMA_VERSION, TaskEvidenceError, load_yaml, receipt_template
from scripts.ai_plane.utils import die, read_text, rel, slugify
from scripts.verify_core import to_posix_rel


def generated_prompt_pairs(
    root: Path,
    ai: Path,
    task_dir: Path,
    data: dict[str, Any],
    change_paths: set[str],
    manifest: dict[str, dict[str, str]],
) -> list[tuple[str, str, dict[str, str], dict[str, str]]]:
    """The tool-aware dispatch/review prompt-pair exemption, INJECTED into the config-free contract
    checker (scripts/verify_contract.py) as a callback. It needs the tool roster to reconstruct a
    generated prompt's command, so it lives here (the tool-aware layer), not in maw_core."""
    if ".ai/config.yaml" in change_paths:
        return []
    try:
        registry = config_module.load_tool_registry(ai / "config.yaml")
        adapters = config_module.build_tool_adapters(registry)
    except (config_module.ConfigError, ValueError):
        return []
    task_id = str(data.get("id", task_dir.name))
    task_root = to_posix_rel(root, task_dir)
    pairs: list[tuple[str, str, dict[str, str], dict[str, str]]] = []
    pattern = re.compile(
        r"^\.ai/adapters/([^/]+)/(dispatch|review)/" + re.escape(task_id) + r"\.prompt\.md$"
    )
    for adapter_path in sorted(change_paths):
        match = pattern.fullmatch(adapter_path)
        if not match:
            continue
        tool, purpose = match.groups()
        if tool not in registry:
            continue
        suffix = f"prompt.{tool}.md" if purpose == "dispatch" else f"prompt.{tool}.review.md"
        task_prompt = f"{task_root}/{suffix}"
        if task_prompt not in change_paths:
            continue
        adapter_file = root / adapter_path
        task_file = root / task_prompt
        if not adapter_file.is_file() or not task_file.is_file():
            continue
        adapter_bytes = adapter_file.read_bytes()
        task_bytes = task_file.read_bytes()
        if adapter_bytes != task_bytes:
            continue
        command = adapter_invocation(
            adapters[tool], purpose.upper(), task_id, "--tool", tool
        )
        digest = hashlib.sha256(adapter_bytes).hexdigest()
        adapter_entry = {"path": adapter_path, "sha256": digest, "command": command}
        task_entry = {"path": task_prompt, "sha256": digest, "command": command}
        if manifest.get(adapter_path) != adapter_entry or manifest.get(task_prompt) != task_entry:
            continue
        pairs.append((adapter_path, task_prompt, adapter_entry, task_entry))
    return pairs



def format_bullets(items: list[str]) -> str:
    if not items:
        return "- None specified"
    return "\n".join(f"- `{item}`" for item in items)


def format_text_bullets(items: list[str]) -> str:
    if not items:
        return "- None specified"
    return "\n".join(f"- {item}" for item in items)


def prompt_common(task_dir: Path, data: dict[str, Any], tool: str, purpose: str) -> str:
    task_id = data.get("id", task_dir.name)
    branch = git_value("branch", "--show-current")
    base_commit = git_value("rev-parse", "HEAD")
    brief = read_text(task_dir / "brief.md", "_No brief.md found._")
    context = read_text(task_dir / "context.md", "_No context.md found._")
    commands = task_list(data.get("commands"))
    targets = task_list(data.get("target_files"))
    forbidden = task_list(data.get("forbidden_files"))
    dependencies = task_list(data.get("depends_on"))
    acceptance_tests = task_list(data.get("acceptance_tests"))
    known_risks = task_list(data.get("known_risks"))
    isolation = data.get("isolation_strategy") or config_module.TOOL_DEFAULTS.get(tool, "patch")
    routing_axes = task_list(data.get("routing_axes"))
    routing_summary = (
        f"- Policy version: `{data.get('routing_policy_version', '')}`\n"
        f"- Zone: `{data.get('routing_zone', '')}`\n"
        f"- Complexity band: `{data.get('routing_complexity_band', '')}`\n"
        f"- Profile: `{data.get('routing_profile_tool', '')}/{data.get('routing_profile', '')}`\n"
        f"- Reasoning level: `{data.get('routing_reasoning_level', '')}`\n"
        f"- Assignment provenance: `{data.get('routing_provenance', '')}`\n"
        f"- Availability provenance: `{data.get('assignment_availability_provenance', '')}`\n"
        f"- Resolution: `{data.get('assignment_resolution_selector', '')}`\n"
        f"- Rationale: {data.get('routing_rationale') or data.get('assignment_rationale', '')}\n"
        f"- Axes:\n{format_text_bullets(routing_axes)}"
    )

    if tool not in config_module.TOOL_NOTES:
        die(f"Tool '{tool}' is not declared in .ai/config.yaml")
    tool_note = config_module.TOOL_NOTES[tool]
    if purpose == "review":
        tool_note = (
            f"Use {tool} for an independent, read-only review of the supplied contract and exact diff. "
            "Reviewer authority does not include implementation changes. Review the declared feature "
            "and evidence first; record an unrelated platform or security concern as one concise "
            "nonblocking follow-up unless it directly violates an acceptance criterion or demonstrates "
            "concrete destructive loss."
        )
    receipt = "receipt.executor.yaml" if purpose == "dispatch" else "receipt.qa.yaml"
    if purpose == "review":
        target_heading = "Files Under Review (Read-Only Evidence)"
        target_authority = (
            "Product targets in this set are read-only evidence. Do not edit product targets, do not "
            "implement fixes, and do not modify task-contract inputs during this review. The only "
            "authorized write is the QA receipt named below."
        )
        receipt_instruction = (
            "Write the schema-versioned immutable QA receipt below and tie it to the exact reviewed "
            "revision and diff. Record every finding, risk, limitation, observation, and follow-up as "
            "a typed context item; acceptance never erases a nonblocking item. Do not overwrite an earlier round."
        )
        next_handoff = (
            "- Return the read-only findings and QA receipt to the owner.\n"
            "- If changes are required, request a separate executor revision; do not make reviewer edits."
        )
        commands_authority = (
            "The commands above are contract evidence. Run only the read-only verification commands "
            "needed for review; do not execute mutating implementation steps."
        )
    else:
        target_heading = "Files You May Edit"
        target_authority = "Edit only the target paths below and preserve every forbidden path."
        receipt_instruction = (
            "Write the schema-versioned immutable executor receipt below and tie its commands, evidence "
            "references, typed context items, and tests to the exact implementation diff."
        )
        next_handoff = (
            "- Executor dispatch hands off to review or QA with diff, tests, and receipt.\n"
            "- Review/QA checks scope, contract, tests, risks, and knowledge to capture."
        )
        commands_authority = "Run the applicable commands and record their actual results in the executor receipt."

    return f"""{constants.WARNING}

# {purpose.title()} Prompt: {task_id}

## Tool

{tool}

{tool_note}

The selected tool's job is defined by this task brief and context, not by a fixed global phase assignment.

## Working Directory

`.`

## Current Git Context

- Branch: `{branch}`
- Base commit: `{base_commit}`

## Isolation Strategy

`{isolation}`

If this conflicts with the selected tool's safety profile, stop and ask before changing workspace topology. Worktrees are not default.

## Task Contract

- Task ID: `{task_id}`
- Title: {data.get("title", "")}
- Feature: {data.get("feature", "")}
- Status: {data.get("status", "")}
- Risk: `{data.get("risk", "")}`
- Preferred tool: `{data.get("preferred_tool", "")}`
- Review tool: `{data.get("review_tool", "")}`

## Routing Assignment

{routing_summary}

## Transport Boundary

This file is a manual handoff artifact. Rendering it does not launch a provider, submit a prompt, or prove that work started. A deeplink may prefill a composer only; a human must press Send. Any automatic launch is a separately requested transport action and is never submission evidence.

## Dependencies

{format_text_bullets(dependencies)}

## Input Contract

{data.get("input_contract", "")}

## Output Contract

{data.get("output_contract", "")}

## Acceptance Tests

{format_text_bullets(acceptance_tests)}

## Known Risks

{format_text_bullets(known_risks)}

## {target_heading}

{format_bullets(targets)}

{target_authority}

## Files You Must Not Edit

{format_bullets(forbidden)}

## Commands

{format_bullets(commands)}

{commands_authority}

## Brief

{brief}

## Context

{context}

## Required Receipt

{receipt_instruction}

Write `{receipt}` in the task folder:

`{rel(task_dir / receipt)}`

## Next Handoff

{next_handoff}
"""


def adapter_invocation(adapter: dict[str, Any], token: str, *arguments: str) -> str:
    separator = adapter["invoke_separator"]
    return separator.join([adapter["commands"][token], *arguments])


def write_dispatch_record(task_dir: Path, task_id: str, tool: str, outcome: auto_dispatch.LaunchOutcome) -> None:
    """Task evidence, not a generated/manifest-tracked file: which lane dispatch actually used."""
    record: dict[str, Any] = {
        "task_id": task_id,
        "tool": tool,
        "auto_requested": "true",
        "lane_used": outcome.lane,
        "attempted": "true" if outcome.attempted else "false",
        "success": "true" if outcome.success else "false",
        "detail": outcome.detail,
    }
    if outcome.argv is not None:
        record["argv"] = outcome.argv
    if outcome.url is not None:
        record["url"] = outcome.url
    write_simple_yaml(task_dir / "dispatch-record.yaml", record)


def cmd_dispatch(args: argparse.Namespace) -> None:
    task_dir, data = find_task(args.task_id)
    task_contract_vocabulary_violations(task_dir, data)
    explicit_tool = args.tool is not None
    tool = args.tool or data.get("preferred_tool")
    if tool == config_module.PENDING_ASSIGNMENT:
        die(f"Task assignment pending; run ai route explain {args.task_id}, then ai route apply {args.task_id}.")
    if tool not in config_module.TOOLS:
        die(f"Unknown tool: {tool}")
    task_id = data.get("id", task_dir.name)
    if not explicit_tool:
        tool_profile.require_enabled_tool(tool, config_module.TOOLS)
    prompt = prompt_common(task_dir, data, tool, "dispatch")
    task_prompt = task_dir / f"prompt.{tool}.md"
    adapter_prompt = config_module.generated_dispatch_root() / tool / "dispatch" / f"{task_id}.prompt.md"
    command = adapter_invocation(config_module.ADAPTERS[tool], "DISPATCH", str(task_id), "--tool", tool)
    with generation_session(command):
        write_generated(task_prompt, prompt)
        write_generated(adapter_prompt, prompt)
    print(f"Prompt written: {rel(task_prompt)}")
    print(f"Adapter copy: {rel(adapter_prompt)}")

    if getattr(args, "auto", False):
        tool_profile.require_enabled_tool(tool, config_module.TOOLS)
        outcome = auto_dispatch.perform_auto_dispatch(tool, str(task_id), task_prompt, prompt)
        write_dispatch_record(task_dir, task_id, tool, outcome)
        if outcome.success:
            print(f"Auto-dispatch: launched {tool} via the {outcome.lane} lane.")
        else:
            print(
                f"Auto-dispatch unavailable ({outcome.detail}); the prompt files above are ready — "
                "use the manual handoff."
            )


def cmd_review(args: argparse.Namespace) -> None:
    task_dir, data = find_task(args.task_id)
    task_contract_vocabulary_violations(task_dir, data)
    tool = args.tool or data.get("review_tool")
    explicit_tool = args.tool is not None
    if tool == config_module.PENDING_ASSIGNMENT:
        die(f"Task assignment pending; run ai route explain {args.task_id}, then ai route apply {args.task_id}.")
    if tool not in config_module.TOOLS:
        die(f"Unknown tool: {tool}")
    if not explicit_tool:
        tool_profile.require_enabled_review_tool(tool, config_module.TOOLS)
    task_id = data.get("id", task_dir.name)
    prompt = prompt_common(task_dir, data, tool, "review")
    task_prompt = task_dir / f"prompt.{tool}.review.md"
    adapter_prompt = config_module.generated_dispatch_root() / tool / "review" / f"{task_id}.prompt.md"
    command = adapter_invocation(config_module.ADAPTERS[tool], "REVIEW", str(task_id), "--tool", tool)
    with generation_session(command):
        write_generated(task_prompt, prompt)
        write_generated(adapter_prompt, prompt)
    print(f"Review prompt written: {rel(task_prompt)}")
    print(f"Adapter copy: {rel(adapter_prompt)}")


def cmd_qa(args: argparse.Namespace) -> None:
    task_dir, data = find_task(args.task_id)
    task_contract_vocabulary_violations(task_dir, data)
    receipt = task_dir / "receipt.qa.yaml"
    if receipt.exists():
        try:
            existing = load_yaml(receipt)
        except TaskEvidenceError:
            existing = {}
        if existing.get("schema_version") == SCHEMA_VERSION and args.force:
            die("Versioned receipt events are immutable. Preserve the earlier round under a round-specific filename before creating the next receipt.qa.yaml.")
        if not args.force:
            print(f"QA receipt already exists: {rel(receipt)}")
            return
    template = receipt_template(
        str(data.get("id", task_dir.name)), "qa", str(data.get("review_tool", "fill-me")),
        git_value("rev-parse", "HEAD"),
    )
    receipt.write_text(json.dumps(template, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"QA receipt schema v1 template written: {rel(receipt)}")
def cmd_learn(args: argparse.Namespace) -> None:
    task_dir, data = find_task(args.task_id)
    qa_path = task_dir / "receipt.qa.yaml"
    try:
        qa = load_yaml(qa_path)
    except TaskEvidenceError:
        qa = parse_simple_yaml(qa_path)
    items = task_list(qa.get("knowledge_to_capture"))
    if not items:
        print("No knowledge_to_capture entries found in QA receipt.")
        return
    lessons = constants.AI / "memory" / "lessons.md"
    with lessons.open("a", encoding="utf-8") as fh:
        fh.write(f"\n## {data.get('id', task_dir.name)}\n\n")
        for item in items:
            fh.write(f"- {item}\n")
    print(f"Captured lessons in {rel(lessons)}")


def cmd_archive(args: argparse.Namespace) -> None:
    feature_slug = slugify(args.feature)
    archive_dir = constants.AI / "tasks" / "archive" / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{feature_slug}"
    archive_dir.mkdir(parents=True, exist_ok=False)
    moved = 0
    for state in ("done",):
        for task_file in sorted((constants.AI / "tasks" / state).glob("*/task.yaml")):
            data = parse_simple_yaml(task_file)
            if slugify(data.get("feature", "")) == feature_slug:
                shutil.move(str(task_file.parent), str(archive_dir / task_file.parent.name))
                moved += 1
    print(f"Archived {moved} done task(s) to {rel(archive_dir)}")


def cmd_research(args: argparse.Namespace) -> None:
    ensure_dirs()
    topic = args.topic
    tool, review_tool, routing_metadata = creation_routing(args)
    task_dir = create_task(
        title=f"Research {topic}",
        feature=topic,
        risk="medium",
        preferred_tool=tool,
        review_tool=review_tool,
        isolation_strategy="readonly-research",
        brief=(
            f"# Research Brief: {topic}\n\n"
            "Produce factual research artifacts only. Separate facts from recommendations.\n\n"
            "Required artifacts:\n"
            "- research_digest.md\n"
            "- api_surface.md\n"
            "- dependency_map.md\n"
            "- risk_map.md\n"
        ),
        context="Use .ai/workflows/research.md and .ai/agents/strategist.md.",
        target_files=[rel(constants.AI / "tasks" / "queue")],
        forbidden_files=["src/", "app/", "packages/", *config_module.adapter_contract_paths()],
        acceptance_tests=[
            "Facts cite exact files or mark uncertainty.",
            "Recommendations are not mixed into factual research artifacts.",
        ],
        commands=creation_commands(tool, ("DISPATCH", "<task_id>")),
        known_risks=["Readonly research may discover that planning needs additional source inspection."],
        routing_metadata=routing_metadata,
    )
    print(f"Created research task: {rel(task_dir)}")


def cmd_plan(args: argparse.Namespace) -> None:
    ensure_dirs()
    feature = args.feature
    planning_tool, review_tool, routing_metadata = creation_routing(args)
    task_dir = create_task(
        title=f"Plan {feature}",
        feature=feature,
        risk="medium",
        preferred_tool=planning_tool,
        review_tool=review_tool,
        isolation_strategy="readonly-research",
        brief=(
            f"# Planning Brief: {feature}\n\n"
            "Create concrete implementation task folders. Use risk tiers, explicit isolation, "
            "target files, forbidden files, acceptance tests, and review gates.\n\n"
            "If the input is only a short requirement, treat that brief as enough to begin planning. "
            "Brainstorm the workstreams, split the work, and assign tools without requiring the user "
            "to pre-design the workflow."
        ),
        context=(
            "Use .ai/workflows/planning.md, .ai/workflows/brief-intake.md, and the migration audit when relevant."
        ),
        target_files=[rel(constants.AI / "tasks")],
        forbidden_files=config_module.adapter_contract_paths(),
        acceptance_tests=[
            "Generated tasks include task.yaml, brief.md, and context.md.",
            "High-risk tasks require independent review and explicit merge approval.",
        ],
        commands=creation_commands(
            planning_tool, ("TASKS", None), ("DISPATCH", "<task_id>")
        ),
        known_risks=["A planner may need research artifacts before task boundaries are reliable."],
        routing_metadata=routing_metadata,
    )
    print(f"Created planning task: {rel(task_dir)}")
