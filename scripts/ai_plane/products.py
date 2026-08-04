"""Where the product lives, and what it is built with.

The control plane coordinates work ON a product; it is not the product. Conflating the two is the
root of a whole class of adoption failures: an installer that treats the current checkout as both
coordination workspace and product worktree, a hard-coded singular `project/` path, a graph that
indexes the plane's own `scripts/` instead of the application, and rebuild guidance that tells a
Node workspace to run Cargo.

This module is the single answer to two questions -- *which directories are products* and *what is
each one built with* -- and it answers both from authoritative manifests on disk. Nothing here
infers a stack from a directory name, a file extension, or the presence of a lockfile alone: a
`package.json` is what makes something a Node package, and a repository with no manifest has no
detected stack rather than a guessed one.

Topology contract
-----------------

    <workspace>/            control plane: .ai/, scripts/, ai, generated adapters
    <workspace>/projects/   products, one directory per product id

`PROJECTS_ROOT` is the plural `projects`. The singular `project/` is recognised only as a legacy
layout so an existing repository keeps working; it is never produced.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

PROJECTS_ROOT = "projects"
LEGACY_PROJECT_ROOT = "project"
ARCHIVE_ROOT = "legacy"

# Never scanned, never indexed, never registered. `legacy/` is on this list by contract: a
# migration needs somewhere lossless to put superseded material, and material that is still
# discoverable has not been superseded.
EXCLUDED_DIR_NAMES = frozenset({
    ARCHIVE_ROOT, ".ai", ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "target", "dist", "build", ".next", ".nuxt", ".turbo", "coverage", "_site", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".gradle", "vendor", "Pods", ".terraform",
})

# Authoritative manifests, in the order a mixed repository should be described. A stack is
# detected ONLY by one of these files existing -- never by a source extension, because a repository
# with one stray `.rs` file is not a Rust project.
STACK_MANIFESTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("rust", ("Cargo.toml",)),
    ("node", ("package.json",)),
    ("python", ("pyproject.toml", "setup.py", "setup.cfg")),
    ("go", ("go.mod",)),
    ("jvm", ("pom.xml", "build.gradle", "build.gradle.kts")),
    ("dotnet", ("*.sln", "*.csproj", "*.fsproj")),
    ("ruby", ("Gemfile",)),
    ("php", ("composer.json",)),
)


def is_excluded(relative: Path | str) -> bool:
    """True when a repository-relative path sits under a directory nothing may scan."""
    parts = Path(str(relative)).parts
    return any(part in EXCLUDED_DIR_NAMES for part in parts)


def _manifests_in(directory: Path) -> dict[str, list[str]]:
    """Detected stacks in one directory, mapped to the manifest files that prove each one."""
    found: dict[str, list[str]] = {}
    for stack, patterns in STACK_MANIFESTS:
        hits: list[str] = []
        for pattern in patterns:
            if "*" in pattern:
                hits.extend(sorted(item.name for item in directory.glob(pattern) if item.is_file()))
            elif (directory / pattern).is_file():
                hits.append(pattern)
        if hits:
            found[stack] = hits
    return found


def _npm_workspace_globs(package_json: Path) -> list[str]:
    """The `workspaces` globs an npm/yarn/pnpm root declares, if any.

    A malformed or unreadable manifest yields no workspaces rather than an error: discovery is a
    convenience layer, and a broken product manifest must not stop the control plane from running.
    """
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    declared = data.get("workspaces")
    if isinstance(declared, dict):
        declared = declared.get("packages")
    if not isinstance(declared, list):
        return []
    return [item for item in declared if isinstance(item, str) and item]


@dataclass(frozen=True)
class Package:
    """One buildable unit inside a product: the product root itself, or a workspace member."""

    name: str
    relative_path: str
    stacks: tuple[str, ...]
    manifests: tuple[str, ...]


@dataclass(frozen=True)
class Product:
    """A product the control plane coordinates work on."""

    product_id: str
    relative_path: str
    stacks: tuple[str, ...]
    nested: bool
    packages: tuple[Package, ...] = field(default_factory=tuple)

    @property
    def is_workspace_root(self) -> bool:
        return self.relative_path == "."

    def path(self, root: Path) -> Path:
        return root if self.is_workspace_root else root / self.relative_path


def _packages_for(root: Path, product_rel: str) -> tuple[Package, ...]:
    """The product's own manifest plus any npm workspace members it declares."""
    base = root if product_rel == "." else root / product_rel
    packages: list[Package] = []
    own = _manifests_in(base)
    if own:
        packages.append(Package(
            name=base.name if product_rel == "." else Path(product_rel).name,
            relative_path=product_rel,
            stacks=tuple(sorted(own)),
            manifests=tuple(sorted(name for names in own.values() for name in names)),
        ))
    package_json = base / "package.json"
    if package_json.is_file():
        for pattern in _npm_workspace_globs(package_json):
            for member in sorted(base.glob(pattern)):
                if not member.is_dir():
                    continue
                relative = member.relative_to(root).as_posix()
                if is_excluded(member.relative_to(root)):
                    continue
                stacks = _manifests_in(member)
                if not stacks:
                    continue
                packages.append(Package(
                    name=member.name,
                    relative_path=relative,
                    stacks=tuple(sorted(stacks)),
                    manifests=tuple(sorted(n for names in stacks.values() for n in names)),
                ))
    # A workspace root that only declares members is not itself a package to index twice.
    unique: dict[str, Package] = {}
    for package in packages:
        unique.setdefault(package.relative_path, package)
    return tuple(unique[key] for key in sorted(unique))


def discover_products(root: Path) -> list[Product]:
    """Every product in the workspace, nested ones first.

    Nested products come first because they are the explicit topology: a repository that has said
    "the product is `projects/<id>`" must never have the control plane's own source described as
    its product instead.
    """
    products: list[Product] = []
    projects_dir = root / PROJECTS_ROOT
    if projects_dir.is_dir():
        for entry in sorted(projects_dir.iterdir()):
            if not entry.is_dir() or entry.name in EXCLUDED_DIR_NAMES:
                continue
            relative = entry.relative_to(root).as_posix()
            packages = _packages_for(root, relative)
            if not packages:
                continue
            stacks = sorted({stack for package in packages for stack in package.stacks})
            products.append(Product(entry.name, relative, tuple(stacks), True, packages))

    legacy_dir = root / LEGACY_PROJECT_ROOT
    if legacy_dir.is_dir():
        packages = _packages_for(root, LEGACY_PROJECT_ROOT)
        if packages:
            stacks = sorted({stack for package in packages for stack in package.stacks})
            products.append(Product(
                LEGACY_PROJECT_ROOT, LEGACY_PROJECT_ROOT, tuple(stacks), True, packages))

    if not products:
        packages = _packages_for(root, ".")
        if packages:
            stacks = sorted({stack for package in packages for stack in package.stacks})
            products.append(Product(root.name, ".", tuple(stacks), False, packages))
    return products


def product_document_roots(root: Path) -> list[str]:
    """Repository-relative product documentation roots, discovered rather than assumed.

    A second editable mirror is what let one product's docs live in two stale places at once, so
    only directories that actually exist are returned and the legacy singular path is included
    only when no plural one does.
    """
    roots: list[str] = []
    for product in discover_products(root):
        base = "" if product.is_workspace_root else product.relative_path + "/"
        candidate = root / f"{base}docs" if base else root / "docs"
        if candidate.is_dir():
            roots.append(f"{base}docs")
    return roots


def mixed_install_conflicts(root: Path) -> list[str]:
    """Reasons this checkout would become a control plane and a product at the same level.

    Installing over a product's own worktree is what coupled Git metadata, put control-plane paths
    in the product's `.gitignore`, and set two different `AGENTS.md` files against each other.

    Only a MANIFEST counts. A bare `src/` is a directory name, and refusing to install because of
    one would be the same guess-from-a-name this module exists to avoid -- with the cost paid by
    every repository that has a `src/` and no product in it.
    """
    detected = _manifests_in(root)
    if not detected:
        return []
    manifests = sorted(name for names in detected.values() for name in names)
    return [
        f"the workspace root is itself a {', '.join(sorted(detected))} product "
        f"({', '.join(manifests)})"
    ]
