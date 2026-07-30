from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import scripts.ai_plane.config as config_module
import scripts.ai_plane.constants as constants
from scripts.ai_plane.primitives import (  # config-free primitives (task_181 Q181-5)
    MANIFEST_SCHEMA_VERSION,
    parse_manifest_bytes,
    serialize_manifest,
    sha256_bytes,
)
from scripts.ai_plane.utils import rel

_ACTIVE_GENERATION: GenerationSession | None = None


def manifest_path() -> Path:
    return constants.AI / "_manifest.json"


def read_generated_manifest(*, warn: bool = False) -> dict[str, dict[str, str]]:
    path = manifest_path()
    if not path.exists():
        return {}
    try:
        return parse_manifest_bytes(path.read_bytes(), rel(path))
    except (OSError, ValueError) as error:
        if warn:
            print(f"WARNING: ignoring unreadable generated-file manifest: {error}", file=sys.stderr)
        return {}


def serialize_manifest(entries: dict[str, dict[str, str]]) -> bytes:
    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "entries": [entries[path] for path in sorted(entries)],
    }
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def atomic_write_manifest(entries: dict[str, dict[str, str]]) -> None:
    path = manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    content = serialize_manifest(entries)
    fd, temp_name = tempfile.mkstemp(prefix="._manifest.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


class GenerationSession:
    """One command's customization-safe generated writes and manifest transaction."""

    def __init__(self, command: str, *, replace_sync_entries: bool = False) -> None:
        self.command = command
        self.replace_sync_entries = replace_sync_entries
        self.original = read_generated_manifest(warn=True)
        self.generated: dict[str, dict[str, str]] = {}
        self.attempted: set[str] = set()
        self.preserved: set[str] = set()

    def __enter__(self) -> GenerationSession:
        global _ACTIVE_GENERATION
        if _ACTIVE_GENERATION is not None:
            raise RuntimeError("Nested generated-file sessions are not supported")
        _ACTIVE_GENERATION = self
        return self

    def __exit__(self, exc_type, _exc, _traceback) -> bool:
        global _ACTIVE_GENERATION
        _ACTIVE_GENERATION = None
        if exc_type is None:
            self.finish()
        return False

    def repository_path(self, path: Path) -> str:
        try:
            relative = path.resolve().relative_to(constants.ROOT.resolve())
        except ValueError as error:
            raise ValueError(f"Generated path escapes repository: {path}") from error
        path_value = relative.as_posix()
        if path_value == ".ai/_manifest.json":
            raise ValueError("The generated-file manifest may not record itself")
        return path_value

    def write_bytes(self, path: Path, content: bytes) -> bool:
        path_value = self.repository_path(path)
        self.attempted.add(path_value)
        recorded = self.original.get(path_value)
        if path.exists():
            current = path.read_bytes()
            current_hash = sha256_bytes(current)
            lf_hash = sha256_bytes(current.replace(b"\r\n", b"\n"))
            crlf_hash = sha256_bytes(current.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
            if recorded is not None and recorded["sha256"] not in (current_hash, lf_hash, crlf_hash):
                print(
                    f"WARNING: preserving user-modified generated file: {path_value}",
                    file=sys.stderr,
                )
                self.preserved.add(path_value)
                return False
            if recorded is None and current != content:
                print(
                    f"WARNING: preserving untracked existing generated file: {path_value}",
                    file=sys.stderr,
                )
                self.preserved.add(path_value)
                return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        self.generated[path_value] = {
            "path": path_value,
            "sha256": sha256_bytes(content),
            "command": self.command,
        }
        return True

    def finish(self) -> None:
        if self.replace_sync_entries:
            sync_commands = {adapter["commands"]["SYNC"] for adapter in config_module.ADAPTERS.values()}
            entries = {
                path: entry
                for path, entry in self.original.items()
                if entry["command"] not in sync_commands
                or path in self.preserved
                or path in self.generated
            }
            stale = [
                (path, entry)
                for path, entry in self.original.items()
                if entry["command"] in sync_commands
                and path not in self.attempted
            ]
            emptied: set[Path] = set()
            for path_value, entry in stale:
                target = constants.ROOT / path_value
                if target.is_file() and sha256_bytes(target.read_bytes()) == entry["sha256"]:
                    target.unlink()
                    emptied.add(target.parent)
                elif target.exists():
                    print(
                        f"WARNING: preserving user-modified stale generated file: {path_value}",
                        file=sys.stderr,
                    )
                entries.pop(path_value, None)
            _prune_empty_directories(emptied)
        else:
            entries = dict(self.original)
        entries.update(self.generated)
        entries.pop(".ai/_manifest.json", None)
        atomic_write_manifest(entries)


def _prune_empty_directories(directories: set[Path]) -> None:
    """Remove directories left empty by stale-file pruning, walking upward.

    Deleting a removed pack's generated files but keeping its empty directory leaves something
    that still reads as an installed pack. Only genuinely empty directories are removed, and the
    walk never ascends past the repository root.
    """
    root = constants.ROOT.resolve()
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        current = directory
        while True:
            try:
                resolved = current.resolve()
            except OSError:
                break
            if resolved == root or root not in resolved.parents:
                break
            if not current.is_dir() or any(current.iterdir()):
                break
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent

def generation_session(command: str, *, replace_sync_entries: bool = False) -> GenerationSession:
    return GenerationSession(command, replace_sync_entries=replace_sync_entries)


def write_generated_bytes(path: Path, content: bytes) -> None:
    if _ACTIVE_GENERATION is not None:
        _ACTIVE_GENERATION.write_bytes(path, content)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def platform_text_bytes(content: str) -> bytes:
    """Serialize generated text with LF endings on every platform.

    These bytes are hashed into `.ai/_manifest.json`, so they must not depend on the machine that
    produced them. Emitting `os.linesep` meant a repository generated on Windows failed its own
    manifest check on Linux and vice versa: every generated file mismatched, `doctor` reported
    wholesale drift, and `sync` rewrote the tree on the other platform. Git owns the working-copy
    convention; the recorded bytes stay LF.
    """
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    return (normalized.rstrip() + "\n").encode("utf-8")


def write_generated(path: Path, content: str) -> None:
    write_generated_bytes(path, platform_text_bytes(content))
