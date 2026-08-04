"""Choose the index adapter from what the repository actually contains.

Three failures came from having no such choice. The exporter was hard-coded to a Rust tool, so a
Node product got no graph. The Python fallback was rooted at `scripts`, so an adopting repository
saw the control plane's own source presented as its product. And the boundary text -- indexed
roots, rebuild guidance, error prose -- was written at the presentation layer, so a Node workspace
was told to rebuild with Cargo.

An adapter now owns all of it: what it indexes, how to rebuild it, and what it cannot do. Selection
prefers a discovered PRODUCT over the control plane, because a repository that has said "the
product is `projects/<id>`" must never be described by the plane's own scripts.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from scripts.ai_plane.products import Product, discover_products

REBUILD_COMMAND = "python scripts/ai_cli.py docs build"


@dataclass(frozen=True)
class Adapter:
    """One way to turn source into the export contract, with the truth it can vouch for."""

    adapter_id: str
    stack: str
    product_id: str
    indexed_roots: tuple[str, ...]
    include_rules: tuple[str, ...]
    exclude_rules: tuple[str, ...]
    rebuild_guidance: str
    build: Callable[[], dict[str, Any] | None]

    def describe(self) -> dict[str, Any]:
        """Adapter provenance for the boundary envelope and for `ai doctor`."""
        return {
            "adapter": self.adapter_id,
            "stack": self.stack,
            "product": self.product_id,
            "indexed_roots": list(self.indexed_roots),
        }


def _rust_adapter(root: Path, product: Product) -> Adapter | None:
    """The governed `tools/ai-impact` exporter. Selected only when its source is present."""
    tool_manifest = root / "tools" / "ai-impact" / "Cargo.toml"
    product_manifest = product.path(root) / "Cargo.toml"
    if not tool_manifest.is_file() or not product_manifest.is_file():
        return None
    base = "" if product.is_workspace_root else product.relative_path + "/"
    return Adapter(
        adapter_id="ai-impact",
        stack="rust",
        product_id=product.product_id,
        indexed_roots=(f"{base}Cargo.toml", f"{base}crates/"),
        include_rules=("deterministic index export contract", "resolved semantic relations"),
        exclude_rules=("SQLite internals", "pending references as edges",
                       "presentation groups as semantics"),
        rebuild_guidance=(
            f"Rerun `{REBUILD_COMMAND}`; it compiles the governed tools/ai-impact source with "
            "Cargo --locked before exporting."
        ),
        # The Rust path runs a subprocess and is driven by project.py, which owns the process
        # plumbing and the agent proof bundles. The adapter exists here to carry its boundary.
        build=lambda: None,
    )


def _node_adapter(root: Path, product: Product) -> Adapter | None:
    from scripts.ai_plane.knowledge_projection import ts_index

    packages = [
        {"name": package.name, "relative_path": package.relative_path}
        for package in product.packages if "node" in package.stacks
    ]
    if not packages:
        return None

    def build() -> dict[str, Any] | None:
        export = ts_index.build_export(root, packages)
        return export if export["packages"] else None

    return Adapter(
        adapter_id="ts-structural",
        stack="node",
        product_id=product.product_id,
        indexed_roots=tuple(sorted(
            (package["relative_path"] + "/") if package["relative_path"] != "." else "./"
            for package in packages)),
        include_rules=("deterministic index export contract",
                       "declarations found by anchored source patterns"),
        exclude_rules=("call and import edges", "generated and vendored trees",
                       "ambient .d.ts declarations"),
        rebuild_guidance=(
            f"Rerun `{REBUILD_COMMAND}`. This index is structural and needs no Node toolchain; it "
            "reports packages, modules, files, and declarations, and states that call edges were "
            "not attempted rather than inventing them."
        ),
        build=build,
    )


def _python_adapter(root: Path, product: Product) -> Adapter | None:
    from scripts.ai_plane.knowledge_projection import py_index

    base = product.path(root)
    roots = sorted(
        entry.name for entry in base.iterdir()
        if entry.is_dir() and (entry / "__init__.py").is_file()
    ) if base.is_dir() else []
    if not roots:
        return None
    prefix = "" if product.is_workspace_root else product.relative_path + "/"

    def build() -> dict[str, Any] | None:
        export = py_index.build_export(base, roots)
        return export if export["packages"] else None

    return Adapter(
        adapter_id="py-ast",
        stack="python",
        product_id=product.product_id,
        indexed_roots=tuple(f"{prefix}{name}/" for name in roots),
        include_rules=("deterministic index export contract",
                       "calls resolved to exactly one indexed definition"),
        exclude_rules=("ambiguous and dynamic dispatch", "builtins", "generated trees"),
        rebuild_guidance=(
            f"Rerun `{REBUILD_COMMAND}`. This index is built from the Python standard library and "
            "needs no toolchain."
        ),
        build=build,
    )


def _control_plane_adapter(root: Path) -> Adapter | None:
    """The plane's own Python source, indexed only when no product was discovered.

    This is the fallback that used to be the DEFAULT, which is how an adopting repository opened
    the graph and found the control plane's `scripts/` described as its product. It is still worth
    having -- a plane-only checkout has a real graph -- but it must lose to every product.
    """
    from scripts.ai_plane.knowledge_projection import py_index

    roots = sorted(
        entry.name for entry in root.iterdir()
        if entry.is_dir() and (entry / "__init__.py").is_file()
    ) if root.is_dir() else []
    if not roots:
        return None

    def build() -> dict[str, Any] | None:
        export = py_index.build_export(root, roots)
        return export if export["packages"] else None

    return Adapter(
        adapter_id="py-ast",
        stack="python",
        product_id="(control plane)",
        indexed_roots=tuple(f"{name}/" for name in roots),
        include_rules=("deterministic index export contract",
                       "calls resolved to exactly one indexed definition"),
        exclude_rules=("ambiguous and dynamic dispatch", "builtins", "generated trees"),
        rebuild_guidance=(
            f"Rerun `{REBUILD_COMMAND}`. No product manifest was discovered, so this graph "
            "describes the control plane itself. To index a product instead, place it under "
            "`projects/<product-id>/`."
        ),
        build=build,
    )


_BUILDERS: tuple[tuple[str, Callable[[Path, Product], Adapter | None]], ...] = (
    ("rust", _rust_adapter),
    ("node", _node_adapter),
    ("python", _python_adapter),
)


def candidates(root: Path) -> list[Adapter]:
    """Every adapter this repository could use, best first.

    Ordering is topology before stack: every adapter for a nested product outranks every adapter
    for the workspace root, and the control plane's own source is only ever a last resort.
    """
    found: list[Adapter] = []
    products = discover_products(root)
    for product in sorted(products, key=lambda item: not item.nested):
        for stack, builder in _BUILDERS:
            if stack not in product.stacks:
                continue
            adapter = builder(root, product)
            if adapter is not None:
                found.append(adapter)
    if not found:
        fallback = _control_plane_adapter(root)
        if fallback is not None:
            found.append(fallback)
    return found


def select(root: Path) -> Adapter | None:
    """The adapter to index with, or None when no product declares a supported stack."""
    for adapter in candidates(root):
        return adapter
    return None


def unavailable_boundary_fields(root: Path) -> dict[str, Any]:
    """Boundary fields for the case where nothing can be indexed.

    Naming exporter files a repository was never expected to have reads as a broken install, so the
    unavailable envelope describes what WAS discovered instead of what a Rust repository would have.
    """
    products = discover_products(root)
    if not products:
        return {
            "indexed_roots": [],
            "rebuild_guidance": (
                "No product manifest was found. Project Intelligence indexes a product; place it "
                f"under `projects/<product-id>/` and rerun `{REBUILD_COMMAND}`."
            ),
        }
    described = ", ".join(
        f"{product.relative_path} ({', '.join(product.stacks) or 'no detected stack'})"
        for product in products
    )
    # A Rust product with no `tools/ai-impact` is not an unsupported stack; it is a supported stack
    # whose exporter this repository does not carry. Saying otherwise sends the reader to write an
    # adapter that already exists.
    if any("rust" in product.stacks for product in products) and not (
            root / "tools" / "ai-impact" / "Cargo.toml").is_file():
        return {
            "indexed_roots": [product.relative_path for product in products],
            "rebuild_guidance": (
                f"Discovered {described}. The Rust indexer lives at `tools/ai-impact/` and is not "
                f"present in this repository, so no graph is claimed. Rerun `{REBUILD_COMMAND}` "
                "once it is."
            ),
        }
    # "No adapter supports that stack" and "the adapter found nothing to index" are different
    # situations with different remedies, and telling someone to write an adapter that already
    # exists sends them a long way in the wrong direction.
    supported = [adapter.stack for adapter in candidates(root)]
    if supported:
        return {
            "indexed_roots": [product.relative_path for product in products],
            "rebuild_guidance": (
                f"Discovered {described}. The {', '.join(sorted(set(supported)))} index found no "
                f"source to index there yet, so no graph is claimed. Rerun `{REBUILD_COMMAND}` "
                "once there is."
            ),
        }
    return {
        "indexed_roots": [product.relative_path for product in products],
        "rebuild_guidance": (
            f"Discovered {described}. No index adapter supports that stack yet, so no graph is "
            f"claimed. Rerun `{REBUILD_COMMAND}` after adding one."
        ),
    }
