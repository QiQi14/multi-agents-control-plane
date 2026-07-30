"""Thin, optional bridge from the control-plane CLI to the local ai-impact binary."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from scripts.ai_plane.constants import ROOT


def _binary_candidates(root: Path) -> list[Path]:
    name = "ai-impact.exe" if sys.platform == "win32" else "ai-impact"
    target = root / "tools" / "ai-impact" / "target"
    return [target / "release" / name, target / "debug" / name]


def cmd_impact(
    args: argparse.Namespace,
    *,
    root: Path = ROOT,
    run=subprocess.run,
    binary_candidates: list[Path] | None = None,
) -> None:
    candidates = _binary_candidates(root) if binary_candidates is None else binary_candidates
    binary = next((candidate for candidate in candidates if candidate.is_file()), None)
    if binary is None:
        print(
            "ai impact advisory unavailable: build tools/ai-impact first with "
            "`cargo build --manifest-path tools/ai-impact/Cargo.toml --locked`, then retry."
        )
        return
    database = Path(args.database)
    if not database.is_absolute():
        database = root / database
    if not database.is_file():
        initialize_command = subprocess.list2cmdline(
            [
                str(binary),
                "explore",
                "--manifest",
                args.manifest,
                "--database",
                args.database,
                args.symbol,
            ]
        )
        print(
            "ai impact advisory unavailable: initialize the index with "
            f"`{initialize_command}`, then retry."
        )
        return
    argv = [
        str(binary),
        "impact",
        "--manifest",
        args.manifest,
        "--database",
        args.database,
    ]
    if args.content_audit:
        argv.append("--content-audit")
    argv.append(args.symbol)
    try:
        result = run(argv, cwd=root, capture_output=True, check=False)
    except OSError as error:
        print(
            "ai impact advisory unavailable: could not launch the built binary "
            f"({error}); rebuild it and retry."
        )
        return
    if result.returncode != 0:
        raw_detail = result.stderr or result.stdout
        detail = (
            raw_detail.decode("utf-8", errors="replace").strip()
            if isinstance(raw_detail, bytes)
            else (raw_detail or "no diagnostic output").strip()
        )
        print(
            "ai impact advisory unavailable: query failed "
            f"({detail}); refresh the index and retry."
        )
        return
    if isinstance(result.stdout, bytes) and hasattr(sys.stdout, "buffer"):
        sys.stdout.buffer.write(result.stdout)
    elif isinstance(result.stdout, bytes):
        sys.stdout.write(result.stdout.decode("utf-8"))
    else:
        sys.stdout.write(result.stdout)
