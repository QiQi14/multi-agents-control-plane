"""Render PR Blueprint specs to clean static HTML."""

from __future__ import annotations

import base64
import html
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from mermaid_render import DiagramResult, prepare_diagrams


GENERATED_COMMENT = "<!-- GENERATED FILE. DO NOT EDIT DIRECTLY. Edit the source spec and rebuild. -->"
ROOT = Path(__file__).resolve().parents[4]
TOKEN_RE = re.compile(r"{{([^{}]+)}}")


def render_report(
    spec: dict[str, Any],
    preset: dict[str, Any],
    source_path: Path,
    output_path: Path,
    warnings: list[str],
    css_path: Path,
    layout_path: Path,
) -> tuple[str, list[str]]:
    metadata = spec["metadata"]
    allowed_sections = preset.get("sections", [])
    sections: list[tuple[str, str, str]] = []

    source_label = repo_relative_label(source_path)
    ui_html = ""
    if "ui_states" in allowed_sections and spec.get("ui_states"):
        label = state_label(metadata)
        ui_html = render_ui_states(spec["ui_states"], warnings)
    diagram_results: list[DiagramResult] = []
    mermaid_runtime = ""
    if "architecture" in allowed_sections and spec.get("architecture"):
        diagram_results, mermaid_runtime, diagram_warnings = prepare_diagrams(spec["architecture"])
        warnings.extend(diagram_warnings)
    add_section(sections, "metadata", "Warnings", render_metadata(metadata, source_label, warnings))

    if "overview" in allowed_sections and spec.get("overview"):
        add_section(sections, "overview", "Overview", render_markdown(spec["overview"]))
    if "execution_summary" in allowed_sections and spec.get("execution_summary"):
        add_section(sections, "execution-summary", "Execution Summary", render_markdown(spec["execution_summary"]))
    if "file_inventory" in allowed_sections and spec.get("file_inventory"):
        add_section(sections, "file-inventory", "File Inventory", render_markdown(spec["file_inventory"]))
    if "api" in allowed_sections and spec.get("api"):
        add_section(sections, "api", "API Endpoints", render_api(spec["api"]))
    if "websocket" in allowed_sections and has_websocket(spec.get("websocket", {})):
        add_section(sections, "websocket", "WebSocket", render_websocket(spec["websocket"]))
    if "data_models" in allowed_sections and spec.get("data_models"):
        add_section(sections, "data-models", "Data Models", render_data_models(spec["data_models"]))
    if "validation" in allowed_sections and spec.get("validation"):
        add_section(sections, "validation", "Validation Matrix", render_validation(spec["validation"]))
    if "state_matrix" in allowed_sections and spec.get("state_matrix"):
        add_section(sections, "state-matrix", "State Matrix", render_state_matrix(spec["state_matrix"]))
    if ui_html:
        add_section(sections, "component-states", label, ui_html)
    if "function_log" in allowed_sections and spec.get("function_log"):
        add_section(sections, "function-log", "Function Log", render_function_log(spec["function_log"]))
    if "motion" in allowed_sections and spec.get("motion"):
        add_section(sections, "motion", "Motion", render_motion(spec["motion"]))
    if diagram_results:
        add_section(sections, "architecture", "Architecture", render_diagrams(diagram_results))
    if "qa" in allowed_sections and spec.get("qa"):
        add_section(sections, "qa", "QA Checklist", render_checklist(spec["qa"]))
    if "risks" in allowed_sections and spec.get("risks"):
        add_section(sections, "risks", "Risks", render_callout_list(spec["risks"], "risk"))
    if "open_questions" in allowed_sections and spec.get("open_questions"):
        add_section(sections, "open-questions", "Open Questions", render_callout_list(spec["open_questions"], "question"))
    if "decisions" in allowed_sections and spec.get("decisions"):
        add_section(sections, "decisions", "Decisions", render_callout_list(spec["decisions"], "decision"))
    if "notes" in allowed_sections and spec.get("notes"):
        add_section(sections, "notes", "Notes", render_markdown(spec["notes"]))

    css = css_path.read_text(encoding="utf-8")
    nav = "\n".join(
        f'<li class="lvl-2"><a href="#{section_id}">{escape(title)}</a></li>'
        for section_id, title, _ in sections
    )
    body = "\n".join(content for _, _, content in sections)
    sections_included = [section_id for section_id, _, _ in sections]
    template = layout_path.read_text(encoding="utf-8")
    values = {
        "GENERATED_COMMENT": GENERATED_COMMENT,
        "TITLE": escape(metadata.get("title", "PR Blueprint")),
        "SOURCE": render_source_label(source_label),
        "CSS": css,
        "NAV": nav,
        "META_STRIP": render_meta_strip(metadata, source_label),
        "GLANCE": render_glance(metadata),
        "BODY": body,
        "MERMAID_RUNTIME": mermaid_runtime,
    }
    return render_layout(template, values), sections_included


def render_layout(template: str, values: dict[str, str]) -> str:
    required = set(values)
    found = TOKEN_RE.findall(template)
    found_set = set(found)
    missing = sorted(required - found_set)
    unknown = sorted(found_set - required)
    problems = []
    if missing:
        problems.append(f"missing tokens: {', '.join(missing)}")
    if unknown:
        problems.append(f"unknown tokens: {', '.join(unknown)}")
    if problems:
        raise ValueError("invalid layout template: " + "; ".join(problems))
    return TOKEN_RE.sub(lambda match: values[match.group(1)], template)


def repo_relative_label(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.name


def render_source_label(source_label: str) -> str:
    escaped = escape(source_label)
    return re.sub(r"([/_.-])", r"\1<wbr>", escaped)


def add_section(sections: list[tuple[str, str, str]], section_id: str, title: str, inner_html: str) -> None:
    if inner_html.strip():
        sections.append((section_id, title, f'<section id="{section_id}" class="report-section"><h2>{escape(title)}</h2>{inner_html}</section>'))


def render_meta_strip(metadata: dict[str, Any], source_path: str) -> str:
    """The compact horizontal fact strip under the title, matching the reader's document head.

    A grid of eight cards reads as a dashboard. The reader states the same facts as one quiet strip,
    and two views of the same repository should not disagree about how metadata looks.
    """
    rows = [
        ("Preset", metadata.get("preset", "")),
        ("Kind", metadata.get("kind", "app")),
        ("Status", metadata.get("status", "")),
        ("Ticket", metadata.get("ticket", "")),
        ("Source", str(source_path)),
    ]
    return "".join(f"<div><dt>{escape(k)}</dt><dd>{escape(v)}</dd></div>" for k, v in rows if v)


def render_glance(metadata: dict[str, Any]) -> str:
    """The rail's at-a-glance list: who produced this and against what. Mirrors the reader's
    `Task at a glance` block, which uses the same `kv` definition list."""
    rows = [
        ("Author", metadata.get("author", "")),
        ("Reviewer", metadata.get("reviewer", "")),
        ("Platform", metadata.get("platform", "")),
        ("Version", metadata.get("version", "")),
    ]
    return "".join(f"<dt>{escape(k)}</dt><dd>{escape(v)}</dd>" for k, v in rows if v)


def render_metadata(metadata: dict[str, Any], source_path: str, warnings: list[str]) -> str:
    """Warnings only.

    Every metadata fact now appears once: the identifying ones in the head strip, the
    provenance ones in the rail. Repeating them as a card grid in a Summary section made the
    report open on a dashboard of things the reader had just been told. With no warnings this
    returns empty and `add_section` drops the section entirely.
    """
    del metadata, source_path  # rendered by render_meta_strip and render_glance
    if not warnings:
        return ""
    items = "".join(f"<li>{escape(w)}</li>" for w in warnings)
    return f'<div class="notice warn"><strong>Warnings</strong><ul>{items}</ul></div>'

def render_api(endpoints: list[dict[str, Any]]) -> str:
    cards = []
    for ep in endpoints:
        params = render_list(ep.get("parameters", []))
        headers = render_list(ep.get("headers", []))
        responses = []
        for response in ep.get("responses", []):
            body = code_block(response.get("body", ""), response.get("lang", ""))
            responses.append(f'<div class="response"><strong>{escape(response.get("status", ""))}</strong>{body}</div>')
        request = code_block(ep.get("request_body", ""), ep.get("request_body_lang", "")) if ep.get("request_body") else ""
        cards.append(
            f'<article class="endpoint"><h3><span class="method {escape(ep.get("method", "").lower())}">{escape(ep.get("method", ""))}</span> '
            f'<code>{escape(ep.get("path", ""))}</code></h3>'
            f'{paragraph(ep.get("description", ""))}'
            f'{subblock("Headers", headers)}{subblock("Parameters", params)}{subblock("Request Body", request)}'
            f'{subblock("Responses", "".join(responses))}</article>'
        )
    return "".join(cards)


def render_websocket(websocket: dict[str, Any]) -> str:
    parts = []
    if websocket.get("url"):
        parts.append(f'<p><strong>Endpoint:</strong> <code>{escape(websocket["url"])}</code></p>')
    if websocket.get("auth"):
        parts.append(f'<p><strong>Auth:</strong> {escape(websocket["auth"])}</p>')
    for msg in websocket.get("messages", []):
        parts.append(
            f'<article class="message"><h3>{escape(msg.get("direction", ""))}: <code>{escape(msg.get("topic", ""))}</code></h3>'
            f'{paragraph(msg.get("description", ""))}{code_block(msg.get("payload", ""), msg.get("payload_lang", ""))}</article>'
        )
    return "".join(parts)


def render_data_models(models: list[dict[str, str]]) -> str:
    return "".join(
        f'<article class="model"><h3>{escape(model.get("name", ""))}</h3>{paragraph(model.get("description", ""))}'
        f'{code_block(model.get("schema", ""), model.get("lang", ""))}</article>'
        for model in models
    )


def render_validation(rows: list[dict[str, Any]]) -> str:
    headers = ["Field", "Type", "Rules", "UI/Runtime Behavior", "Test ID"]
    body = []
    for row in rows:
        body.append([
            row.get("field") or row.get("field_name") or "",
            row.get("type", ""),
            render_list(row.get("rules", [])),
            render_list(row.get("ui_behavior", row.get("behavior", []))),
            row.get("test_id", ""),
        ])
    return table(headers, body)


def render_state_matrix(rows: list[dict[str, Any]]) -> str:
    body = []
    for row in rows:
        body.append([
            row.get("state") or row.get("name", ""),
            row.get("trigger", ""),
            render_list(row.get("expected", [])),
            render_list(row.get("notes", [])),
        ])
    return table(["State", "Trigger", "Expected Result", "Notes"], body)


def render_ui_states(rows: list[dict[str, Any]], warnings: list[str]) -> str:
    body = []
    for index, row in enumerate(rows, start=1):
        state = row.get("state") or row.get("component") or row.get("name", "")
        body.append([
            state,
            row.get("surface", row.get("target", "")),
            render_list(row.get("signals", [])),
            render_list(row.get("expected", row.get("notes", []))),
            render_evidence(str(state), row.get("evidence", []), index, warnings),
        ])
    return table(["State/Component", "Surface", "Signals", "Expected", "Evidence"], body)


def render_evidence(state: str, paths: list[str], row_index: int, warnings: list[str]) -> str:
    items = []
    for path in paths:
        candidate = ROOT.joinpath(*path.split("/"))
        try:
            encoded = base64.b64encode(candidate.read_bytes()).decode("ascii")
        except OSError:
            warning = f"Component States row {row_index} evidence '{path}': missing or unreadable; image omitted."
            if warning not in warnings:
                warnings.append(warning)
            items.append(f'<div class="missing-evidence">Missing evidence: {escape(path)}</div>')
            continue
        alt = f"Evidence for {state}: {Path(path).name}"
        items.append(
            f'<figure class="evidence-item"><img src="data:image/png;base64,{encoded}" alt="{escape(alt)}">'
            f'<figcaption>{escape(path)}</figcaption></figure>'
        )
    return f'<div class="evidence-list">{"".join(items)}</div>' if items else ""


def render_function_log(rows: list[dict[str, Any]]) -> str:
    body = []
    for row in rows:
        body.append([
            row.get("name", ""),
            row.get("trigger", ""),
            render_list(row.get("side_effects", [])),
            row.get("test_id", ""),
            row.get("priority", ""),
        ])
    return table(["Function", "Trigger", "Side Effects", "Test ID", "Priority"], body)


def render_motion(rows: list[dict[str, Any]]) -> str:
    body = []
    for row in rows:
        body.append([
            row.get("element") or row.get("target", ""),
            row.get("property", ""),
            row.get("duration", ""),
            row.get("easing", ""),
            row.get("delay", ""),
            render_list(row.get("notes", [])) if isinstance(row.get("notes"), list) else row.get("notes", ""),
        ])
    return table(["Element", "Property", "Duration", "Easing", "Delay", "Notes"], body)


def render_diagrams(diagrams: list[DiagramResult]) -> str:
    blocks = []
    for diagram in diagrams:
        warning = (
            f'<div class="notice warn diagram-warning">{escape(diagram.warning)}</div>'
            if diagram.warning
            else ""
        )
        if diagram.state == "static-svg":
            visual = (
                '<div class="diagram-output static-svg" data-diagram-state="static-svg">'
                '<div class="diagram-state">Static SVG / Mermaid CLI 11.16.0</div>'
                f"{diagram.svg}</div>"
            )
        else:
            source_class = "mermaid" if diagram.state == "browser-fallback" else "mermaid-source"
            state_label = "Browser fallback / Mermaid 11.16.0" if diagram.state == "browser-fallback" else "Source only"
            visual = (
                f'<div class="diagram-output {diagram.state}" data-diagram-state="{diagram.state}">'
                f'<div class="diagram-state">{state_label}</div>'
                f'<pre class="{source_class}">{escape(diagram.source)}</pre></div>'
            )
        blocks.append(
            f'<article class="diagram"><h3>{escape(diagram.title)}</h3>'
            f"{paragraph(diagram.description)}{warning}{visual}</article>"
        )
    return "".join(blocks)


def render_checklist(items: list[str]) -> str:
    return '<ul class="checklist">' + "".join(f"<li>{escape(item)}</li>" for item in items) + "</ul>"


def render_callout_list(items: list[str], class_name: str) -> str:
    return "".join(f'<div class="callout {class_name}">{escape(item)}</div>' for item in items)


def render_markdown(text: str) -> str:
    html_parts: list[str] = []
    in_code = False
    code_lang = ""
    code_lines: list[str] = []
    list_items: list[str] = []
    lines = text.splitlines()
    index = 0

    paragraph_lines: list[str] = []

    def flush_list() -> None:
        if list_items:
            html_parts.append("<ul>" + "".join(f"<li>{render_inline(item)}</li>" for item in list_items) + "</ul>")
            list_items.clear()

    def flush_paragraph() -> None:
        """Emit buffered prose as ONE paragraph.

        Markdown soft-wraps: consecutive non-blank prose lines are one paragraph, and only a blank
        line starts a new one. Emitting a <p> per source line shredded every hard-wrapped paragraph
        into fragments, which looks like broken content in the rendered report.
        """
        if paragraph_lines:
            html_parts.append(f"<p>{render_inline(' '.join(paragraph_lines))}</p>")
            paragraph_lines.clear()

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code:
                html_parts.append(code_block("\n".join(code_lines), code_lang))
                in_code = False
                code_lang = ""
                code_lines = []
            else:
                flush_paragraph()
                flush_list()
                in_code = True
                code_lang = stripped[3:].strip()
                code_lines = []
            index += 1
            continue
        if in_code:
            code_lines.append(line)
            index += 1
            continue
        if not stripped:
            flush_paragraph()
            flush_list()
            index += 1
            continue

        table_result = markdown_table_at(lines, index)
        if table_result:
            flush_paragraph()
            flush_list()
            rendered_table, consumed = table_result
            html_parts.append(rendered_table)
            index += consumed
            continue
        if stripped.startswith("### "):
            flush_paragraph()
            flush_list()
            html_parts.append(f"<h3>{render_inline(stripped[4:])}</h3>")
        elif stripped.startswith("## "):
            flush_paragraph()
            flush_list()
            html_parts.append(f"<h3>{render_inline(stripped[3:])}</h3>")
        elif stripped.startswith("- "):
            flush_paragraph()
            list_items.append(stripped[2:])
        else:
            flush_list()
            paragraph_lines.append(stripped)
        index += 1

    flush_paragraph()
    flush_list()
    if in_code:
        html_parts.append(code_block("\n".join(code_lines), code_lang))
    return "".join(html_parts)


def render_inline(text: str) -> str:
    parts: list[str] = []
    cursor = 0
    pattern = re.compile(r"`([^`\n]+)`|\*\*([^*\n]+)\*\*|\[([^\]\n]+)\]\(([^)\n]+)\)")
    for match in pattern.finditer(text):
        parts.append(escape(text[cursor:match.start()]))
        if match.group(1) is not None:
            parts.append(f"<code>{escape(match.group(1))}</code>")
        elif match.group(2) is not None:
            parts.append(f"<strong>{escape(match.group(2))}</strong>")
        elif safe_link_target(match.group(4)):
            parts.append(f'<a href="{escape(match.group(4))}">{escape(match.group(3))}</a>')
        else:
            parts.append(escape(match.group(0)))
        cursor = match.end()
    parts.append(escape(text[cursor:]))
    return "".join(parts)


def safe_link_target(target: str) -> bool:
    if target != target.strip() or any(ord(char) < 32 for char in target):
        return False
    parsed = urlsplit(target)
    if parsed.scheme:
        return parsed.scheme.lower() in {"http", "https", "mailto"}
    return bool(target) and not target.startswith(("/", "\\"))


def markdown_table_at(lines: list[str], index: int) -> tuple[str, int] | None:
    if index + 1 >= len(lines):
        return None
    headers = split_pipe_row(lines[index])
    delimiters = split_pipe_row(lines[index + 1])
    if not headers or not delimiters or len(headers) != len(delimiters):
        return None
    if not all(re.fullmatch(r":?-{3,}:?", cell) for cell in delimiters):
        return None

    rows: list[list[str]] = []
    cursor = index + 2
    while cursor < len(lines) and lines[cursor].strip() and "|" in lines[cursor]:
        row = split_pipe_row(lines[cursor])
        if not row or len(row) != len(headers):
            return None
        rows.append(row)
        cursor += 1
    head = "".join(f"<th>{render_inline(cell)}</th>" for cell in headers)
    body = "".join("<tr>" + "".join(f"<td>{render_inline(cell)}</td>" for cell in row) + "</tr>" for row in rows)
    rendered = f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'
    return rendered, cursor - index


def split_pipe_row(line: str) -> list[str] | None:
    stripped = line.strip()
    if "|" not in stripped:
        return None
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def table(headers: list[str], rows: list[list[Any]]) -> str:
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body = ""
    for row in rows:
        body += "<tr>" + "".join(f"<td>{cell if is_html(cell) else escape(cell)}</td>" for cell in row) + "</tr>"
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def render_list(items: Any) -> str:
    if not items:
        return ""
    if isinstance(items, str):
        items = [items]
    # Inline markdown, not plain escaping: a parameter or checklist line routinely names a
    # literal value in backticks, and rendering those as raw backticks looks like a bug.
    # render_inline escapes every branch, including an unsafe link target.
    return "<ul>" + "".join(f"<li>{render_inline(str(item))}</li>" for item in items if str(item).strip()) + "</ul>"


def subblock(title: str, content: str) -> str:
    if not content:
        return ""
    return f'<div class="subblock"><strong>{escape(title)}</strong>{content}</div>'


def code_block(code: str, lang: str = "") -> str:
    if not code:
        return ""
    return f'<pre class="code"><code data-lang="{escape(lang)}">{escape(code)}</code></pre>'


def paragraph(text: str) -> str:
    if not text:
        return ""
    return f"<p>{escape(text)}</p>"


def has_websocket(websocket: dict[str, Any]) -> bool:
    return bool(websocket.get("url") or websocket.get("messages"))


def state_label(metadata: dict[str, Any]) -> str:
    kind = str(metadata.get("kind", "app")).lower()
    if kind in {"engine", "game-engine", "canvas", "tool"}:
        return "Runtime States"
    return "Component States"


def is_html(value: Any) -> bool:
    return isinstance(value, str) and value.lstrip().startswith(("<ul", "<pre", "<div", "<p", "<article"))


def escape(value: Any) -> str:
    return html.escape(str(value), quote=True)
