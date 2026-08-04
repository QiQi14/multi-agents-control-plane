"""Take authority over a product's existing documentation, explicitly.

A repository adopted onto the control plane usually already has Markdown -- specs, runbooks, notes
written long before any of this. Two ways of handling that are both wrong. Ignoring it means only
the handful of schema-governed files ever reach the reader, and the rest quietly does not exist.
Registering it automatically assigns authority nobody granted: a two-year-old draft becomes a
governed document because it happened to be in a directory.

So this reports, and only writes when asked. `ai docs adopt` lists what is unregistered. With
`--write-baseline` it freezes those files as `legacy-untyped` at their current hash -- indexed and
findable, carrying no authority claim, and from then on any edit is real drift the linter will say
so about. Promoting one to a typed document stays a human act: add frontmatter.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import scripts.ai_plane.constants as constants
from scripts.ai_plane import products
from scripts.ai_plane.frontmatter import parse_frontmatter
from scripts.ai_plane.registry import (
    LEGACY_BASELINE_SCHEMA_VERSION,
    product_baseline_declared,
    product_document_roots,
)

BASELINE_REL = "project/product-doc-legacy-baseline.json"


def unregistered_product_documents(root: Path) -> list[dict[str, str]]:
    """Product Markdown with no registry frontmatter, newest-path-order stable."""
    found: list[dict[str, str]] = []
    for relative_root in product_document_roots(root):
        base = root / relative_root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.md")):
            relative = path.relative_to(root)
            if products.is_excluded(relative):
                continue
            try:
                raw = path.read_bytes()
                meta, _ = parse_frontmatter(raw.decode("utf-8-sig"))
            except (OSError, UnicodeDecodeError):
                continue
            if isinstance(meta, dict) and meta.get("id"):
                continue
            found.append({
                "path": relative.as_posix(),
                "sha256": hashlib.sha256(raw).hexdigest(),
            })
    return found


def write_baseline(root: Path, records: list[dict[str, str]]) -> Path:
    """Freeze the listed documents as legacy-untyped at their current bytes.

    Merges rather than replaces: a second adoption must not silently drop what the first froze.
    """
    path = root / ".ai" / BASELINE_REL
    existing: dict[str, str] = {}
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            for item in payload.get("documents", []):
                if isinstance(item, dict) and isinstance(item.get("path"), str):
                    existing[item["path"]] = str(item.get("sha256", ""))
        except (OSError, ValueError):
            existing = {}
    for record in records:
        existing.setdefault(record["path"], record["sha256"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": LEGACY_BASELINE_SCHEMA_VERSION,
        "documents": [{"path": key, "sha256": existing[key]} for key in sorted(existing)],
    }, indent=2) + "\n", encoding="utf-8")
    return path


def cmd_docs_adopt(args: Any = None, *, root: Path | None = None) -> int:
    root = root if root is not None else constants.ROOT
    roots = product_document_roots(root)
    if not roots:
        print("No product documentation root was discovered. Product docs live at "
              f"{products.PROJECTS_ROOT}/<product-id>/docs/.")
        return 0
    records = unregistered_product_documents(root)
    declared = product_baseline_declared(root)
    print(f"Product documentation roots: {', '.join(roots)}")
    print(f"Authority baseline: {'declared' if declared else 'not declared'} (.ai/{BASELINE_REL})")
    if not records:
        print("Every product document carries registry frontmatter. Nothing to adopt.")
        return 0
    print(f"\n{len(records)} unregistered document(s), carrying no authority:")
    for record in records:
        print(f"  {record['path']}")
    if not getattr(args, "write_baseline", False):
        print("\nThese are indexed by nobody and claim nothing. To make them findable as "
              "legacy-untyped\nat their current bytes, re-run with --write-baseline. To make one "
              "governed instead,\nadd registry frontmatter to it.")
        return 0
    written = write_baseline(root, records)
    print(f"\nFroze {len(records)} document(s) in {written.relative_to(root).as_posix()} as "
          "legacy-untyped.\nThey are now indexed, still claim no authority, and any edit from here "
          "is reported as drift.\nRegenerate the registry with: python scripts/ai_cli.py sync")
    return 0


def add_docs_adopt_parser(docs_sub: Any) -> None:
    parser = docs_sub.add_parser(
        "adopt",
        help="Report product Markdown that carries no registry authority (and optionally freeze it)",
    )
    parser.add_argument("--write-baseline", dest="write_baseline", action="store_true",
                        help="Freeze the listed documents as legacy-untyped at their current hash")
