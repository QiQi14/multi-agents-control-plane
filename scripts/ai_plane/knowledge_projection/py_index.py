"""Index a Python repository into the Project Intelligence export contract.

The shipped Rust indexer walks a Cargo workspace, so a repository whose source is Python had no
index at all and the reader reported Project Intelligence unavailable forever. This produces the
same contract from the standard library alone -- `ast`, `hashlib`, `pathlib` -- so it needs no
toolchain, no network, and no build step, which is what the plane promises about itself.

Node identity follows CodeGraph's scheme, ``kind:sha256(path:kind:name:line)[:32]``: stable across
runs, dependent only on where a symbol is, and requiring no global counter that two indexers would
have to agree on.

What it deliberately does not do is resolve dynamically. A call is linked only when its name
resolves to exactly one indexed definition. Anything ambiguous or unknown becomes a pending
boundary rather than a guessed edge, because a wrong edge in a graph people navigate by is worse
than a missing one that says it is missing.
"""

from __future__ import annotations

import ast
import builtins
import hashlib
from pathlib import Path
from typing import Any

CONTRACT_VERSION = 2
SKIP_DIRS = {"__pycache__", ".git", ".ai", "node_modules", ".venv", "venv", "target", "_site"}
# A call to `len` is not an unresolved reference; it is resolved, to something outside the indexed
# set. Recording those as pending boundaries buried the real ones and made the index read as far
# less complete than it is. `builtins` is imported rather than reading `__builtins__`, which is a
# module here but a dict inside an imported module.
_BUILTINS = frozenset(dir(builtins))


def node_id(path: str, kind: str, name: str, line: int) -> str:
    digest = hashlib.sha256(f"{path}:{kind}:{name}:{line}".encode("utf-8")).hexdigest()
    return f"{kind}:{digest[:32]}"


def _first_paragraph(text: str | None) -> str:
    """The first paragraph of a docstring, which is the authored one-line purpose."""
    if not text:
        return ""
    lines: list[str] = []
    for raw in text.strip().splitlines():
        stripped = raw.strip()
        if not stripped:
            break
        lines.append(stripped)
    return " ".join(lines)


def _purpose(value: str, provenance: str) -> dict[str, Any]:
    return {"value": value, "provenance": provenance} if value else {
        "value": "", "provenance": "unavailable"}


def _iter_python_files(root: Path, indexed_roots: list[str]) -> list[Path]:
    files: list[Path] = []
    for prefix in indexed_roots:
        base = root / prefix
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
                continue
            files.append(path)
    return files


def _unit_for(path: Path, root: Path) -> tuple[str, str]:
    """The package a module belongs to, and the module's dotted path within the repository.

    A package is a directory with `__init__.py`, matching how Python itself decides. A module in a
    plain directory is attributed to the nearest ancestor package, or to the directory itself when
    there is none, so no file is dropped for lacking an `__init__.py`.
    """
    relative = path.relative_to(root)
    parts = list(relative.parts[:-1])
    unit_parts: list[str] = []
    current = root
    for part in parts:
        current = current / part
        unit_parts.append(part)
        if not (current / "__init__.py").is_file():
            break
    unit = ".".join(unit_parts) if unit_parts else relative.stem
    module = ".".join([*parts, relative.stem])
    return unit, module


class _Collector(ast.NodeVisitor):
    """One pass over a module: definitions, the calls they make, and imports."""

    def __init__(self, rel_path: str, unit: str, module: str) -> None:
        self.rel_path = rel_path
        self.unit = unit
        self.module = module
        self.nodes: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str, int, int]] = []   # (caller_id, callee_name, row, col)
        self.imports: list[str] = []
        self._scope: list[str] = []

    def _define(self, name: str, kind: str, node: ast.AST, doc: str | None) -> str:
        qualified = "::".join([*self._scope, name])
        identifier = node_id(self.rel_path, kind, qualified, getattr(node, "lineno", 0))
        self.nodes.append({
            "id": identifier,
            "path": self.rel_path,
            "kind": kind,
            "identity_name": name,
            "qualified_name": qualified,
            "unit_name": self.unit,
            "module_path": self.module,
            "start_row": getattr(node, "lineno", 0),
            "start_column": getattr(node, "col_offset", 0),
            "end_row": getattr(node, "end_lineno", getattr(node, "lineno", 0)) or 0,
            "end_column": getattr(node, "end_col_offset", 0) or 0,
            "purpose": _purpose(_first_paragraph(doc), "authored-docstring"),
        })
        return identifier

    def _visit_def(self, node: ast.AST, kind: str) -> None:
        # A function nested inside a class is a method; the scope stack is what distinguishes them.
        resolved = "method" if kind == "function" and self._scope else kind
        identifier = self._define(node.name, resolved, node, ast.get_docstring(node))
        self._scope.append(node.name)
        previous, self._current = getattr(self, "_current", None), identifier
        for child in ast.iter_child_nodes(node):
            self.visit(child)
        self._current = previous
        self._scope.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_def(node, "class")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_def(node, "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_def(node, "function")

    def visit_Import(self, node: ast.Import) -> None:
        self.imports.extend(alias.name for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.imports.append(node.module)

    def visit_Call(self, node: ast.Call) -> None:
        caller = getattr(self, "_current", None)
        if caller:
            target = node.func
            name = (target.attr if isinstance(target, ast.Attribute)
                    else target.id if isinstance(target, ast.Name) else None)
            if name:
                self.calls.append((caller, name, node.lineno, node.col_offset))
        self.generic_visit(node)


def build_export(root: Path, indexed_roots: list[str] | None = None) -> dict[str, Any]:
    """Index the repository and return a contract-2 export."""
    roots = indexed_roots or ["scripts"]
    packages: dict[str, dict[str, Any]] = {}
    modules: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    hierarchy: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    collectors: list[_Collector] = []
    by_name: dict[str, list[str]] = {}

    for path in _iter_python_files(root, roots):
        rel = path.relative_to(root).as_posix()
        raw = path.read_bytes()
        try:
            tree = ast.parse(raw.decode("utf-8"), filename=rel)
        except (SyntaxError, UnicodeDecodeError):
            # Unparseable source is recorded as a boundary, never silently dropped: an index that
            # quietly omits a file understates the graph while looking complete.
            pending.append({"owner_path": rel, "source_node_id": "", "spelling": rel,
                            "reason": "unparsed", "start_row": 0, "start_column": 0})
            continue
        unit, module = _unit_for(path, root)
        # Nodes key on the SYMBOL form and packages expose it as `symbol_namespace`; clusters are
        # built by matching those two. Rust does the same thing (crate `aios-core`, symbol
        # `aios_core`), and keying nodes on the dotted display form instead collapsed every
        # cluster into one unnamed bucket.
        unit_key = unit.replace(".", "_")
        collector = _Collector(rel, unit_key, module)
        collector.visit(tree)
        collectors.append(collector)

        if unit not in packages:
            init = root / Path(*unit.split(".")) / "__init__.py"
            doc = ""
            if init.is_file():
                try:
                    doc = _first_paragraph(ast.get_docstring(ast.parse(init.read_text(
                        encoding="utf-8", errors="replace"))) or "")
                except SyntaxError:
                    doc = ""
            packages[unit] = {
                "package_id": unit,
                "manifest_path": (init.relative_to(root).as_posix() if init.is_file()
                                  else rel),
                "display_name": unit,
                "symbol_namespace": unit_key,
                "purpose": _purpose(doc, "authored-package-docstring"),
                "related_product_document_ids": [],
            }
        modules.append({
            "path": rel, "unit_name": unit_key, "module_path": module,
            "purpose": _purpose(_first_paragraph(ast.get_docstring(tree)), "authored-docstring"),
        })
        files.append({
            "path": rel, "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            "unit_name": unit_key, "module_path": module,
        })
        nodes.extend(collector.nodes)
        hierarchy.append({
            "unit_name": unit_key, "module_path": module, "path": rel,
            "semantic_node_ids": [item["id"] for item in collector.nodes],
        })
        for item in collector.nodes:
            by_name.setdefault(item["identity_name"], []).append(item["id"])

    # A call links only when its name resolves to exactly ONE definition in the whole index.
    # Python dispatch is dynamic; picking a winner among same-named methods would invent an edge.
    for collector in collectors:
        for caller, name, row, column in collector.calls:
            targets = by_name.get(name, [])
            if len(targets) == 1 and targets[0] != caller:
                relations.append({
                    "source_id": caller, "target_id": targets[0], "kind": "calls",
                    "provenance": "resolved-lexical-reference", "confidence": 100,
                })
            elif name not in _BUILTINS:
                pending.append({
                    "owner_path": collector.rel_path, "source_node_id": caller,
                    "spelling": name,
                    "reason": "ambiguous" if targets else "unresolved",
                    "start_row": row, "start_column": column,
                })

    seen: set[tuple[str, str, str]] = set()
    unique: list[dict[str, Any]] = []
    for relation in relations:
        key = (relation["source_id"], relation["target_id"], relation["kind"])
        if key not in seen:
            seen.add(key)
            unique.append(relation)

    return {
        "contract_version": CONTRACT_VERSION,
        "source_fingerprint": hashlib.sha256(
            "".join(sorted(item["sha256"] for item in files)).encode("utf-8")).hexdigest(),
        "packages": sorted(packages.values(), key=lambda item: item["package_id"]),
        "modules": modules,
        "files": files,
        "semantic_nodes": nodes,
        "semantic_hierarchy": hierarchy,
        "relations": unique,
        "pending_boundaries": pending,
        "omissions": {
            "packages_missing_description": [
                item["display_name"] for item in packages.values()
                if not item["purpose"]["value"]],
            "modules_missing_authored_purpose": [
                item["path"] for item in modules if not item["purpose"]["value"]],
        },
    }
