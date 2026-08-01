"""The task dependency hierarchy.

The properties that matter here are the ones a reader trusts without checking: depth reflects the
real dependency chain, an edge only ever joins two nodes that are both on the page, a cyclic corpus
still renders, and the same corpus produces the same bytes on every machine.
"""

from __future__ import annotations

import unittest

import scripts.ai_plane.task_graph as task_graph


def task(task_id: str, *, lifecycle: str = "queue", depends: list[str] | None = None,
         title: str | None = None) -> dict:
    return {
        "task_id": task_id,
        "lifecycle": lifecycle,
        "contract": {"title": title or task_id},
        "dependencies": [{"task_id": d} for d in (depends or [])],
    }


class ScopeTests(unittest.TestCase):
    def test_only_the_requested_lifecycles_become_nodes(self) -> None:
        tasks = [task("a"), task("b", lifecycle="archive"), task("c", lifecycle="done")]
        live = task_graph.task_nodes(tasks, lifecycles=task_graph.LIVE_LIFECYCLES)
        self.assertEqual({"a", "c"}, set(live))
        every = task_graph.task_nodes(tasks, lifecycles=("queue", "active", "done", "archive"))
        self.assertEqual({"a", "b", "c"}, set(every))

    def test_a_dependency_outside_the_scope_is_dropped_not_drawn(self) -> None:
        # A dangling arrow reads as a missing task rather than as an out-of-scope one, so an edge
        # survives only when both of its ends are on the page.
        tasks = [task("a", depends=["archived"]), task("archived", lifecycle="archive")]
        nodes = task_graph.task_nodes(tasks, lifecycles=task_graph.LIVE_LIFECYCLES)
        self.assertEqual([], task_graph.dependency_edges(nodes))

    def test_a_self_dependency_is_not_an_edge(self) -> None:
        nodes = task_graph.task_nodes([task("a", depends=["a"])],
                                      lifecycles=task_graph.LIVE_LIFECYCLES)
        self.assertEqual([], task_graph.dependency_edges(nodes))

    def test_a_plain_string_dependency_is_understood(self) -> None:
        tasks = [{"task_id": "a", "lifecycle": "queue", "dependencies": ["b"]}, task("b")]
        nodes = task_graph.task_nodes(tasks, lifecycles=task_graph.LIVE_LIFECYCLES)
        self.assertEqual([("b", "a")], task_graph.dependency_edges(nodes))


class LayeringTests(unittest.TestCase):
    def layers(self, tasks: list[dict]) -> dict[str, int]:
        nodes = task_graph.task_nodes(tasks, lifecycles=task_graph.LIVE_LIFECYCLES)
        return task_graph.layer_of(nodes, task_graph.dependency_edges(nodes))

    def test_depth_follows_the_dependency_chain(self) -> None:
        depth = self.layers([task("a"), task("b", depends=["a"]), task("c", depends=["b"])])
        self.assertEqual({"a": 0, "b": 1, "c": 2}, depth)

    def test_depth_is_the_longest_path_not_the_shortest(self) -> None:
        # d depends on a directly AND through b->c. It belongs below the deepest of them, or its
        # edges would point upward through the layout.
        depth = self.layers([
            task("a"), task("b", depends=["a"]), task("c", depends=["b"]),
            task("d", depends=["a", "c"]),
        ])
        self.assertEqual(3, depth["d"])

    def test_an_independent_task_is_a_root(self) -> None:
        depth = self.layers([task("a"), task("b")])
        self.assertEqual({"a": 0, "b": 0}, depth)

    def test_a_dependency_cycle_renders_instead_of_hanging(self) -> None:
        # A cycle is a contract defect, not a reason to crash the reader.
        tasks = [task("a", depends=["c"]), task("b", depends=["a"]), task("c", depends=["b"])]
        depth = self.layers(tasks)
        self.assertEqual({"a", "b", "c"}, set(depth))
        svg = task_graph.render_svg(tasks)
        self.assertIn("<svg", svg)


class RenderTests(unittest.TestCase):
    def test_every_node_and_edge_is_drawn(self) -> None:
        svg = task_graph.render_svg([task("a"), task("b", depends=["a"])])
        self.assertEqual(2, svg.count('class="n"'))
        self.assertEqual(1, svg.count('class="e"'))

    def test_lifecycle_selects_the_fill(self) -> None:
        svg = task_graph.render_svg([task("a", lifecycle="done")])
        self.assertIn(task_graph.LIFECYCLE_FILL["done"], svg)

    def test_output_is_byte_identical_for_the_same_corpus(self) -> None:
        # Deterministic output is what lets a generated file be committed and diffed.
        tasks = [task("b", depends=["a"]), task("a"), task("c", depends=["a"])]
        self.assertEqual(task_graph.render_svg(tasks), task_graph.render_svg(list(reversed(tasks))))

    def test_a_long_title_is_truncated_rather_than_overflowing_its_box(self) -> None:
        svg = task_graph.render_svg([task("a", title="x" * 120)])
        self.assertIn("…", svg)
        self.assertNotIn("x" * 60, svg)

    def test_markup_in_a_title_is_escaped(self) -> None:
        svg = task_graph.render_svg([task("a", title="<script>bad()</script>")])
        self.assertNotIn("<script>", svg)
        self.assertIn("&lt;script&gt;", svg)

    def test_an_empty_corpus_renders_a_statement_not_a_crash(self) -> None:
        svg = task_graph.render_svg([])
        self.assertIn("<svg", svg)
        self.assertIn("No tasks", svg)

    def test_the_svg_declares_what_it_shows(self) -> None:
        svg = task_graph.render_svg([task("a"), task("b", depends=["a"])])
        self.assertIn('aria-label="Task dependency hierarchy: 2 tasks, 1 dependencies"', svg)


class SummaryTests(unittest.TestCase):
    def test_counts_match_the_drawing(self) -> None:
        tasks = [task("a"), task("b", depends=["a"]), task("c", depends=["b"]), task("d")]
        counts = task_graph.summarize(tasks)
        self.assertEqual({"tasks": 4, "dependencies": 2, "layers": 3, "roots": 2}, counts)

    def test_an_empty_corpus_summarizes_to_zero(self) -> None:
        self.assertEqual(
            {"tasks": 0, "dependencies": 0, "layers": 0, "roots": 0}, task_graph.summarize([]))


class ArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        from pathlib import Path
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)

    def test_both_the_svg_and_its_page_are_written(self) -> None:
        # The reader links the page; a bare .svg opens as a file rather than as a view.
        path = task_graph.write_task_graph([task("a")], self.out)
        self.assertEqual("graph-tasks.svg", path.name)
        page = self.out / "graph-tasks.html"
        self.assertTrue(page.is_file())
        body = page.read_text(encoding="utf-8")
        self.assertIn("<svg", body)
        self.assertIn("Task contracts", body)

    def test_the_full_scope_writes_a_separate_artifact(self) -> None:
        task_graph.write_task_graph(
            [task("a", lifecycle="archive")], self.out,
            lifecycles=("queue", "active", "done", "archive"))
        self.assertTrue((self.out / "graph-tasks-all.svg").is_file())
        self.assertFalse((self.out / "graph-tasks.svg").is_file())

    def test_the_page_states_the_scope_it_drew(self) -> None:
        task_graph.write_task_graph([task("a")], self.out)
        self.assertIn("queue, active, done",
                      (self.out / "graph-tasks.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
