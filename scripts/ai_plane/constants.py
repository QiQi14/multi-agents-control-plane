from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AI = ROOT / ".ai"
WARNING = "<!-- GENERATED FROM .ai/. DO NOT EDIT DIRECTLY. Run `ai sync`. -->"
CATALOG_NOTE = (
    "The sections below are a **catalog**, not inlined text. Each entry gives a title, "
    "a one-line summary, and a path under `.ai/`. When a summary is relevant to the current "
    "task, open that file with your own file tools (read/grep) — do not preload every file, "
    "and do not ask the user to paste them; they are all in this repository. This keeps the "
    "always-loaded prompt small so the token budget is spent on the few files a task actually needs."
)
BLUEPRINT_DIR = AI / "templates" / "pr-blueprint"
