"""Refresh the reader's task data without reprojecting the corpus.

`ai docs build` rebuilds three peer truth systems and every rendered page. Almost all of that cost
is documents, project intelligence, and HTML rendering; projecting the tasks themselves is under a
second even for a few hundred of them. So reading a task you just finished should not require the
full projection, and waiting for several tasks to accumulate before rebuilding is a workaround for
a command that was missing rather than a cost anyone has to pay.

This replaces the tasks truth system inside the site that is already built, then rewrites the reader
payload through the same writer a full build uses, so the result is byte-identical in shape. Every
task is refreshed rather than one, because projecting all of them costs the same as projecting one
and a whole-system swap cannot leave the payload half-updated.

What it does NOT refresh: documents, project intelligence, the rendered document pages, the relation
graphs, and the search index. A task's own page is data-driven and current; a document that now
points at the task is not. Run `ai docs build` when those matter.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import scripts.ai_plane.constants as constants
from scripts.ai_plane.knowledge_projection import write_knowledge_assets
from scripts.ai_plane.knowledge_projection.common import (
    PROJECTION_SCHEMA_VERSION,
    aggregate_state,
    fingerprint,
    repository_revision,
)
from scripts.ai_plane.knowledge_projection.tasks import build_tasks
from scripts.ai_plane.utils import rel

READER_DATA = "reader-data.json"


def site_dir(ai: Path | None = None) -> Path:
    return (ai or constants.AI) / "_site"


def load_model(site: Path) -> dict[str, Any]:
    return json.loads((site / READER_DATA).read_text(encoding="utf-8"))


def task_ids(tasks: dict[str, Any]) -> set[str]:
    # `task_id`, not `id`: a projected task carries its contract under `contract`, and the contract
    # is where an `id` key lives. Reading `id` off the projection silently yields nothing.
    return {
        str(task.get("task_id"))
        for task in tasks.get("tasks", [])
        if isinstance(task, dict) and task.get("task_id")
    }


def refresh_tasks(model: dict[str, Any], root: Path) -> dict[str, Any]:
    """Swap in a freshly projected tasks truth system and restate what depends on it.

    The revision, the aggregate state, and the fingerprint are all derived from the whole model, so
    replacing a truth system without recomputing them would leave the payload describing itself
    incorrectly -- a stale fingerprint is worse than no fingerprint, because it reads as verified.
    """
    model["truth_systems"]["tasks_features"] = build_tasks(root)
    model["source"] = {
        **model.get("source", {}),
        **repository_revision(root),
        "state": aggregate_state([
            str(model["truth_systems"][name]["boundary"]["state"])
            for name in ("project_intelligence", "documents", "tasks_features")
        ]),
        "fingerprint": "",
    }
    model["source"]["fingerprint"] = fingerprint(model)
    return model


def cmd_docs_sync(args: argparse.Namespace, *, die) -> None:
    site = site_dir()
    if not (site / READER_DATA).is_file():
        die(
            f"No reader payload at {rel(site / READER_DATA)}. "
            "Run `ai docs build` once to create the site, then sync tasks into it."
        )

    model = load_model(site)
    if model.get("schema_version") != PROJECTION_SCHEMA_VERSION:
        # A payload from an older projection has a different shape; splicing one truth system into
        # it would produce a model that matches neither version.
        die(
            f"Reader payload is schema {model.get('schema_version')}, this plane builds "
            f"{PROJECTION_SCHEMA_VERSION}. Run `ai docs build` to reproject it."
        )

    before = task_ids(model["truth_systems"].get("tasks_features", {}))
    model = refresh_tasks(model, constants.ROOT)
    after = task_ids(model["truth_systems"]["tasks_features"])

    requested = getattr(args, "task_id", None)
    if requested and requested not in after:
        # Success-shaped: the payload was still refreshed, so report the miss without discarding it.
        print(f"Task not found: {requested}. The reader was refreshed anyway.")

    write_knowledge_assets(model, site)

    errors = model["truth_systems"]["tasks_features"].get("boundary", {}).get("errors") or []
    print(f"Reader tasks refreshed: {len(after)} task(s) in {rel(site / READER_DATA)}")
    if requested and requested in after:
        print(f"Task: {requested}")
    for label, ids in (("added", after - before), ("removed", before - after)):
        if ids:
            print(f"{label.capitalize()}: {', '.join(sorted(ids))}")
    if errors:
        print(f"Task projection errors: {len(errors)}")
        for error in errors[:5]:
            print(f"- {error}")
    print("Documents, graphs, and search are unchanged; run `ai docs build` to reproject those.")


def add_docs_sync_parser(docs_sub) -> None:
    sync = docs_sub.add_parser(
        "sync",
        help="Refresh the reader's task data in place, without reprojecting the whole corpus",
    )
    sync.add_argument(
        "task_id",
        nargs="?",
        help="Optional task id to confirm is present after the refresh (all tasks are refreshed)",
    )
