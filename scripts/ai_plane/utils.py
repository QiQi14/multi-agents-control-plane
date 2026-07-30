from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

import scripts.ai_plane.constants as constants


def configure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def die(message: str) -> NoReturn:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def rel(path: Path) -> str:
    try:
        return path.relative_to(constants.ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def slugify(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip()).strip("_").lower()
    return cleaned or "task"






def read_text(path: Path, fallback: str = "") -> str:
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return fallback


def read_md_dir(path: Path) -> list[tuple[str, str]]:
    if not path.exists():
        return []
    files = sorted(p for p in path.glob("*.md") if p.is_file())
    return [(p.name, p.read_text(encoding="utf-8").strip()) for p in files]


def generated_markdown(title: str, body: str) -> str:
    return f"{constants.WARNING}\n\n# {title}\n\n{body.strip()}\n"
