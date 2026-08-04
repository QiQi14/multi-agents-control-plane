#!/usr/bin/env python3
"""Install the control plane into another repository.

    python install.py ../my-project              # install
    python install.py ../my-project --dry-run    # show the plan, touch nothing
    python install.py ../my-project --update     # refresh the plane, keep your content
    python install.py ../my-project --uninstall  # remove what this installed

Standard library only. Nothing is downloaded, nothing is written outside the target repository,
and no file this script did not place is ever deleted.

Uninstall is manifest-driven: every installed file is recorded with its sha256, and removal skips
anything you have since edited. That is the same rule the plane applies to its own generated files,
so "uninstall" can never quietly take your work with it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parent
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))
from scripts.ai_plane import products  # noqa: E402  (stdlib-only, source-local)

MANIFEST_REL = Path(".ai/.install-manifest.json")
MANIFEST_SCHEMA = 1

# The control plane coordinates work ON a product; it is not the product. Installing it over a
# product's own worktree is what coupled the two Git directories, put control-plane paths into the
# product's .gitignore, and set two AGENTS.md files against each other. The topology is:
#
#     <workspace>/            the control plane
#     <workspace>/projects/   products, one directory per product id
#
# Anything the installer would write outside this surface is a bug, so the surface is declared.
INSTALL_SURFACE_ROOTS = (".ai/", "scripts/", "ai", "ai.cmd")
# Product-owned files. The installer never writes them and `ai sync` never generates into a
# product; collision avoidance belongs in the topology, not inside someone's repository.
PRODUCT_OWNED = (".gitignore", ".gitattributes", ".git")

# Canonical control-plane content. Generated adapters (AGENTS.md, CLAUDE.md, GEMINI.md, .claude/,
# .agents/) are deliberately absent: `ai sync` produces them from `.ai/`, so shipping them would
# install two copies of the same truth.
TREES = [
    ".ai/agents",
    ".ai/project",
    ".ai/rules",
    ".ai/skills",
    ".ai/templates",
    ".ai/workflows",
    "scripts/ai_plane",
    "scripts/extensions",
]
FILES = [
    ".ai/config.yaml",
    "ai",
    "ai.cmd",
    "scripts/__init__.py",
    "scripts/ai_cli.py",
    "scripts/ai_docs.py",
    "scripts/extension_registry.py",
    "scripts/rust_verify.py",
    "scripts/verify_contract.py",
    "scripts/verify_core.py",
]
# The plane's own suite. Useful to prove a copy works, but it carries multi-megabyte render
# fixtures, so it is opt-in rather than a tax on every adopting repository.
TEST_TREES = ["scripts/tests"]

SCAFFOLD_DIRS = [
    ".ai/tasks/queue", ".ai/tasks/active", ".ai/tasks/done", ".ai/tasks/archive",
    ".ai/adapters/codex", ".ai/adapters/claude", ".ai/adapters/antigravity",
]
MEMORY_FILES = {
    "api-surface.md": "# API Surface\n\nStable public interfaces other code depends on.\n",
    "decisions.md": "# Decisions\n\nDurable technical decisions and the reasoning behind them.\n",
    "deprecated.md": "# Deprecated\n\nRetired approaches and what replaced them.\n",
    "feature-ledger.md": "# Feature Ledger\n\nWhat shipped, where it lives, and what it depends on.\n",
    "gotchas.md": "# Gotchas\n\nNon-obvious traps verified in this repository.\n",
    "lessons.md": "# Lessons\n\nProcess and engineering lessons captured after QA.\n",
}
# `ai sync` writes these. An existing file at any of these paths WILL be replaced.
GENERATED_BY_SYNC = ["AGENTS.md", "CLAUDE.md", "GEMINI.md", ".claude/", ".agents/"]

SKIP_NAMES = {"__pycache__", ".pytest_cache", ".DS_Store"}
SKIP_SUFFIXES = {".pyc", ".pyo"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wanted(path: Path) -> bool:
    if path.suffix in SKIP_SUFFIXES:
        return False
    return not any(part in SKIP_NAMES for part in path.parts)


def payload(include_tests: bool) -> list[str]:
    """Every repository-relative path this installer would place."""
    out: list[str] = []
    for rel in FILES:
        if (SOURCE / rel).is_file():
            out.append(rel)
    for tree in TREES + (TEST_TREES if include_tests else []):
        root = SOURCE / tree
        if not root.is_dir():
            continue
        for item in sorted(root.rglob("*")):
            if item.is_file() and wanted(item.relative_to(SOURCE)):
                out.append(item.relative_to(SOURCE).as_posix())
    return sorted(set(out))


def off_surface(paths: list[str]) -> list[str]:
    """Payload paths that would land outside the control-plane surface."""
    return [
        rel for rel in paths
        if not any(rel == root or rel.startswith(root) for root in INSTALL_SURFACE_ROOTS)
    ]


def topology_report(target: Path) -> dict:
    """What this target already is, before anything is written to it."""
    conflicts = products.mixed_install_conflicts(target)
    discovered = products.discover_products(target)
    return {
        "mixed": conflicts,
        "products": [
            {"id": item.product_id, "path": item.relative_path,
             "stacks": list(item.stacks), "nested": item.nested}
            for item in discovered
        ],
        "stacks": sorted({stack for item in discovered for stack in item.stacks}),
    }


def read_manifest(target: Path) -> dict:
    path = target / MANIFEST_REL
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_manifest(target: Path, entries: dict[str, str], include_tests: bool) -> None:
    path = target / MANIFEST_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"schema_version": MANIFEST_SCHEMA, "with_tests": include_tests, "files": entries},
            indent=2, sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )


def describe_conflicts(target: Path, paths: list[str], installed: dict[str, str]) -> list[str]:
    """Existing files this install would overwrite that it did not previously place unmodified."""
    conflicts = []
    for rel in paths:
        dest = target / rel
        if not dest.is_file():
            continue
        recorded = installed.get(rel)
        if recorded is None or recorded != sha256(dest):
            conflicts.append(rel)
    return conflicts


def do_install(target: Path, args: argparse.Namespace) -> int:
    paths = payload(args.with_tests)
    escaped = off_surface(paths)
    if escaped:
        print("Refusing to install: these payload paths are outside the control-plane surface "
              f"({', '.join(INSTALL_SURFACE_ROOTS)}):", file=sys.stderr)
        for rel in escaped[:10]:
            print(f"  {rel}", file=sys.stderr)
        return 1

    topology = topology_report(target)
    if topology["mixed"] and not getattr(args, "here", False):
        print(f"Refusing to install into {target}: it is a product worktree, not a workspace.",
              file=sys.stderr)
        for reason in topology["mixed"]:
            print(f"  - {reason}", file=sys.stderr)
        print(
            "\nThe control plane coordinates work ON a product; it is not the product. Sharing"
            "\none directory couples the two Git checkouts, pushes control-plane paths into the"
            "\nproduct's .gitignore, and puts two AGENTS.md files in conflict.",
            file=sys.stderr)
        print("\nDo this instead:", file=sys.stderr)
        print("  mkdir ../workspace && cd ../workspace", file=sys.stderr)
        print(f"  mv {target} projects/<product-id>", file=sys.stderr)
        print("  python <plane>/install.py .", file=sys.stderr)
        print("\nOr pass --here if you have decided the two really do share a directory.",
              file=sys.stderr)
        return 1
    # Always consult the manifest, not just under --update: a plain re-install must recognise the
    # files it placed itself. Reading it only under --update made a second run report every
    # installed file as a conflict.
    manifest = read_manifest(target)
    installed = manifest.get("files", {})
    conflicts = describe_conflicts(target, paths, installed)

    if manifest and not conflicts and not args.update:
        print(f"Already installed at {target}, and nothing has drifted.")
        print("Re-run with --update to refresh the plane, or --uninstall to remove it.")
        return 0

    if conflicts and not (args.force or args.update):
        print(f"Refusing to overwrite {len(conflicts)} existing file(s) in {target}:", file=sys.stderr)
        for rel in conflicts[:15]:
            print(f"  {rel}", file=sys.stderr)
        if len(conflicts) > 15:
            print(f"  ... and {len(conflicts) - 15} more", file=sys.stderr)
        print("\nRe-run with --update to refresh a previous install, or --force to overwrite.",
              file=sys.stderr)
        return 1

    if args.update and conflicts:
        print(f"note: {len(conflicts)} file(s) differ from the recorded install and will be replaced.")
        for rel in conflicts[:10]:
            print(f"  {rel}")

    if args.dry_run:
        print(f"Would install {len(paths)} file(s) into {target}")
        _report_topology(topology)
        print(f"Would create {len(SCAFFOLD_DIRS)} scaffold director(ies) and "
              f"{len(MEMORY_FILES)} typed-memory file(s)")
        existing_generated = [g for g in GENERATED_BY_SYNC if (target / g.rstrip('/')).exists()]
        if existing_generated:
            print("\n`ai sync` will REPLACE these existing files:")
            for name in existing_generated:
                print(f"  {name}")
        print("\nNothing was written.")
        return 0

    entries: dict[str, str] = {}
    for rel in paths:
        src, dest = SOURCE / rel, target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        entries[rel] = sha256(dest)

    for rel in SCAFFOLD_DIRS:
        keep = target / rel / ".gitkeep"
        keep.parent.mkdir(parents=True, exist_ok=True)
        if not keep.exists():
            keep.write_text("", encoding="utf-8")
        entries[(Path(rel) / ".gitkeep").as_posix()] = sha256(keep)

    memory = target / ".ai" / "memory"
    memory.mkdir(parents=True, exist_ok=True)
    for name, body in MEMORY_FILES.items():
        path = memory / name
        if not path.exists():           # never clobber captured knowledge on --update
            path.write_text(body, encoding="utf-8")
        entries[f".ai/memory/{name}"] = sha256(path)

    stack_note = tailor_extensions(target, topology["stacks"])
    if stack_note and (target / ".ai" / "config.yaml").is_file():
        # Record the hash AFTER tailoring. Recording the shipped bytes made the very next run see
        # its own edit as drift and refuse to reinstall.
        entries[".ai/config.yaml"] = sha256(target / ".ai" / "config.yaml")
    write_manifest(target, entries, args.with_tests)

    print(f"Installed {len(paths)} file(s) into {target}")
    _report_topology(topology)
    if stack_note:
        print(stack_note)
    existing_generated = [g for g in GENERATED_BY_SYNC if (target / g.rstrip('/')).exists()]
    if existing_generated:
        print("\nWARNING: `ai sync` generates these, and will replace what is there now:")
        for name in existing_generated:
            print(f"  {name}")
        print("  Move anything you wrote by hand into .ai/ first.")
    print("\nNext:")
    print(f"  cd {target}")
    print("  python scripts/ai_cli.py init")
    print("  python scripts/ai_cli.py sync")
    print("  python scripts/ai_cli.py doctor")
    return 0


def _report_topology(topology: dict) -> None:
    if topology["products"]:
        print("\nProducts discovered from manifests:")
        for item in topology["products"]:
            where = item["path"] if item["nested"] else f"{item['path']} (workspace root)"
            stacks = ", ".join(item["stacks"]) or "no detected stack"
            print(f"  {item['id']}: {where} [{stacks}]")
    else:
        print("\nNo product discovered. Place one under projects/<product-id>/ so Project"
              "\nIntelligence indexes your application rather than the control plane's own"
              "\nscripts.")


# Language extensions carry gates: the Rust one makes `cargo` evidence a merge requirement. Enabling
# it on a React/NestJS product because it happened to be in the shipped default is a gate nobody
# asked for, on a toolchain that is not installed.
STACK_EXTENSIONS = {"rust": "rust"}


def tailor_extensions(target: Path, stacks: list[str]) -> str:
    """Disable language extensions no discovered product's manifests justify."""
    config = target / ".ai" / "config.yaml"
    if not config.is_file():
        return ""
    lines = config.read_text(encoding="utf-8").splitlines(keepends=True)
    removed: list[str] = []
    kept: list[str] = []
    in_enabled = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "enabled:":
            in_enabled = True
            continue
        if in_enabled:
            if not stripped.startswith("- "):
                in_enabled = False
                continue
            name = stripped[2:].strip()
            required_stack = next((s for s, ext in STACK_EXTENSIONS.items() if ext == name), None)
            if required_stack is not None and required_stack not in stacks:
                lines[index] = line.replace("- " + name, "# - " + name, 1)
                removed.append(name)
            else:
                kept.append(name)
    if not removed:
        return ""
    config.write_text("".join(lines), encoding="utf-8")
    detected = ", ".join(stacks) or "none"
    return (
        f"\nDisabled {len(removed)} language extension(s) no product manifest justifies: "
        f"{', '.join(removed)}."
        f"\n  Detected stacks: {detected}. Re-enable any of them in .ai/config.yaml under"
        "\n  extensions.enabled."
    )


def remove_generated(target: Path) -> tuple[list[str], list[str]]:
    """Remove adapter files the plane generated, using the plane's own hash manifest.

    Only sha256-matching files are removed. An adapter someone edited by hand is kept, even though
    hand-editing a generated file is never how the plane is meant to be used.
    """
    manifest_path = target / ".ai" / "_manifest.json"
    if not manifest_path.is_file():
        return [], []
    try:
        entries = json.loads(manifest_path.read_text(encoding="utf-8")).get("entries", [])
    except (OSError, json.JSONDecodeError):
        return [], []
    removed: list[str] = []
    kept: list[str] = []
    # The plane's index files are generated by init/sync, not by this installer, so they are not in
    # our manifest and would otherwise survive as a lone `.ai/_manifest.json` after a full removal.
    for generated_index in (".ai/_manifest.json", ".ai/_registry.json"):
        index_path = target / generated_index
        if index_path.is_file():
            index_path.unlink()
            removed.append(generated_index)
    site = target / ".ai" / "_site"
    if site.is_dir():
        shutil.rmtree(site)
        removed.append(".ai/_site")
    for entry in entries if isinstance(entries, list) else []:
        rel = entry.get("path") if isinstance(entry, dict) else None
        digest = entry.get("sha256") if isinstance(entry, dict) else None
        if not isinstance(rel, str) or not isinstance(digest, str):
            continue
        path = target / rel
        if not path.is_file():
            continue
        if sha256(path) == digest:
            path.unlink()
            removed.append(rel)
        else:
            kept.append(rel)
    return removed, kept


def do_uninstall(target: Path, args: argparse.Namespace) -> int:
    manifest = read_manifest(target)
    entries: dict[str, str] = manifest.get("files", {})
    if not entries:
        print(f"No install manifest found at {target / MANIFEST_REL}.", file=sys.stderr)
        print("Nothing was removed. This installer only deletes files it recorded placing.",
              file=sys.stderr)
        return 1

    removable, modified, missing = [], [], []
    for rel, digest in sorted(entries.items()):
        path = target / rel
        if not path.is_file():
            missing.append(rel)
        elif sha256(path) == digest:
            removable.append(rel)
        else:
            modified.append(rel)

    if args.dry_run:
        print(f"Would remove {len(removable)} file(s) from {target}")
        print(f"Would KEEP {len(modified)} file(s) you have modified since install")
        for rel in modified[:10]:
            print(f"  kept: {rel}")
        print("\nNothing was written.")
        return 0

    # Read the plane's OWN manifest before .ai/ is removed, so a complete uninstall does not leave
    # AGENTS.md and .agents/ behind for the user to discover and delete by hand.
    generated_removed, generated_kept = remove_generated(target) if args.include_generated else ([], [])

    for rel in removable:
        (target / rel).unlink()

    # Byte-code caches are produced by Python from the files just deleted. They are not "ours" in
    # the manifest sense, but leaving them keeps otherwise-empty directories alive.
    for cache in sorted(target.rglob("__pycache__"), key=lambda item: len(item.parts), reverse=True):
        if cache.is_dir() and all(child.suffix in SKIP_SUFFIXES for child in cache.iterdir()):
            shutil.rmtree(cache)

    # Prune directories the removal emptied, so no hollow .ai/ skeleton is left behind.
    for rel in sorted({str(Path(r).parent) for r in removable + generated_removed},
                      key=len, reverse=True):
        current = target / rel
        while current != target and current.is_dir() and not any(current.iterdir()):
            current.rmdir()
            current = current.parent

    (target / MANIFEST_REL).unlink(missing_ok=True)

    # `ai init` creates scaffold directories this installer never placed (`.ai/migration`, and any
    # left empty above). Sweep empty directories under `.ai/` so a full uninstall does not leave a
    # skeleton behind; `.ai/` itself goes only if nothing of the user's remains inside it.
    ai_root = target / ".ai"
    if ai_root.is_dir():
        for directory in sorted((d for d in ai_root.rglob("*") if d.is_dir()),
                                key=lambda item: len(item.parts), reverse=True):
            if not any(directory.iterdir()):
                directory.rmdir()
        if not any(ai_root.iterdir()):
            ai_root.rmdir()

    print(f"Removed {len(removable)} file(s) from {target}")
    if args.include_generated:
        print(f"Removed {len(generated_removed)} generated adapter file(s)")
        if generated_kept:
            print(f"Kept {len(generated_kept)} generated file(s) you edited by hand")
    if modified:
        print(f"\nKept {len(modified)} file(s) modified since install:")
        for rel in modified[:10]:
            print(f"  {rel}")
        if len(modified) > 10:
            print(f"  ... and {len(modified) - 10} more")
    if missing:
        print(f"\n{len(missing)} recorded file(s) were already gone.")
    if not args.include_generated:
        print("\nGenerated adapters were left in place. Re-run with --include-generated to remove")
        print("the ones the plane generated and you have not edited:")
        for name in GENERATED_BY_SYNC:
            print(f"  {name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install the control plane into another repository.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("target", help="Path to the repository to install into")
    parser.add_argument("--update", action="store_true",
                        help="Refresh a previous install, preserving your tasks, memory and config")
    parser.add_argument("--uninstall", action="store_true",
                        help="Remove files this installer placed and you have not modified")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan and write nothing")
    parser.add_argument("--force", action="store_true", help="Overwrite conflicting existing files")
    parser.add_argument("--here", action="store_true",
                        help="Install into a product worktree anyway (see the refusal message)")
    parser.add_argument("--include-generated", action="store_true",
                        help="On uninstall, also remove generated adapters "
                             "(AGENTS.md, CLAUDE.md, GEMINI.md, .claude/, .agents/)")
    parser.add_argument("--with-tests", action="store_true",
                        help="Also install the plane's own test suite (adds several MB of fixtures)")
    args = parser.parse_args(argv)

    target = Path(args.target).expanduser().resolve()
    if not target.is_dir():
        print(f"Target is not a directory: {target}", file=sys.stderr)
        return 1
    if target == SOURCE:
        print("Target is this repository. Choose the repository you want to install INTO.",
              file=sys.stderr)
        return 1
    if not (target / ".git").exists():
        print(f"note: {target} is not a git repository. The plane works anyway, but its "
              f"verification gates read git history.")

    if args.uninstall:
        return do_uninstall(target, args)
    return do_install(target, args)


if __name__ == "__main__":
    raise SystemExit(main())
