from __future__ import annotations

import argparse
import difflib
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import scripts.ai_plane.constants as constants
from scripts.ai_plane.primitives import TASK_STATES, parse_simple_yaml, task_list
from scripts.ai_plane.utils import die, rel, slugify


SECTION_TITLES: dict[str, str] = {
    "overview": "Overview",
    "execution_summary": "Execution Summary",
    "file_inventory": "File Inventory",
    "api": "API Endpoints",
    "websocket": "WebSocket Messages",
    "data_models": "Data Models",
    "validation": "Validation Matrix",
    "function_log": "Function Log",
    "state_matrix": "State Matrix",
    "ui_states": "Component States",
    "motion": "Motion Spec",
    "architecture": "Architecture Diagrams",
    "qa": "QA Checklist",
    "risks": "Risks",
    "open_questions": "Open Questions",
    "decisions": "Decisions",
    "notes": "Notes",
}

SECTION_BODIES: dict[str, str] = {
    "overview": "Describe the feature, tool, API, engine, or canvas behavior in a few paragraphs.\n",
    "execution_summary": "- Add execution evidence after implementation.\n",
    "file_inventory": "- Add the bounded file inventory.\n",
    "api": "- Replace or remove this section.\n",
    "websocket": "- Replace or remove this section.\n",
    "data_models": "- Replace or remove this section.\n",
    "validation": "- Field: example\n  Type: String\n  Rules:\n    - Replace or remove this row\n  UI Behavior:\n    - Replace or remove this row\n  Test ID: val-example\n",
    "function_log": "- Name: example_function\n  Trigger: User action\n  Side Effects:\n    - Replace or remove this row\n",
    "state_matrix": "- State: default\n  Trigger: Initial load\n  Expected:\n    - Replace or remove this row\n",
    "ui_states": "- State: default\n  Surface: App, tool, engine, or canvas surface\n  Signals:\n    - Replace or remove this row\n  Expected:\n    - Replace or remove this row\n",
    "motion": "- Element: container\n  Property: opacity\n  Duration: 200ms\n",
    "architecture": "## Main Flow\n```mermaid\nflowchart LR\n    Input[Input] --> System[System]\n    System --> Output[Output]\n```\n",
    "qa": "- Replace with a concrete verification item.\n",
    "risks": "- Replace with a concrete risk, or remove this section.\n",
    "open_questions": "- Replace or remove this section.\n",
    "decisions": "- Replace or remove this section.\n",
    "notes": "Generated HTML is build output. Edit this spec and rebuild.\n",
}

SECTION_SNIPPETS: dict[str, str] = {
    section: f"# {SECTION_TITLES[section]}\n{body}"
    for section, body in SECTION_BODIES.items()
}

Run = Callable[..., subprocess.CompletedProcess[Any]]


def load_preset_sections(preset: str) -> list[str]:
    preset_file = constants.BLUEPRINT_DIR / "presets" / f"{preset}.yaml"
    if not preset_file.exists():
        preset_file = constants.BLUEPRINT_DIR / "presets" / "fullstack.yaml"
    if not preset_file.exists():
        return list(SECTION_SNIPPETS.keys())
    sections: list[str] = []
    in_sections = False
    for line in preset_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped == "sections:":
            in_sections = True
            continue
        if in_sections:
            if stripped.startswith("- "):
                sec = stripped[2:].strip()
                if sec != "metadata":
                    sections.append(sec)
            elif stripped and not stripped.startswith("#"):
                break
    return sections or [key for key in SECTION_SNIPPETS if key != "metadata"]


def blueprint_spec_template(
    feature: str,
    preset: str,
    kind: str,
    *,
    section_bodies: dict[str, str] | None = None,
    section_markers: dict[str, list[str]] | None = None,
) -> str:
    title = feature.strip()
    spec_id = f"spec-{slugify(title)}"
    sections = load_preset_sections(preset)
    parts = [
        "---",
        f"id: {spec_id}",
        "type: spec",
        "domain: general",
        "status: draft",
        "owner: system",
        f"preset: {preset}",
        f"title: {title}",
        f"kind: {kind}",
        "author: Agent",
        "reviewer: Human Reviewer",
        "platform: TBD",
        "version: v1",
        "relations: []",
        "---\n",
    ]
    bodies = section_bodies or {}
    markers = section_markers or {}
    for section in sections:
        if section not in SECTION_TITLES:
            continue
        block = [f"# {SECTION_TITLES[section]}"]
        block.extend(markers.get(section, []))
        body = bodies.get(section, SECTION_BODIES[section]).rstrip()
        if body:
            block.append(body)
        parts.append("\n".join(block) + "\n")
    return "\n".join(parts)


def task_candidates(root: Path | None = None) -> list[tuple[str, Path, dict[str, Any]]]:
    root = root or constants.ROOT
    rows: list[tuple[str, Path, dict[str, Any]]] = []
    task_root = root / ".ai" / "tasks"
    for state in TASK_STATES:
        for task_file in sorted((task_root / state).glob("*/task.yaml")):
            data = parse_simple_yaml(task_file)
            task_id = str(data.get("id") or task_file.parent.name)
            rows.append((task_id, task_file.parent, data))
    return rows


def resolve_task_source(
    task_id: str, root: Path | None = None
) -> tuple[Path | None, dict[str, Any], list[str]]:
    rows = task_candidates(root)
    exact = [row for row in rows if row[0] == task_id or row[1].name == task_id]
    if len(exact) == 1:
        return exact[0][1], exact[0][2], []
    ids = sorted({row[0] for row in rows})
    candidates = difflib.get_close_matches(task_id, ids, n=5, cutoff=0.0)
    return None, {}, candidates


def git_name_inventory(
    base: str | None,
    *,
    root: Path = constants.ROOT,
    run: Run = subprocess.run,
) -> tuple[list[str], str | None]:
    if not base:
        return [], "--base was not supplied; no Git diff inventory was read."
    if base.startswith("-") or any(ord(character) < 32 for character in base):
        raise ValueError("Git base must be a revision value, not an option or control-bearing string")
    try:
        diff = run(
            ["git", "diff", "--no-ext-diff", "--no-textconv", "--name-only", "-z", base, "--"],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError(f"Unable to read Git diff from base {base!r}: {error}") from error
    if diff.returncode != 0:
        detail = diff.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"Unable to read Git diff from base {base!r}: {detail or 'git diff failed'}")
    try:
        untracked = run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError(f"Unable to read untracked Git paths: {error}") from error
    if untracked.returncode != 0:
        detail = untracked.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"Unable to read untracked Git paths: {detail or 'git ls-files failed'}")
    paths = set(_decode_nul_paths(diff.stdout)) | set(_decode_nul_paths(untracked.stdout))
    return sorted(paths), None

def _decode_nul_paths(payload: bytes) -> list[str]:
    return [value for value in payload.decode("utf-8", errors="surrogateescape").split("\0") if value]


def task_blueprint_spec(
    task_dir: Path,
    task: dict[str, Any],
    *,
    preset: str,
    kind: str,
    base: str | None,
    root: Path = constants.ROOT,
    run: Run = subprocess.run,
) -> str:
    title = str(task.get("title") or task.get("id") or task_dir.name)
    bodies: dict[str, str] = {}
    markers: dict[str, list[str]] = {}

    overview_parts: list[str] = []
    overview_sources: list[str] = []
    for heading, field in (("Task", "title"), ("Input Contract", "input_contract"), ("Output Contract", "output_contract")):
        value = task.get(field)
        if value:
            overview_parts.extend((f"## {heading}", _safe_prose(str(value)), ""))
            overview_sources.append(f"task.yaml#{field}")
    bodies["overview"] = "\n".join(overview_parts).strip() or "No overview fields were present in task.yaml."
    markers["overview"] = [_source_marker(overview_sources)] if overview_sources else [_needs_marker("task.yaml has no title or contract prose")]

    acceptance = task_list(task.get("acceptance_tests"))
    bodies["qa"] = _markdown_list(acceptance) if acceptance else "- No acceptance tests were present in task.yaml."
    markers["qa"] = [_source_marker(["task.yaml#acceptance_tests"])] if acceptance else [_needs_marker("task.yaml#acceptance_tests is absent")]

    risks = task_list(task.get("known_risks"))
    bodies["risks"] = _markdown_list(risks) if risks else "- No known risks were present in task.yaml."
    markers["risks"] = [_source_marker(["task.yaml#known_risks"])] if risks else [_needs_marker("task.yaml#known_risks is absent")]

    executor_path = task_dir / "receipt.executor.yaml"
    executor = parse_simple_yaml(executor_path)
    if executor:
        summary = _receipt_summary(executor, ("status", "agent", "tool", "branch", "base_commit", "test_result"))
        changed = task_list(executor.get("changed_files"))
        if changed:
            summary.append("Changed files: " + ", ".join(changed))
        bodies["execution_summary"] = _markdown_list(summary)
        markers["execution_summary"] = [_source_marker(["receipt.executor.yaml"])]
    else:
        bodies["execution_summary"] = "- Execution receipt was not present during extraction."
        markers["execution_summary"] = [_needs_marker("receipt.executor.yaml is absent")]

    qa_path = task_dir / "receipt.qa.yaml"
    qa = parse_simple_yaml(qa_path)
    if qa:
        decisions = _receipt_summary(qa, ("decision", "review_round", "reviewer", "review_tool"))
        bodies["decisions"] = _markdown_list(decisions)
        markers["decisions"] = [_source_marker(["receipt.qa.yaml"])]
    else:
        bodies["decisions"] = "- QA receipt was not present during extraction."
        markers["decisions"] = [_needs_marker("receipt.qa.yaml is absent")]

    inventory, inventory_note = git_name_inventory(base, root=root, run=run)
    if inventory:
        bodies["file_inventory"] = _markdown_list([f"Git base: {base}", *inventory])
        markers["file_inventory"] = [_source_marker(["git-diff", "git-untracked"])]
    else:
        bodies["file_inventory"] = f"- {inventory_note or 'The base-to-working-tree file inventory is empty.'}"
        reason = "--base is absent" if not base else "the base-to-working-tree inventory is empty"
        markers["file_inventory"] = [_needs_marker(reason)]

    for section in load_preset_sections(preset):
        if section not in bodies and section not in {"notes"}:
            markers[section] = [_needs_marker("no deterministic task source maps to this section")]
    bodies["notes"] = "Generated from repository facts. Confirm every needs-judgment marker before treating this spec as settled."
    markers["notes"] = [_source_marker(["task-extractor"])]

    return blueprint_spec_template(
        title,
        preset,
        kind,
        section_bodies=bodies,
        section_markers=markers,
    )


def _receipt_summary(data: dict[str, Any], fields: tuple[str, ...]) -> list[str]:
    rows: list[str] = []
    for field in fields:
        value = data.get(field)
        if value not in (None, "", []):
            label = field.replace("_", " ").title()
            rows.append(f"{label}: {value}")
    return rows or ["Receipt exists but contains no recognized summary fields."]


def _safe_prose(value: str) -> str:
    lines: list[str] = []
    for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.startswith("# "):
            line = "#" + line
        if line.strip().startswith("<!-- ai:"):
            line = "> " + line
        lines.append(line)
    return "\n".join(lines)


def _markdown_list(items: list[str]) -> str:
    rows = [" ".join(str(item).split()) for item in items]
    return "\n".join(f"- {row}" for row in rows if row)


def _source_marker(sources: list[str]) -> str:
    return f"<!-- ai:source {','.join(sources)} -->"


def _needs_marker(reason: str) -> str:
    safe_reason = reason.replace('"', "'")
    return f'<!-- ai:needs-judgment reason="{safe_reason}" -->'


def blueprint_output_path(feature: str, root: Path = constants.ROOT) -> Path:
    resolved_root = root.resolve()
    spec_dir = root / "docs" / "blueprints"
    resolved_dir = spec_dir.resolve()
    candidate = spec_dir / f"{slugify(feature)}.spec.md"
    resolved_candidate = (resolved_dir / candidate.name).resolve()
    try:
        resolved_dir.relative_to(resolved_root)
        resolved_candidate.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError("docs/blueprints resolves outside the repository root") from error
    return candidate

def cmd_blueprint_init(args: argparse.Namespace) -> None:
    from_task = getattr(args, "from_task", None)
    feature = getattr(args, "feature", None)
    base = getattr(args, "base", None)
    if from_task and feature:
        die("Choose either a feature name or --from-task, not both. No spec was written.")
    if base and not from_task:
        die("--base requires --from-task. No spec was written.")
    if not from_task and not feature:
        die("Provide a feature name or --from-task <task-id>. No spec was written.")

    preset = args.preset
    kind = args.kind
    if from_task:
        task_dir, task, candidates = resolve_task_source(from_task)
        if task_dir is None:
            print(f"Task not found: {from_task}. No blueprint spec was written.")
            if candidates:
                print("Candidate task ids:")
                for candidate in candidates:
                    print(f"- {candidate}")
            else:
                print("No task ids are available in queue, active, or done.")
            return
        feature = str(task.get("title") or task.get("id") or task_dir.name)
        try:
            content = task_blueprint_spec(task_dir, task, preset=preset, kind=kind, base=base)
        except ValueError as error:
            die(f"Blueprint extraction failed: {error}. No spec was written.")
    else:
        content = blueprint_spec_template(str(feature), preset, kind)

    try:
        path = blueprint_output_path(str(feature), constants.ROOT)
    except ValueError as error:
        die(f"Blueprint output refused: {error}. No spec was written.")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not args.force:
        die(f"Blueprint spec already exists: {rel(path)}. Use --force to overwrite.")
    path.write_text(content, encoding="utf-8")
    print(f"Created blueprint spec: {rel(path)}")
    if from_task:
        print(f"Extracted from task: {from_task}")
    print(f"Build with: ai blueprint build {rel(path)}")


def cmd_blueprint_build(args: argparse.Namespace) -> None:
    renderer = constants.BLUEPRINT_DIR / "renderer" / "build_report.py"
    if not renderer.exists():
        die(f"Blueprint renderer not found: {rel(renderer)}")
    command = [sys.executable, str(renderer), "--spec", args.spec]
    if args.out:
        command.extend(["--out", args.out])
    result = subprocess.run(command, cwd=constants.ROOT)
    if result.returncode != 0:
        raise SystemExit(result.returncode)
