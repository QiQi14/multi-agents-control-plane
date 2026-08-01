"""What `ai docs graph` says when it finishes.

It always wrote its artifacts to `.ai/_site/graphs/` -- hundreds of them -- and then printed the
requested SVG to stdout. So the visible result of a successful run was a screenful of markup and no
statement of what had been produced or where, which reads as a malfunction rather than as output.

The markup is still available, now on request: `--stdout` for a pipe, `--out` for a file. The
default is a report, because that is what the command actually did.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Callable

import scripts.ai_plane.constants as constants
from scripts.ai_plane.task_graph import (
    LIVE_LIFECYCLES,
    render_svg,
    summarize,
    write_task_graph,
)
from scripts.ai_plane.utils import rel

ALL_LIFECYCLES = ("queue", "active", "done", "archive")


def graphs_dir(ai: Path | None = None) -> Path:
    return (ai or constants.AI) / "_site" / "graphs"


def _tasks(root: Path | None = None) -> list[dict[str, Any]]:
    from scripts.ai_plane.knowledge_projection.tasks import build_tasks

    return build_tasks(root or constants.ROOT).get("tasks", [])


def cmd_docs_graph_cli(args: argparse.Namespace, *, emit: Callable[..., str]) -> None:
    out = getattr(args, "out", None)
    to_stdout = bool(getattr(args, "stdout", False))

    if getattr(args, "tasks", False):
        lifecycles = ALL_LIFECYCLES if getattr(args, "all", False) else LIVE_LIFECYCLES
        tasks = _tasks()
        svg = render_svg(tasks, lifecycles=lifecycles)
        if to_stdout:
            print(svg, end="")
            return
        path = Path(out) if out else None
        if path is not None:
            if not path.is_absolute():
                path = constants.ROOT / path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(svg, encoding="utf-8", newline="\n")
        else:
            path = write_task_graph(tasks, graphs_dir(), lifecycles=lifecycles)
        counts = summarize(tasks, lifecycles=lifecycles)
        scope = "all lifecycles" if getattr(args, "all", False) else "queue, active, done"
        print(f"Task hierarchy: {rel(path)}")
        print(f"  {counts['tasks']} task(s), {counts['dependencies']} dependency edge(s), "
              f"{counts['layers']} layer(s), {counts['roots']} with nothing to wait on")
        print(f"  scope: {scope}"
              + ("" if getattr(args, "all", False) else "  (use --all to include archive)"))
        return

    svg = emit(doc_id=getattr(args, "doc_id", None), domain=getattr(args, "domain", None))
    if to_stdout:
        print(svg, end="")
        return
    if out:
        path = Path(out)
        if not path.is_absolute():
            path = constants.ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(svg, encoding="utf-8", newline="\n")
        print(f"Graph: {rel(path)}")
    written = sorted(graphs_dir().glob("*.svg"))
    focus = getattr(args, "doc_id", None)
    print(f"Document relation graphs: {len(written)} SVG file(s) in {rel(graphs_dir())}")
    if focus:
        print(f"  focused on {focus}")
    print("  graph-global.svg is the whole corpus; graph-local-<id>.svg is one document")
    print("  --tasks draws the task dependency hierarchy; --stdout prints markup for a pipe")
