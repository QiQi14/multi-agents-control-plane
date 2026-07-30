#!/usr/bin/env python3
"""Build a PR Blueprint HTML report from a compact spec file."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

SCRIPT_DIR = Path(__file__).resolve().parent
BLUEPRINT_DIR = SCRIPT_DIR.parent
ROOT = SCRIPT_DIR.parents[3]
PRESET_DIR = BLUEPRINT_DIR / "presets"
CSS_PATH = BLUEPRINT_DIR / "templates" / "assets" / "blueprint.css"
LAYOUT_PATH = BLUEPRINT_DIR / "templates" / "layout.html"

sys.path.insert(0, str(SCRIPT_DIR))

from parse_spec import parse_spec, slugify  # noqa: E402
from render_html import render_report  # noqa: E402
from validate_spec import validate_spec  # noqa: E402


def build(spec_path: Path, out_path: Path | None = None) -> tuple[Path, list[str], list[str]]:
    spec, parse_errors = parse_spec(spec_path)
    errors, warnings, preset = validate_spec(spec, PRESET_DIR, parse_errors)
    if errors:
        print("PR Blueprint validation failed:")
        for error in errors:
            print(f"- {error}")
        raise SystemExit(1)

    title = spec["metadata"].get("title", spec_path.stem)
    output = out_path or ROOT / "docs" / "reports" / f"pr-blueprint-{slugify(title)}.html"
    output.parent.mkdir(parents=True, exist_ok=True)

    html, sections = render_report(spec, preset, spec_path, output, warnings, CSS_PATH, LAYOUT_PATH)
    output.write_text(html, encoding="utf-8")

    print(f"Generated report: {relative(output)}")
    print(f"Source spec: {relative(spec_path)}")
    print(f"Sections included: {', '.join(sections)}")
    print(f"Warnings: {len(warnings)}")
    for warning in warnings:
        print(f"- {warning}")
    return output, sections, warnings


def relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a spec-first PR Blueprint report")
    parser.add_argument("--spec", required=True, help="Path to .spec.md source file")
    parser.add_argument("--out", help="Optional output HTML path")
    args = parser.parse_args()

    build(Path(args.spec), Path(args.out) if args.out else None)


if __name__ == "__main__":
    main()
