# GENERATED FROM .ai/. DO NOT EDIT DIRECTLY. Run `ai sync`.
"""Parse compact Markdown PR Blueprint specs."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


SECTION_ALIASES = {
    "metadata": "metadata",
    "description": "overview",
    "overview": "overview",
    "execution summary": "execution_summary",
    "execution": "execution_summary",
    "file inventory": "file_inventory",
    "files": "file_inventory",
    "api endpoints": "api",
    "api": "api",
    "api contract": "api",
    "websocket": "websocket",
    "websocket spec": "websocket",
    "websocket messages": "websocket",
    "data models": "data_models",
    "models": "data_models",
    "validation matrix": "validation",
    "validation": "validation",
    "function log": "function_log",
    "functions": "function_log",
    "state matrix": "state_matrix",
    "states": "state_matrix",
    "ui states": "ui_states",
    "component states": "ui_states",
    "tool states": "ui_states",
    "engine states": "ui_states",
    "canvas states": "ui_states",
    "motion spec": "motion",
    "motion": "motion",
    "architecture diagrams": "architecture",
    "architecture": "architecture",
    "flow diagram": "architecture",
    "flow diagrams": "architecture",
    "qa checklist": "qa",
    "qa": "qa",
    "risks": "risks",
    "open questions": "open_questions",
    "decisions": "decisions",
    "notes": "notes",
}

RECORD_CONTRACTS = {
    "validation": ("field", "type"),
    "function_log": ("name", "trigger"),
    "state_matrix": ("state", "trigger", "expected"),
    "ui_states": ("state", "surface", "expected"),
    "motion": ("element", "property", "duration"),
}

HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
MARKER_RE = re.compile(r'^\s*<!--\s+ai:(source|needs-judgment)\s+(.+?)\s+-->\s*$')
SOURCE_VALUE_RE = re.compile(r'^[A-Za-z0-9._/#-]+(?:,[A-Za-z0-9._/#-]+)*$')
NEEDS_VALUE_RE = re.compile(r'^reason="([^"]+)"$')


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "blueprint"


def parse_spec(path: str | Path) -> tuple[dict[str, Any], list[str]]:
    spec_path = Path(path)
    if not spec_path.exists():
        raise FileNotFoundError(f"Spec file not found: {spec_path}")

    text = spec_path.read_text(encoding="utf-8")
    errors: list[str] = []
    metadata, body = parse_frontmatter(text, errors)
    body, section_markers = extract_section_markers(body, errors)
    sections, unknown_headings, raw_section_list = split_sections(body)

    if "metadata" in sections:
        metadata.update(parse_metadata_block(sections["metadata"]))

    spec: dict[str, Any] = {
        "source": str(spec_path),
        "metadata": normalize_metadata(metadata),
        "overview": sections.get("overview", "").strip(),
        "execution_summary": sections.get("execution_summary", "").strip(),
        "file_inventory": sections.get("file_inventory", "").strip(),
        "api": parse_api(sections.get("api", ""), errors),
        "websocket": parse_websocket(sections.get("websocket", ""), errors),
        "data_models": parse_data_models(sections.get("data_models", ""), errors),
        "validation": parse_records(
            sections.get("validation", ""),
            start_keys=("field", "field_name"),
            list_fields=("rules", "ui_behavior", "behavior"),
            key_aliases={"field_name": "field"},
        ),
        "function_log": parse_records(
            sections.get("function_log", ""),
            start_keys=("name", "function"),
            list_fields=("side_effects", "side_effects"),
            key_aliases={"function": "name"},
        ),
        "state_matrix": parse_records(
            sections.get("state_matrix", ""),
            start_keys=("state", "name"),
            list_fields=("triggers", "expected", "notes"),
            key_aliases={"name": "state"},
        ),
        "ui_states": parse_records(
            sections.get("ui_states", ""),
            start_keys=("state", "component", "name"),
            list_fields=("signals", "expected", "notes", "evidence"),
            key_aliases={"component": "state", "name": "state"},
        ),
        "motion": parse_records(
            sections.get("motion", ""),
            start_keys=("element", "target"),
            list_fields=("notes",),
            key_aliases={"target": "element"},
        ),
        "architecture": parse_diagrams(sections.get("architecture", ""), errors),
        "qa": parse_plain_list(sections.get("qa", "")),
        "risks": parse_plain_list(sections.get("risks", "")),
        "open_questions": parse_plain_list(sections.get("open_questions", "")),
        "decisions": parse_plain_list(sections.get("decisions", "")),
        "notes": sections.get("notes", "").strip(),
        "_raw_sections": raw_section_list,
        "_unknown_headings": unknown_headings,
        "_section_markers": section_markers,
    }

    return spec, errors


def parse_frontmatter(text: str, errors: list[str]) -> tuple[dict[str, Any], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text

    metadata: dict[str, Any] = {}
    current_list: str | None = None
    end = None
    for index, line in enumerate(lines[1:], start=1):
        stripped = line.strip()
        if stripped == "---":
            end = index
            break
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") and current_list:
            metadata.setdefault(current_list, []).append(stripped[2:].strip())
            continue
        if ":" in line and not line.startswith(" "):
            key, value = line.split(":", 1)
            key = key.strip().lower().replace("-", "_")
            value = value.strip()
            if value:
                metadata[key] = value.strip("\"'")
                current_list = None
            else:
                metadata[key] = []
                current_list = key
    if end is None:
        errors.append("frontmatter: opening --- has no closing ---")
        return metadata, text
    return metadata, "\n".join(lines[end + 1 :])


def extract_section_markers(text: str, errors: list[str]) -> tuple[str, dict[str, list[dict[str, str]]]]:
    """Capture task-extractor comments as metadata and remove them from semantic body text."""
    clean_lines: list[str] = []
    markers: dict[str, list[dict[str, str]]] = {}
    current_section: str | None = None
    marker_prefix_open = False
    for line_no, line in enumerate(text.splitlines(), start=1):
        if line.startswith("# "):
            raw_heading = line[2:].strip()
            clean_heading = re.sub(r"[:\s]+$", "", raw_heading.lower())
            current_section = SECTION_ALIASES.get(clean_heading, slug_key(raw_heading))
            marker_prefix_open = True
            clean_lines.append(line)
            continue
        match = MARKER_RE.match(line)
        if not match:
            if line.strip():
                marker_prefix_open = False
            clean_lines.append(line)
            continue
        kind, value = match.groups()
        if current_section is None:
            errors.append(f"marker line {line_no}: marker must follow a top-level section heading")
            continue
        if not marker_prefix_open:
            errors.append(f"marker line {line_no}: marker must immediately follow its top-level section heading")
            continue
        if kind == "source":
            source_parts = [part.strip() for part in value.split(",")]
            compact = ",".join(source_parts)
            if any(not part or re.search(r"\s", part) for part in source_parts) or not SOURCE_VALUE_RE.fullmatch(compact):
                errors.append(f"marker line {line_no}: invalid ai:source value")
                continue
            marker = {"kind": kind, "value": compact}
        else:
            reason = NEEDS_VALUE_RE.fullmatch(value)
            if not reason:
                errors.append(f"marker line {line_no}: ai:needs-judgment requires reason=\"...\"")
                continue
            marker = {"kind": kind, "value": reason.group(1)}
        markers.setdefault(current_section, []).append(marker)
    return "\n".join(clean_lines), markers

def split_sections(text: str) -> tuple[dict[str, str], dict[str, str], list[str]]:
    sections: dict[str, list[str]] = {}
    unknown_headings: dict[str, str] = {}
    raw_section_keys: list[str] = []
    current = "overview"
    sections[current] = []

    for line in text.splitlines():
        if line.startswith("# "):
            raw_heading = line[2:].strip()
            clean_heading = re.sub(r"[:\s]+$", "", raw_heading.lower())
            if clean_heading in SECTION_ALIASES:
                current = SECTION_ALIASES[clean_heading]
            elif raw_heading.lower() in SECTION_ALIASES:
                current = SECTION_ALIASES[raw_heading.lower()]
            else:
                current = slug_key(raw_heading)
                unknown_headings[current] = raw_heading
            sections.setdefault(current, [])
            raw_section_keys.append(current)
            continue
        if sections[current] or line.strip():
            if current == "overview" and "overview" not in raw_section_keys:
                raw_section_keys.append("overview")
            sections.setdefault(current, []).append(line)

    result = {key: "\n".join(value).strip() for key, value in sections.items()}
    seen = set()
    ordered_keys = []
    for k in raw_section_keys:
        if k not in seen:
            seen.add(k)
            ordered_keys.append(k)

    return result, unknown_headings, ordered_keys


def slug_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def normalize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    aliases = {
        "target_version": "version",
        "target version": "version",
        "feature_type": "kind",
    }
    normalized: dict[str, Any] = {}
    for key, value in metadata.items():
        clean = key.strip().lower().replace("-", "_")
        clean = aliases.get(clean, aliases.get(clean.replace("_", " "), clean))
        normalized[clean] = value
    if "kind" not in normalized:
        normalized["kind"] = "app"
    return normalized


def parse_metadata_block(text: str) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            stripped = stripped[2:].strip()
        if ":" in stripped:
            key, value = stripped.split(":", 1)
            metadata[key.strip().lower().replace("-", "_")] = value.strip()
    return metadata


def parse_api(text: str, errors: list[str]) -> list[dict[str, Any]]:
    endpoints: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    sub = ""
    current_response: dict[str, str] | None = None
    in_code = False
    code_lang = ""
    code_lines: list[str] = []

    def flush_code() -> None:
        nonlocal code_lines, code_lang
        code = "\n".join(code_lines).strip()
        if current is None:
            return
        if sub == "request body":
            current["request_body"] = code
            current["request_body_lang"] = code_lang
        elif sub == "responses" and current_response is not None:
            current_response["body"] = code
            current_response["lang"] = code_lang
        code_lines = []
        code_lang = ""

    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code:
                flush_code()
                in_code = False
            else:
                in_code = True
                code_lang = stripped[3:].strip()
                code_lines = []
            continue
        if in_code:
            code_lines.append(line)
            continue

        if line.startswith("## "):
            heading = line[3:].strip()
            parts = heading.split(None, 1)
            if len(parts) < 2 or parts[0].upper() not in HTTP_METHODS:
                errors.append(f"api line {line_no}: endpoint heading must look like '## GET /path'")
                current = None
                continue
            current = {
                "method": parts[0].upper(),
                "path": parts[1].strip(),
                "description": "",
                "headers": [],
                "parameters": [],
                "request_body": "",
                "request_body_lang": "",
                "responses": [],
            }
            endpoints.append(current)
            sub = ""
            current_response = None
            continue

        if current is None:
            continue

        if line.startswith("### ") and not line.startswith("####"):
            sub = line[4:].strip().lower()
            current_response = None
            continue

        if line.startswith("#### "):
            current_response = {"status": line[5:].strip(), "body": "", "lang": ""}
            current["responses"].append(current_response)
            sub = "responses"
            continue

        if not stripped:
            continue

        if sub == "description":
            current["description"] = append_text(current["description"], line)
        elif sub in {"request headers", "headers"}:
            current["headers"].append(stripped[2:].strip() if stripped.startswith("- ") else stripped)
        elif sub in {"query parameters", "path parameters", "parameters"}:
            current["parameters"].append(stripped[2:].strip() if stripped.startswith("- ") else stripped)

    if in_code:
        errors.append("api: unclosed code fence")
    return endpoints


def parse_websocket(text: str, errors: list[str]) -> dict[str, Any]:
    spec = {"url": "", "auth": "", "messages": []}
    current: dict[str, str] | None = None
    sub = ""
    in_code = False
    code_lines: list[str] = []
    code_lang = ""

    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code:
                if current is not None:
                    current["payload"] = "\n".join(code_lines).strip()
                    current["payload_lang"] = code_lang
                    current["has_fence"] = True
                else:
                    errors.append(f"websocket line {line_no}: code fence found outside any message heading '### Direction: topic'")
                in_code = False
                code_lines = []
                code_lang = ""
            else:
                in_code = True
                code_lang = stripped[3:].strip()
                code_lines = []
                if current is None:
                    errors.append(f"websocket line {line_no}: code fence started outside any message heading '### Direction: topic'")
            continue
        if in_code:
            code_lines.append(line)
            continue
        if line.startswith("## "):
            content = line[3:].strip()
            lower = content.lower()
            if lower.startswith("ws endpoint url:"):
                spec["url"] = content.split(":", 1)[1].strip()
            elif lower.startswith("auth") and ":" in content:
                spec["auth"] = content.split(":", 1)[1].strip()
            continue
        if line.startswith("### "):
            content = line[4:].strip()
            direction = "message"
            topic = content
            if ":" in content:
                direction, topic = content.split(":", 1)
            current = {"direction": direction.strip(), "topic": topic.strip(), "description": "", "payload": "", "payload_lang": ""}
            spec["messages"].append(current)
            sub = ""
            continue
        if line.startswith("#### "):
            sub = line[5:].strip().lower()
            continue
        if current and sub == "description" and stripped:
            current["description"] = append_text(current["description"], line)

    if in_code:
        errors.append(f"websocket line {line_no}: unclosed code fence")
    return spec


def parse_data_models(text: str, errors: list[str]) -> list[dict[str, str]]:
    models: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    in_code = False
    code_lines: list[str] = []
    code_lang = ""

    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code:
                if current is not None:
                    current["schema"] = "\n".join(code_lines).strip()
                    current["lang"] = code_lang
                    current["has_fence"] = True
                else:
                    errors.append(f"data models line {line_no}: code fence found outside any '## ModelName' heading")
                in_code = False
                code_lines = []
                code_lang = ""
            else:
                in_code = True
                code_lang = stripped[3:].strip()
                code_lines = []
                if current is None:
                    errors.append(f"data models line {line_no}: code fence started outside any '## ModelName' heading")
            continue
        if in_code:
            code_lines.append(line)
            continue
        if line.startswith("## "):
            current = {"name": line[3:].strip(), "description": "", "schema": "", "lang": ""}
            models.append(current)
            continue
        if current and stripped:
            current["description"] = append_text(current["description"], line)

    if in_code:
        errors.append(f"data models line {line_no}: unclosed code fence")
    return models


def parse_records(
    text: str,
    start_keys: tuple[str, ...],
    list_fields: tuple[str, ...] = (),
    key_aliases: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    current_field = ""
    list_field_keys = {slug_key(field) for field in list_fields}
    aliases = key_aliases or {}

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        is_unindented_bullet = stripped.startswith("- ") and not line.startswith(" ") and not line.startswith("\t")
        if is_unindented_bullet:
            if current:
                records.append(current)
            current = {}
            current_field = ""

            maybe = stripped[2:].strip()
            if ":" in maybe:
                key, value = maybe.split(":", 1)
                key_slug = slug_key(key)
                if key_slug in aliases:
                    key_slug = aliases[key_slug]
                value = value.strip()
                if key_slug in list_field_keys:
                    current[key_slug] = [value] if value else []
                else:
                    current[key_slug] = value
                current_field = key_slug
            else:
                current["_raw_bullet"] = maybe
                current_field = "_raw_bullet"
            continue

        if ":" in line and current is not None:
            key, value = line.split(":", 1)
            key_slug = slug_key(key.strip())
            if key_slug in aliases:
                key_slug = aliases[key_slug]
            value = value.strip()
            if key_slug in list_field_keys:
                current[key_slug] = [value] if value else []
            else:
                current[key_slug] = value
            current_field = key_slug
            continue

        if current is not None and current_field:
            val_to_add = stripped[2:].strip() if stripped.startswith("- ") else stripped
            if current_field in list_field_keys:
                current.setdefault(current_field, []).append(val_to_add)
            else:
                current[current_field] = append_text(str(current.get(current_field, "")), val_to_add)

    if current:
        records.append(current)
    return records


def parse_diagrams(text: str, errors: list[str]) -> list[dict[str, str]]:
    diagrams: list[dict[str, str]] = []
    current_title = ""
    current_description: list[str] = []
    in_code = False
    code_lang = ""
    code_lines: list[str] = []

    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if line.startswith("## "):
            current_title = line[3:].strip()
            current_description = []
            continue
        if stripped.startswith("```"):
            if in_code:
                if code_lang.lower() == "mermaid":
                    diagrams.append({
                        "title": current_title,
                        "description": "\n".join(current_description).strip(),
                        "diagram": "\n".join(code_lines).strip(),
                    })
                in_code = False
                code_lang = ""
                code_lines = []
            else:
                in_code = True
                code_lang = stripped[3:].strip()
                code_lines = []
                if not current_title:
                    errors.append(f"architecture line {line_no}: mermaid diagram is missing a preceding '## Title'")
            continue
        if in_code:
            code_lines.append(line)
        elif stripped:
            current_description.append(line)

    if in_code:
        errors.append("architecture: unclosed code fence")
    return diagrams


def parse_plain_list(text: str) -> list[str]:
    items: list[str] = []
    paragraph: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- "):
            if paragraph:
                items.append(" ".join(paragraph))
                paragraph = []
            items.append(stripped[2:].strip())
        else:
            paragraph.append(stripped)
    if paragraph:
        items.append(" ".join(paragraph))
    return items


def append_text(existing: str, value: str) -> str:
    return (existing + "\n" + value).strip() if existing else value.strip()
