"""Selective stack-pack installation.

Stack packs are OPTIONAL. A Rust project has no use for the React guidance and a Swift project has
no use for the Rust guidance, so the plane ships a *catalog* (``packs/``) and installs only what a
project asks for into ``.ai/skills/``.

Installed packs are ordinary control-plane content: they are indexed by the registry, composed into
generated adapters by ``ai sync``, and removed cleanly by ``ai skills remove``. Nothing is enabled
by merely existing in the catalog.
"""

from __future__ import annotations

import argparse
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

CATALOG_DIRNAME = "packs"
INSTALL_DIRNAME = "skills"


def catalog_root(root: Path) -> Path:
    return root / CATALOG_DIRNAME


def install_root(ai: Path) -> Path:
    return ai / INSTALL_DIRNAME


def available(root: Path) -> list[str]:
    """Pack ids present in the shipped catalog."""
    catalog = catalog_root(root)
    if not catalog.is_dir():
        return []
    return sorted(p.name for p in catalog.iterdir() if p.is_dir() and not p.name.startswith("."))


# A pack may carry more than skill content. `rules/` and `workflows/` are canonical control-plane
# kinds and must land in their own homes to be indexed and composed; everything else is skill
# content and lands under `.ai/skills/<pack>/`. Installing is a distribution, not a plain copy.
_KIND_HOMES: dict[str, str] = {"rules": "rules", "workflows": "workflows"}


def resolve_source(root: Path, pack: str, source_dir: str | None) -> Path:
    """Where a pack's content is read from.

    Without ``--from`` this is the shipped catalog. With it, the given directory is treated as a
    catalog root (``<dir>/<pack>``); pointing ``--from`` straight at the pack directory itself also
    works, so a user can install their own pack from anywhere without staging it into `packs/`.
    """
    if source_dir is None:
        return catalog_root(root) / pack
    base = Path(source_dir).expanduser()
    if not base.is_absolute():
        base = (root / base).resolve()
    nested = base / pack
    if nested.is_dir():
        return nested
    if base.is_dir() and base.name == pack:
        return base
    return nested


def install_plan(root: Path, ai: Path, pack: str, source_dir: str | None = None) -> list[tuple[Path, Path]]:
    """Every (source, destination) pair for one pack.

    `add` and `remove` share this, so an uninstall reverses exactly what the install placed
    without a bookkeeping file that could drift from the filesystem.
    """
    source = resolve_source(root, pack, source_dir)
    pairs: list[tuple[Path, Path]] = []
    for item in sorted(source.rglob("*")):
        if not item.is_file():
            continue
        relative = item.relative_to(source)
        head = relative.parts[0]
        if head in _KIND_HOMES and len(relative.parts) > 1:
            pairs.append((item, ai / _KIND_HOMES[head] / Path(*relative.parts[1:])))
        else:
            pairs.append((item, install_root(ai) / pack / relative))
    return pairs


def installed(ai: Path) -> list[str]:
    """Pack ids currently installed into this project's control plane."""
    target = install_root(ai)
    if not target.is_dir():
        return []
    return sorted(p.name for p in target.iterdir() if p.is_dir() and not p.name.startswith("."))


def summary(root: Path, pack: str) -> str:
    """First description/summary line from the pack's SKILL.md or README.md, for `list` output."""
    for name in ("SKILL.md", "README.md"):
        candidate = catalog_root(root) / pack / name
        if not candidate.is_file():
            continue
        for line in candidate.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped.startswith("description:"):
                return stripped.split(":", 1)[1].strip()
        for line in candidate.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith(("#", "-", "|", "---", "id:", "type:")):
                return stripped
    return ""


def cmd_skills(
    args: argparse.Namespace,
    *,
    root: Path,
    ai: Path,
    die: Callable[[str], Any],
) -> None:
    command = getattr(args, "skills_command", "list")
    catalog = available(root)
    present = installed(ai)

    if command == "list":
        if not catalog:
            # No pack ships by default. Stack conventions are opinionated and age quickly, so the
            # plane does not pretend to have validated any -- it installs yours instead.
            print("No packs ship with the control plane.\n")
            if present:
                print("Installed in this project:")
                for name in present:
                    print(f"  {name}")
                print()
            print("Install your own from anywhere:")
            print("  ai skills add <name> --from <dir>")
            print("\nA pack is a folder of `rules/`, `workflows/`, and skill files.")
            return
        width = max(len(name) for name in catalog)
        # Count catalog packs only. `.ai/skills/` also holds core surface (pr-blueprint) that is
        # not an optional stack pack and cannot be added or removed through this command.
        from_catalog = [name for name in present if name in catalog]
        print(f"Stack packs ({len(from_catalog)} of {len(catalog)} installed)\n")
        for name in catalog:
            mark = "installed" if name in present else "         "
            text = summary(root, name)
            print(f"  [{mark}] {name.ljust(width)}  {text[:78]}")
        print(f"\nInstall with:  ai skills add <name>")
        return

    if command == "add":
        source_dir = getattr(args, "from_dir", None)
        if source_dir is None:
            unknown = [n for n in args.names if n not in catalog]
            if unknown:
                die(f"Unknown pack(s): {', '.join(unknown)}. Run `ai skills list` to see the catalog.")
        else:
            missing = [n for n in args.names if not resolve_source(root, n, source_dir).is_dir()]
            if missing:
                die(f"Not found under {source_dir}: {', '.join(missing)}")
        for name in args.names:
            pairs = install_plan(root, ai, name, source_dir)
            if not pairs:
                die(f"{name} contributes no files")
            if any(dest.exists() for _src, dest in pairs) and not args.force:
                print(f"  already installed (use --force to reinstall): {name}")
                continue
            for src, dest in pairs:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
            homes = sorted({dest.relative_to(ai).parts[0] for _src, dest in pairs})
            print(f"  installed: {name}  ->  {', '.join('.ai/' + home for home in homes)}")
        print("\nRun `ai sync` to regenerate adapters with the new pack content.")
        return

    if command == "remove":
        source_dir = getattr(args, "from_dir", None)
        for name in args.names:
            traceable = name in catalog or (
                source_dir is not None and resolve_source(root, name, source_dir).is_dir()
            )
            if not traceable and not (install_root(ai) / name).is_dir():
                die(f"Not installed and not in the catalog: {name}. "
                    f"For a pack installed from elsewhere, pass --from <dir> so its rules and "
                    f"workflows can be traced.")
            touched: set[Path] = set()
            count = 0
            if traceable:
                targets = [dest for _src, dest in install_plan(root, ai, name, source_dir)]
            else:
                # The pack's source is gone, so only its skill content can be located. Anything it
                # contributed to .ai/rules or .ai/workflows is NOT removed -- say so rather than
                # implying a clean uninstall.
                targets = [p for p in (install_root(ai) / name).rglob("*") if p.is_file()]
                print(f"  note: {name} is not in the catalog; removing its skill content only. "
                      f"Re-run with --from <dir> to also remove rules/workflows it contributed.")
            for dest in targets:
                if dest.is_file():
                    dest.unlink()
                    touched.add(dest.parent)
                    count += 1
            # Drop directories the uninstall emptied, so no hollow pack folder is left behind.
            for directory in sorted(touched, key=lambda item: len(item.parts), reverse=True):
                current = directory
                while current != ai and current.is_dir() and not any(current.iterdir()):
                    current.rmdir()
                    current = current.parent
            print(f"  removed: {name} ({count} file(s))")
        print("\nRun `ai sync` to regenerate adapters without the removed pack content.")
        return

    die(f"Unknown skills command: {command}")


def add_skills_parser(sub: Any) -> None:
    skills = sub.add_parser(
        "skills",
        help="List, install, or remove optional stack packs (a project installs only what it needs)",
    )
    skills_sub = skills.add_subparsers(dest="skills_command", required=True)
    skills_sub.add_parser("list", help="Show the shipped pack catalog and which packs are installed")
    add = skills_sub.add_parser("add", help="Install one or more packs into this project")
    add.add_argument("names", nargs="+", help="Pack ids from `ai skills list`, or your own pack name")
    add.add_argument("--force", action="store_true", help="Reinstall a pack that is already present")
    add.add_argument("--from", dest="from_dir", metavar="DIR",
                     help="Install from your own directory instead of the shipped catalog. DIR may "
                          "be a folder of packs or the pack folder itself.")
    remove = skills_sub.add_parser("remove", help="Remove installed packs from this project")
    remove.add_argument("names", nargs="+", help="Installed pack ids")
    remove.add_argument("--from", dest="from_dir", metavar="DIR",
                        help="Source directory a custom pack was installed from, so its rules and "
                             "workflows can be traced and removed too")
