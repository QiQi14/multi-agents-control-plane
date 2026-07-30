"""One-step task report export.

The docs projection and the PR Blueprint solve two halves of the same problem: the projection makes
the whole corpus browsable *inside* the repository, and a blueprint produces one self-contained file
for a reader who has no checkout -- a reviewer on a merge request, or a stakeholder.

What did NOT need to be two things was the command surface. Producing a report for a task used to
mean authoring a spec file, then building it. `ai docs export <task_id>` does both in one step and
writes the result where the reader can link it, so the report is a property of the task rather than
a separate artifact the user has to remember to maintain.

Hand-authored specs keep their own path: `ai blueprint init` / `ai blueprint build` are still how a
settled review specification is written and rendered when the judgement content is the point.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import scripts.ai_plane.constants as constants
from scripts.ai_plane.blueprint import resolve_task_source, task_blueprint_spec
from scripts.ai_plane.utils import rel, slugify

REPORTS_DIRNAME = "reports"
# Kept in step with `ai blueprint init`, so the two entry points cannot drift into producing
# differently-shaped reports for the same task.
PRESETS = ("frontend", "backend", "fullstack", "api-only", "engine", "tool")
DEFAULT_PRESET = "fullstack"
DEFAULT_KIND = "app"


def reports_root(ai: Path | None = None) -> Path:
    """Reports live inside the generated site so the reader can link them directly."""
    return (ai or constants.AI) / "_site" / REPORTS_DIRNAME


def report_path(task_id: str, ai: Path | None = None) -> Path:
    return reports_root(ai) / f"{slugify(task_id)}.html"


def build_report(spec_path: Path, out_path: Path) -> int:
    """Render one spec through the blueprint renderer. Returns its exit status."""
    renderer = constants.BLUEPRINT_DIR / "renderer" / "build_report.py"
    if not renderer.exists():
        raise FileNotFoundError(f"Blueprint renderer not found: {rel(renderer)}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, str(renderer), "--spec", str(spec_path), "--out", str(out_path)],
        cwd=constants.ROOT,
    )
    return result.returncode


def cmd_docs_export(args: argparse.Namespace, *, die) -> None:
    task_id = args.task_id
    task_dir, task, candidates = resolve_task_source(task_id)
    if task_dir is None:
        # Success-shaped: an unknown id is an ordinary mistake, so name the alternatives instead of
        # failing with a bare error the caller has to go investigate.
        print(f"Task not found: {task_id}. No report was written.")
        if candidates:
            print("Candidate task ids:")
            for candidate in candidates:
                print(f"- {candidate}")
        else:
            print("No task ids are available in queue, active, or done.")
        return

    try:
        spec_text = task_blueprint_spec(
            task_dir, task,
            preset=getattr(args, "preset", None),
            kind=getattr(args, "kind", None),
            base=getattr(args, "base", None),
        )
    except ValueError as error:
        die(f"Report extraction failed: {error}. No report was written.")

    resolved_id = str(task.get("id") or task_dir.name)
    out = Path(args.out) if getattr(args, "out", None) else report_path(resolved_id)
    if not out.is_absolute():
        out = constants.ROOT / out

    # The spec is an intermediate here, not an artifact: a task report is derived from the task
    # contract and receipts, so persisting a copy would immediately drift from its source.
    with tempfile.TemporaryDirectory(prefix="maw-docs-export-") as temp:
        spec_path = Path(temp) / f"{slugify(resolved_id)}.spec.md"
        spec_path.write_text(spec_text, encoding="utf-8")
        code = build_report(spec_path, out)
    if code:
        raise SystemExit(code)
    print(f"Report: {rel(out)}")
    print(f"Task: {resolved_id}")


def add_docs_export_parser(docs_sub) -> None:
    export = docs_sub.add_parser(
        "export",
        help="Render one task to a self-contained HTML report (contract + receipts + evidence)",
    )
    export.add_argument("task_id", help="Task id or directory name in queue, active, or done")
    export.add_argument("--out", help="Output path (default: .ai/_site/reports/<task-id>.html)")
    export.add_argument("--base", help="Base commit for the changed-file inventory")
    export.add_argument("--preset", default=DEFAULT_PRESET, choices=sorted(PRESETS),
                        help=f"Report section preset (default: {DEFAULT_PRESET})")
    export.add_argument("--kind", default=DEFAULT_KIND, help=f"Report kind (default: {DEFAULT_KIND})")
