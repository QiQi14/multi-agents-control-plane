# GENERATED FROM .ai/. DO NOT EDIT DIRECTLY. Run `ai sync`.
"""Validate PR Blueprint specs with actionable errors."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from parse_spec import RECORD_CONTRACTS

ROOT = Path(__file__).resolve().parents[4]


MERMAID_STARTS = (
    "sequenceDiagram",
    "flowchart",
    "graph",
    "stateDiagram",
    "classDiagram",
    "erDiagram",
    "gantt",
    "journey",
    "pie",
    "mindmap",
    "timeline",
    "gitGraph",
    "requirementDiagram",
)


def load_preset(preset_dir: Path, name: str) -> dict[str, Any]:
    path = preset_dir / f"{name}.yaml"
    if not path.exists():
        return {}
    data: dict[str, Any] = {"sections": []}
    current = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith(":") and not stripped.startswith("- "):
            current = stripped[:-1]
            data.setdefault(current, [])
            continue
        if stripped.startswith("- ") and current:
            data.setdefault(current, []).append(stripped[2:].strip())
            continue
        if ":" in stripped:
            key, value = stripped.split(":", 1)
            data[key.strip()] = value.strip().strip('"')
    return data


def validate_spec(spec: dict[str, Any], preset_dir: Path, parse_errors: list[str]) -> tuple[list[str], list[str], dict[str, Any]]:
    errors = list(parse_errors)
    warnings: list[str] = []
    metadata = spec.get("metadata", {})

    for key in ("preset", "title", "status"):
        if not metadata.get(key):
            errors.append(f"metadata: missing required '{key}'")

    preset_name = str(metadata.get("preset", "")).strip()
    preset = load_preset(preset_dir, preset_name) if preset_name else {}
    if preset_name and not preset:
        supported = ", ".join(sorted(p.stem for p in preset_dir.glob("*.yaml")))
        errors.append(f"metadata.preset: unsupported preset '{preset_name}'. Supported presets: {supported}")
        preset = {"sections": []}

    if not metadata.get("author"):
        warnings.append("metadata.author is missing; report will show 'Unspecified'.")
    if not metadata.get("reviewer"):
        warnings.append("metadata.reviewer is missing; report will show 'Unspecified'.")

    validate_api(spec.get("api", []), errors, warnings)
    validate_websocket(spec.get("websocket", {}), errors)
    validate_data_models(spec.get("data_models", []), errors)
    for section, required_keys in RECORD_CONTRACTS.items():
        validate_records(section, spec.get(section, []), list(required_keys), errors)
    validate_evidence(spec.get("ui_states", []), errors, warnings)
    validate_diagrams(spec.get("architecture", []), errors)

    allowed = set(preset.get("sections", []))
    raw_sections = set(spec.get("_raw_sections", []))
    unknown_headings = spec.get("_unknown_headings", {})
    for section in sorted(raw_sections - allowed - {"metadata"}):
        if section in unknown_headings:
            original = unknown_headings[section]
            errors.append(f"section '{original}': unknown section heading")
        elif allowed:
            warnings.append(f"section '{section}' is not included by preset '{preset_name}' and will be omitted.")

    return errors, warnings, preset


def validate_api(endpoints: list[dict[str, Any]], errors: list[str], warnings: list[str]) -> None:
    for index, endpoint in enumerate(endpoints, start=1):
        label = f"api endpoint {index}"
        if not endpoint.get("method"):
            errors.append(f"{label}: missing HTTP method")
        if not endpoint.get("path"):
            errors.append(f"{label}: missing path")
        if not endpoint.get("responses"):
            warnings.append(f"{label} {endpoint.get('method', '')} {endpoint.get('path', '')}: no responses listed.")
        for response_index, response in enumerate(endpoint.get("responses", []), start=1):
            if not response.get("status"):
                errors.append(f"{label} response {response_index}: missing status heading, expected '#### 200 OK'")


def validate_websocket(websocket: dict[str, Any], errors: list[str]) -> None:
    for index, message in enumerate(websocket.get("messages", []), start=1):
        topic = message.get("topic")
        if not topic:
            errors.append(f"websocket message {index}: missing topic in '### Direction: topic'")
        if message.get("has_fence") or message.get("payload") or message.get("payload_lang"):
            if not message.get("payload_lang"):
                label = f" ('{topic}')" if topic else ""
                errors.append(f"websocket message {index}{label}: missing language in code fence")


def validate_data_models(models: list[dict[str, Any]], errors: list[str]) -> None:
    for index, model in enumerate(models, start=1):
        name = model.get("name")
        if not name:
            errors.append(f"data model {index}: missing model name heading '## ModelName'")
        schema = str(model.get("schema", "")).strip()
        if not schema:
            label = f" ('{name}')" if name else ""
            errors.append(f"data model {index}{label}: missing code fence schema (```ts / ```json)")
        if model.get("has_fence") or model.get("lang"):
            if not model.get("lang"):
                label = f" ('{name}')" if name else ""
                errors.append(f"data model {index}{label}: missing language in code fence")


def validate_records(section: str, rows: list[dict[str, Any]], required: list[str], errors: list[str]) -> None:
    for index, row in enumerate(rows, start=1):
        for key in required:
            val = row.get(key)
            if val is None or val == "" or (isinstance(val, list) and not val):
                errors.append(f"{section} row {index}: missing '{key}'")



def validate_evidence(rows: list[dict[str, Any]], errors: list[str], warnings: list[str]) -> None:
    root = ROOT.resolve()
    for index, row in enumerate(rows, start=1):
        evidence = row.get("evidence", [])
        if "evidence" in row and not evidence:
            errors.append(f"Component States row {index} evidence '': path must not be empty")
            continue
        for declared_path in evidence:
            path = str(declared_path)
            label = f"Component States row {index} evidence '{path}'"
            reason = invalid_evidence_path_reason(path)
            if reason:
                errors.append(f"{label}: {reason}")
                continue

            candidate = root.joinpath(*path.split("/"))
            try:
                resolved = candidate.resolve()
                resolved.relative_to(root)
            except (OSError, ValueError):
                errors.append(f"{label}: resolved path escapes the repository root")
                continue

            if candidate.exists():
                try:
                    with candidate.open("rb") as image:
                        image.read(1)
                except OSError:
                    warnings.append(f"{label}: missing or unreadable; image omitted.")
            else:
                warnings.append(f"{label}: missing or unreadable; image omitted.")


def invalid_evidence_path_reason(path: str) -> str:
    if not path:
        return "path must not be empty"
    if "\\" in path:
        return "path must use forward slashes"
    if path.startswith(("/", "//")) or os.path.isabs(path):
        return "path must be repository-root-relative"
    if re.match(r"^[A-Za-z]:", path):
        return "drive-qualified paths are not allowed"
    if urlsplit(path).scheme:
        return "URI paths are not allowed"
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return "path must not contain empty, '.' or '..' segments"
    if Path(path).suffix.lower() != ".png":
        return "path must end in .png"
    return ""


def validate_diagrams(diagrams: list[dict[str, str]], errors: list[str]) -> None:
    for index, diagram in enumerate(diagrams, start=1):
        if not diagram.get("title"):
            errors.append(f"architecture diagram {index}: missing title")
        code = diagram.get("diagram", "").strip()
        if not code:
            errors.append(f"architecture diagram {index}: empty mermaid diagram")
            continue
        first_line = code.splitlines()[0].strip()
        if not first_line.startswith(MERMAID_STARTS):
            errors.append(
                f"architecture diagram {index}: invalid Mermaid start '{first_line}'. "
                f"Expected one of: {', '.join(MERMAID_STARTS)}"
            )
