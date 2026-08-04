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


class CompactLegendTests(unittest.TestCase):
    def test_task_facets_live_in_the_task_legend(self) -> None:
        source = app().split("function screenTasksGraph() {", 1)[1].split(
            "function screenTasks() {", 1)[0]
        self.assertIn('class="page task-graph-page"', source)
        self.assertIn('aria-label="Task graph filters"', source)
        self.assertIn(".task-graph-page {", css())
        self.assertIn('class="legend-toggle"', source)
        self.assertIn('data-life=', source)
        self.assertIn('data-link=', source)
        self.assertNotIn('<span class="rel-kind">Lifecycle</span>', source)
        self.assertNotIn('<span class="rel-kind">Links</span>', source)

    def test_document_facets_live_in_the_document_legend(self) -> None:
        source = app().split("function screenGraph() {", 1)[1].split(
            "function screenProject() {", 1)[0]
        self.assertIn('aria-label="Document graph filters"', source)
        self.assertIn('data-doc-type=', source)
        self.assertIn('data-doc-prov=', source)
        self.assertNotIn('id="graph-type"', source)
        self.assertNotIn('data-prov=', source)

    def test_legend_toggle_state_is_visibly_and_nonchromatically_distinct(self) -> None:
        rules = css()
        for state in ("true", "false"):
            self.assertIn(
                f'.graph-filter-legend .legend-toggle[aria-pressed="{state}"] {{',
                rules,
            )
        pressed_marker = (
            '.graph-filter-legend .legend-toggle[aria-pressed="true"]::after {'
        )
        self.assertIn(pressed_marker, rules)
        self.assertIn('content:', rules.split(pressed_marker, 1)[1].split("}", 1)[0])

    def test_legend_layout_is_content_measured_and_canvas_bounded(self) -> None:
        source = app()
        rules = css()
        self.assertIn("function measureLegendToggle(button)", source)
        self.assertIn("var measurementHost = button.parentElement", source)
        self.assertIn("measurementHost.appendChild(clone)", source)
        self.assertNotIn("document.body.appendChild(clone)", source)
        self.assertIn("function fitGraphLegend(legend)", source)
        self.assertIn("new ResizeObserver(schedule)", source)
        self.assertIn("Math.floor(wrap.clientWidth * 0.46)", source)
        self.assertIn("repeat(var(--legend-columns, 1), minmax(0, 1fr))", rules)
        self.assertIn("inline-size: min(var(--legend-inline-size, 260px), 46%)", rules)
        self.assertIn("max-block-size: min(55%, 420px)", rules)
        legend_block = rules.split(".graph-filter-legend {", 1)[1].split("}", 1)[0]
        self.assertNotIn("scrollbar-gutter", legend_block)
        label_block = rules.split(
            ".graph-filter-legend .legend-toggle span {", 1
        )[1].split("}", 1)[0]
        self.assertIn("white-space: nowrap", label_block)
        self.assertIn("text-overflow: ellipsis", label_block)
        self.assertNotIn("overflow-wrap: anywhere", label_block)
        self.assertIn("overflow: auto", rules)



class SearchFocusTests(unittest.TestCase):
    def test_search_is_not_part_of_topology_visibility(self) -> None:
        visible = app().split("function visible(node) {", 1)[1].split(
            "function matchesSearch(node, terms) {", 1
        )[0]
        self.assertNotIn("filters.q", visible)
        self.assertNotIn("scoreDoc", visible)
        self.assertIn("function matchesSearch(node, terms)", app())

    def test_task_search_is_separate_from_lifecycle_visibility(self) -> None:
        source = app().split("function taskGraphDataset() {", 1)[1].split(
            "function renderTaskGraphReader", 1
        )[0]
        self.assertIn("activeTaskLifecycles(filters.life)", source)
        self.assertIn("searchMatches: function (node, terms)", source)
        matches = source.split("matches: function (node, filters) {", 1)[1].split(
            "searchMatches:", 1
        )[0]
        self.assertNotIn("filters.q", matches)

    def test_search_dims_context_instead_of_removing_it(self) -> None:
        source = app()
        self.assertIn("var searchFocus = {};", source)
        self.assertIn("var searchNeighbours = {};", source)
        self.assertIn("var searchDim =", source)
        self.assertIn("var live = searchTerms.length ? searchLive : selectedLive;", source)
        self.assertNotIn("var searchDim = !selected", source)
        self.assertIn("ctx.globalAlpha = focused ? (live ? 1 : 0.18) : 0.72;", source)


class TaskFreezeTests(unittest.TestCase):
    def test_task_graph_is_frozen_by_default_and_exposes_a_toggle(self) -> None:
        source = app()
        helper = source.split("function taskGraphFrozen(value) {", 1)[1].split(
            "function toggleExclusiveGraphFacet", 1
        )[0]
        self.assertIn("value !== '0'", helper)
        self.assertIn("data-task-freeze", source)
        self.assertIn("Freeze layout", source)

    def test_task_actions_reuse_the_mounted_canvas(self) -> None:
        source = app()
        self.assertIn("var sameTaskGraph =", source)
        block = source.split("if (sameTaskGraph) {", 1)[1].split(
            "if (hadGraph) graph.teardown();", 1
        )[0]
        self.assertIn("graph.setFrozen", block)
        self.assertIn("graph.setFilters", block)
        self.assertIn("graph.setSelection", block)
        self.assertNotIn("graph.init", block)

    def test_freezing_cancels_simulation_and_clears_velocity(self) -> None:
        body = app().split("function setFrozen(next) {", 1)[1].split(
            "function graphStats() {", 1
        )[0]
        self.assertIn("cancelAnimationFrame", body)
        self.assertIn("node.vx = 0; node.vy = 0", body)
        self.assertIn("animate(60)", body)

    def test_task_remount_restores_cached_layout_without_full_warmup(self) -> None:
        source = app()
        dataset = source.split("function taskGraphDataset() {", 1)[1].split(
            "function renderTaskGraphReader", 1
        )[0]
        self.assertIn("layoutKey: 'tasks'", dataset)
        self.assertIn("initialTicks: 160", dataset)
        self.assertIn("layoutCache[source.layoutKey]", source)
        self.assertIn("initialTickCount = restoredLayout ? 0", source)
        self.assertIn("layoutState: graphLayoutState", source)



class TaskGraphPresentationTests(unittest.TestCase):
    def test_task_graph_uses_the_full_reader_width(self) -> None:
        rules = css().split(".task-graph-page {", 1)[1].split("}", 1)[0]
        self.assertIn("max-width: none", rules)
        self.assertIn("padding: var(--space-4) 0 0", rules)
        self.assertIn(".task-graph-page .graph-screen", css())
        self.assertIn("width: 100%", css().split(".task-graph-page .graph-screen", 1)[1].split("}", 1)[0])

    def test_selected_task_panel_uses_governed_human_and_execution_context(self) -> None:
        source = app().split("function renderTaskGraphReader(id) {", 1)[1].split(
            "function screenTasksGraph() {", 1
        )[0]
        for expected in (
            "taskPresentation(task)", "Required outcome", "Preferred tool", "Review tool",
            "Scope", "Waits on", "Waited on by", "Open full task", "graphTaskHash",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, source)


class TaskRelationStyleTests(unittest.TestCase):
    def test_all_task_relation_kinds_have_distinct_canvas_patterns(self) -> None:
        block = app().split("var TASK_LINKS = [", 1)[1].split("];", 1)[0]
        entries = block.split("{ key: '")[1:]
        patterns = []
        for entry in entries:
            key = entry.split("'", 1)[0]
            dash = entry.split("dash: [", 1)[1].split("]", 1)[0].strip()
            width = entry.split("width:", 1)[1].split("}", 1)[0].strip(" ,")
            patterns.append((key, dash, width))
        self.assertEqual(7, len(patterns))
        signatures = {(dash, width) for _key, dash, width in patterns}
        self.assertEqual(7, len(signatures), "task relation styles must be visually distinct")
        self.assertIn("edgeStyle: function (edge)", app())
        self.assertIn("style ? style.dash", app())

    def test_every_task_relation_pattern_is_mirrored_in_the_legend(self) -> None:
        source = app()
        rules = css()
        for key in (
            "dependsOn", "blockedBy", "decomposedInto", "slices", "sliceRef",
            "informedBy", "parallelWith",
        ):
            with self.subTest(key=key):
                self.assertIn("task-link-' + link.key", source)
                self.assertIn(f".task-link-{key}", rules)


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


class LayoutSettlingTests(unittest.TestCase):
    """The layout must stop because it came to rest, not because a frame budget ran out.

    Shipped behaviour before this: `animate(60)` ran exactly 60 frames, about one second, then
    stopped whatever the layout was doing. On the task graph the fastest node was still travelling
    hundreds of pixels per frame at that point, so the physics appeared to die mid-flight and the
    only way to resume it was toggling Freeze, which bought another second. Users had to toggle
    repeatedly to reach a settled layout.
    """

    def test_the_run_is_not_ended_by_a_frame_countdown(self) -> None:
        source = app()
        self.assertNotIn("if (left > 0 || drag)", source,
                         "a decrementing frame budget is what stopped the layout mid-settle")
        self.assertIn("atRest", source, "termination must be decided by rest, not by a count")

    def test_the_simulation_cools_so_rest_is_guaranteed(self) -> None:
        """Quiet-frame detection alone never fires: this force field does not converge on its own.

        Measured over 1,800 frames without cooling, the task graph was still moving several pixels
        per frame, so every run would have ended at the ceiling instead of at rest.
        """
        source = app()
        self.assertIn("ALPHA_COOL", source)
        self.assertIn("alpha *= ALPHA_COOL", source, "alpha must actually decay each frame")
        self.assertIn("alpha < ALPHA_MIN", source, "a cooled simulation must be allowed to finish")

    def test_tick_reports_how_far_the_layout_moved(self) -> None:
        """Rest cannot be detected if the integrator reports nothing."""
        source = app()
        body = source[source.index("function tick(alpha)"):source.index("function animate(")]
        self.assertIn("return moved", body)

    def test_repulsion_is_clamped_at_short_range(self) -> None:
        """Unclamped, 7200/d^2 explodes as two nodes approach: one frame moved a node 40,791px,
        which is what flung stray nodes into a long tail away from the cluster."""
        source = app()
        self.assertIn("REPEL_MIN_D", source)
        self.assertNotIn("var repel = 7200 / (d * d);", source,
                         "the unclamped inverse-square term is the blow-up")

    def test_freeze_still_stops_the_layout_immediately(self) -> None:
        """Cooling must not weaken the control: freezing mid-run has to halt on the next frame."""
        source = app()
        body = source[source.index("function animate(minFrames)"):source.index("function radius(")]
        self.assertIn("if (frozen) { raf = null; draw(); return; }", body)


class ProgressiveDrilldownTests(unittest.TestCase):
    """A package must reveal ONE level at a time.

    Listing every distinct module path at a package's level dumped the whole subtree: real
    products rendered ~500 and 222 fully-qualified siblings. Both were unnavigable while every
    count looked correct.
    """

    def project_js(self) -> str:
        return (READER / "project.js").read_text(encoding="utf-8")

    def test_the_package_level_shows_one_segment_and_routes_by_the_path(self) -> None:
        """The label is a segment; the route is the path. Emitting the segment as the route is
        what made the next level unable to find anything."""
        source = self.project_js()
        self.assertIn("var route = modulePrefix(shape.raw, shape.skip + collapsed + 1);", source,
                      "the package level must route by a real module path")
        self.assertIn("label: shape.tail[collapsed], nodes: 0, files: {}, pending: 0", source,
                      "the label must be the single segment at the current depth")
        self.assertNotIn("var moduleName = node.module || '(root)';\n      var id = 'module:'",
                         source, "bucketing on the full module path is the flat dump")

    def test_a_single_child_chain_is_collapsed(self) -> None:
        """A package whose only entry is `src/` must show `src`'s contents, not `src` itself:
        a level with one choice is not a choice."""
        source = self.project_js()
        # Assert each CALL, not the name: a substring check also matches a renamed definition, and
        # one shared assertion stays green while the other call site is broken.
        self.assertIn("var collapsed = collapsePassthrough(levelTails(context, []), true);", source)
        self.assertIn("var rootDepth = collapsePassthrough(levelTails(context, []), true);", source)
        self.assertIn("var collapsed = isRoot ? rootDepth : collapsePassthrough(members.map(",
                      source)
        self.assertIn("function collapsePassthrough(pathsList, packageLevel) {", source)

    def test_collapsing_stops_where_a_real_choice_appears(self) -> None:
        """`src` plus `test` must both stay visible; collapsing past a fork would hide a sibling."""
        source = self.project_js()
        self.assertIn("if (segs[depth] !== candidate) return depth;", source)
        self.assertIn("if (!deeper) return depth;", source)

    def test_a_package_root_file_does_not_cost_a_navigation_level(self) -> None:
        """A build file at a package's root is not a CHOICE of where to descend. Counting it as a
        fork put one stray `vite.config.ts` in front of every folder the product has."""
        source = self.project_js()
        self.assertIn("          if (depth > 0 || !packageLevel) return depth;\n"
                      "          continue;", source,
                      "a package's root files ride along; anything deeper stops the collapse")
        self.assertNotIn("if (segs.length <= depth + 1) return depth;", source,
                         "stopping at any terminator is what charged a level for a stray file")

    def test_the_root_file_exception_is_confined_to_the_package_level(self) -> None:
        """Applied at every depth it flattened a single-child module chain, printing a file beside
        its own grandchildren -- the levels below a package must stop at their own files."""
        source = self.project_js()
        self.assertIn("collapsePassthrough(members.map(function (entry) {\n"
                      "      return entry.shape.tail.slice(prefix.tail.length);\n"
                      "    }), false);", source,
                      "a module level must not claim the package-root exception")
        self.assertIn("depth === 0) + 1;", source,
                      "the trail walk must apply the exception only to the package level")

    def test_the_package_level_shows_its_own_files_rather_than_a_root_bucket(self) -> None:
        """Every package a directory-per-package indexer produces is flat, so a `(root)` bucket
        was one click that led to exactly one node."""
        source = self.project_js()
        self.assertIn("        ownFiles.push(file);\n"
                      "        ownerOf[file.path] = 'file:' + file.path;\n"
                      "        return;", source)
        self.assertNotIn("label: deeper ? shape.tail[collapsed] : '(root)'", source)

    def test_levels_are_driven_by_files_so_a_symbolless_file_stays_reachable(self) -> None:
        """Bucketing the package level from P.nodes dropped any file that declares no symbol, and
        measured the collapse against a different set than the level below it used."""
        source = self.project_js()
        self.assertIn("      bucket.nodes += file.nodes;", source)
        self.assertNotIn("var owned = P.nodes.filter(function (node) "
                         "{ return node.crate === context.rust; });", source)

    def test_separators_from_every_indexed_language_are_handled(self) -> None:
        """Rust writes `a::b`, Python and TypeScript write `a.b`, paths write `a/b`."""
        self.assertIn("/::|[./]/", self.project_js())

    def test_a_module_level_owns_its_whole_subtree(self) -> None:
        """Selecting a level's files by whole-path equality is a DEAD END once labels are
        segments: a branch name matched no file and rendered a single node while the 113 files
        beneath it were unreachable. Membership is a prefix relation, not equality."""
        source = self.project_js()
        self.assertNotIn("rawModulePath(file.module) === rawModule", source,
                         "whole-path equality drops every descendant of the selected module")
        self.assertIn("!underPrefix(shape.tail, prefix.tail)", source,
                      "a module level must take everything beneath its prefix")

    def test_a_module_level_offers_sub_modules_as_well_as_files(self) -> None:
        """Descent has to continue: a level that can only end in files cannot reach depth 3."""
        source = self.project_js()
        self.assertIn(
            "var route = modulePrefix(\n"
            "        entry.shape.raw, entry.shape.skip + prefix.tail.length + collapsed + 1);",
            source, "a module level must emit deeper module routes, not only files")

    def test_the_package_root_is_not_read_as_a_wildcard(self) -> None:
        """An empty prefix is a prefix of everything. Treated as one, `(root)` would swallow the
        entire package instead of the files above its first real branch."""
        source = self.project_js()
        self.assertIn("var isRoot = !prefix.tail.length;", source)
        self.assertIn("if (isRoot ? shape.tail.length > rootDepth : !underPrefix(", source)

    def test_the_root_route_and_the_package_level_measure_depth_the_same_way(self) -> None:
        """Two depths would strand whatever they disagreed about: a file the package level counts
        as its own but the `(root)` route then refuses to list is unreachable."""
        source = self.project_js()
        self.assertEqual(2, source.count("collapsePassthrough(levelTails(context, []), true)"),
                         "the package level and the (root) route take one depth from one place")

    def test_level_edges_aggregate_through_the_same_buckets(self) -> None:
        """The relation modes key on the SAME grouping the nodes were built with. Keying on the
        full module path matched no bucket, so every non-hierarchy mode drew an edgeless level."""
        source = self.project_js()
        self.assertNotIn("return 'module:' + context.route + '|' + (nodeModule[id] || '(root)');",
                         source, "the full module path is no longer a bucket key")
        self.assertEqual(2, source.count("return node ? (ownerOf[node.path] || '') : '';"),
                         "both levels aggregate through the map their own nodes were built from")

    def test_up_navigation_climbs_one_level_not_one_segment(self) -> None:
        """Dropping a single segment can land on a collapsed pass-through, whose level collapses
        straight back to where it started -- the button then looks broken."""
        source = self.project_js()
        self.assertIn("var trail = currentScope === 'module' || currentScope === 'file'\n"
                      "      ? moduleTrail(crateRouteContext(currentCrate), currentModule) : [];",
                      source)
        self.assertIn("var parent = trail.length > 1 ? trail[trail.length - 2] : null;", source)

    def test_the_breadcrumb_names_every_level_descended_through(self) -> None:
        source = self.project_js()
        self.assertIn("var steps = moduleTrail(crateRouteContext(crateValue), moduleName);", source)
        self.assertIn("function moduleTrail(context, moduleName) {", source)

    def test_the_segment_scan_cannot_leak_state_between_calls(self) -> None:
        """A shared /g regex keeps lastIndex, so the second caller starts mid-string and cuts the
        route in the wrong place."""
        source = self.project_js()
        self.assertIn("var scan = /::|[./]/g;", source)
        self.assertNotIn("MODULE_SEPARATORS.exec(", source)
