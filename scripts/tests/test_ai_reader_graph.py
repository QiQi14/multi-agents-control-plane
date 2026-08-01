"""Structural guards over the reader's graph surface.

These are source contracts, not behavioural tests: there is no JavaScript runtime in this suite, so
nothing here proves the canvas draws. Every defect this file pins was found by driving the page in a
browser, which is slow, manual, and exactly why the same class kept recurring -- a dropped field, an
unbound handler, a filter that could not express "none", a toggle with no visible state. Each one is
detectable in the shipped source, so each one is caught here before it reaches a browser again.

What this cannot catch is anything about rendering or interaction. Treat a green run as "the wiring
that broke before is still wired", not as "the graph works".
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

READER = (Path(__file__).resolve().parents[1] / "ai_plane" / "knowledge_projection"
          / "reader_assets" / "production")
APP_JS = READER / "app.js"
DELTA_CSS = READER / "production-delta.css"


def app() -> str:
    return APP_JS.read_text(encoding="utf-8")


def css() -> str:
    return DELTA_CSS.read_text(encoding="utf-8")


class EdgeMappingTests(unittest.TestCase):
    def test_the_engine_carries_the_dataset_edge_kind(self) -> None:
        # The mapping copied s, t, type, prov and bridge only, so `kind` was silently dropped and
        # every dataset edge filter compared against undefined. The graph drew no links at all.
        mapping = re.search(r"edges = source\.edges\.map\(function \(e\) \{(.+?)\}\)",
                            app(), re.S)
        self.assertIsNotNone(mapping, "edge mapping not found")
        self.assertIn("kind: e.kind", mapping.group(1))

    def test_edge_visibility_defers_to_the_dataset(self) -> None:
        self.assertIn("source.edgeVisible", app())

    def test_node_visibility_defers_to_the_dataset(self) -> None:
        self.assertIn("source.matches", app())

    def test_init_accepts_a_dataset_and_defaults_to_documents(self) -> None:
        # Omitting the argument must leave the documents surface byte-identical in behaviour.
        self.assertIn("function init(container, initialSelection, initialFilters, dataset)", app())
        self.assertIn("source = dataset || documentDataset()", app())


class ViewSwitchTests(unittest.TestCase):
    def test_both_task_screens_bind_the_view_toggle(self) -> None:
        # The library screen rendered the toggle and bound nothing, so the Graph button looked live
        # and did nothing from the screen a reader actually starts on. Count CALL sites only: the
        # definition line also reads `bindTaskViewSwitch()`, and counting it hid a missing caller.
        calls = re.findall(r"^\s+bindTaskViewSwitch\(\);", app(), re.M)
        self.assertEqual(2, len(calls), "expected a call from each task screen")

    def test_the_toggle_is_rendered_by_one_shared_helper(self) -> None:
        # Two hand-written copies drift; the pressed state is the thing that drifts first.
        self.assertEqual(1, len(re.findall(r"function taskViewSwitch\(", app())))


class FilterSetTests(unittest.TestCase):
    """Both filters are sets, and both can express the empty set.

    setQuery drops empty parameters, so an empty string cannot survive a round trip through the
    URL. Without an explicit sentinel, turning every option off silently restored the defaults on
    the next render, which reads as the toggles being ignored.
    """

    def test_links_and_lifecycles_both_declare_an_empty_sentinel(self) -> None:
        source = app()
        self.assertIn("var NO_TASK_LINKS = 'none';", source)
        self.assertIn("var NO_TASK_LIFECYCLES = 'none';", source)

    def test_each_reader_returns_the_empty_set_for_its_sentinel(self) -> None:
        source = app()
        for reader, sentinel in (("activeTaskLinks", "NO_TASK_LINKS"),
                                 ("activeTaskLifecycles", "NO_TASK_LIFECYCLES")):
            body = re.search(rf"function {reader}\(value\) \{{(.+?)\n  \}}", source, re.S)
            with self.subTest(reader=reader):
                self.assertIsNotNone(body, f"{reader} not found")
                self.assertIn(f"value === {sentinel}", body.group(1))
                self.assertIn("return []", body.group(1))

    def test_each_toggle_emits_the_sentinel_when_it_empties(self) -> None:
        source = app()
        for toggle, sentinel in (("toggleTaskLink", "NO_TASK_LINKS"),
                                 ("toggleTaskLifecycle", "NO_TASK_LIFECYCLES")):
            body = re.search(rf"function {toggle}\(value, key\) \{{(.+?)\n  \}}", source, re.S)
            with self.subTest(toggle=toggle):
                self.assertIsNotNone(body, f"{toggle} not found")
                self.assertIn(sentinel, body.group(1))


class LifecycleFilterTests(unittest.TestCase):
    def test_lifecycle_offers_the_four_states_and_no_all_button(self) -> None:
        source = app()
        self.assertIn("var TASK_LIFECYCLES = ['queued', 'active', 'done', 'archived'];", source)
        # The old single-select row rendered '' as an "all" entry alongside the four states.
        self.assertNotIn("['', 'queued', 'active', 'done', 'archived']", source)

    def test_lifecycle_matching_uses_the_set_not_an_equality(self) -> None:
        self.assertNotIn("task.lifecycle !== filters.life", app())
        self.assertIn("activeTaskLifecycles(filters.life).indexOf(task.lifecycle)", app())


class LinkVocabularyTests(unittest.TestCase):
    def links(self) -> list[str]:
        block = re.search(r"var TASK_LINKS = \[(.+?)\n  \];", app(), re.S)
        self.assertIsNotNone(block, "TASK_LINKS not found")
        return re.findall(r"key: '([a-zA-Z]+)'", block.group(1))

    def test_the_drawn_relations_are_declared_once(self) -> None:
        self.assertEqual(
            ["dependsOn", "blockedBy", "decomposedInto", "slices", "sliceRef",
             "informedBy", "parallelWith"],
            self.links())

    def test_blocks_is_not_drawn_because_it_is_the_stored_inverse(self) -> None:
        # `blocks` mirrors dependsOn: 437 entries describing the same 455 relationships. Drawing
        # both puts two lines between one pair and doubles every degree count.
        self.assertNotIn("blocks", self.links())


class ToggleStateTests(unittest.TestCase):
    def test_pressed_state_is_visible_not_only_accessible(self) -> None:
        # aria-pressed was set correctly and styled nowhere, so on and off were the same button.
        rules = css()
        # Match the declaration block, not merely the selector text: a ::before rule mentions the
        # same selector, so a substring check passed even with the block itself renamed away.
        for state in ("true", "false"):
            block = re.search(
                r'\.graph-controls \.icon-btn\[aria-pressed="%s"\] \{(.+?)\}' % state,
                rules, re.S)
            with self.subTest(state=state):
                self.assertIsNotNone(block, f"no styling block for aria-pressed={state}")
                self.assertTrue(block.group(1).strip(), "empty styling block")

    def test_state_does_not_rely_on_colour_alone(self) -> None:
        marker = re.search(r'\[aria-pressed="true"\]::before \{\s*\n\s*content: "([^"]*)";', css())
        self.assertIsNotNone(marker, "no non-colour marker on the pressed state")
        self.assertIn("✓", marker.group(1))


class AssetIntegrityTests(unittest.TestCase):
    def test_reader_sources_contain_no_control_bytes(self) -> None:
        # A shell round trip wrote a NUL into the stylesheet twice while this surface was built,
        # which turned it binary to every text tool that touched it afterwards.
        for path in (APP_JS, DELTA_CSS):
            raw = path.read_bytes()
            with self.subTest(asset=path.name):
                self.assertEqual(0, raw.count(b"\x00"), "NUL byte in a text asset")
                self.assertEqual(0, raw.count(b"\r"), "CR byte in an LF-contract asset")

    def test_reader_sources_are_valid_utf8(self) -> None:
        for path in (APP_JS, DELTA_CSS):
            with self.subTest(asset=path.name):
                path.read_bytes().decode("utf-8")


if __name__ == "__main__":
    unittest.main()
