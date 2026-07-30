"""Config-free control-plane primitives (task_181 Q181-5).

The reusable verification substrate (`scripts/verify_core.py`, maw_core) needs a handful of
neutral helpers — the task-contract YAML subset parser, the task-list coercion, and the
generated-file manifest byte parser/serializer. Those helpers historically lived in
`scripts/ai_plane/tasks.py` and `scripts/ai_plane/manifest.py`, both of which import
`scripts/ai_plane/config.py` and therefore transitively pull in the tool roster
(antigravity/codex/claude). Homing the neutral helpers here — with NO import of config, the tool
roster, prompts, dispatch, or any vendor/stack policy — lets the substrate depend on them without
inheriting that contextual graph, which is what makes the core cleanly extractable.

This module must never import scripts.ai_plane.config (or anything that does). Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

TASK_STATES = ("queue", "active", "done", "archive")
MANIFEST_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Task-contract YAML subset
# ---------------------------------------------------------------------------


def strip_yaml_inline_comment(value: str) -> str:
    """Remove a YAML inline comment while preserving hash characters inside quotes."""
    quote: str | None = None
    escaped = False
    for index, character in enumerate(value):
        if escaped:
            escaped = False
            continue
        if character == "\\" and quote == '"':
            escaped = True
            continue
        if character in ('"', "'"):
            if quote == character:
                quote = None
            elif quote is None:
                quote = character
            continue
        if character == "#" and quote is None and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
    return value


def parse_simple_yaml(path: Path) -> dict[str, Any]:
    """Parse the top-level scalar and list subset used by task contracts and receipts."""
    data: dict[str, Any] = {}
    if not path.exists():
        return data

    lines = path.read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or line.startswith((" ", "\t")):
            index += 1
            continue
        if ":" not in line:
            index += 1
            continue

        key, value = line.split(":", 1)
        key = key.strip()
        value = strip_yaml_inline_comment(value.strip())

        if value in (">", ">-", ">+", "|", "|-", "|+"):
            block: list[str] = []
            index += 1
            while index < len(lines):
                block_line = lines[index].rstrip()
                if block_line and not block_line.startswith((" ", "\t")):
                    break
                block.append(block_line.strip())
                index += 1
            if value.startswith(">"):
                paragraphs = [
                    " ".join(part for part in paragraph.splitlines() if part)
                    for paragraph in "\n".join(block).split("\n\n")
                ]
                data[key] = "\n\n".join(paragraphs).strip()
            else:
                data[key] = "\n".join(block).strip()
            continue

        if value:
            if value.startswith("[") and value.endswith("]"):
                try:
                    parsed = json.loads(value)
                except json.JSONDecodeError:
                    parsed = value
                data[key] = parsed
            elif len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                if value[0] == '"':
                    try:
                        data[key] = json.loads(value)
                    except json.JSONDecodeError:
                        data[key] = value[1:-1]
                else:
                    data[key] = value[1:-1]
            else:
                data[key] = value
            index += 1
            continue

        items: list[str] = []
        index += 1
        while index < len(lines):
            item_line = lines[index].rstrip()
            if item_line and not item_line.startswith((" ", "\t")):
                break
            item = item_line.strip()
            if item.startswith("- "):
                item_value = strip_yaml_inline_comment(item[2:].strip())
                if len(item_value) >= 2 and item_value[0] == item_value[-1] and item_value[0] in ('"', "'"):
                    item_value = item_value[1:-1]
                items.append(item_value)
            index += 1
        data[key] = items
    return data


def yaml_scalar(value: Any) -> str:
    text = ("" if value is None else str(value)).replace("\n", " ").strip()
    if re.search(r"(?:^|\s)#", text):
        return json.dumps(text, ensure_ascii=False)
    return text


def task_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if value in (None, ""):
        return []
    return [str(value)]


# ---------------------------------------------------------------------------
# Generated-file manifest bytes
# ---------------------------------------------------------------------------


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def parse_manifest_bytes(content: bytes, source: str) -> dict[str, dict[str, str]]:
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{source} is not valid UTF-8 JSON: {error}") from error
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "entries"}:
        raise ValueError(f"{source} must contain only schema_version and entries")
    if payload["schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise ValueError(f"{source} has unsupported schema_version {payload['schema_version']!r}")
    entries = payload["entries"]
    if not isinstance(entries, list):
        raise ValueError(f"{source} entries must be a list")
    result: dict[str, dict[str, str]] = {}
    for index, entry in enumerate(entries):
        where = f"{source} entries[{index}]"
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "command"}:
            raise ValueError(f"{where} must contain only path, sha256, and command")
        path_value = entry["path"]
        digest = entry["sha256"]
        command = entry["command"]
        if not isinstance(path_value, str) or not path_value or "\\" in path_value:
            raise ValueError(f"{where}.path must be a non-empty forward-slash path")
        candidate = Path(path_value)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError(f"{where}.path must be repository-relative and contained")
        if path_value == ".ai/_manifest.json":
            raise ValueError(f"{where}.path may not name the manifest itself")
        if path_value in result:
            raise ValueError(f"{source} has duplicate path {path_value!r}")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"{where}.sha256 must be 64 lowercase hex characters")
        if not isinstance(command, str) or not command:
            raise ValueError(f"{where}.command must be a non-empty string")
        result[path_value] = {"path": path_value, "sha256": digest, "command": command}
    return result


def serialize_manifest(entries: dict[str, dict[str, str]]) -> bytes:
    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "entries": [entries[path] for path in sorted(entries)],
    }
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
