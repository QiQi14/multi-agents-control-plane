"""The project hierarchy: which task depends on which.

Every graph the projection emitted before this one was a *document* relation graph -- 354 artifacts
across corpus, domain, local, and global views, all built from the document registry. Meanwhile the
task projection carried 455 `depends_on` edges across 236 tasks and nothing drew them, so the one
structure a person actually asks about ("what is this task waiting on, and what waits on it?") was
the one structure with no picture.

Layout is deterministic: a node sits one layer below the deepest thing it depends on, and nodes are
ordered by id inside a layer. The same corpus therefore renders identically on every machine, which
is what lets the output be committed and diffed like any other generated file.
"""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

LIFECYCLE_FILL = {
    "queue": "#2d3748",
    "active": "#2b6cb0",
    "done": "#276749",
    "archive": "#4a5568",
}
LIVE_LIFECYCLES = ("queue", "active", "done")

NODE_WIDTH = 210
NODE_HEIGHT = 34
GAP_X = 40
GAP_Y = 26
MARGIN = 24


def task_nodes(tasks: list[dict[str, Any]], *, lifecycles: tuple[str, ...]) -> dict[str, dict]:
    """Tasks in scope, keyed by id."""
    return {
        str(task.get("task_id")): task
        for task in tasks
        if task.get("task_id") and str(task.get("lifecycle")) in lifecycles
    }


def dependency_edges(nodes: dict[str, dict]) -> list[tuple[str, str]]:
    """`depends_on` pairs where BOTH ends are in scope.

    A dependency pointing outside the selection is dropped rather than drawn to a node that is not
    there: a dangling arrow reads as a missing task instead of as an out-of-scope one.
    """
    edges: list[tuple[str, str]] = []
    for task_id, task in nodes.items():
        for dependency in task.get("dependencies") or []:
            target = dependency.get("task_id") if isinstance(dependency, dict) else dependency
            target = str(target) if target else ""
            if target and target in nodes and target != task_id:
                edges.append((target, task_id))
    return sorted(set(edges))


def layer_of(nodes: dict[str, dict], edges: list[tuple[str, str]]) -> dict[str, int]:
    """Longest-path depth per node, with cycles broken rather than hung on.

    A dependency cycle is a contract defect, not something to crash over: the traversal caps its
    depth so a cyclic corpus still renders and the cycle is visible as tasks that never separate.
    """
    incoming: dict[str, list[str]] = {node: [] for node in nodes}
    for source, target in edges:
        incoming[target].append(source)
    depth: dict[str, int] = {}
    limit = len(nodes) + 1

    def resolve(node: str, seen: frozenset[str]) -> int:
        if node in depth:
            return depth[node]
        if node in seen or len(seen) > limit:
            return 0
        parents = incoming.get(node) or []
        value = 0 if not parents else 1 + max(
            resolve(parent, seen | {node}) for parent in parents)
        depth[node] = value
        return value

    for node in sorted(nodes):
        resolve(node, frozenset())
    return depth


def _label(task: dict[str, Any], task_id: str) -> str:
    contract = task.get("contract") or {}
    title = contract.get("title") if isinstance(contract, dict) else None
    text = str(title or task_id)
    return text if len(text) <= 30 else text[:29] + "…"


def render_svg(tasks: list[dict[str, Any]], *, lifecycles: tuple[str, ...] = LIVE_LIFECYCLES) -> str:
    nodes = task_nodes(tasks, lifecycles=lifecycles)
    if not nodes:
        return _empty_svg(lifecycles)
    edges = dependency_edges(nodes)
    depth = layer_of(nodes, edges)

    layers: dict[int, list[str]] = {}
    for node in sorted(nodes):
        layers.setdefault(depth.get(node, 0), []).append(node)

    position: dict[str, tuple[int, int]] = {}
    for layer_index in sorted(layers):
        for column, node in enumerate(layers[layer_index]):
            x = MARGIN + column * (NODE_WIDTH + GAP_X)
            y = MARGIN + layer_index * (NODE_HEIGHT + GAP_Y)
            position[node] = (x, y)

    width = MARGIN * 2 + max(len(v) for v in layers.values()) * (NODE_WIDTH + GAP_X) - GAP_X
    height = MARGIN * 2 + (max(layers) + 1) * (NODE_HEIGHT + GAP_Y) - GAP_Y

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Task dependency hierarchy: {len(nodes)} tasks, {len(edges)} dependencies">',
        '<defs><marker id="dep" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
        'markerHeight="6" orient="auto-start-reverse">'
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="#8b93a1"/></marker></defs>',
        '<style>.n{stroke:#1a202c;stroke-width:1}.t{fill:#edf2f7;font-family:system-ui,sans-serif;'
        'font-size:11px}.e{stroke:#8b93a1;stroke-width:1.4;fill:none;marker-end:url(#dep)}</style>',
        f'<rect width="{width}" height="{height}" fill="#12151b"/>',
    ]
    for source, target in edges:
        x1, y1 = position[source]
        x2, y2 = position[target]
        parts.append(
            f'<path class="e" d="M {x1 + NODE_WIDTH // 2} {y1 + NODE_HEIGHT} '
            f'C {x1 + NODE_WIDTH // 2} {y1 + NODE_HEIGHT + GAP_Y // 2}, '
            f'{x2 + NODE_WIDTH // 2} {y2 - GAP_Y // 2}, {x2 + NODE_WIDTH // 2} {y2}"/>')
    for node in sorted(nodes):
        x, y = position[node]
        fill = LIFECYCLE_FILL.get(str(nodes[node].get("lifecycle")), "#4a5568")
        parts.append(
            f'<rect class="n" x="{x}" y="{y}" width="{NODE_WIDTH}" height="{NODE_HEIGHT}" '
            f'rx="5" fill="{fill}"><title>{escape(node)}</title></rect>')
        parts.append(
            f'<text class="t" x="{x + 10}" y="{y + 21}">{escape(_label(nodes[node], node))}</text>')
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def _empty_svg(lifecycles: tuple[str, ...]) -> str:
    states = ", ".join(lifecycles)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="420" height="60" viewBox="0 0 420 60" '
        f'role="img" aria-label="No tasks in {escape(states)}">'
        '<rect width="420" height="60" fill="#12151b"/>'
        '<text x="16" y="35" fill="#edf2f7" font-family="system-ui,sans-serif" font-size="12">'
        f'No tasks in {escape(states)}.</text></svg>\n'
    )


def render_page(svg: str, counts: dict[str, int], scope: str) -> str:
    """A viewable page around the SVG.

    The sibling document graphs each ship an .html beside their .svg, and the reader links the page
    rather than the raw markup. A bare .svg would open as a file rather than as a view, so the task
    graph gets the same treatment: legend, counts, and a way back.
    """
    swatches = "".join(
        f'<span class="k"><i style="background:{LIFECYCLE_FILL[name]}"></i>{name}</span>'
        for name in ("queue", "active", "done", "archive") if name in LIFECYCLE_FILL
    )
    return (
        '<!doctype html><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Task dependency hierarchy</title>"
        "<style>body{margin:0;background:#0d1016;color:#edf2f7;"
        "font-family:system-ui,sans-serif}header{padding:16px 20px;border-bottom:1px solid #232936}"
        "h1{font-size:16px;margin:0 0 6px}p{margin:0;font-size:12px;color:#9aa4b2}"
        ".k{display:inline-flex;align-items:center;gap:6px;margin-right:14px;font-size:12px}"
        ".k i{width:11px;height:11px;border-radius:3px;display:inline-block}"
        "main{padding:20px;overflow:auto}a{color:#7fb3ff}</style>"
        "<header><h1>Task dependency hierarchy</h1>"
        f"<p>{counts['tasks']} task(s) &middot; {counts['dependencies']} dependency edge(s) "
        f"&middot; {counts['layers']} layer(s) &middot; {counts['roots']} with nothing to wait on "
        f"&middot; scope: {escape(scope)}</p>"
        f"<p style='margin-top:8px'>{swatches}<a href='../index.html#/tasks'>&larr; Task contracts</a></p>"
        f"</header><main>{svg}</main>\n"
    )


def write_task_graph(tasks: list[dict[str, Any]], out_dir: Path, *,
                     lifecycles: tuple[str, ...] = LIVE_LIFECYCLES) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    live = lifecycles == LIVE_LIFECYCLES
    stem = "graph-tasks" if live else "graph-tasks-all"
    svg = render_svg(tasks, lifecycles=lifecycles)
    path = out_dir / f"{stem}.svg"
    path.write_text(svg, encoding="utf-8", newline="\n")
    scope = "queue, active, done" if live else "all lifecycles"
    (out_dir / f"{stem}.html").write_text(
        render_page(svg, summarize(tasks, lifecycles=lifecycles), scope),
        encoding="utf-8", newline="\n")
    return path


def summarize(tasks: list[dict[str, Any]], *,
              lifecycles: tuple[str, ...] = LIVE_LIFECYCLES) -> dict[str, int]:
    nodes = task_nodes(tasks, lifecycles=lifecycles)
    edges = dependency_edges(nodes)
    depth = layer_of(nodes, edges)
    return {
        "tasks": len(nodes),
        "dependencies": len(edges),
        "layers": (max(depth.values()) + 1) if depth else 0,
        "roots": sum(1 for node in nodes if depth.get(node, 0) == 0),
    }
