from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from scripts.ai_plane.utils import rel

_KEY_VALUE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):(?:\s*(.*))?$")


def _strip_inline_comment(value: str) -> str:
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
    if quote is not None:
        raise ValueError("unterminated quoted scalar")
    return value


def _split_inline_list(value: str) -> list[str]:
    inner = value[1:-1].strip()
    if not inner:
        return []

    items: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False
    for character in inner:
        if escaped:
            current.append(character)
            escaped = False
            continue
        if character == "\\" and quote == '"':
            current.append(character)
            escaped = True
            continue
        if character in ('"', "'"):
            current.append(character)
            if quote == character:
                quote = None
            elif quote is None:
                quote = character
            continue
        if character == "," and quote is None:
            item = "".join(current).strip()
            if not item:
                raise ValueError("empty inline-list item")
            items.append(item)
            current = []
            continue
        if character in "[]" and quote is None:
            raise ValueError("nested inline collections are unsupported")
        current.append(character)

    if quote is not None:
        raise ValueError("unterminated inline-list quote")
    item = "".join(current).strip()
    if not item:
        raise ValueError("empty inline-list item")
    items.append(item)
    return items


def _parse_scalar(value: str) -> str | list[str] | None:
    value = _strip_inline_comment(value).strip()
    if not value:
        raise ValueError("missing scalar value")
    if value.startswith("["):
        if not value.endswith("]"):
            raise ValueError("unterminated inline list")
        parsed_items = [_parse_scalar(item) for item in _split_inline_list(value)]
        if any(not isinstance(item, str) for item in parsed_items):
            raise ValueError("inline lists may contain only string scalars")
        return [item for item in parsed_items if isinstance(item, str)]
    if value.startswith(("{", "}")) or value.endswith(("}", "]")):
        raise ValueError("unsupported inline collection")
    if value[0] == '"':
        if value[-1] != '"':
            raise ValueError("unterminated double-quoted scalar")
        parsed = json.loads(value)
        if not isinstance(parsed, str):
            raise ValueError("frontmatter scalars must be strings")
        return parsed
    if value[0] == "'":
        if value[-1] != "'":
            raise ValueError("unterminated single-quoted scalar")
        return value[1:-1].replace("''", "'")
    if value in ("null", "Null", "NULL", "~"):
        return None
    return value


def _parse_frontmatter_block(block: str) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    active_list: list[Any] | None = None
    active_mapping: dict[str, Any] | None = None
    active_scalar_key: str | None = None

    for raw_line in block.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" \t"))

        if indent == 0 and not stripped.startswith("-"):
            match = _KEY_VALUE.fullmatch(stripped)
            if match is None:
                raise ValueError("invalid top-level frontmatter entry")
            key, raw_value = match.groups()
            if key in meta:
                raise ValueError(f"duplicate frontmatter key: {key}")
            if raw_value:
                parsed_value = _parse_scalar(raw_value)
                meta[key] = parsed_value
                active_list = None
                active_mapping = None
                active_scalar_key = key if isinstance(parsed_value, str) else None
            else:
                active_list = []
                meta[key] = active_list
                active_mapping = None
                active_scalar_key = None
            continue

        if active_scalar_key is not None and indent > 0:
            if stripped.startswith("-") or _KEY_VALUE.fullmatch(stripped):
                raise ValueError("unsupported nested structure after a scalar")
            continuation = _strip_inline_comment(stripped).strip()
            if continuation:
                meta[active_scalar_key] = f"{meta[active_scalar_key]} {continuation}"
            continue

        if active_list is None:
            raise ValueError("nested entry without a list-valued key")

        if stripped.startswith("-"):
            item_text = stripped[1:].strip()
            if not item_text:
                raise ValueError("empty block-list item")
            match = _KEY_VALUE.fullmatch(item_text)
            if match is None:
                scalar = _parse_scalar(item_text)
                if not isinstance(scalar, str):
                    raise ValueError("block lists may contain only strings or mappings")
                active_list.append(scalar)
                active_mapping = None
                continue
            key, raw_value = match.groups()
            if not raw_value:
                raise ValueError("nested collections in mappings are unsupported")
            active_mapping = {key: _parse_scalar(raw_value)}
            active_list.append(active_mapping)
            continue

        if active_mapping is None:
            raise ValueError("mapping continuation without a mapping list item")
        match = _KEY_VALUE.fullmatch(stripped)
        if match is None:
            raise ValueError("invalid mapping continuation")
        key, raw_value = match.groups()
        if not raw_value or key in active_mapping:
            raise ValueError("invalid or duplicate mapping field")
        active_mapping[key] = _parse_scalar(raw_value)

    return meta


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse the canonical stdlib-only frontmatter subset and return (meta, body)."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            block = text[3:end].strip()
            body = text[end + 4 :].lstrip("\n")
            try:
                return _parse_frontmatter_block(block), body
            except ValueError:
                return {}, body
    return {}, text


def summarize_md(path: Path, max_len: int = 180) -> tuple[str, str]:
    """Extract a (title, one-line summary) for the catalog without inlining the file."""
    text = path.read_text(encoding="utf-8-sig")
    meta, body = parse_frontmatter(text)
    lines = body.splitlines()

    title = path.stem.replace("_", " ").replace("-", " ").title()
    start = 0
    for idx, line in enumerate(lines):
        if line.strip().startswith("# "):
            title = line.strip().lstrip("#").strip()
            start = idx + 1
            break
    title = re.sub(r"^(Rule|Workflow|Agent Role|Project|Skill):\s*", "", title)

    summary = meta.get("description", "").strip()
    if not summary:
        para_lines: list[str] = []
        intro_done = False
        first_sub: str | None = None
        sub_count = 0
        in_fence = False
        for line in lines[start:]:
            s = line.strip()
            if s.startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            if not s:
                if para_lines:
                    intro_done = True
                continue
            if s.startswith("## "):
                sub_count += 1
                if first_sub is None:
                    first_sub = s.lstrip("#").strip()
                intro_done = True
                continue
            if s.startswith("#"):
                continue
            if sub_count == 0 and not intro_done:
                if re.match(r"^([-*+]|\d+[.)])\s", s) or s.startswith("|"):
                    if not para_lines:
                        para_lines.append(s)
                    intro_done = True
                    continue
                para_lines.append(s.lstrip("> ").strip())
        if para_lines:
            summary = " ".join(para_lines)
        elif first_sub:
            noun = "entry" if sub_count == 1 else "entries"
            summary = f"{sub_count} {noun}; e.g. “{first_sub}”"

    summary = re.sub(r"\s+", " ", summary).replace("**", "").replace("`", "").strip()
    if len(summary) > max_len:
        summary = summary[: max_len - 1].rstrip() + "…"
    return title, summary


def render_catalog(title: str, source: Path, only: set[str] | None = None) -> str:
    """Render a directory of `*.md` as a compact catalog (one line per file)."""
    files = sorted(p for p in source.glob("*.md") if p.is_file())
    if only is not None:
        files = [p for p in files if p.name in only]
    if not files:
        return f"## {title}\n\n_No files found._\n"
    lines = [f"## {title}", ""]
    for p in files:
        name, summary = summarize_md(p)
        if summary:
            lines.append(f"- **{name}** — {summary} — `{rel(p)}`")
        else:
            lines.append(f"- **{name}** — `{rel(p)}`")
    return "\n".join(lines) + "\n"
