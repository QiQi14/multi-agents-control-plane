#!/usr/bin/env python3
"""Knowledge-graph documentation projection CLI and rendering engine for AI Control Plane."""

from __future__ import annotations

import argparse
import html
import json
import posixpath
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import scripts.ai_plane.constants as constants
from scripts.ai_plane.frontmatter import parse_frontmatter
from scripts.ai_plane.knowledge_projection import build_knowledge_projection, write_knowledge_assets
from scripts.ai_plane.knowledge_projection.renderer import write_reader_presentation
from scripts.ai_plane.registry import generate_registry, registry_path, resolve_registry_source_path


class RequiredProjectIntelligenceError(RuntimeError):
    """A production reader build could not produce its required Project truth."""


def _required_project_intelligence_error(model: dict[str, Any]) -> str | None:
    project = model.get("truth_systems", {}).get("project_intelligence", {})
    boundary = project.get("boundary", {})
    state = str(boundary.get("state", "missing"))
    packages = project.get("packages") if isinstance(project.get("packages"), list) else []
    hierarchy = (
        project.get("semantic_hierarchy")
        if isinstance(project.get("semantic_hierarchy"), list)
        else []
    )
    workspace = project.get("views", {}).get("workspace", {}).get("visible_nodes", [])
    workspace = workspace if isinstance(workspace, list) else []
    if state == "fresh" and packages and hierarchy and len(workspace) > 1:
        return None
    errors = boundary.get("errors") if isinstance(boundary.get("errors"), list) else []
    detail = "; ".join(str(error) for error in errors) or "no exporter diagnostic was recorded"
    guidance = str(boundary.get("rebuild_guidance", "Rerun the docs build."))
    return (
        "Project Intelligence is required for the production reader, but the build produced "
        f"state={state}, packages={len(packages)}, hierarchy_rows={len(hierarchy)}, "
        f"workspace_nodes={len(workspace)}. {detail}. {guidance}"
    )


def compute_staleness_info(root_dir: Path | None = None) -> dict[str, Any]:
    """Compute commit anchor and changed-file count for staleness honesty banners."""
    target_root = root_dir if root_dir is not None else constants.ROOT
    commit = "0000000"
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=target_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            commit = res.stdout.strip()
    except Exception:
        pass

    changed_count = 0
    try:
        res = subprocess.run(
            ["git", "status", "--porcelain=v1"],
            cwd=target_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            lines = [line for line in res.stdout.splitlines() if line.strip()]
            changed_count = len(lines)
    except Exception:
        pass

    banner = f"generated at {commit}; {changed_count} source files changed since"
    return {
        "commit": commit,
        "changed_files_count": changed_count,
        "banner": banner,
    }


def extract_name_segments(text: str) -> list[str]:
    """Extract identifier-segment vocabulary from snake_case, CamelCase, kebab-case terms."""
    words = re.findall(r"[A-Za-z0-9_]+", text)
    segments: set[str] = set()
    for word in words:
        parts = re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?=[A-Z][a-z0-9]|\b)|[0-9]+", word)
        if not parts:
            parts = word.split("_")
        for p in parts:
            sub = p.strip("_").lower()
            if len(sub) > 1:
                segments.add(sub)
    return sorted(list(segments))


def compute_backlinks(documents: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    """Compute inverse relation backlinks: doc_id -> list of referencing doc entries."""
    backlinks: dict[str, list[dict[str, str]]] = {doc["id"]: [] for doc in documents}
    for doc in documents:
        src_id = doc["id"]
        src_title = doc.get("title", src_id)
        relations = doc.get("relations")
        if isinstance(relations, list):
            for rel in relations:
                if isinstance(rel, dict):
                    target = rel.get("target")
                    rel_type = rel.get("type", "relates_to")
                    if isinstance(target, str):
                        if target not in backlinks:
                            backlinks[target] = []
                        backlinks[target].append({
                            "source_id": src_id,
                            "source_title": src_title,
                            "type": rel_type,
                        })
    return backlinks


def _slugify_heading(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "section"


def render_markdown_body_to_html(
    text: str,
    doc_id_set: set[str] | None = None,
    *,
    anchor_headings: bool = False,
    omit_first_h1: bool = False,
) -> str:
    """Convert the supported Markdown subset to continuous, semantic HTML."""
    lines = text.splitlines()
    html_out: list[str] = []
    in_code = False
    code_lang = ""
    code_lines: list[str] = []
    list_tag: str | None = None
    list_item_lines: list[str] = []
    in_table = False
    para_lines: list[str] = []
    omitted_h1 = False
    heading_counts: dict[str, int] = {}

    def inline_format(val: str) -> str:
        escaped = html.escape(val)
        escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
        escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
        escaped = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", escaped)

        def link_replacer(match: re.Match[str]) -> str:
            label = match.group(1)
            target = match.group(2).strip()
            if target.startswith("http://") or target.startswith("https://") or target.startswith("#"):
                return f'<a href="{target}">{label}</a>'
            clean_tgt = target[:-3] if target.endswith(".md") else target
            if doc_id_set and clean_tgt in doc_id_set:
                return f'<a href="{clean_tgt}.html">{label}</a>'
            return f'<code>{label}</code>'

        return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link_replacer, escaped)

    def flush_paragraph() -> None:
        nonlocal para_lines
        if para_lines:
            html_out.append(f"<p>{inline_format(' '.join(para_lines))}</p>")
            para_lines = []

    def flush_list_item() -> None:
        nonlocal list_item_lines
        if list_item_lines:
            html_out.append(f"<li>{inline_format(' '.join(list_item_lines))}</li>")
            list_item_lines = []

    def close_list() -> None:
        nonlocal list_tag
        if list_tag is not None:
            flush_list_item()
            html_out.append(f"</{list_tag}>")
            list_tag = None

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            flush_paragraph()
            close_list()
            if in_code:
                code_content = html.escape("\n".join(code_lines))
                html_out.append(f'<pre><code class="language-{code_lang}">{code_content}</code></pre>')
                in_code = False
                code_lines = []
            else:
                in_code = True
                code_lang = stripped.lstrip("```").strip()
            continue

        if in_code:
            code_lines.append(line)
            continue

        if not stripped:
            flush_paragraph()
            close_list()
            if in_table:
                html_out.append("</tbody></table>")
                in_table = False
            continue

        heading = re.match(r"(#{1,6})\s+(.*)", stripped)
        if heading:
            flush_paragraph()
            close_list()
            if in_table:
                html_out.append("</tbody></table>")
                in_table = False
            level = len(heading.group(1))
            label = heading.group(2)
            if omit_first_h1 and level == 1 and not omitted_h1:
                omitted_h1 = True
                continue
            if omit_first_h1 and level == 1:
                level = 2
            anchor = ""
            if anchor_headings:
                base = _slugify_heading(re.sub(r"[*_`]", "", label))
                count = heading_counts.get(base, 0) + 1
                heading_counts[base] = count
                anchor = f' id="{base if count == 1 else f"{base}-{count}"}"'
            html_out.append(f"<h{level}{anchor}>{inline_format(label)}</h{level}>")
            continue

        bullet = re.match(r"^([-*]|\d+\.)\s+(.*)", stripped)
        if bullet:
            flush_paragraph()
            wanted_tag = "ol" if bullet.group(1)[0].isdigit() else "ul"
            if list_tag != wanted_tag:
                close_list()
                list_tag = wanted_tag
                html_out.append(f"<{list_tag}>")
            else:
                flush_list_item()
            list_item_lines = [bullet.group(2)]
            continue

        if stripped.startswith("|") and stripped.endswith("|"):
            flush_paragraph()
            close_list()
            if "---" in stripped:
                continue
            cols = [c.strip() for c in stripped.split("|")[1:-1]]
            if not in_table:
                html_out.append("<table><thead><tr>")
                html_out.extend(f"<th>{inline_format(c)}</th>" for c in cols)
                html_out.append("</tr></thead><tbody>")
                in_table = True
            else:
                html_out.append("<tr>")
                html_out.extend(f"<td>{inline_format(c)}</td>" for c in cols)
                html_out.append("</tr>")
            continue

        if list_tag is not None:
            list_item_lines.append(stripped)
        else:
            if in_table:
                html_out.append("</tbody></table>")
                in_table = False
            para_lines.append(stripped)

    flush_paragraph()
    close_list()
    if in_table:
        html_out.append("</tbody></table>")
    if in_code:
        code_content = html.escape("\n".join(code_lines))
        html_out.append(f'<pre><code class="language-{code_lang}">{code_content}</code></pre>')

    return "\n".join(html_out)

_MARKDOWN_LINK_TARGET_RE = re.compile(
    r"\[[^\]]+\]\(\s*(?:<([^>]+)>|([^\s)]+))"
)
_REGISTERED_MD_REFERENCE_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])"
    r"(?:\.{0,2}/|/)?(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.md"
    r"(?:[?#][^\s`'\"<>)\]]+)?"
)


def _normalize_registry_path(value: str) -> str:
    raw = html.unescape(str(value)).strip().strip("`'\"<>").replace("\\", "/")
    raw = raw.split("#", 1)[0].split("?", 1)[0]
    if not raw:
        return ""
    normalized = posixpath.normpath(raw).replace("\\", "/")
    if normalized in ("", "."):
        return ""
    return normalized.lstrip("/")


def _registry_path_index(docs_by_id: dict[str, dict[str, Any]]) -> dict[str, str]:
    paths: dict[str, str] = {}
    for doc_id, document in docs_by_id.items():
        path = _normalize_registry_path(str(document.get("path", "")))
        if not path:
            continue
        paths[path] = doc_id
        if path.startswith(".ai/"):
            paths.setdefault(path[4:], doc_id)
    return paths


def _resolve_registered_reference(
    reference: str,
    source_path: str,
    docs_by_id: dict[str, dict[str, Any]],
    path_to_id: dict[str, str],
) -> str | None:
    raw = html.unescape(str(reference)).strip().strip("`'\"<>")
    if not raw or raw.startswith(("#", "http:", "https:", "mailto:", "data:")):
        return None
    raw = raw.split("#", 1)[0].split("?", 1)[0].replace("\\", "/")
    if raw in docs_by_id:
        return raw

    direct = _normalize_registry_path(raw)
    source_registry_path = _normalize_registry_path(source_path)
    candidates = [direct]
    if direct and not direct.startswith((".ai/", "project/")):
        candidates.append(
            _normalize_registry_path(posixpath.join(posixpath.dirname(source_registry_path), direct))
        )
        if source_registry_path.startswith(".ai/"):
            candidates.append(_normalize_registry_path(".ai/" + direct))
    for candidate in candidates:
        if candidate in path_to_id:
            return path_to_id[candidate]
    return None


def _extract_registered_reference_targets(
    body: str,
    source_path: str,
    docs_by_id: dict[str, dict[str, Any]],
    path_to_id: dict[str, str],
) -> set[str]:
    references = {
        match.group(1) or match.group(2)
        for match in _MARKDOWN_LINK_TARGET_RE.finditer(body)
    }
    references.update(match.group(0) for match in _REGISTERED_MD_REFERENCE_RE.finditer(body))
    return {
        target
        for reference in references
        if (target := _resolve_registered_reference(
            reference, source_path, docs_by_id, path_to_id
        )) is not None
    }


def _document_corpus(document: dict[str, Any]) -> str:
    return str(document.get("corpus", "control-plane"))


def _collect_edges(docs: list[dict[str, Any]], ai_root: Path | None = None) -> list[dict[str, Any]]:
    """Collect corpus-aware authored edges and same-corpus inferred Markdown edges."""
    edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str, str]] = set()
    docs_by_id = {str(document["id"]): document for document in docs}
    path_to_id = _registry_path_index(docs_by_id)
    corpus_by_id = {doc_id: _document_corpus(document) for doc_id, document in docs_by_id.items()}
    target_ai = ai_root if ai_root is not None else constants.AI

    def append_edge(edge: dict[str, Any]) -> None:
        key = (
            str(edge["source"]), str(edge["target"]),
            str(edge["type"]), str(edge["provenance"]),
        )
        if key not in seen_edges:
            seen_edges.add(key)
            edges.append(edge)

    for document in docs:
        source = str(document["id"])
        source_corpus = _document_corpus(document)
        relations = document.get("relations")
        if isinstance(relations, list):
            for relation in relations:
                if not isinstance(relation, dict):
                    continue
                target = relation.get("target")
                if not isinstance(target, str):
                    continue
                target_corpus = corpus_by_id.get(target)
                bridge = target_corpus is not None and target_corpus != source_corpus
                append_edge({
                    "source": source,
                    "target": target,
                    "type": str(relation.get("type", "relates_to")),
                    "provenance": "authored",
                    "source_corpus": source_corpus,
                    "target_corpus": target_corpus or "external",
                    "bridge": bridge,
                })

        rel_path = str(document.get("path", ""))
        source_path = resolve_registry_source_path(target_ai, rel_path)
        if not source_path.exists():
            continue
        try:
            raw_text = source_path.read_text(encoding="utf-8")
            _, body = parse_frontmatter(raw_text)
            authored_targets = {
                str(relation["target"])
                for relation in relations or []
                if isinstance(relation, dict) and isinstance(relation.get("target"), str)
            }
            inferred_targets = _extract_registered_reference_targets(
                body, rel_path, docs_by_id, path_to_id
            )
            for target in sorted(inferred_targets):
                if target == source or target in authored_targets or target not in docs_by_id:
                    continue
                target_corpus = corpus_by_id[target]
                if target_corpus != source_corpus:
                    continue
                append_edge({
                    "source": source,
                    "target": target,
                    "type": "references",
                    "provenance": "inferred",
                    "source_corpus": source_corpus,
                    "target_corpus": target_corpus,
                    "bridge": False,
                })
        except (OSError, UnicodeDecodeError):
            pass

    return edges

def generate_svg_graph(
    focus_doc_id: str | None,
    registry_data: dict[str, Any],
    domain: str | None = None,
    ai_root: Path | None = None,
    corpus: str | None = None,
) -> str:
    """Generate self-contained build-time SVG relation graph with content-free slug node IDs."""
    all_docs = registry_data.get("documents", [])
    if corpus is None:
        focus_doc = next((doc for doc in all_docs if doc.get("id") == focus_doc_id), None)
        corpus = _document_corpus(focus_doc) if focus_doc is not None else "control-plane"
    docs = [doc for doc in all_docs if _document_corpus(doc) == corpus]
    if domain:
        docs = [d for d in docs if d.get("domain") == domain]

    nodes_map: dict[str, dict[str, Any]] = {}
    for d in docs:
        nodes_map[d["id"]] = d

    node_ids_in_corpus = set(nodes_map)
    edges = [
        edge for edge in _collect_edges(all_docs, ai_root)
        if not edge.get("bridge") and edge["source"] in node_ids_in_corpus and edge["target"] in node_ids_in_corpus
    ]

    if focus_doc_id and focus_doc_id in nodes_map:
        connected = {focus_doc_id}
        for e in edges:
            if e["source"] == focus_doc_id:
                connected.add(e["target"])
            elif e["target"] == focus_doc_id:
                connected.add(e["source"])
        nodes_map = {k: v for k, v in nodes_map.items() if k in connected}
        edges = [e for e in edges if e["source"] in connected and e["target"] in connected]

    node_ids = sorted(list(nodes_map.keys()))
    if not node_ids:
        return '<svg width="400" height="100"><text x="20" y="50" fill="#666">No graph nodes to display</text></svg>'

    width = 800
    row_height = 60
    margin_x = 150
    margin_y = 50
    cols = 3
    node_coords: dict[str, tuple[int, int]] = {}

    for idx, nid in enumerate(node_ids):
        r = idx // cols
        c = idx % cols
        x = margin_x + c * 250
        y = margin_y + r * row_height
        node_coords[nid] = (x, y)

    max_y = max(y for _, y in node_coords.values()) + 80 if node_coords else 200

    svg_lines: list[str] = [
        f'<svg width="{width}" height="{max_y}" viewBox="0 0 {width} {max_y}" xmlns="http://www.w3.org/2000/svg">',
        '  <defs>',
        '    <marker id="arrow" viewBox="0 0 10 10" refX="6" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">',
        '      <path d="M 0 0 L 10 5 L 0 10 z" fill="#888" />',
        '    </marker>',
        '  </defs>',
        '  <style>',
        '    .node-rect { fill: #2d3748; stroke: #4a5568; stroke-width: 1.5; rx: 6px; ry: 6px; }',
        '    .node-focus { fill: #2b6cb0; stroke: #63b3ed; stroke-width: 2; }',
        '    .node-text { fill: #edf2f7; font-family: system-ui, sans-serif; font-size: 12px; text-anchor: middle; }',
        '    .edge-authored { stroke: #4a5568; stroke-width: 1.5; marker-end: url(#arrow); }',
        '    .edge-inferred { stroke: #a0aec0; stroke-width: 1.5; stroke-dasharray: 4; marker-end: url(#arrow); }',
        '    .edge-text { fill: #a0aec0; font-family: system-ui, sans-serif; font-size: 10px; text-anchor: middle; }',
        '  </style>',
    ]

    for e in edges:
        src = e["source"]
        tgt = e["target"]
        if src in node_coords and tgt in node_coords:
            x1, y1 = node_coords[src]
            x2, y2 = node_coords[tgt]
            # P1-3 fix: same-row edges get a curved path offset instead of wrong y2
            css_cls = "edge-authored" if e["provenance"] == "authored" else "edge-inferred"
            if y1 == y2:
                # Same row: route edge via an arc above the nodes
                arc_y = max(margin_y - 30, 10)
                svg_lines.append(f'  <path d="M {x1} {y1} Q {(x1 + x2) // 2} {arc_y} {x2} {y2}" fill="none" class="{css_cls}" />')
            else:
                svg_lines.append(f'  <line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" class="{css_cls}" />')

    for nid, (x, y) in node_coords.items():
        is_focus = (nid == focus_doc_id)
        rect_cls = "node-focus" if is_focus else "node-rect"
        doc_item = nodes_map.get(nid, {})
        title = doc_item.get("title", nid)
        if len(title) > 24:
            title = title[:21] + "..."
        svg_lines.append(f'  <g transform="translate({x - 80}, {y - 20})">')
        svg_lines.append(f'    <rect width="160" height="40" class="{rect_cls}" />')
        svg_lines.append(f'    <text x="80" y="24" class="node-text">{html.escape(title)}</text>')
        svg_lines.append('  </g>')

    svg_lines.append('</svg>')
    return "\n".join(svg_lines)


def emit_graph_artifacts(
    registry_data: dict[str, Any],
    out_dir: Path,
    ai_root: Path | None = None,
) -> list[Path]:
    """Emit separate corpus, domain, and local SVG graph artifacts."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    documents = registry_data.get("documents", [])

    global_path = out_dir / "graph-global.svg"
    global_path.write_text(
        generate_svg_graph(None, registry_data, ai_root=ai_root, corpus="control-plane"),
        encoding="utf-8",
    )
    written.append(global_path)

    corpora = sorted({_document_corpus(document) for document in documents})
    for corpus in corpora:
        corpus_path = out_dir / f"graph-corpus-{corpus}.svg"
        corpus_path.write_text(
            generate_svg_graph(None, registry_data, ai_root=ai_root, corpus=corpus),
            encoding="utf-8",
        )
        written.append(corpus_path)

        domains = sorted({
            str(document.get("domain", ""))
            for document in documents
            if _document_corpus(document) == corpus and document.get("domain")
        })
        for domain in domains:
            corpus_domain_path = out_dir / f"graph-corpus-{corpus}-domain-{domain}.svg"
            corpus_domain_path.write_text(
                generate_svg_graph(None, registry_data, domain=domain, ai_root=ai_root, corpus=corpus),
                encoding="utf-8",
            )
            written.append(corpus_domain_path)
            if corpus == "control-plane":
                compatibility_path = out_dir / f"graph-domain-{domain}.svg"
                compatibility_path.write_text(
                    generate_svg_graph(None, registry_data, domain=domain, ai_root=ai_root, corpus=corpus),
                    encoding="utf-8",
                )
                written.append(compatibility_path)

    for document in documents:
        doc_id = str(document["id"])
        local_path = out_dir / f"graph-local-{doc_id}.svg"
        local_path.write_text(
            generate_svg_graph(doc_id, registry_data, ai_root=ai_root),
            encoding="utf-8",
        )
        written.append(local_path)

    return written

DOCS_CSS = r"""
:root {
  color-scheme: light dark;
  --canvas: #f4f6f8;
  --surface: #ffffff;
  --surface-subtle: #f8fafc;
  --surface-raised: #ffffff;
  --text: #172033;
  --text-muted: #5c667a;
  --border: #d8dee8;
  --border-strong: #b8c1d1;
  --accent: #2563eb;
  --accent-strong: #1d4ed8;
  --accent-soft: #e8f0ff;
  --success: #16794d;
  --success-soft: #e8f6ef;
  --warning: #a15c00;
  --warning-soft: #fff3d6;
  --danger: #b42318;
  --focus: #7c3aed;
  --shadow: 0 8px 24px rgba(23, 32, 51, .08);
  --radius: 10px;
  --sans: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  --serif: ui-serif, Georgia, Cambria, "Times New Roman", serif;
}
@media (prefers-color-scheme: dark) {
  :root {
    --canvas: #111722;
    --surface: #192130;
    --surface-subtle: #151d2a;
    --surface-raised: #202a3b;
    --text: #edf2f9;
    --text-muted: #aeb9ca;
    --border: #344157;
    --border-strong: #506079;
    --accent: #70b7ff;
    --accent-strong: #9bcbff;
    --accent-soft: #183a5e;
    --success: #68d5a5;
    --success-soft: #173d30;
    --warning: #ffc76b;
    --warning-soft: #493515;
    --danger: #ff9a91;
    --focus: #c4a7ff;
    --shadow: 0 10px 30px rgba(0, 0, 0, .24);
  }
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body { margin: 0; background: var(--canvas); color: var(--text); font-family: var(--sans); line-height: 1.5; }
a { color: var(--accent); text-underline-offset: .18em; }
a:hover { color: var(--accent-strong); }
a:focus-visible, button:focus-visible, input:focus-visible, select:focus-visible, summary:focus-visible {
  outline: 3px solid var(--focus); outline-offset: 2px;
}
.skip-link { position: fixed; left: 1rem; top: -5rem; z-index: 20; padding: .65rem 1rem; background: var(--surface); border: 1px solid var(--border); border-radius: 6px; }
.skip-link:focus { top: 1rem; }
.site-header { background: var(--surface); border-bottom: 1px solid var(--border); }
.site-header__inner { max-width: 1440px; margin: auto; padding: 1rem clamp(1rem, 3vw, 2.5rem); display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
.brand { color: var(--text); text-decoration: none; font-weight: 750; letter-spacing: -.02em; }
.header-link { font-size: .9rem; }
.build-state { margin: 0; padding: .62rem clamp(1rem, 3vw, 2.5rem); border-bottom: 1px solid var(--border); color: var(--text-muted); font-size: .85rem; }
.build-state strong { color: var(--text); }
.build-state--stale { background: var(--warning-soft); color: var(--warning); border-color: color-mix(in srgb, var(--warning) 32%, transparent); }
.build-state--current { background: var(--success-soft); color: var(--success); border-color: color-mix(in srgb, var(--success) 26%, transparent); }
.page-shell { max-width: 1440px; margin: auto; padding: clamp(1.25rem, 3vw, 2.5rem); }
.eyebrow { margin: 0 0 .45rem; color: var(--accent); font-size: .78rem; font-weight: 750; letter-spacing: .09em; text-transform: uppercase; }
.hero { display: flex; align-items: end; justify-content: space-between; gap: 2rem; margin-bottom: 1.5rem; }
.hero h1 { margin: 0; max-width: 18ch; font-size: clamp(2rem, 4vw, 3.6rem); line-height: 1.02; letter-spacing: -.045em; }
.hero p { max-width: 62ch; color: var(--text-muted); }
.hero__actions { display: flex; flex-wrap: wrap; gap: .65rem; }
.button { display: inline-flex; align-items: center; justify-content: center; min-height: 2.55rem; padding: .58rem .9rem; border: 1px solid var(--border-strong); border-radius: 7px; background: var(--surface); color: var(--text); font: inherit; font-size: .9rem; font-weight: 650; text-decoration: none; cursor: pointer; }
.button:hover { border-color: var(--accent); color: var(--accent-strong); }
.button--primary { background: var(--accent); border-color: var(--accent); color: white; }
.button--full { width:100%; margin-top:1rem; }
.library-layout { display: grid; grid-template-columns: 210px minmax(0, 1fr); gap: 1.5rem; align-items: start; }
.category-rail { position: sticky; top: 1rem; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: .85rem; }
.category-rail h2 { margin: .2rem .45rem .65rem; font-size: .8rem; text-transform: uppercase; letter-spacing: .08em; color: var(--text-muted); }
.category-rail a { display: flex; justify-content: space-between; gap: .6rem; padding: .48rem .55rem; border-radius: 6px; color: var(--text); text-decoration: none; font-size: .88rem; }
.category-rail a:hover { background: var(--accent-soft); color: var(--accent-strong); }
.library-main { min-width: 0; }
.filters { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 1rem; box-shadow: var(--shadow); }
.search-field label, .filter-field label { display: block; margin-bottom: .35rem; color: var(--text-muted); font-size: .78rem; font-weight: 700; }
.search-field input, .filter-field select { width: 100%; min-height: 2.65rem; padding: .58rem .7rem; border: 1px solid var(--border-strong); border-radius: 7px; background: var(--surface-subtle); color: var(--text); font: inherit; }
.search-field input { font-size: 1rem; }
.filter-row { display: grid; grid-template-columns: repeat(3, minmax(120px, 1fr)) auto; gap: .75rem; align-items: end; margin-top: .75rem; }
.result-summary { display: flex; justify-content: space-between; gap: 1rem; align-items: center; margin: 1rem 0 .5rem; color: var(--text-muted); font-size: .9rem; }
.catalog-group { margin-top: 1.25rem; }
.catalog-group__heading { display: flex; align-items: baseline; justify-content: space-between; gap: 1rem; margin: 0 0 .45rem; padding-bottom: .4rem; border-bottom: 1px solid var(--border); }
.catalog-group__heading h2 { margin: 0; font-size: 1.05rem; text-transform: capitalize; }
.catalog-group__heading span { color: var(--text-muted); font-size: .8rem; }
.catalog-list { display: grid; gap: .45rem; }
.catalog-item { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: .8rem 1.25rem; padding: .8rem .9rem; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; }
.catalog-item:hover { border-color: var(--border-strong); box-shadow: 0 4px 14px rgba(23, 32, 51, .06); }
.catalog-item h3 { margin: 0; font-size: .98rem; line-height: 1.35; }
.catalog-item h3 a { color: var(--text); text-decoration: none; }
.catalog-item h3 a:hover { color: var(--accent-strong); }
.catalog-item p { margin: .22rem 0 0; max-width: 78ch; color: var(--text-muted); font-size: .86rem; }
.catalog-meta { display: flex; flex-wrap: wrap; gap: .4rem .7rem; justify-content: end; align-content: start; color: var(--text-muted); font-size: .75rem; }
.badge { display: inline-flex; align-items: center; padding: .15rem .45rem; border: 1px solid var(--border); border-radius: 999px; background: var(--surface-subtle); color: var(--text-muted); font-size: .73rem; }
.badge--active { color: var(--success); border-color: color-mix(in srgb, var(--success) 35%, var(--border)); background: var(--success-soft); }
.empty-state { margin-top: 1rem; padding: 2rem; text-align: center; background: var(--surface); border: 1px dashed var(--border-strong); border-radius: var(--radius); }
[hidden] { display: none !important; }
.reader-layout { max-width: 1440px; margin: auto; padding: 1.25rem clamp(1rem, 3vw, 2.5rem) 3rem; display: grid; grid-template-columns: 200px minmax(0, 72ch) minmax(220px, 290px); justify-content: center; gap: clamp(1.25rem, 3vw, 2.5rem); align-items: start; }
.reader-nav, .relation-rail { position: sticky; top: 1rem; min-width: 0; }
.panel { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 1rem; }
.panel + .panel { margin-top: .75rem; }
.panel h2 { margin: 0 0 .7rem; font-size: .84rem; text-transform: uppercase; letter-spacing: .07em; color: var(--text-muted); }
.toc ol { margin: 0; padding-left: 1.15rem; }
.toc li { margin: .35rem 0; font-size: .82rem; }
.toc a { color: var(--text-muted); text-decoration: none; }
.toc a:hover { color: var(--accent-strong); }
.breadcrumb { margin-bottom: 1.25rem; color: var(--text-muted); font-size: .86rem; }
.article { min-width: 0; }
.article-header { padding-bottom: 1.25rem; border-bottom: 1px solid var(--border); }
.article-header h1 { margin: .25rem 0 .65rem; font-size: clamp(2rem, 5vw, 3.25rem); line-height: 1.05; letter-spacing: -.04em; }
.article-summary { margin: 0; color: var(--text-muted); font: 1.08rem/1.65 var(--serif); }
.metadata { display: flex; flex-wrap: wrap; gap: .5rem 1rem; margin: 1rem 0 0; }
.metadata div { display: grid; grid-template-columns: auto auto; gap: .35rem; font-size: .76rem; }
.metadata dt { color: var(--text-muted); }
.metadata dd { margin: 0; font-weight: 650; }
.prose { padding-top: 1rem; font: 17px/1.76 var(--serif); overflow-wrap: anywhere; }
.prose h2, .prose h3, .prose h4, .prose h5, .prose h6 { scroll-margin-top: 1rem; font-family: var(--sans); line-height: 1.25; letter-spacing: -.02em; }
.prose h2 { margin: 2.2rem 0 .75rem; font-size: 1.55rem; }
.prose h3 { margin: 1.7rem 0 .6rem; font-size: 1.25rem; }
.prose p, .prose ul, .prose ol { margin: .85rem 0; }
.prose li + li { margin-top: .3rem; }
code { padding: .12rem .32rem; border-radius: 4px; background: var(--accent-soft); color: var(--text); font: .86em ui-monospace, SFMono-Regular, Consolas, monospace; }
pre { padding: 1rem; overflow: auto; border: 1px solid var(--border); border-radius: 8px; background: var(--surface-subtle); }
pre code { padding: 0; background: none; }
table { display: block; max-width: 100%; overflow-x: auto; border-collapse: collapse; font-family: var(--sans); font-size: .85rem; }
th, td { padding: .55rem .7rem; border: 1px solid var(--border); text-align: left; }
.relation-list { margin: 0; padding: 0; list-style: none; }
.relation-list li { padding: .48rem 0; border-top: 1px solid var(--border); font-size: .8rem; overflow-wrap: anywhere; }
.relation-list li:first-child { border-top: 0; }
.graph-page {
  --cluster-agent:#35a7d6; --cluster-workflow:#39a875; --cluster-rule:#d49a35;
  --cluster-specification:#8a73d7; --cluster-decision:#d66d69; --cluster-memory:#7c8c9f;
  --cluster-project:#4f79d8; --cluster-migration:#c56cae;
  --edge-authored:#718096; --edge-inferred:#8a73d7;
}
@media (prefers-color-scheme: dark) {
  .graph-page {
    --cluster-agent:#5fc9f0; --cluster-workflow:#67d59d; --cluster-rule:#f0bd5f;
    --cluster-specification:#b29bf0; --cluster-decision:#f08d88; --cluster-memory:#aab6c5;
    --cluster-project:#7fa1f3; --cluster-migration:#e197ca;
    --edge-authored:#aeb8c7; --edge-inferred:#b49bdd;
  }
}
.graph-page-layout { max-width:none; margin:0; padding:0; }
.graph-header { min-height:76px; padding:14px clamp(1rem, 2vw, 1.4rem); background:var(--surface); border-bottom:1px solid var(--border); display:flex; align-items:center; justify-content:space-between; gap:1.25rem; }
.graph-heading { min-width:0; }
.graph-heading h1 { margin:0 0 .2rem; font-size:clamp(1.15rem, 2vw, 1.35rem); letter-spacing:-.02em; }
.graph-heading p { margin:0; color:var(--text-muted); font-size:.8rem; }
.graph-actions { display:flex; align-items:center; justify-content:flex-end; gap:.5rem; flex-wrap:wrap; }
.graph-filter-menu { position:relative; }
.graph-filter-menu summary { list-style:none; }
.graph-filter-menu summary::-webkit-details-marker { display:none; }
.graph-filter-panel { position:absolute; z-index:20; right:0; top:calc(100% + .45rem); width:min(330px, calc(100vw - 2rem)); padding:1rem; background:var(--surface-raised); border:1px solid var(--border); border-radius:var(--radius); box-shadow:var(--shadow); }
.graph-filter-panel fieldset { margin:0; padding:0; border:0; }
.graph-filter-panel fieldset + fieldset { margin-top:.9rem; padding-top:.9rem; border-top:1px solid var(--border); }
.graph-filter-panel legend { margin-bottom:.55rem; font-size:.76rem; font-weight:750; color:var(--text-muted); text-transform:uppercase; letter-spacing:.06em; }
.graph-filter-options { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:.45rem .75rem; }
.graph-filter-options label { display:flex; align-items:center; gap:.45rem; font-size:.82rem; }
.graph-workspace { display:grid; grid-template-columns:minmax(0,1fr) 286px; min-height:calc(100vh - 180px); }
.graph-canvas-wrap { min-width:0; position:relative; overflow:hidden; background:radial-gradient(circle at 1px 1px, color-mix(in srgb, var(--border) 54%, transparent) 1px, transparent 0); background-size:22px 22px; }
.graph-canvas { width:100%; height:max(620px, calc(100vh - 180px)); position:relative; overflow:hidden; cursor:grab; user-select:none; touch-action:none; }
.graph-canvas.is-interacting { cursor:grabbing; }
.force-canvas { position:absolute; inset:0; width:100%; height:100%; display:block; touch-action:none; outline:none; }
.graph-hint { position:absolute; left:.9rem; top:.9rem; max-width:310px; padding:.48rem .6rem; border:1px solid var(--border); border-radius:7px; background:color-mix(in srgb, var(--surface-raised) 91%, transparent); color:var(--text-muted); font-size:.7rem; box-shadow:var(--shadow); pointer-events:none; }
.graph-tooltip { position:absolute; z-index:8; display:none; width:min(280px, calc(100% - 24px)); padding:.7rem .75rem; border:1px solid var(--border-strong); border-radius:9px; background:var(--surface-raised); box-shadow:var(--shadow); pointer-events:none; }
.graph-tooltip.is-visible { display:block; }
.graph-tooltip-type { margin-bottom:.2rem; color:var(--text-muted); font-size:.64rem; font-weight:760; letter-spacing:.065em; text-transform:uppercase; }
.graph-tooltip-title { font-size:.82rem; line-height:1.35; font-weight:750; }
.graph-tooltip-meta { margin-top:.25rem; color:var(--text-muted); font-size:.69rem; }
.graph-tooltip-summary { margin-top:.45rem; color:var(--text); font-size:.75rem; line-height:1.42; }
.graph-controls { position:absolute; left:.9rem; bottom:.9rem; display:flex; gap:.35rem; padding:.3rem; border:1px solid var(--border); border-radius:9px; background:var(--surface-raised); box-shadow:var(--shadow); }
.graph-controls button { min-width:32px; height:32px; padding:0 .5rem; border:0; border-radius:6px; background:transparent; color:var(--text); cursor:pointer; font:700 .8rem var(--sans); }
.graph-controls button:hover { background:var(--surface-subtle); }
.graph-legend { position:absolute; right:.9rem; bottom:.9rem; display:flex; align-items:center; flex-wrap:wrap; gap:.55rem .7rem; padding:.5rem .65rem; border:1px solid var(--border); border-radius:8px; background:var(--surface-raised); box-shadow:var(--shadow); font-size:.68rem; color:var(--text-muted); }
.legend-line { width:22px; border-top:2px solid var(--edge-authored); }
.legend-line.inferred { border-top-color:var(--edge-inferred); border-top-style:dashed; }
.legend-dot { width:8px; height:8px; border-radius:50%; background:currentColor; box-shadow:0 0 0 2px color-mix(in srgb, currentColor 18%, transparent); }
.legend-dot.agent { color:var(--cluster-agent); } .legend-dot.workflow { color:var(--cluster-workflow); }
.legend-dot.rule { color:var(--cluster-rule); } .legend-dot.specification { color:var(--cluster-specification); }
.legend-dot.decision { color:var(--cluster-decision); } .legend-dot.memory { color:var(--cluster-memory); }
.legend-dot.project { color:var(--cluster-project); } .legend-dot.migration { color:var(--cluster-migration); }
.graph-details { border-left:1px solid var(--border); background:var(--surface); padding:1.4rem 1.1rem; min-width:0; }
.graph-details .eyebrow { color:var(--text); font-family:var(--serif); }
.graph-details h2 { margin:0 0 .3rem; font-size:1rem; }
.graph-details .detail-id { color:var(--text-muted); font: .7rem ui-monospace,monospace; margin-bottom:1rem; overflow-wrap:anywhere; }
.graph-selected-summary { color:var(--text-muted); font-size:.78rem; line-height:1.5; }
.detail-list { border-top:1px solid var(--border); margin-top:1rem; }
.detail-row { display:flex; justify-content:space-between; gap:.75rem; padding:.58rem 0; border-bottom:1px solid var(--border); font-size:.75rem; }
.detail-row span:first-child { color:var(--text-muted); }
.detail-row span:last-child { font-weight:650; text-align:right; }
.graph-empty { display:none; position:absolute; inset:0; z-index:10; place-items:center; padding:1.5rem; text-align:center; background:color-mix(in srgb, var(--surface-subtle) 90%, transparent); }
.graph-empty.is-visible { display:grid; }
.graph-empty-card { max-width:420px; border:1px solid var(--border); border-radius:12px; background:var(--surface); padding:1.75rem; box-shadow:var(--shadow); }
.graph-empty h2 { margin:0 0 .45rem; font-size:1.1rem; }
.graph-empty p { color:var(--text-muted); }
@media (max-width: 1050px) {
  .reader-layout { grid-template-columns: minmax(0, 72ch) minmax(210px, 270px); }
  .reader-nav { position: static; grid-column: 1 / -1; }
  .toc { columns: 2; }
  .graph-workspace { grid-template-columns: 1fr; }
  .graph-canvas { height:64vh; min-height:520px; }
  .graph-details { border-left:0; border-top:1px solid var(--border); }
}
@media (max-width: 760px) {
  .hero { display: block; }
  .hero__actions { margin-top: 1rem; }
  .library-layout { grid-template-columns: 1fr; }
  .category-rail { position: static; }
  .category-rail nav { display: flex; overflow-x: auto; gap: .3rem; padding-bottom: .2rem; }
  .category-rail a { flex: 0 0 auto; border: 1px solid var(--border); }
  .filter-row { grid-template-columns: 1fr; }
  .catalog-item { grid-template-columns: 1fr; }
  .catalog-meta { justify-content: start; }
  .reader-layout { grid-template-columns: minmax(0, 1fr); padding-top: 1rem; }
  .reader-nav, .relation-rail { position: static; }
  .toc { columns: 1; }
  .relation-rail { order: 3; }
  .metadata { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .graph-header { align-items:start; flex-direction:column; }
  .graph-actions { justify-content:flex-start; }
  .graph-canvas { min-height:460px; height:62vh; }
  .graph-legend { left:.9rem; right:auto; max-width:calc(100% - 1.8rem); }
}
@media (max-width: 430px) {
  .site-header__inner, .build-state, .page-shell, .reader-layout, .graph-page-layout { padding-left: .9rem; padding-right: .9rem; }
  .hero h1 { font-size: 2.15rem; }
  .article-header h1 { font-size: 2rem; }
  .metadata { grid-template-columns: 1fr; }
  .prose { font-size: 16px; line-height: 1.7; }
}
""".strip()

DOCS_JS = r"""
(() => {
  function initCatalog() {
    const root = document.querySelector('[data-catalog]');
    if (!root) return;
    const search = document.querySelector('#catalog-search');
    const type = document.querySelector('#filter-type');
    const domain = document.querySelector('#filter-domain');
    const status = document.querySelector('#filter-status');
    const reset = document.querySelector('#filters-reset');
    const summary = document.querySelector('#result-count');
    const empty = document.querySelector('#empty-state');
    const emptyReset = document.querySelector('#empty-reset');
    const items = [...document.querySelectorAll('[data-document]')];
    const groups = [...document.querySelectorAll('[data-catalog-group]')];
    const normalize = value => value.toLocaleLowerCase().trim();
    function apply() {
      const query = normalize(search.value);
      let visible = 0;
      items.forEach(item => {
        const matches = (!query || normalize(item.dataset.search).includes(query)) &&
          (!type.value || item.dataset.type === type.value) &&
          (!domain.value || item.dataset.domain === domain.value) &&
          (!status.value || item.dataset.status === status.value);
        item.hidden = !matches;
        if (matches) visible += 1;
      });
      groups.forEach(group => { group.hidden = !group.querySelector('[data-document]:not([hidden])'); });
      summary.textContent = `${visible} document${visible === 1 ? '' : 's'}`;
      empty.hidden = visible !== 0;
    }
    [search, type, domain, status].forEach(control => control.addEventListener('input', apply));
    root.querySelector('form').addEventListener('submit', event => event.preventDefault());
    reset.addEventListener('click', () => {
      search.value = ''; type.value = ''; domain.value = ''; status.value = '';
      apply(); search.focus();
    });
    emptyReset.addEventListener('click', () => reset.click());
    document.addEventListener('keydown', event => {
      if (event.key === '/' && document.activeElement !== search) { event.preventDefault(); search.focus(); }
      if (event.key === 'Escape' && document.activeElement === search && search.value) { search.value = ''; apply(); }
    });
    apply();
  }

  function initGraph() {
    const wrap = document.getElementById('graphCanvas');
    const canvas = document.getElementById('forceGraphCanvas');
    const dataNode = document.getElementById('graph-data');
    if (!wrap || !canvas || !dataNode) return;
    const data = JSON.parse(dataNode.textContent);
    const ctx = canvas.getContext('2d');
    const tooltip = document.getElementById('graphTooltip');
    const summary = document.getElementById('graphSummary');
    const empty = document.getElementById('graphEmpty');
    const openDocument = document.getElementById('openSelectedDocument');
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const typeLabels = {
      agent:'Agent role', workflow:'Workflow', rule:'Rule', specification:'Specification',
      decision:'Decision', memory:'Memory', project:'Project documentation', migration:'Migration record'
    };
    const typeOrder = ['agent','workflow','rule','specification','decision','memory','project','migration'];
    const nodes = data.nodes.map(node => ({...node, vx:0, vy:0, radius:node.id === data.focus_id ? 12 : 8}));
    const byId = new Map(nodes.map(node => [node.id, node]));
    const edges = data.edges.map(edge => ({...edge, length:135 + (hashText(`${edge.source}:${edge.target}:${edge.type}`) % 90)}));
    let visibleNodes = nodes;
    let visibleEdges = edges;
    let visibleIds = new Set(nodes.map(node => node.id));
    let selected = byId.get(data.focus_id) || nodes[0] || null;
    let hovered = null;
    let dragged = null;
    let panning = false;
    let pointerId = null;
    let startPointer = {x:0,y:0};
    let startCamera = {x:0,y:0};
    let camera = {x:0,y:0,scale:1};
    let width = 1, height = 1, dpr = 1, energy = 1;
    let colors = {};
    let lastTime = performance.now();

    function hashText(value) {
      let hash = 2166136261;
      for (let i=0; i<value.length; i++) { hash ^= value.charCodeAt(i); hash = Math.imul(hash, 16777619); }
      return hash >>> 0;
    }
    nodes.forEach((node, index) => {
      if (node.id === data.focus_id) { node.x = 0; node.y = 0; return; }
      const seed = hashText(node.id);
      const typeIndex = Math.max(0, typeOrder.indexOf(node.type));
      const base = (typeIndex / typeOrder.length) * Math.PI * 2;
      const spread = ((seed % 1000) / 1000 - .5) * 1.05;
      const ring = 105 + (index % 4) * 64 + ((seed >>> 10) % 32);
      node.x = Math.cos(base + spread) * ring;
      node.y = Math.sin(base + spread) * ring;
    });

    function css(name, fallback) {
      return getComputedStyle(document.body).getPropertyValue(name).trim() || fallback;
    }
    function refreshTheme() {
      colors = {
        text:css('--text','#172033'), muted:css('--text-muted','#5c667a'), surface:css('--surface-raised','#fff'),
        border:css('--border','#d8dee8'), authored:css('--edge-authored','#718096'), inferred:css('--edge-inferred','#8a73d7'),
        focus:css('--accent','#2563eb'), agent:css('--cluster-agent','#35a7d6'), workflow:css('--cluster-workflow','#39a875'),
        rule:css('--cluster-rule','#d49a35'), specification:css('--cluster-specification','#8a73d7'),
        decision:css('--cluster-decision','#d66d69'), memory:css('--cluster-memory','#7c8c9f'),
        project:css('--cluster-project','#4f79d8'), migration:css('--cluster-migration','#c56cae')
      };
      draw();
    }
    function colorFor(node) { return colors[node.type] || colors.muted; }
    function worldToScreen(x,y) { return {x:width/2+(x-camera.x)*camera.scale, y:height/2+(y-camera.y)*camera.scale}; }
    function screenToWorld(x,y) { return {x:camera.x+(x-width/2)/camera.scale, y:camera.y+(y-height/2)/camera.scale}; }
    function nodeRadius(node) { return Math.max(node.id === data.focus_id ? 5.8 : 3.2, Math.min(18,node.radius*camera.scale)); }
    function findNodeAt(x,y) {
      let best=null, distance=Infinity;
      for (const node of visibleNodes) {
        const p=worldToScreen(node.x,node.y), d=Math.hypot(x-p.x,y-p.y), r=Math.max(11,nodeRadius(node)+6);
        if (d<=r && d<distance) { best=node; distance=d; }
      }
      return best;
    }
    function degrees(node) {
      let outgoing=0, incoming=0;
      visibleEdges.forEach(edge => { if(edge.source===node.id) outgoing++; if(edge.target===node.id) incoming++; });
      return {outgoing,incoming};
    }
    function setSelected(node) {
      if (!node) return;
      selected=node;
      const degree=degrees(node);
      document.getElementById('selectedNodeTitle').textContent=node.title;
      document.getElementById('selectedNodeId').textContent=node.id;
      document.getElementById('selectedNodeSummary').textContent=node.summary;
      document.getElementById('selectedNodeType').textContent=typeLabels[node.type] || node.type;
      document.getElementById('selectedNodeDomain').textContent=node.domain.replaceAll('-', ' ').replace(/(^|\s)\w/g, value => value.toUpperCase());
      document.getElementById('selectedNodeOutgoing').textContent=`${degree.outgoing} relationship${degree.outgoing===1?'':'s'}`;
      document.getElementById('selectedNodeBacklinks').textContent=`${degree.incoming} backlink${degree.incoming===1?'':'s'}`;
      document.getElementById('selectedNodeStatus').textContent=node.status.replaceAll('-', ' ').replace(/^\w/, value => value.toUpperCase());
      openDocument.href=node.href;
      draw();
    }
    function showTooltip(node,x,y) {
      const degree=degrees(node);
      document.getElementById('graphTooltipType').textContent=typeLabels[node.type] || node.type;
      document.getElementById('graphTooltipTitle').textContent=node.title;
      document.getElementById('graphTooltipMeta').textContent=`${node.id} · ${node.domain} · ${degree.incoming+degree.outgoing} relationships`;
      document.getElementById('graphTooltipSummary').textContent=node.summary;
      tooltip.classList.add('is-visible');
      const maxX=Math.max(12,width-Math.min(280,width-24)-12);
      tooltip.style.left=`${Math.min(maxX,x+15)}px`;
      tooltip.style.top=`${Math.max(12,Math.min(height-145,y+15))}px`;
    }
    function hideTooltip() { tooltip.classList.remove('is-visible'); }
    function roundedRect(x,y,w,h,r) { ctx.beginPath(); ctx.roundRect(x,y,w,h,r); }
    function drawArrow(x1,y1,x2,y2,targetRadius,color) {
      const angle=Math.atan2(y2-y1,x2-x1), endX=x2-Math.cos(angle)*(targetRadius+3), endY=y2-Math.sin(angle)*(targetRadius+3);
      const size=camera.scale<.62?3.5:5;
      ctx.fillStyle=color; ctx.beginPath(); ctx.moveTo(endX,endY);
      ctx.lineTo(endX-Math.cos(angle-.55)*size,endY-Math.sin(angle-.55)*size);
      ctx.lineTo(endX-Math.cos(angle+.55)*size,endY-Math.sin(angle+.55)*size); ctx.closePath(); ctx.fill();
    }
    function drawEdgeLabel(edge,a,b) {
      const mx=(a.x+b.x)/2, my=(a.y+b.y)/2;
      ctx.font='600 10px '+getComputedStyle(document.body).fontFamily;
      const tw=ctx.measureText(edge.type).width;
      roundedRect(mx-tw/2-5,my-9,tw+10,18,5); ctx.fillStyle=colors.surface; ctx.fill();
      ctx.strokeStyle=colors.border; ctx.lineWidth=1; ctx.stroke();
      ctx.fillStyle=colors.muted; ctx.textAlign='center'; ctx.textBaseline='middle'; ctx.fillText(edge.type,mx,my+.5);
    }
    function draw() {
      if (!ctx) return;
      ctx.setTransform(dpr,0,0,dpr,0,0); ctx.clearRect(0,0,width,height);
      const lowZoom=camera.scale<.68, showEdgeLabels=camera.scale>=.95;
      for (const edge of visibleEdges) {
        const source=byId.get(edge.source), target=byId.get(edge.target);
        if (!source || !target) continue;
        const a=worldToScreen(source.x,source.y), b=worldToScreen(target.x,target.y);
        const connected=selected && (edge.source===selected.id || edge.target===selected.id);
        ctx.save(); ctx.globalAlpha=lowZoom?(connected?.58:.24):(connected?.9:.42);
        ctx.strokeStyle=edge.provenance==='inferred'?colors.inferred:colors.authored;
        ctx.lineWidth=connected?1.7:1.05; ctx.setLineDash(edge.provenance==='inferred'?[5,5]:[]);
        ctx.beginPath(); ctx.moveTo(a.x,a.y); ctx.lineTo(b.x,b.y); ctx.stroke();
        drawArrow(a.x,a.y,b.x,b.y,nodeRadius(target),edge.provenance==='inferred'?colors.inferred:colors.authored);
        ctx.restore(); if(showEdgeLabels && connected) drawEdgeLabel(edge,a,b);
      }
      const showAllLabels=camera.scale>=1.28, showFocusLabels=camera.scale>=.78;
      for (const node of visibleNodes) {
        const p=worldToScreen(node.x,node.y); if(p.x<-70||p.x>width+70||p.y<-70||p.y>height+70) continue;
        const radius=nodeRadius(node), color=colorFor(node), focus=node===selected||node===hovered||node.id===data.focus_id;
        if(lowZoom||focus) {
          const glowRadius=Math.max(12,radius*(lowZoom?4.6:3.1));
          const glow=ctx.createRadialGradient(p.x,p.y,0,p.x,p.y,glowRadius);
          glow.addColorStop(0,color); glow.addColorStop(.22,color+'99'); glow.addColorStop(1,color+'00');
          ctx.fillStyle=glow; ctx.beginPath(); ctx.arc(p.x,p.y,glowRadius,0,Math.PI*2); ctx.fill();
        }
        ctx.beginPath(); ctx.arc(p.x,p.y,radius,0,Math.PI*2); ctx.fillStyle=color; ctx.globalAlpha=focus?1:.86; ctx.fill(); ctx.globalAlpha=1;
        if(node.id===data.focus_id||node===selected||node===hovered) {
          ctx.beginPath(); ctx.arc(p.x,p.y,radius+(node===selected?4:3),0,Math.PI*2);
          ctx.strokeStyle=node===selected?colors.focus:colors.text; ctx.lineWidth=node===selected?2.2:1.4; ctx.stroke();
        }
        if(!lowZoom && (showAllLabels||(showFocusLabels&&focus))) {
          ctx.font=(focus?'700 12px ':'600 11px ')+getComputedStyle(document.body).fontFamily;
          ctx.textAlign='left'; ctx.textBaseline='middle';
          const tx=p.x+radius+8, ty=p.y, tw=Math.min(240,ctx.measureText(node.title).width);
          roundedRect(tx-4,ty-11,tw+9,22,5); ctx.fillStyle=colors.surface; ctx.globalAlpha=.92; ctx.fill(); ctx.globalAlpha=1;
          ctx.fillStyle=colors.text; ctx.fillText(node.title,tx,ty,240);
        }
      }
    }
    function simulateStep() {
      const spring=.0042, repel=3600, center=.00055, damping=.89;
      for (const edge of visibleEdges) {
        const a=byId.get(edge.source), b=byId.get(edge.target); if(!a||!b) continue;
        const dx=b.x-a.x, dy=b.y-a.y, distance=Math.max(1,Math.hypot(dx,dy));
        const force=(distance-edge.length)*spring, fx=dx/distance*force, fy=dy/distance*force;
        if(a!==dragged){a.vx+=fx;a.vy+=fy;} if(b!==dragged){b.vx-=fx;b.vy-=fy;}
      }
      for(let i=0;i<visibleNodes.length;i++) for(let j=i+1;j<visibleNodes.length;j++) {
        const a=visibleNodes[i], b=visibleNodes[j], dx=b.x-a.x, dy=b.y-a.y, d2=dx*dx+dy*dy+80, distance=Math.sqrt(d2);
        const force=Math.min(.72,repel/d2), fx=dx/distance*force, fy=dy/distance*force;
        if(a!==dragged){a.vx-=fx;a.vy-=fy;} if(b!==dragged){b.vx+=fx;b.vy+=fy;}
      }
      visibleNodes.forEach(node => {
        if(node===dragged) return;
        node.vx+=-node.x*center; node.vy+=-node.y*center; node.vx*=damping; node.vy*=damping;
        node.vx=Math.max(-7,Math.min(7,node.vx)); node.vy=Math.max(-7,Math.min(7,node.vy));
        node.x+=node.vx; node.y+=node.vy;
      });
      energy*=.992;
    }
    function resize() {
      const rect=canvas.getBoundingClientRect(); width=Math.max(1,rect.width); height=Math.max(1,rect.height);
      dpr=Math.min(2,window.devicePixelRatio||1); canvas.width=Math.round(width*dpr); canvas.height=Math.round(height*dpr); draw();
    }
    function fitGraph() {
      if(!visibleNodes.length) return;
      const xs=visibleNodes.map(node=>node.x), ys=visibleNodes.map(node=>node.y);
      const minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys),margin=width<500?58:92;
      camera.x=(minX+maxX)/2; camera.y=(minY+maxY)/2;
      camera.scale=Math.max(.28,Math.min(1.38,Math.min((width-margin*2)/Math.max(220,maxX-minX),(height-margin*2)/Math.max(220,maxY-minY))));
      hideTooltip(); draw();
    }
    function zoomAt(factor,x=width/2,y=height/2) {
      const before=screenToWorld(x,y); camera.scale=Math.max(.24,Math.min(2.6,camera.scale*factor));
      const after=screenToWorld(x,y); camera.x+=before.x-after.x; camera.y+=before.y-after.y; hideTooltip(); draw();
    }
    function applyFilters() {
      const enabledTypes=new Set([...document.querySelectorAll('[data-graph-type]:checked')].map(input=>input.value));
      const includeAuthored=document.getElementById('filter-authored').checked;
      const includeInferred=document.getElementById('filter-inferred').checked;
      visibleNodes=nodes.filter(node=>enabledTypes.has(node.type)); visibleIds=new Set(visibleNodes.map(node=>node.id));
      visibleEdges=edges.filter(edge=>visibleIds.has(edge.source)&&visibleIds.has(edge.target)&&((edge.provenance==='authored'&&includeAuthored)||(edge.provenance==='inferred'&&includeInferred)));
      if(!selected||!visibleIds.has(selected.id)) selected=visibleNodes.find(node=>node.id===data.focus_id)||visibleNodes[0]||null;
      summary.textContent=`${visibleNodes.length} visible document${visibleNodes.length===1?'':'s'} · ${visibleEdges.length} directed relationship${visibleEdges.length===1?'':'s'} · force-directed layout`;
      empty.classList.toggle('is-visible',visibleNodes.length<2||visibleEdges.length===0);
      energy=1; setSelected(selected); for(let i=0;i<(reduceMotion?40:80);i++) simulateStep(); fitGraph();
    }
    canvas.addEventListener('pointerdown',event=>{
      const rect=canvas.getBoundingClientRect(), x=event.clientX-rect.left, y=event.clientY-rect.top, node=findNodeAt(x,y);
      pointerId=event.pointerId; canvas.setPointerCapture(pointerId); startPointer={x:event.clientX,y:event.clientY}; startCamera={x:camera.x,y:camera.y};
      if(node){dragged=node;setSelected(node);hovered=node;energy=1;} else panning=true;
      wrap.classList.add('is-interacting'); hideTooltip();
    });
    canvas.addEventListener('pointermove',event=>{
      const rect=canvas.getBoundingClientRect(),x=event.clientX-rect.left,y=event.clientY-rect.top;
      if(dragged){const world=screenToWorld(x,y);dragged.x=world.x;dragged.y=world.y;dragged.vx=0;dragged.vy=0;energy=1;draw();return;}
      if(panning){camera.x=startCamera.x-(event.clientX-startPointer.x)/camera.scale;camera.y=startCamera.y-(event.clientY-startPointer.y)/camera.scale;draw();return;}
      const next=findNodeAt(x,y); if(next!==hovered){hovered=next;draw();} if(next)showTooltip(next,x,y);else hideTooltip();
    });
    function release(event){if(pointerId!==null&&canvas.hasPointerCapture(pointerId))canvas.releasePointerCapture(pointerId);dragged=null;panning=false;pointerId=null;wrap.classList.remove('is-interacting');}
    canvas.addEventListener('pointerup',release); canvas.addEventListener('pointercancel',release);
    canvas.addEventListener('pointerleave',()=>{if(!dragged&&!panning){hovered=null;hideTooltip();draw();}});
    canvas.addEventListener('wheel',event=>{event.preventDefault();const rect=canvas.getBoundingClientRect();zoomAt(Math.exp(-event.deltaY*.00135),event.clientX-rect.left,event.clientY-rect.top);},{passive:false});
    canvas.addEventListener('click',event=>{const rect=canvas.getBoundingClientRect(),node=findNodeAt(event.clientX-rect.left,event.clientY-rect.top);if(node)setSelected(node);});
    canvas.addEventListener('keydown',event=>{
      const amount=32/camera.scale;
      if(event.key==='+'||event.key==='=')zoomAt(1.18); else if(event.key==='-')zoomAt(1/1.18); else if(event.key==='0')fitGraph();
      else if(event.key==='ArrowLeft')camera.x-=amount; else if(event.key==='ArrowRight')camera.x+=amount;
      else if(event.key==='ArrowUp')camera.y-=amount; else if(event.key==='ArrowDown')camera.y+=amount;
      else if(event.key==='Enter'&&selected){window.location.href=selected.href;} else return;
      event.preventDefault(); draw();
    });
    document.getElementById('graphZoomIn').addEventListener('click',()=>zoomAt(1.22));
    document.getElementById('graphZoomOut').addEventListener('click',()=>zoomAt(1/1.22));
    document.getElementById('graphFit').addEventListener('click',fitGraph);
    document.querySelectorAll('[data-graph-filter]').forEach(input=>input.addEventListener('change',applyFilters));
    const observer=new ResizeObserver(()=>{resize();fitGraph();}); observer.observe(wrap);
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change',refreshTheme);
    function tick(now){const delta=Math.min(32,now-lastTime);lastTime=now;const active=!reduceMotion&&(energy>.005||dragged);if(active){for(let i=0;i<(delta>20?2:1);i++)simulateStep();draw();}requestAnimationFrame(tick);}
    refreshTheme(); resize(); applyFilters(); requestAnimationFrame(tick);
  }

  initCatalog();
  initGraph();
})();
""".strip()


def _write_docs_assets(site_dir: Path) -> None:
    assets_dir = site_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    (assets_dir / "docs.css").write_text(DOCS_CSS + "\n", encoding="utf-8")
    (assets_dir / "docs.js").write_text(DOCS_JS + "\n", encoding="utf-8")


def _staleness_markup(staleness: dict[str, Any]) -> str:
    commit = html.escape(str(staleness["commit"]))
    count = int(staleness["changed_files_count"])
    if count:
        message = (
            f'<strong>Source changed.</strong> Built from <code>{commit}</code>; '
            f'{count} source file{"s" if count != 1 else ""} changed since. '
            'Run <code>python scripts/ai_cli.py docs build</code> to refresh.'
        )
        state = "stale"
    else:
        message = (
            f'<strong>Current.</strong> Built from <code>{commit}</code> with no source changes. '
            'Rebuild after editing control-plane documentation.'
        )
        state = "current"
    return f'<p class="build-state build-state--{state}">Staleness Honesty: {message}</p>'


def _first_markdown_paragraph(body: str) -> str:
    lines: list[str] = []
    in_front_block = False
    for raw in body.splitlines():
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_front_block = not in_front_block
            continue
        if in_front_block or not stripped:
            if lines:
                break
            continue
        if stripped.startswith(("#", "- ", "* ", "|")) or re.match(r"\d+\.\s", stripped):
            if lines:
                break
            continue
        lines.append(stripped)
    summary = re.sub(r"[`*_]", "", " ".join(lines))
    summary = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", summary)
    return summary[:280].rstrip() + ("..." if len(summary) > 280 else "")


def _table_of_contents(body_html: str) -> str:
    entries: list[tuple[int, str, str]] = []
    for match in re.finditer(r'<h([2-4]) id="([^"]+)">(.*?)</h\1>', body_html):
        label = re.sub(r"<[^>]+>", "", match.group(3))
        entries.append((int(match.group(1)), match.group(2), html.unescape(label)))
    if len(entries) < 2:
        return ""
    parts = ['<nav class="toc" aria-label="On this page"><ol>']
    for level, anchor, label in entries:
        parts.append(f'<li class="toc-level-{level}"><a href="#{anchor}">{html.escape(label)}</a></li>')
    parts.append("</ol></nav>")
    return "\n".join(parts)


def _html_head(title: str, asset_prefix: str) -> list[str]:
    return [
        '<!DOCTYPE html>',
        '<html lang="en">',
        '<head>',
        '  <meta charset="utf-8">',
        '  <meta name="viewport" content="width=device-width, initial-scale=1">',
        f'  <title>{html.escape(title)}</title>',
        f'  <link rel="stylesheet" href="{asset_prefix}assets/docs.css">',
        '</head>',
    ]


def _site_header(asset_prefix: str = "") -> list[str]:
    return [
        '<a class="skip-link" href="#main-content">Skip to content</a>',
        '<header class="site-header">',
        '  <div class="site-header__inner">',
        f'    <a class="brand" href="{asset_prefix}index.html">AI Knowledge Library</a>',
        f'    <a class="header-link" href="{asset_prefix}graphs/graph-global.html">Relationship graph</a>',
        '  </div>',
        '</header>',
    ]


def _graph_display_type(doc_type: str) -> str:
    return {
        "spec": "specification",
        "project-doc": "project",
    }.get(doc_type, doc_type if doc_type in {
        "agent", "workflow", "rule", "decision", "memory", "migration"
    } else "project")


def _graph_payload(
    documents: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    summaries: dict[str, str],
    *,
    focus_doc_id: str | None = None,
    domain: str | None = None,
    corpus: str | None = None,
    focused_limit: int = 24,
) -> dict[str, Any]:
    all_docs_by_id = {str(document["id"]): document for document in documents}
    if corpus is None:
        focus_doc = all_docs_by_id.get(str(focus_doc_id)) if focus_doc_id else None
        corpus = _document_corpus(focus_doc) if focus_doc is not None else "control-plane"
    docs_by_id = {
        doc_id: document
        for doc_id, document in all_docs_by_id.items()
        if _document_corpus(document) == corpus
    }

    normalized_edges: list[dict[str, Any]] = []
    for edge in edges:
        source = str(edge.get("source", ""))
        target = str(edge.get("target", ""))
        if source not in all_docs_by_id or target not in all_docs_by_id:
            continue
        source_corpus = _document_corpus(all_docs_by_id[source])
        target_corpus = _document_corpus(all_docs_by_id[target])
        bridge = source_corpus != target_corpus
        provenance = str(edge.get("provenance", "authored"))
        if bridge and provenance != "authored":
            continue
        normalized = dict(edge)
        normalized.update({
            "source": source,
            "target": target,
            "type": str(edge.get("type", "relates_to")),
            "provenance": provenance,
            "source_corpus": source_corpus,
            "target_corpus": target_corpus,
            "bridge": bridge,
        })
        normalized_edges.append(normalized)

    valid_edges = sorted(
        (
            edge for edge in normalized_edges
            if not edge["bridge"] and edge["source"] in docs_by_id and edge["target"] in docs_by_id
        ),
        key=lambda edge: (edge["source"], edge["target"], edge["type"], edge["provenance"]),
    )
    if domain:
        selected_ids = sorted(
            doc_id for doc_id, document in docs_by_id.items()
            if str(document.get("domain", "")) == domain
        )
    elif focus_doc_id and focus_doc_id in docs_by_id:
        adjacency: dict[str, set[str]] = {doc_id: set() for doc_id in docs_by_id}
        for edge in valid_edges:
            adjacency[edge["source"]].add(edge["target"])
            adjacency[edge["target"]].add(edge["source"])
        selected_ids = [focus_doc_id]
        seen = {focus_doc_id}
        frontier = [focus_doc_id]
        while frontier and len(selected_ids) < focused_limit:
            next_frontier: list[str] = []
            for current in frontier:
                for neighbor in sorted(adjacency[current]):
                    if neighbor in seen:
                        continue
                    seen.add(neighbor)
                    selected_ids.append(neighbor)
                    next_frontier.append(neighbor)
                    if len(selected_ids) >= focused_limit:
                        break
                if len(selected_ids) >= focused_limit:
                    break
            frontier = next_frontier
    else:
        selected_ids = sorted(docs_by_id)

    selected_set = set(selected_ids)
    selected_edges = [
        edge for edge in valid_edges
        if edge["source"] in selected_set and edge["target"] in selected_set
    ]
    bridges = sorted(
        (
            edge for edge in normalized_edges
            if edge["bridge"] and (edge["source"] in selected_set or edge["target"] in selected_set)
        ),
        key=lambda edge: (edge["source"], edge["target"], edge["type"]),
    )
    nodes = []
    for doc_id in selected_ids:
        document = docs_by_id[doc_id]
        doc_type = str(document.get("type", "project-doc"))
        nodes.append({
            "id": doc_id,
            "title": str(document.get("title", doc_id)),
            "type": _graph_display_type(doc_type),
            "raw_type": doc_type,
            "corpus": _document_corpus(document),
            "domain": str(document.get("domain", "general")),
            "status": str(document.get("status", "unknown")),
            "owner": str(document.get("owner", "system")),
            "summary": summaries.get(doc_id) or f"{doc_type.title()} documentation.",
            "href": f"../docs/{doc_id}.html",
        })
    effective_focus = focus_doc_id if focus_doc_id in selected_set else (selected_ids[0] if selected_ids else None)
    return {
        "corpus": corpus,
        "focus_id": effective_focus,
        "nodes": nodes,
        "edges": selected_edges,
        "bridges": bridges,
    }

def _render_graph_page(
    title: str,
    payload: dict[str, Any],
    raw_svg_name: str,
    staleness: dict[str, Any],
) -> str:
    nodes = payload["nodes"]
    edges = payload["edges"]
    focus_id = payload.get("focus_id")
    selected = next((node for node in nodes if node["id"] == focus_id), nodes[0] if nodes else None)
    selected_id = selected["id"] if selected else ""
    selected_title = selected["title"] if selected else "No document selected"
    selected_summary = selected["summary"] if selected else "No related documents are available."
    selected_type = selected["raw_type"] if selected else ""
    selected_domain = selected["domain"] if selected else ""
    selected_status = selected["status"] if selected else ""
    selected_href = selected["href"] if selected else "../index.html"
    outgoing = sum(1 for edge in edges if edge["source"] == selected_id)
    incoming = sum(1 for edge in edges if edge["target"] == selected_id)
    back_href = selected_href if focus_id else "../index.html"
    back_label = "Back to document" if focus_id else "Back to catalog"
    graph_types = sorted({node["type"] for node in nodes})
    type_labels = {
        "agent": "Agent", "workflow": "Workflow", "rule": "Rule",
        "specification": "Spec", "decision": "Decision", "memory": "Memory",
        "project": "Project", "migration": "Migration",
    }
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":")).replace("</", "<\\/")

    parts = _html_head(title, "../") + ['<body class="graph-page">']
    parts.extend(_site_header("../"))
    parts.extend([
        _staleness_markup(staleness),
        '<main id="main-content" class="graph-page-layout">',
        '  <header class="graph-header">',
        '    <div class="graph-heading">',
        f'      <h1>{html.escape(title)}</h1>',
        f'      <p id="graphSummary">{len(nodes)} visible documents · {len(edges)} directed relationships · force-directed layout</p>',
        '    </div>',
        '    <div class="graph-actions">',
        f'      <a class="button" href="{html.escape(back_href, quote=True)}">{back_label}</a>',
        '      <details class="graph-filter-menu">',
        '        <summary class="button">Relationship filters</summary>',
        '        <div class="graph-filter-panel">',
        '          <fieldset><legend>Document types</legend><div class="graph-filter-options">',
    ])
    for graph_type in graph_types:
        label = type_labels.get(graph_type, graph_type.title())
        parts.append(
            f'            <label><input type="checkbox" value="{html.escape(graph_type, quote=True)}" data-graph-filter data-graph-type checked> {html.escape(label)}</label>'
        )
    parts.extend([
        '          </div></fieldset>',
        '          <fieldset><legend>Relationship provenance</legend><div class="graph-filter-options">',
        '            <label><input id="filter-authored" type="checkbox" data-graph-filter checked> Authored</label>',
        '            <label><input id="filter-inferred" type="checkbox" data-graph-filter checked> Inferred</label>',
        '          </div></fieldset>',
        '        </div>',
        '      </details>',
        f'      <a class="button" href="{html.escape(raw_svg_name, quote=True)}">Raw SVG</a>',
        '    </div>',
        '  </header>',
        '  <div class="graph-workspace">',
        '    <section class="graph-canvas-wrap" aria-label="Relationship graph workspace">',
        '      <div class="graph-canvas" id="graphCanvas">',
        '        <canvas class="force-canvas" id="forceGraphCanvas" tabindex="0" aria-label="Interactive relationship graph. Drag nodes to rearrange them, drag empty space to pan, and use the mouse wheel, keyboard, or controls to zoom."></canvas>',
        '        <div class="graph-hint">Drag a node to disturb the cluster · drag empty space to pan · wheel to zoom</div>',
        '        <div class="graph-tooltip" id="graphTooltip" role="tooltip">',
        '          <div class="graph-tooltip-type" id="graphTooltipType"></div>',
        '          <div class="graph-tooltip-title" id="graphTooltipTitle"></div>',
        '          <div class="graph-tooltip-meta" id="graphTooltipMeta"></div>',
        '          <div class="graph-tooltip-summary" id="graphTooltipSummary"></div>',
        '        </div>',
        '        <div class="graph-controls" aria-label="Graph controls">',
        '          <button id="graphZoomIn" type="button" aria-label="Zoom in">+</button>',
        '          <button id="graphZoomOut" type="button" aria-label="Zoom out">−</button>',
        '          <button id="graphFit" type="button" aria-label="Fit graph to canvas">Fit</button>',
        '        </div>',
        '        <div class="graph-legend" aria-label="Graph legend">',
    ])
    for graph_type in graph_types:
        label = type_labels.get(graph_type, graph_type.title())
        parts.append(f'          <span class="legend-dot {graph_type}"></span><span>{html.escape(label)}</span>')
    parts.extend([
        '          <span class="legend-line"></span><span>Authored</span>',
        '          <span class="legend-line inferred"></span><span>Inferred</span>',
        '        </div>',
        '        <div class="graph-empty" id="graphEmpty">',
        '          <div class="graph-empty-card"><h2>No visible relationships</h2><p>Enable more document types or relationship provenance in the filters.</p></div>',
        '        </div>',
        '      </div>',
        '    </section>',
        '    <aside class="graph-details" aria-label="Selected document">',
        '      <p class="eyebrow">Selected document</p>',
        f'      <h2 id="selectedNodeTitle">{html.escape(selected_title)}</h2>',
        f'      <div class="detail-id" id="selectedNodeId">{html.escape(selected_id)}</div>',
        f'      <p class="graph-selected-summary" id="selectedNodeSummary">{html.escape(selected_summary)}</p>',
        '      <div class="detail-list">',
        f'        <div class="detail-row"><span>Type</span><span id="selectedNodeType">{html.escape(selected_type)}</span></div>',
        f'        <div class="detail-row"><span>Domain</span><span id="selectedNodeDomain">{html.escape(selected_domain)}</span></div>',
        f'        <div class="detail-row"><span>Outgoing</span><span id="selectedNodeOutgoing">{outgoing} relationships</span></div>',
        f'        <div class="detail-row"><span>Backlinks</span><span id="selectedNodeBacklinks">{incoming} backlinks</span></div>',
        f'        <div class="detail-row"><span>Status</span><span id="selectedNodeStatus">{html.escape(selected_status)}</span></div>',
        '      </div>',
        f'      <a class="button button--primary button--full" id="openSelectedDocument" href="{html.escape(selected_href, quote=True)}" >Open document</a>',
        '    </aside>',
        '  </div>',
        '</main>',
        f'<script id="graph-data" type="application/json">{payload_json}</script>',
        '<script src="../assets/docs.js" defer></script>',
        '</body>',
        '</html>',
    ])
    return "\n".join(parts)

def cmd_docs_build(
    ai_root: Path | None = None,
    out_dir: Path | None = None,
    *,
    require_project_intelligence: bool = False,
) -> Path:
    """Render the static documentation library to .ai/_site/."""
    target_ai = ai_root if ai_root is not None else constants.AI
    site_dir = out_dir if out_dir is not None else target_ai / "_site"
    docs_out_dir = site_dir / "docs"
    graphs_dir = site_dir / "graphs"
    site_dir.mkdir(parents=True, exist_ok=True)
    docs_out_dir.mkdir(parents=True, exist_ok=True)
    _write_docs_assets(site_dir)

    reg = generate_registry(target_ai)
    documents = reg.get("documents", [])
    staleness = compute_staleness_info(target_ai.parent)
    backlinks = compute_backlinks(documents)
    doc_id_set = {d["id"] for d in documents}
    docs_by_id = {d["id"]: d for d in documents}

    site_search_index = generate_search_index_payload(reg)
    (site_dir / "search_index.json").write_text(
        json.dumps(site_search_index, indent=2), encoding="utf-8"
    )
    search_by_id = {entry["id"]: entry for entry in site_search_index["documents"]}

    source_bodies: dict[str, str] = {}
    summaries: dict[str, str] = {}
    for doc in documents:
        source_path = resolve_registry_source_path(target_ai, doc["path"])
        body = ""
        if source_path.exists():
            try:
                _, body = parse_frontmatter(source_path.read_text(encoding="utf-8"))
            except Exception:
                body = ""
        source_bodies[doc["id"]] = body
        summaries[doc["id"]] = _first_markdown_paragraph(body)

    corpus_ids = sorted({_document_corpus(document) for document in documents}, key=len, reverse=True)
    graph_paths = emit_graph_artifacts(reg, graphs_dir, ai_root=target_ai)
    # The task hierarchy ships with the document graphs so the reader's link always resolves,
    # rather than only after someone happens to run `ai docs graph --tasks`.
    try:
        from scripts.ai_plane.knowledge_projection.tasks import build_tasks
        from scripts.ai_plane.task_graph import write_task_graph

        write_task_graph(build_tasks(target_ai.parent).get("tasks", []), graphs_dir)
    except (OSError, ValueError, KeyError) as error:
        # A task corpus that cannot be projected must not fail the documentation build.
        print(f"WARNING: task hierarchy graph skipped: {error}")
    graph_edges = _collect_edges(documents, target_ai)
    reader_model = build_knowledge_projection(
        target_ai.parent,
        registry_data=reg,
        document_edges=graph_edges,
        document_bodies=source_bodies,
    )
    if require_project_intelligence:
        required_project_error = _required_project_intelligence_error(reader_model)
        if required_project_error is not None:
            raise RequiredProjectIntelligenceError(required_project_error)
    write_knowledge_assets(reader_model, site_dir)
    for graph_path in graph_paths:
        stem = graph_path.stem
        focus_doc_id: str | None = None
        graph_corpus: str | None = None
        graph_domain: str | None = None
        if stem == "graph-global":
            graph_corpus = "control-plane"
            graph_title = "Control Plane relationships"
        elif stem.startswith("graph-corpus-"):
            suffix = stem.removeprefix("graph-corpus-")
            graph_corpus = next(
                (item for item in corpus_ids if suffix == item or suffix.startswith(f"{item}-domain-")),
                None,
            )
            if graph_corpus and suffix.startswith(f"{graph_corpus}-domain-"):
                graph_domain = suffix.removeprefix(f"{graph_corpus}-domain-")
                graph_title = (
                    f'{graph_corpus.replace("-", " ").title()} relationships in '
                    f'{graph_domain.replace("-", " ").title()}'
                )
            else:
                graph_title = f'{(graph_corpus or "Unknown").replace("-", " ").title()} relationships'
        elif stem.startswith("graph-domain-"):
            graph_corpus = "control-plane"
            graph_domain = stem.removeprefix("graph-domain-")
            graph_title = f'Relationships in {graph_domain.replace("-", " ").title()}'
        elif stem.startswith("graph-local-"):
            focus_doc_id = stem.removeprefix("graph-local-")
            graph_doc = docs_by_id.get(focus_doc_id, {})
            graph_corpus = _document_corpus(graph_doc) if graph_doc else None
            graph_title = f'Relationships for {graph_doc.get("title", focus_doc_id)}'
        else:
            graph_title = "Relationships"
        payload = _graph_payload(
            documents,
            graph_edges,
            summaries,
            corpus=graph_corpus,
            focus_doc_id=focus_doc_id,
            domain=graph_domain,
        )
        graph_path.with_suffix(".html").write_text(
            _render_graph_page(graph_title, payload, graph_path.name, staleness),
            encoding="utf-8",
        )
    types = sorted({str(d.get("type", "document")) for d in documents})
    domains = sorted({str(d.get("domain", "general")) for d in documents})
    statuses = sorted({str(d.get("status", "unknown")) for d in documents})

    index_parts = _html_head("AI Knowledge Library", "") + ['<body class="catalog-page">']
    index_parts.extend(_site_header())
    index_parts.extend([
        _staleness_markup(staleness),
        '<main id="main-content" class="page-shell">',
        '  <section class="hero" aria-labelledby="catalog-title">',
        '    <div>',
        '      <p class="eyebrow">Repository documentation</p>',
        '      <h1 id="catalog-title">AI Knowledge Library</h1>',
        f'      <p>Browse {len(documents)} governed documents, their purpose, and the relationships that connect them.</p>',
        '    </div>',
        '    <div class="hero__actions">',
        '      <a class="button button--primary" href="graphs/graph-global.html">Explore relationships</a>',
        '    </div>',
        '  </section>',
        '  <div class="library-layout">',
        '    <aside class="category-rail" aria-label="Document categories">',
        '      <h2>Categories</h2>',
        '      <nav>',
    ])
    for doc_type in types:
        type_count = sum(1 for doc in documents if str(doc.get("type", "document")) == doc_type)
        index_parts.append(
            f'        <a href="#type-{_slugify_heading(doc_type)}"><span>{html.escape(doc_type.title())}</span><span>{type_count}</span></a>'
        )
    index_parts.extend([
        '      </nav>',
        '    </aside>',
        '    <section class="library-main" data-catalog data-search-index="search_index.json" aria-label="Document catalog">',
        '      <form class="filters" role="search">',
        '        <div class="search-field">',
        '          <label for="catalog-search">Search documentation</label>',
        '          <input id="catalog-search" type="search" autocomplete="off" placeholder="Search titles, IDs, domains, or concepts" aria-describedby="search-hint">',
        '          <small id="search-hint">Press / to focus search.</small>',
        '        </div>',
        '        <div class="filter-row">',
        '          <div class="filter-field"><label for="filter-type">Type</label><select id="filter-type"><option value="">All types</option>',
    ])
    index_parts.extend(f'            <option value="{html.escape(value, quote=True)}">{html.escape(value.title())}</option>' for value in types)
    index_parts.extend([
        '          </select></div>',
        '          <div class="filter-field"><label for="filter-domain">Domain</label><select id="filter-domain"><option value="">All domains</option>',
    ])
    index_parts.extend(f'            <option value="{html.escape(value, quote=True)}">{html.escape(value)}</option>' for value in domains)
    index_parts.extend([
        '          </select></div>',
        '          <div class="filter-field"><label for="filter-status">Status</label><select id="filter-status"><option value="">All statuses</option>',
    ])
    index_parts.extend(f'            <option value="{html.escape(value, quote=True)}">{html.escape(value.title())}</option>' for value in statuses)
    index_parts.extend([
        '          </select></div>',
        '          <button class="button" id="filters-reset" type="button">Reset filters</button>',
        '        </div>',
        '      </form>',
        '      <div class="result-summary"><span id="result-count" role="status" aria-live="polite"></span><span>Sorted by title within type</span></div>',
    ])

    for doc_type in types:
        typed_docs = sorted(
            (doc for doc in documents if str(doc.get("type", "document")) == doc_type),
            key=lambda item: (str(item.get("title", item["id"])).lower(), item["id"]),
        )
        index_parts.extend([
            f'      <section class="catalog-group" id="type-{_slugify_heading(doc_type)}" data-catalog-group>',
            f'        <div class="catalog-group__heading"><h2>{html.escape(doc_type.title())}</h2><span>{len(typed_docs)} documents</span></div>',
            '        <div class="catalog-list">',
        ])
        for doc in typed_docs:
            doc_id = doc["id"]
            title = str(doc.get("title", doc_id))
            domain = str(doc.get("domain", "general"))
            status = str(doc.get("status", "unknown"))
            owner = str(doc.get("owner", "system"))
            summary = summaries.get(doc_id) or f'{doc_type.title()} documentation for {domain}.'
            segments = " ".join(search_by_id.get(doc_id, {}).get("segments", []))
            search_text = " ".join((doc_id, title, doc_type, domain, status, owner, summary, segments))
            status_class = _slugify_heading(status)
            index_parts.extend([
                f'          <article class="catalog-item" data-document data-type="{html.escape(doc_type, quote=True)}" data-domain="{html.escape(domain, quote=True)}" data-status="{html.escape(status, quote=True)}" data-search="{html.escape(search_text, quote=True)}">',
                '            <div>',
                f'              <h3><a href="docs/{html.escape(doc_id, quote=True)}.html">{html.escape(title)}</a></h3>',
                f'              <p>{html.escape(summary)}</p>',
                '            </div>',
                '            <div class="catalog-meta">',
                f'              <span class="badge badge--{status_class}">{html.escape(status)}</span>',
                f'              <span>{html.escape(domain)}</span>',
                f'              <code>{html.escape(doc_id)}</code>',
                '            </div>',
                '          </article>',
            ])
        index_parts.extend(['        </div>', '      </section>'])

    index_parts.extend([
        '      <section id="empty-state" class="empty-state" hidden>',
        '        <h2>No documents match</h2>',
        '        <p>Try a broader search, remove a filter, or reset the catalog.</p>',
        '        <button class="button" id="empty-reset" type="button">Reset filters</button>',
        '      </section>',
        '    </section>',
        '  </div>',
        '</main>',
        '<script src="assets/docs.js" defer></script>',
        '</body>',
        '</html>',
    ])
    (site_dir / "index.html").write_text("\n".join(index_parts), encoding="utf-8")

    for doc in documents:
        doc_id = doc["id"]
        title = str(doc.get("title", doc_id))
        doc_type = str(doc.get("type", "document"))
        domain = str(doc.get("domain", "general"))
        status = str(doc.get("status", "unknown"))
        owner = str(doc.get("owner", "system"))
        body = source_bodies.get(doc_id, "")
        body_html = render_markdown_body_to_html(
            body, doc_id_set=doc_id_set, anchor_headings=True, omit_first_h1=True
        )
        toc_html = _table_of_contents(body_html)
        summary = summaries.get(doc_id) or f'{doc_type.title()} documentation for {domain}.'
        doc_backlinks = backlinks.get(doc_id, [])
        relations = doc.get("relations", [])

        relation_items: list[str] = []
        if isinstance(relations, list):
            for relation in relations:
                if not isinstance(relation, dict):
                    continue
                target = str(relation.get("target", ""))
                relation_type = str(relation.get("type", "relates_to"))
                target_markup = (
                    f'<a href="{html.escape(target, quote=True)}.html">{html.escape(target)}</a>'
                    if target in doc_id_set else f'<code>{html.escape(target)}</code>'
                )
                relation_items.append(
                    f'<li><code>{html.escape(relation_type)}</code> &rarr; {target_markup}</li>'
                )
        if not relation_items:
            relation_items.append('<li><em>None declared</em></li>')

        backlink_items = [
            f'<li><code>{html.escape(str(backlink["type"]))}</code> &larr; '
            f'<a href="{html.escape(backlink["source_id"], quote=True)}.html">{html.escape(backlink["source_title"])}</a></li>'
            for backlink in doc_backlinks
        ] or ['<li><em>No backlinks found</em></li>']

        doc_parts = _html_head(title, "../") + ['<body class="document-page">']
        doc_parts.extend(_site_header("../"))
        doc_parts.append(_staleness_markup(staleness))
        doc_parts.append('<div class="reader-layout">')
        if toc_html:
            doc_parts.extend([
                '  <aside class="reader-nav">',
                '    <div class="panel"><h2>On this page</h2>',
                toc_html,
                '    </div>',
                '  </aside>',
            ])
        else:
            doc_parts.append('  <div class="reader-nav" aria-hidden="true"></div>')
        doc_parts.extend([
            '  <main id="main-content" class="article">',
            f'    <nav class="breadcrumb" aria-label="Breadcrumb"><a href="../index.html">Library</a> / <span>{html.escape(doc_type.title())}</span></nav>',
            '    <article>',
            '      <header class="article-header">',
            f'        <p class="eyebrow">{html.escape(doc_type)} · {html.escape(domain)}</p>',
            f'        <h1>{html.escape(title)}</h1>',
            f'        <p class="article-summary">{html.escape(summary)}</p>',
            '        <dl class="metadata">',
            f'          <div><dt>ID</dt><dd><code>{html.escape(doc_id)}</code></dd></div>',
            f'          <div><dt>Status</dt><dd>{html.escape(status)}</dd></div>',
            f'          <div><dt>Owner</dt><dd>{html.escape(owner)}</dd></div>',
            f'          <div><dt>Domain</dt><dd>{html.escape(domain)}</dd></div>',
            '        </dl>',
            '      </header>',
            f'      <div class="prose">{body_html}</div>',
            '    </article>',
            '  </main>',
            '  <aside class="relation-rail" aria-label="Document relationships">',
            '    <section class="panel">',
            '      <h2>Relationship graph</h2>',
            f'      <p>{len(relation_items) if relations else 0} outgoing · {len(doc_backlinks)} incoming</p>',
            f'      <a class="button" href="../graphs/graph-local-{html.escape(doc_id, quote=True)}.html">Open focused graph</a>',
            '    </section>',
            '    <section class="panel">',
            '      <h2>Outgoing</h2>',
            '      <ul class="relation-list">',
            *[f'        {item}' for item in relation_items],
            '      </ul>',
            '    </section>',
            '    <section class="panel">',
            '      <h2>Referenced by</h2>',
            '      <ul class="relation-list">',
            *[f'        {item}' for item in backlink_items],
            '      </ul>',
            '    </section>',
            '  </aside>',
            '</div>',
            '</body>',
            '</html>',
        ])
        (docs_out_dir / f"{doc_id}.html").write_text("\n".join(doc_parts), encoding="utf-8")

    write_reader_presentation(site_dir)
    return site_dir

def generate_search_index_payload(registry_data: dict[str, Any]) -> dict[str, Any]:
    """Build JSON search index payload with identifier-segment vocabulary."""
    docs = registry_data.get("documents", [])
    entries: list[dict[str, Any]] = []
    vocab_map: dict[str, list[str]] = {}

    for doc in docs:
        doc_id = doc["id"]
        title = doc.get("title", doc_id)
        path = doc.get("path", "")
        domain = doc.get("domain", "")
        tags = doc.get("tags", [])
        text_corpus = f"{doc_id} {title} {domain} {' '.join(tags)}"
        segments = extract_name_segments(text_corpus)

        for seg in segments:
            if seg not in vocab_map:
                vocab_map[seg] = []
            if doc_id not in vocab_map[seg]:
                vocab_map[seg].append(doc_id)

        entries.append({
            "id": doc_id,
            "title": title,
            "path": path,
            "domain": domain,
            "tags": tags,
            "segments": segments,
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_documents": len(entries),
        "documents": entries,
        "name_segment_vocab": {k: sorted(v) for k, v in sorted(vocab_map.items())},
    }


def cmd_docs_lint(ai_root: Path | None = None) -> int:
    """Validate frontmatter, document schemas, and report unresolved relation targets as pending rows."""
    target_ai = ai_root if ai_root is not None else constants.AI
    reg = generate_registry(target_ai)
    unresolved = reg.get("unresolved_references", [])
    errors = reg.get("errors", [])
    warnings = reg.get("warnings", [])

    print(f"Docs Lint Report for {target_ai}:")
    print(f"  Total Registered Documents: {len(reg.get('documents', []))}")
    print(f"  Total Errors: {len(errors)}")
    print(f"  Total Warnings: {len(warnings)}")

    if unresolved:
        print("\nPENDING RELATIONS (Unresolved relation targets):")
        for u in unresolved:
            print(f"  - Pending relation target: {u}")
    else:
        print("\nClean cross-references: 0 pending/unresolved targets.")

    if errors:
        print("\nERRORS (Authoring gate failures):")
        for error in errors:
            print(f"  - {error}")

    return 1 if errors else 0


def cmd_docs_search(ai_root: Path | None = None, query: str | None = None, out_file: Path | None = None) -> dict[str, Any]:
    """Write/query JSON search index. Always writes to .ai/_site/search_index.json by default."""
    target_ai = ai_root if ai_root is not None else constants.AI
    reg = generate_registry(target_ai)
    payload = generate_search_index_payload(reg)

    # Always write index to default site location; explicit out_file overrides
    default_index_path = target_ai / "_site" / "search_index.json"
    write_target = out_file if out_file else default_index_path
    write_target.parent.mkdir(parents=True, exist_ok=True)
    write_target.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if query:
        q_lower = query.lower()
        q_segments = extract_name_segments(q_lower)
        matches: list[dict[str, str]] = []
        for doc in payload["documents"]:
            doc_id = doc["id"]
            title = doc["title"]
            segs = set(doc["segments"])
            if q_lower in doc_id.lower() or q_lower in title.lower() or any(s in segs for s in q_segments):
                matches.append({"id": doc_id, "title": title, "path": doc["path"]})
        print(f"Search results for query '{query}': ({len(matches)} matches)")
        for m in matches:
            print(f"  - [{m['id']}] {m['title']} ({m['path']})")
        payload["query_matches"] = matches

    return payload


def cmd_docs_stats(ai_root: Path | None = None) -> dict[str, Any]:
    """Report health metrics: orphans, stale chains, unlinked tasks, graph coverage."""
    target_ai = ai_root if ai_root is not None else constants.AI
    reg = generate_registry(target_ai)
    docs = reg.get("documents", [])
    doc_ids = {d["id"] for d in docs}
    backlinks = compute_backlinks(docs)

    outgoing_counts = {d["id"]: len(d.get("relations", [])) for d in docs}
    incoming_counts = {d["id"]: len(backlinks.get(d["id"], [])) for d in docs}

    orphans = [did for did in doc_ids if outgoing_counts.get(did, 0) == 0 and incoming_counts.get(did, 0) == 0]
    connected = [did for did in doc_ids if outgoing_counts.get(did, 0) > 0 or incoming_counts.get(did, 0) > 0]
    coverage = (len(connected) / len(doc_ids) * 100.0) if doc_ids else 0.0

    stale_chains: list[str] = []
    for d in docs:
        st = d.get("status")
        if st in ("deprecated", "superseded"):
            if not d.get("superseded_by"):
                stale_chains.append(d["id"])

    unlinked_tasks: list[str] = []
    tasks_dir = target_ai / "tasks"
    if tasks_dir.exists():
        referenced_targets: set[str] = set()
        for d in docs:
            for rel in d.get("relations", []):
                if isinstance(rel, dict) and isinstance(rel.get("target"), str):
                    referenced_targets.add(rel["target"])
        for state in ("queue", "active", "done", "archive"):
            sdir = tasks_dir / state
            if sdir.exists():
                for tf in sdir.iterdir():
                    if tf.is_dir() and tf.name not in referenced_targets and tf.name.split("_")[0] + "_" + tf.name.split("_")[1] not in referenced_targets:
                        unlinked_tasks.append(tf.name)

    stats = {
        "total_documents": len(docs),
        "orphan_documents_count": len(orphans),
        "orphan_documents": sorted(orphans),
        "stale_superseded_chains_count": len(stale_chains),
        "stale_superseded_chains": sorted(stale_chains),
        "unlinked_tasks_count": len(unlinked_tasks),
        "unlinked_tasks": sorted(unlinked_tasks),
        "graph_coverage_percent": round(coverage, 2),
    }

    print("AI Docs Health Stats:")
    print(f"  Total Documents: {stats['total_documents']}")
    print(f"  Orphan Documents: {stats['orphan_documents_count']}")
    print(f"  Stale/Superseded Chains: {stats['stale_superseded_chains_count']}")
    print(f"  Unlinked Tasks: {stats['unlinked_tasks_count']}")
    print(f"  Graph Coverage: {stats['graph_coverage_percent']}%")

    return stats


def cmd_docs_graph(ai_root: Path | None = None, doc_id: str | None = None, domain: str | None = None) -> str:
    """Generate SVG relation graph and emit local/domain/global artifacts."""
    target_ai = ai_root if ai_root is not None else constants.AI
    reg = generate_registry(target_ai)
    # Emit all graph artifact variants
    graphs_dir = target_ai / "_site" / "graphs"
    emit_graph_artifacts(reg, graphs_dir, ai_root=target_ai)
    # Return requested view
    svg = generate_svg_graph(doc_id, reg, domain, ai_root=target_ai)
    return svg


def add_docs_parser(sub: Any) -> None:
    """Add argparse subcommands for ai docs family."""
    docs = sub.add_parser("docs", help="Knowledge-graph documentation projection tools")
    docs_sub = docs.add_subparsers(dest="docs_command", required=True)
    docs_sub.add_parser("build", help="Build static HTML documentation site to .ai/_site/")
    docs_sub.add_parser("lint", help="Validate frontmatter and cross-reference relations")
    docs_search_p = docs_sub.add_parser("search", help="Build/query JSON search index")
    docs_search_p.add_argument("query", nargs="?", help="Optional search query term")
    docs_sub.add_parser("stats", help="Report document health metrics (orphans, stale chains, coverage)")
    docs_graph_p = docs_sub.add_parser(
        "graph", help="Write relation and task-hierarchy graphs to .ai/_site/graphs/")
    docs_graph_p.add_argument("doc_id", nargs="?", help="Optional focus document ID")
    docs_graph_p.add_argument("--domain", help="Optional domain filter")
    docs_graph_p.add_argument("--tasks", action="store_true",
                              help="Task dependency hierarchy instead of document relations")
    docs_graph_p.add_argument("--all", action="store_true",
                              help="With --tasks, include archived tasks (default: live work only)")
    docs_graph_p.add_argument("--out", help="Write the requested graph to this path")
    docs_graph_p.add_argument("--stdout", action="store_true",
                              help="Print the SVG markup instead of reporting where it was written")

    from scripts.ai_plane.docs_serve import add_docs_serve_parser
    add_docs_serve_parser(docs_sub)
    from scripts.ai_plane.docs_adopt import add_docs_adopt_parser
    add_docs_adopt_parser(docs_sub)
    from scripts.ai_plane.docs_export import add_docs_export_parser
    add_docs_export_parser(docs_sub)
    from scripts.ai_plane.docs_sync import add_docs_sync_parser
    add_docs_sync_parser(docs_sub)


def project_intelligence_declared() -> bool:
    """True when this repository actually declares a Project Intelligence source.

    Project Intelligence is an optional capability: it needs a governed exporter and a product
    corpus to describe. A repository that runs the control plane without one still gets a full
    control-plane reader, so the build degrades instead of failing on a source it never had.
    """
    # A candidate adapter is NOT a declaration. A repository with a `package.json` and no source
    # yet has an adapter that matches and produces nothing, and requiring the capability on that
    # basis turned a perfectly reasonable `docs build` into a hard failure. Requiring it means the
    # repository ships an exporter it has committed to; every other adapter degrades to an
    # unconfigured reader, which is what "optional capability" was always supposed to mean.
    return (constants.ROOT / "tools" / "ai-impact" / "Cargo.toml").is_file()


def cmd_docs(args: Any) -> None:
    """Handle ai docs subcommand execution."""
    cmd = getattr(args, "docs_command", "")
    if cmd == "serve":
        from scripts.ai_plane.docs_serve import cmd_docs_serve
        cmd_docs_serve(args)
        return
    if cmd == "adopt":
        from scripts.ai_plane.docs_adopt import cmd_docs_adopt
        raise SystemExit(cmd_docs_adopt(args))
    if cmd == "build":
        try:
            cmd_docs_build(require_project_intelligence=project_intelligence_declared())
        except RequiredProjectIntelligenceError as error:
            print(f"docs build failed: {error}", file=sys.stderr)
            raise SystemExit(1) from None
    elif cmd == "export":
        from scripts.ai_plane.docs_export import cmd_docs_export
        from scripts.ai_plane.utils import die as _die
        cmd_docs_export(args, die=_die)
    elif cmd == "sync":
        from scripts.ai_plane.docs_sync import cmd_docs_sync
        from scripts.ai_plane.utils import die as _die

        cmd_docs_sync(args, die=_die)
    elif cmd == "lint":
        result = cmd_docs_lint()
        if result:
            raise SystemExit(result)
    elif cmd == "search":
        cmd_docs_search(query=getattr(args, "query", None))
    elif cmd == "stats":
        cmd_docs_stats()
    elif cmd == "graph":
        from scripts.ai_plane.docs_graph_cli import cmd_docs_graph_cli

        cmd_docs_graph_cli(args, emit=cmd_docs_graph)
