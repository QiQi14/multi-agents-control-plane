"""Index a TypeScript or JavaScript product into the Project Intelligence export contract.

The shipped indexers walked a Cargo workspace or a Python package tree, so a Node product had no
graph at all and the reader reported Project Intelligence unavailable forever. This produces the
same contract from the standard library alone, so it needs no `node`, no `tsc`, no install step,
and no network -- which is what the plane promises about itself.

What it deliberately does NOT do is resolve calls. A regex cannot follow TypeScript imports,
aliases, overloads, re-exports, or dynamic dispatch, and a graph people navigate by is worse for an
invented edge than for an honest gap. Every unresolved reference becomes a pending boundary, and
`omissions.call_edges` states plainly that relations were not attempted. A compiler-backed adapter
can add them later without changing the contract.

Structure comes from the filesystem: a package is a directory with a manifest, a module is the
containing directory, and declarations are found with anchored patterns rather than a parser.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from scripts.ai_plane.products import EXCLUDED_DIR_NAMES

CONTRACT_VERSION = 2
SOURCE_SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
# Declaration files describe a shape that lives elsewhere; indexing them lists every symbol twice.
DECLARATION_SUFFIX = ".d.ts"
MAX_SOURCE_BYTES = 2_000_000

_MODIFIERS = r"(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:abstract\s+)?(?:async\s+)?"
_NAME = r"([A-Za-z_$][\w$]*)"
_DECLARATIONS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("class", re.compile(rf"^\s*{_MODIFIERS}class\s+{_NAME}", re.MULTILINE)),
    ("interface", re.compile(rf"^\s*{_MODIFIERS}interface\s+{_NAME}", re.MULTILINE)),
    ("enum", re.compile(rf"^\s*{_MODIFIERS}(?:const\s+)?enum\s+{_NAME}", re.MULTILINE)),
    ("type", re.compile(rf"^\s*{_MODIFIERS}type\s+{_NAME}\s*[=<]", re.MULTILINE)),
    ("function", re.compile(rf"^\s*{_MODIFIERS}function\s*\*?\s*{_NAME}", re.MULTILINE)),
    # `export const Foo = () => {}` and `export const Foo = function ...` are the dominant shape in
    # a React/FSD codebase; a plain `const x = 1` is a value, not a declaration worth a graph node.
    ("function", re.compile(
        rf"^\s*(?:export\s+)?(?:const|let|var)\s+{_NAME}\s*(?::[^=\n]+)?=\s*"
        r"(?:async\s+)?(?:function\b|\([^)]*\)\s*(?::[^=>\n]+)?=>|[A-Za-z_$][\w$]*\s*=>)",
        re.MULTILINE)),
)
_IMPORT = re.compile(r"""^\s*(?:import|export)\b[^'"\n]*from\s*['"]([^'"]+)['"]""", re.MULTILINE)
_REQUIRE = re.compile(r"""\brequire\s*\(\s*['"]([^'"]+)['"]\s*\)""")


def node_id(path: str, kind: str, name: str, line: int) -> str:
    """CodeGraph's identity scheme, so two indexers never disagree about what a node is."""
    digest = hashlib.sha256(f"{path}:{kind}:{name}:{line}".encode("utf-8")).hexdigest()
    return f"{kind}:{digest[:32]}"


def _purpose(value: str, provenance: str) -> dict[str, Any]:
    return {"value": value, "provenance": provenance} if value else {
        "value": "", "provenance": "unavailable"}


def _leading_doc_comment(text: str, offset: int) -> str:
    """The first line of a `/** ... */` block immediately above a declaration, if there is one."""
    head = text[:offset].rstrip()
    if not head.endswith("*/"):
        return ""
    start = head.rfind("/**")
    if start == -1:
        return ""
    body = head[start + 3:-2]
    for raw in body.splitlines():
        line = raw.strip().lstrip("*").strip()
        if line and not line.startswith("@"):
            return line
    return ""


def _iter_sources(base: Path, root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
            continue
        if path.name.endswith(DECLARATION_SUFFIX):
            continue
        relative = path.relative_to(root)
        if any(part in EXCLUDED_DIR_NAMES for part in relative.parts):
            continue
        files.append(path)
    return files


def _package_purpose(manifest: Path) -> str:
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    description = data.get("description") if isinstance(data, dict) else None
    return description.strip() if isinstance(description, str) else ""


def _declarations(text: str, rel: str, unit_key: str, module: str) -> list[dict[str, Any]]:
    """Every declaration in one file, at most one node per (name, line).

    The patterns in the table are currently disjoint, so nothing is deduplicated today. This is
    insurance for the next pattern added, where an overlap would silently inflate every count
    downstream -- and it is deliberately unasserted, because no input can make it fire yet. A test
    for it would be coverage that cannot fail.
    """
    line_starts = [0]
    for index, char in enumerate(text):
        if char == "\n":
            line_starts.append(index + 1)

    def position(offset: int) -> tuple[int, int]:
        low, high = 0, len(line_starts) - 1
        while low < high:
            mid = (low + high + 1) // 2
            if line_starts[mid] <= offset:
                low = mid
            else:
                high = mid - 1
        return low + 1, offset - line_starts[low]

    seen: set[tuple[str, int]] = set()
    nodes: list[dict[str, Any]] = []
    for kind, pattern in _DECLARATIONS:
        for match in pattern.finditer(text):
            name = match.group(1)
            row, column = position(match.start(1))
            if (name, row) in seen:
                continue
            seen.add((name, row))
            nodes.append({
                "id": node_id(rel, kind, name, row),
                "path": rel,
                "kind": kind,
                "identity_name": name,
                "qualified_name": name,
                "unit_name": unit_key,
                "module_path": module,
                "start_row": row,
                "start_column": column,
                "end_row": row,
                "end_column": column + len(name),
                "purpose": _purpose(_leading_doc_comment(text, match.start()), "authored-doc-comment"),
            })
    nodes.sort(key=lambda item: (item["start_row"], item["identity_name"]))
    return nodes


def build_export(root: Path, packages: list[dict[str, str]]) -> dict[str, Any]:
    """Index the given packages and return a contract-2 export.

    `packages` is [{"name": ..., "relative_path": ...}] as discovered from manifests, so this
    function never decides on its own what a package is.
    """
    package_records: list[dict[str, Any]] = []
    modules: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    hierarchy: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    claimed: set[str] = set()

    ordered = sorted(packages, key=lambda item: -len(item["relative_path"]))
    for entry in ordered:
        relative_root = entry["relative_path"]
        base = root if relative_root == "." else root / relative_root
        if not base.is_dir():
            continue
        unit = entry["name"]
        unit_key = re.sub(r"[^A-Za-z0-9]+", "_", unit).strip("_") or "package"
        prefix = "" if relative_root == "." else relative_root + "/"
        manifest = base / "package.json"
        owned: list[Path] = []
        for path in _iter_sources(base, root):
            rel = path.relative_to(root).as_posix()
            # A workspace member's files belong to the member, not to the root that lists it.
            if rel in claimed:
                continue
            claimed.add(rel)
            owned.append(path)
        if not owned:
            continue
        package_records.append({
            "package_id": relative_root,
            "manifest_path": (manifest.relative_to(root).as_posix() if manifest.is_file()
                              else relative_root),
            "display_name": unit,
            "symbol_namespace": unit_key,
            "purpose": _purpose(_package_purpose(manifest) if manifest.is_file() else "",
                                "authored-package-manifest"),
            "related_product_document_ids": [],
        })
        for path in owned:
            rel = path.relative_to(root).as_posix()
            raw = path.read_bytes()
            if len(raw) > MAX_SOURCE_BYTES:
                pending.append({"owner_path": rel, "source_node_id": "", "spelling": rel,
                                "reason": "oversized", "start_row": 0, "start_column": 0})
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                pending.append({"owner_path": rel, "source_node_id": "", "spelling": rel,
                                "reason": "undecodable", "start_row": 0, "start_column": 0})
                continue
            inner = rel[len(prefix):] if prefix and rel.startswith(prefix) else rel
            module = "/".join(inner.split("/")[:-1]) or "(root)"
            declared = _declarations(text, rel, unit_key, module)
            nodes.extend(declared)
            modules.append({
                "path": rel, "unit_name": unit_key, "module_path": module,
                "purpose": _purpose("", "unavailable"),
            })
            files.append({
                "path": rel, "size_bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "unit_name": unit_key, "module_path": module,
            })
            hierarchy.append({
                "unit_name": unit_key, "module_path": module, "path": rel,
                "semantic_node_ids": [item["id"] for item in declared],
            })
            # Every import is a real dependency this indexer cannot resolve to a definition.
            # Recording it as a boundary is the honest form; inventing an edge is not.
            for pattern in (_IMPORT, _REQUIRE):
                for match in pattern.finditer(text):
                    spelling = match.group(1)
                    pending.append({
                        "owner_path": rel, "source_node_id": "", "spelling": spelling,
                        "reason": "unresolved-module-reference",
                        "start_row": text.count("\n", 0, match.start(1)) + 1, "start_column": 0,
                    })

    return {
        "contract_version": CONTRACT_VERSION,
        "source_fingerprint": hashlib.sha256(
            "".join(sorted(item["sha256"] for item in files)).encode("utf-8")).hexdigest(),
        "packages": sorted(package_records, key=lambda item: item["package_id"]),
        "modules": modules,
        "files": files,
        "semantic_nodes": nodes,
        "semantic_hierarchy": hierarchy,
        # Structural only, and said so out loud rather than left to be inferred from an empty list.
        "relations": [],
        "pending_boundaries": pending,
        "omissions": {
            "call_edges": (
                "not attempted: a structural index cannot resolve TypeScript imports, aliases, "
                "overloads, or dynamic dispatch, and an invented relation is worse than a stated "
                "gap. Every module reference is recorded as a pending boundary."
            ),
            "packages_missing_description": [
                item["display_name"] for item in package_records
                if not item["purpose"]["value"]],
            "modules_missing_authored_purpose": [item["path"] for item in modules],
        },
    }
