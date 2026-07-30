import hashlib
import json
import re
import io
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RENDERER_DIR = ROOT / ".ai" / "templates" / "pr-blueprint" / "renderer"
if str(RENDERER_DIR) not in sys.path:
    sys.path.insert(0, str(RENDERER_DIR))

from build_report import build as build_report
import mermaid_render
import render_html as renderer
import validate_spec as validator
from parse_spec import RECORD_CONTRACTS, SECTION_ALIASES, parse_spec
from validate_spec import validate_spec
from scripts.ai_plane.blueprint import blueprint_spec_template
from scripts.ai_plane.constants import BLUEPRINT_DIR
from render_html import render_layout, render_markdown



FIXTURE_DIR = ROOT / "scripts" / "tests" / "fixtures" / "pr-blueprint"
REALISTIC_MERMAID_SVG = """<svg id="flowchart-static" width="100%" xmlns="http://www.w3.org/2000/svg" class="flowchart" style="max-width: 420px;" viewBox="0 0 420 120" role="graphics-document document">
<style>#flowchart-static{font-family:Arial,sans-serif}.node rect{fill:#f4f4f4;stroke:#666;stroke-width:1px}.node text{fill:#222}.edgePath path{fill:none;stroke:#555;stroke-width:2px}</style>
<defs><marker id="flowchart-static-pointEnd" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" style="fill:#555;stroke:#555"></path></marker></defs>
<g class="root"><g class="edgePaths"><g class="edgePath"><path d="M 140 60 L 280 60" marker-end="url(#flowchart-static-pointEnd)"></path></g></g><g class="nodes"><g class="node" transform="translate(80,60)"><rect x="-60" y="-24" width="120" height="48" rx="5"></rect><text x="0" y="5" text-anchor="middle">Client</text></g><g class="node" transform="translate(340,60)"><rect x="-60" y="-24" width="120" height="48" rx="5"></rect><text x="0" y="5" text-anchor="middle">Report</text></g></g></g>
</svg>"""
def parse_schema_aliases_from_md(text: str) -> dict[str, str]:
    aliases: dict[str, str] = {}
    in_aliases = False
    for line in text.splitlines():
        if line.startswith("## Heading Aliases"):
            in_aliases = True
            continue
        if in_aliases and line.startswith("## "):
            break
        if in_aliases and line.startswith("- "):
            parts = line[2:].split(":", 1)
            if len(parts) == 2:
                canonical = parts[0].replace("-", "").replace("\x60", "").strip()
                raw_aliases = [a.replace("\x60", "").strip() for a in parts[1].split(",")]
                for clean_a in raw_aliases:
                    if clean_a:
                        aliases[clean_a] = canonical
    return aliases


SPEC_FRONTMATTER_OPTIONAL_KEYS = {"ticket"}
SPEC_REGISTRY_IDENTITY_KEYS = ("id", "type", "domain", "status", "owner", "relations")
EXAMPLE_SPEC_DIR = ROOT / ".ai" / "templates" / "pr-blueprint" / "examples"


def extract_first_yaml_block(text: str) -> str:
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == "\x60\x60\x60yaml"), None)
    if start is None:
        return ""
    for index in range(start + 1, len(lines)):
        if lines[index].strip() == "\x60\x60\x60":
            return "\n".join(lines[start + 1 : index])
    return ""


def parse_frontmatter_fields(text: str) -> dict[str, str]:
    """Top-level frontmatter keys and scalar values from a `---` delimited block.

    Mirrors parse_spec.parse_frontmatter: comments, blank lines, list items, and indented
    continuation lines are not top-level keys. A key opening a list carries an empty value.
    """
    lines = text.splitlines()
    start = next((index for index, line in enumerate(lines) if line.strip() == "---"), None)
    if start is None:
        return {}
    fields: dict[str, str] = {}
    for line in lines[start + 1 :]:
        if line.strip() == "---":
            break
        if not line.strip() or line.strip().startswith("#"):
            continue
        if line.startswith((" ", "\t", "-")):
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip().lower()] = value.strip()
    return fields


def parse_schema_contracts_from_md(text: str) -> dict[str, list[str]]:
    contracts: dict[str, list[str]] = {}
    current_sec = ""
    for line in text.splitlines():
        if line.startswith("### ") and "(" in line and ")" in line:
            sec = line.split("(")[1].split(")")[0].replace("\x60", "").strip()
            current_sec = sec
            contracts[current_sec] = []
            continue
        if current_sec and line.startswith("Required keys:"):
            main_part = re.sub(r"\(.*?\)", "", line.split(":", 1)[1])
            raw_keys = [k.replace("\x60", "").replace(".", "").strip().lower() for k in main_part.split(",") if k.strip()]
            contracts[current_sec] = sorted(raw_keys)
            current_sec = ""
    return contracts


class BlueprintRendererCoreTests(unittest.TestCase):

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.tmp_path = Path(self.temp_dir.name)

    def test_all_six_presets_init_exact_declared_order_and_build(self) -> None:
        presets = ["fullstack", "frontend", "backend", "api-only", "engine", "tool"]
        preset_dir = BLUEPRINT_DIR / "presets"

        for preset in presets:
            spec_content = blueprint_spec_template("test-feature", preset, "app")
            spec_file = self.tmp_path / f"{preset}.spec.md"
            out_file = self.tmp_path / f"{preset}.html"
            spec_file.write_text(spec_content, encoding="utf-8")

            # Parse
            spec, parse_errors = parse_spec(spec_file)
            self.assertEqual(parse_errors, [], f"Preset {preset} had parse errors: {parse_errors}")

            # Validate
            errors, warnings, loaded_preset = validate_spec(spec, preset_dir, parse_errors)
            self.assertEqual(errors, [], f"Preset {preset} had validation errors: {errors}")
            self.assertEqual(warnings, [], f"Preset {preset} had unexpected warnings: {warnings}")

            # Exact section sequence assertion (Q175A-4)
            expected_sections = [s for s in loaded_preset.get("sections", []) if s != "metadata"]
            raw_sections = spec.get("_raw_sections", [])
            self.assertEqual(
                raw_sections,
                expected_sections,
                f"Preset {preset} exact section sequence mismatch",
            )

            # Build report
            res_path, _, _ = build_report(spec_file, out_file)
            self.assertTrue(res_path.exists(), f"Output HTML for {preset} was not created")
            self.assertGreater(res_path.stat().st_size, 0)

    def test_full_grammar_round_trip_parse_validate_build(self) -> None:
        all_grammar_spec = """---
preset: fullstack
title: All Grammar Feature
status: Draft
kind: app
author: Tester
reviewer: Reviewer
platform: Web
version: v1
ticket: GAME-9999
---

# Overview
This exercises every documented grammar component in a single spec.

# API Endpoints
## POST /v1/test
### Description
Endpoint test description.

### Request Body
```json
{ "key": "value" }
```

### Responses
#### 200 OK
```json
{ "status": "ok" }
```

# WebSocket Messages
## WS Endpoint URL: wss://example.com/ws
## Auth: Bearer token

### ClientToServer: test_event
#### Description
Test websocket event payload.

```json
{ "event": "test" }
```

# Data Models
## TestModel
Test data model schema.

```ts
interface TestModel {
  id: string;
}
```

# Validation Matrix
- Field: username
  Type: String
  Rules:
    - Non-empty
  UI Behavior:
    - Error border
  Test ID: val-user

# Function Log
- Name: execute_task
  Trigger: Button click
  Side Effects:
    - Mutates state

# State Matrix
- State: loading
  Trigger: Fetch data
  Expected:
    - Show spinner

# Component States
- State: active
  Surface: Main Dashboard
  Signals:
    - Clicked
  Expected:
    - Highlighted

# Motion Spec
- Element: Modal
  Property: transform
  Duration: 300ms

# Architecture Diagrams
## System Architecture
```mermaid
flowchart LR
    A --> B
```

# QA Checklist
- Verify test event delivery.

# Risks
- Edge case handling in websocket reconnect.

# Open Questions
- None.

# Decisions
- Standardized schemas.

# Notes
Test complete.
"""
        spec_file = self.tmp_path / "all_grammar.spec.md"
        out_file = self.tmp_path / "all_grammar.html"
        spec_file.write_text(all_grammar_spec, encoding="utf-8")

        spec, parse_errors = parse_spec(spec_file)
        self.assertEqual(parse_errors, [])

        preset_dir = BLUEPRINT_DIR / "presets"
        errors, warnings, _ = validate_spec(spec, preset_dir, parse_errors)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

        self.assertEqual(spec["metadata"]["ticket"], "GAME-9999")
        self.assertEqual(len(spec["api"]), 1)
        self.assertEqual(len(spec["websocket"]["messages"]), 1)
        self.assertEqual(len(spec["data_models"]), 1)
        self.assertEqual(len(spec["validation"]), 1)
        self.assertEqual(len(spec["function_log"]), 1)
        self.assertEqual(len(spec["state_matrix"]), 1)
        self.assertEqual(len(spec["ui_states"]), 1)
        self.assertEqual(len(spec["motion"]), 1)

        res_path, _, _ = build_report(spec_file, out_file)
        self.assertTrue(res_path.exists())

    def test_record_validation_missing_start_keys_creates_candidate_row(self) -> None:
        # Q175A-1: Rows that omit the section's start key must create candidate rows and fail validation
        broken_spec = """---
preset: fullstack
title: Broken Records Omitted Start Keys
status: Draft
kind: app
---

# Validation Matrix
- Rules:
    - Rule 1
  Type: String

# Function Log
- Trigger: click
  Side Effects:
    - Debits account

# State Matrix
- Trigger: click
  Expected:
    - Show spinner

# Component States
- Surface: Dashboard
  Expected:
    - Active highlight

# Motion Spec
- Property: opacity
  Duration: 200ms
"""
        spec_file = self.tmp_path / "omitted_start_keys.spec.md"
        spec_file.write_text(broken_spec, encoding="utf-8")

        spec, parse_errors = parse_spec(spec_file)
        preset_dir = BLUEPRINT_DIR / "presets"
        errors, _, _ = validate_spec(spec, preset_dir, parse_errors)

        self.assertIn("validation row 1: missing 'field'", errors)
        self.assertIn("function_log row 1: missing 'name'", errors)
        self.assertIn("state_matrix row 1: missing 'state'", errors)
        self.assertIn("ui_states row 1: missing 'state'", errors)
        self.assertIn("motion row 1: missing 'element'", errors)

    def test_record_validation_two_row_reproduction_later_missing_start_key(self) -> None:
        # Q175A-1: Exact two-row reproduction: valid row 1 followed by unindented row 2 missing start key
        two_row_spec = """---
preset: fullstack
title: Two Row Test
status: Draft
kind: app
---

# Function Log
- Name: first
  Trigger: click
- Side Effects:
    - debits account
"""
        spec_file = self.tmp_path / "two_row.spec.md"
        spec_file.write_text(two_row_spec, encoding="utf-8")

        spec, parse_errors = parse_spec(spec_file)
        preset_dir = BLUEPRINT_DIR / "presets"
        errors, _, _ = validate_spec(spec, preset_dir, parse_errors)

        self.assertEqual(len(spec["function_log"]), 2, "Must produce two separate records")
        self.assertIn("function_log row 2: missing 'name'", errors)
        self.assertIn("function_log row 2: missing 'trigger'", errors)

    def test_record_validation_plain_unindented_bullet_without_colon(self) -> None:
        # Q175A-1: Plain bullet without colon at record level creates candidate row
        plain_bullet_spec = """---
preset: fullstack
title: Plain Bullet Record
status: Draft
kind: app
---

# Function Log
- click
"""
        spec_file = self.tmp_path / "plain_bullet.spec.md"
        spec_file.write_text(plain_bullet_spec, encoding="utf-8")

        spec, parse_errors = parse_spec(spec_file)
        preset_dir = BLUEPRINT_DIR / "presets"
        errors, _, _ = validate_spec(spec, preset_dir, parse_errors)

        self.assertIn("function_log row 1: missing 'name'", errors)
        self.assertIn("function_log row 1: missing 'trigger'", errors)

    def test_empty_headings_diagnostics_unknown_and_preset_excluded(self) -> None:
        # Q175A-2: Empty headings must be tracked and validated for unknown headings and preset exclusions
        spec_with_empty_headings = """---
preset: frontend
title: Empty Heading Test
status: Draft
kind: app
---

# Overview
Overview text.

# Typo Heading

# Function Log

# QA Checklist
- Check item
"""
        spec_file = self.tmp_path / "empty_headings.spec.md"
        spec_file.write_text(spec_with_empty_headings, encoding="utf-8")

        spec, parse_errors = parse_spec(spec_file)
        preset_dir = BLUEPRINT_DIR / "presets"
        errors, warnings, _ = validate_spec(spec, preset_dir, parse_errors)

        self.assertTrue(any("section 'Typo Heading': unknown section heading" in e for e in errors))
        self.assertTrue(any("function_log" in w and "frontend" in w for w in warnings))

    def test_malformed_websocket_and_data_models_diagnostics(self) -> None:
        # Q175A-3: Fences outside model/message headings, untyped fences, and empty typed schemas must diagnose
        malformed_spec = """---
preset: fullstack
title: Malformed Blocks
status: Draft
kind: app
---

# Data Models
```json
{ "id": "1" }
```

## ModelTwo
Model two text.

```
```

## EmptyModel
```json
```

# WebSocket Messages
###\x20
#### Description
Missing topic message.

```
```
"""
        spec_file = self.tmp_path / "malformed.spec.md"
        spec_file.write_text(malformed_spec, encoding="utf-8")

        spec, parse_errors = parse_spec(spec_file)
        preset_dir = BLUEPRINT_DIR / "presets"
        errors, _, _ = validate_spec(spec, preset_dir, parse_errors)

        self.assertTrue(any("data models line" in e and "outside any '## ModelName'" in e for e in errors))
        self.assertTrue(any("data model 1 ('ModelTwo'): missing language in code fence" in e for e in errors))
        self.assertTrue(any("data model 2 ('EmptyModel'): missing code fence schema" in e for e in errors))
        self.assertTrue(any("websocket message 1: missing topic" in e for e in errors))
        self.assertTrue(any("websocket message 1: missing language in code fence" in e for e in errors))

    def test_schema_md_mechanical_drift_guard(self) -> None:
        # Q175A-4: Bidirectional mechanical drift check comparing parser SECTION_ALIASES against schema.md
        schema_file = BLUEPRINT_DIR / "schema.md"
        self.assertTrue(schema_file.exists(), "schema.md must exist")
        schema_text = schema_file.read_text(encoding="utf-8")

        parsed_schema_aliases = parse_schema_aliases_from_md(schema_text)
        self.assertEqual(
            SECTION_ALIASES,
            parsed_schema_aliases,
            "SECTION_ALIASES in parse_spec.py does not match aliases parsed directly from schema.md",
        )

        parsed_contracts = parse_schema_contracts_from_md(schema_text)
        expected_contracts = {section: sorted(keys) for section, keys in RECORD_CONTRACTS.items()}
        self.assertEqual(
            parsed_contracts,
            expected_contracts,
            "Record contracts parsed directly from schema.md do not match expected section contracts",
        )

        # Structure checks
        self.assertIn("ticket:", schema_text)
        self.assertIn("## POST /v1/auth/login", schema_text)
        self.assertIn("## WS Endpoint URL:", schema_text)
        self.assertIn("## PlayerState", schema_text)
        self.assertIn("sequenceDiagram", schema_text)

    def test_schema_drift_guard_fails_on_mutation(self) -> None:
        # Q175A-4: Mutate production SECTION_ALIASES in parse_spec and invoke real drift guard
        removed_alias = "description"
        orig_val = SECTION_ALIASES.pop(removed_alias, None)
        try:
            with self.assertRaises(AssertionError):
                self.test_schema_md_mechanical_drift_guard()
        finally:
            if orig_val is not None:
                SECTION_ALIASES[removed_alias] = orig_val

        orig_canon = SECTION_ALIASES.get("websocket spec")
        SECTION_ALIASES["websocket spec"] = "invalid_canonical"
        try:
            with self.assertRaises(AssertionError):
                self.test_schema_md_mechanical_drift_guard()
        finally:
            if orig_canon is not None:
                SECTION_ALIASES["websocket spec"] = orig_canon

    def _assert_spec_frontmatter_contract(self, schema_text: str, template_text: str) -> None:
        # Q176-R4: schema.md is the authoring contract; blueprint_spec_template is the generator.
        # A spec that follows the documented schema and a spec `ai blueprint init` emits must carry
        # the same identity, or hand-authored specs silently stay outside the registry.
        documented = parse_frontmatter_fields(extract_first_yaml_block(schema_text))
        self.assertTrue(documented, "schema.md documents no spec frontmatter block")
        emitted = parse_frontmatter_fields(template_text)
        self.assertTrue(emitted, "blueprint_spec_template emitted no frontmatter")

        self.assertEqual(
            sorted(set(documented) - SPEC_FRONTMATTER_OPTIONAL_KEYS),
            sorted(set(emitted)),
            "schema.md frontmatter keys and blueprint_spec_template output have drifted",
        )
        for key in SPEC_REGISTRY_IDENTITY_KEYS:
            self.assertIn(key, documented, f"schema.md omits registry identity field '{key}'")
            self.assertIn(key, emitted, f"blueprint_spec_template omits registry identity field '{key}'")
        self.assertEqual("spec", documented.get("type"), "schema.md must document type: spec")
        self.assertEqual("spec", emitted.get("type"), "blueprint_spec_template must emit type: spec")
        self.assertEqual(
            documented.get("status", "").lower(),
            documented.get("status"),
            "schema.md must document a lowercase doc-schema status value",
        )

    def test_spec_frontmatter_schema_matches_generator(self) -> None:
        schema_text = (BLUEPRINT_DIR / "schema.md").read_text(encoding="utf-8")
        template_text = blueprint_spec_template("Auth Flow", "backend", "app")
        self._assert_spec_frontmatter_contract(schema_text, template_text)

        documented = parse_frontmatter_fields(extract_first_yaml_block(schema_text))
        for spec_file in sorted(EXAMPLE_SPEC_DIR.glob("*.spec.md")):
            fields = parse_frontmatter_fields(spec_file.read_text(encoding="utf-8"))
            for key in SPEC_REGISTRY_IDENTITY_KEYS:
                self.assertIn(key, fields, f"{spec_file.name} omits registry identity field '{key}'")
            self.assertEqual("spec", fields.get("type"), f"{spec_file.name} must declare type: spec")
            self.assertEqual(
                sorted(set(fields) - SPEC_FRONTMATTER_OPTIONAL_KEYS),
                sorted(set(documented) - SPEC_FRONTMATTER_OPTIONAL_KEYS),
                f"{spec_file.name} frontmatter keys have drifted from schema.md",
            )

    def test_spec_frontmatter_guard_fails_on_mutation(self) -> None:
        schema_text = (BLUEPRINT_DIR / "schema.md").read_text(encoding="utf-8")
        template_text = blueprint_spec_template("Auth Flow", "backend", "app")

        # The exact round-two/round-three regression: a generator that emits only legacy metadata.
        legacy_template = "---\npreset: backend\ntitle: Auth Flow\nstatus: Draft\nkind: app\n---\n"
        with self.assertRaises(AssertionError):
            self._assert_spec_frontmatter_contract(schema_text, legacy_template)

        # A documented schema that drops an identity field the generator still emits.
        stripped_schema = "\n".join(
            line for line in schema_text.splitlines() if line.strip() != "type: spec"
        )
        with self.assertRaises(AssertionError):
            self._assert_spec_frontmatter_contract(stripped_schema, template_text)

        # A generator that adds a key the schema never documents.
        extra_key_template = template_text.replace("type: spec", "type: spec\nundocumented: 1", 1)
        with self.assertRaises(AssertionError):
            self._assert_spec_frontmatter_contract(schema_text, extra_key_template)



    def test_static_report_complete_golden_and_determinism(self) -> None:
        spec_file = FIXTURE_DIR / "static-report.spec.md"
        golden = (FIXTURE_DIR / "static-report.golden.html").read_text(encoding="utf-8")
        first = self.tmp_path / "first.html"
        second = self.tmp_path / "second.html"

        output = io.StringIO()
        with redirect_stdout(output):
            _, sections, first_warnings = build_report(spec_file, first)
            _, _, second_warnings = build_report(spec_file, second)

        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(first.read_text(encoding="utf-8"), golden)
        self.assertEqual(first_warnings, second_warnings)
        # Exactly one warning: the missing evidence file. An absent `mmdc` is NOT a warning -- the
        # vendored runtime renders the diagram in the browser, so there is nothing to install and
        # nothing wrong. A CLI that is present and unusable still warns; that is covered separately.
        self.assertEqual(len(first_warnings), 1)
        evidence_warning = next(warning for warning in first_warnings if "evidence/missing.png" in warning)
        self.assertIn(evidence_warning, output.getvalue())
        self.assertNotIn("mmdc not found on PATH", "".join(first_warnings))
        self.assertIn("component-states", sections)
        self.assertIn('src="data:image/png;base64,', golden)
        self.assertIn('alt="Evidence for active: active.png"', golden)
        self.assertIn("Missing evidence: scripts/tests/fixtures/pr-blueprint/evidence/missing.png", golden)
        self.assertNotIn('src="scripts/tests/fixtures/pr-blueprint/evidence/missing.png"', golden)
        self.assertNotRegex(golden, r"(?:^|[^A-Za-z0-9])[A-Za-z]:[\\/]")

    def test_soft_wrapped_lines_join_into_one_paragraph(self) -> None:
        """Markdown soft-wraps: consecutive prose lines are one paragraph.

        Emitting a <p> per source line shredded every hard-wrapped paragraph into fragments, which
        reads as broken content in the rendered report.
        """
        source = "Bounded backoff for the worker, plus the surface that\nmakes an exhausted job visible."
        self.assertEqual(
            "<p>Bounded backoff for the worker, plus the surface that makes an exhausted job visible.</p>",
            render_markdown(source),
        )

    def test_blank_line_starts_a_new_paragraph(self) -> None:
        self.assertEqual(
            "<p>First para line one line two</p><p>Second para</p>",
            render_markdown("First para line one\nline two\n\nSecond para"),
        )

    def test_a_trailing_paragraph_is_never_dropped(self) -> None:
        """A buffered paragraph with no trailing blank line must still be emitted.

        Without the final flush the paragraph vanished silently and the renderer returned an empty
        string for a single-line document.
        """
        self.assertEqual("<p>Only line</p>", render_markdown("Only line"))
        self.assertIn("<p>After list</p>", render_markdown("- item\n\nAfter list"))
        self.assertIn("<p>After code</p>", render_markdown("```js\nx=1\n```\nAfter code"))

    def test_block_boundaries_close_an_open_paragraph(self) -> None:
        for source, expected in (
            ("Intro line\n- item", "<p>Intro line</p><ul><li>item</li></ul>"),
            ("Intro line\n## Heading", "<p>Intro line</p><h3>Heading</h3>"),
        ):
            with self.subTest(source=source):
                self.assertEqual(expected, render_markdown(source))

    def test_safe_markdown_subset_and_malformed_table(self) -> None:
        markdown = """**bold** and `code` with [web](https://example.com), [mail](mailto:a@example.com), [repo](docs/a.md), and [fragment](#x).

| A | B |
| --- | :---: |
| <tag> | **safe** |

<img src=x onerror=alert(1)> [js](javascript:alert(1)) [data](data:text/html,x) [file](file:///tmp/x)
"""
        rendered = render_markdown(markdown)
        self.assertIn("<strong>bold</strong>", rendered)
        self.assertIn("<code>code</code>", rendered)
        self.assertIn('<a href="https://example.com">web</a>', rendered)
        self.assertIn('<a href="mailto:a@example.com">mail</a>', rendered)
        self.assertIn('<a href="docs/a.md">repo</a>', rendered)
        self.assertIn('<a href="#x">fragment</a>', rendered)
        self.assertIn("<table>", rendered)
        self.assertIn("&lt;tag&gt;", rendered)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", rendered)
        self.assertNotIn('<a href="javascript:', rendered)
        self.assertNotIn('<a href="data:', rendered)
        self.assertNotIn('<a href="file:', rendered)

        malformed = render_markdown("| A | B |\n| --- |\n| one | two |")
        self.assertNotIn("<table>", malformed)
        self.assertIn("| A | B |", malformed)

    def test_layout_tokens_fail_closed_and_inserted_content_is_not_rescanned(self) -> None:
        template = (BLUEPRINT_DIR / "templates" / "layout.html").read_text(encoding="utf-8")
        values = {
            "GENERATED_COMMENT": "generated",
            "TITLE": "{{BODY}}",
            "SOURCE": "spec.md",
            "CSS": "body{}",
            "NAV": "nav",
            "META_STRIP": "<div><dt>Preset</dt><dd>backend</dd></div>",
            "GLANCE": "<dt>Author</dt><dd>Codex</dd>",
            "BODY": "rendered-body",
            "MERMAID_RUNTIME": "",
        }
        rendered = render_layout(template, values)
        # Expansion is single pass: a token that APPEARS INSIDE a substituted value is left alone.
        # {{TITLE}} occurs three times in the layout (document title, breadcrumb, heading), so its
        # literal "{{BODY}}" value survives three times while the real {{BODY}} is substituted once.
        self.assertEqual(rendered.count("{{BODY}}"), 3)
        self.assertIn("rendered-body", rendered)
        self.assertIn("<dd>backend</dd>", rendered)
        self.assertIn("<dd>Codex</dd>", rendered)

        with self.assertRaisesRegex(ValueError, "missing tokens: BODY"):
            render_layout(template.replace("{{BODY}}", ""), values)
        with self.assertRaisesRegex(ValueError, "unknown tokens: SURPRISE"):
            render_layout(template.replace("{{BODY}}", "{{SURPRISE}}"), values)
        with self.assertRaisesRegex(ValueError, "unknown tokens: surprise"):
            render_layout(template.replace("{{BODY}}", "{{surprise}}"), values)

    def test_evidence_path_validation_rejects_hostile_inputs_with_row_context(self) -> None:
        hostile = [
            "",
            "/absolute.png",
            "C:/drive.png",
            "//server/share.png",
            r"folder\\backslash.png",
            "../escape.png",
            "folder/../escape.png",
            "https://example.com/remote.png",
            "file:///tmp/image.png",
            "folder/image.jpg",
        ]
        rows = [{"state": "active", "evidence": hostile}]
        errors: list[str] = []
        warnings: list[str] = []
        validator.validate_evidence(rows, errors, warnings)
        self.assertEqual(len(errors), len(hostile))
        self.assertEqual(warnings, [])
        for path in hostile:
            self.assertTrue(
                any("Component States row 1 evidence" in error and f"'{path}'" in error for error in errors),
                path,
            )
        empty_errors: list[str] = []
        validator.validate_evidence([{"state": "active", "evidence": []}], empty_errors, [])
        self.assertEqual(empty_errors, ["Component States row 1 evidence '': path must not be empty"])


    def test_evidence_symlink_escape_is_rejected_when_supported(self) -> None:
        root = self.tmp_path / "repo"
        outside = self.tmp_path / "outside"
        root.mkdir()
        outside.mkdir()
        (outside / "escape.png").write_bytes(b"png")
        link = root / "link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")

        errors: list[str] = []
        with mock.patch.object(validator, "ROOT", root):
            validator.validate_evidence([{"state": "active", "evidence": ["link/escape.png"]}], errors, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("resolved path escapes the repository root", errors[0])

    def test_evidence_embed_changes_with_exact_bytes(self) -> None:
        root = self.tmp_path / "repo"
        root.mkdir()
        image = root / "shot.png"
        image.write_bytes(b"first")
        with mock.patch.object(renderer, "ROOT", root):
            first = renderer.render_evidence("active", ["shot.png"], 1, [])
            image.write_bytes(b"second")
            second = renderer.render_evidence("active", ["shot.png"], 1, [])
        self.assertNotEqual(first, second)
        self.assertIn("Zmlyc3Q=", first)
        self.assertIn("c2Vjb25k", second)

    def test_responsive_layout_contains_main_grid_column(self) -> None:
        css = (BLUEPRINT_DIR / "templates" / "assets" / "blueprint.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"main\s*\{[^}]*min-width:\s*0;[^}]*\}")

class MermaidRenderingTests(unittest.TestCase):

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.tmp_path = Path(self.temp_dir.name)
        self.diagram = {"title": "Flow", "description": "", "diagram": "flowchart LR\nA-->B"}

    def test_vendor_provenance_pins_official_exact_artifacts_and_hash(self) -> None:
        provenance = json.loads(mermaid_render.PROVENANCE_PATH.read_text(encoding="utf-8"))
        runtime = mermaid_render.RUNTIME_PATH.read_bytes()
        self.assertEqual(provenance["package"], "mermaid")
        self.assertEqual(provenance["version"], "11.16.0")
        self.assertEqual(provenance["license"], "MIT")
        self.assertEqual(provenance["registry_tarball"], "https://registry.npmjs.org/mermaid/-/mermaid-11.16.0.tgz")
        self.assertEqual(
            provenance["registry_integrity"],
            "sha512-Zvm3kbstgdpvIJPPItlL7fppIZ3kibvc1oZIGxdvk9t6UFz6flv+Jw7FtRGKwfcI8OckmH04LqG6LlS6X4B1pA==",
        )
        self.assertEqual(provenance["asset_sha256"], hashlib.sha256(runtime).hexdigest())
        self.assertEqual(provenance["asset_bytes"], len(runtime))
        self.assertEqual(provenance["compatible_cli"]["package"], "@mermaid-js/mermaid-cli")
        self.assertEqual(provenance["compatible_cli"]["version"], "11.16.0")
        self.assertEqual(
            provenance["compatible_cli"]["registry_integrity"],
            "sha512-0InK2nbVIMtzVzCugmdvPkAuvS6wRUqU6Utntff1n8c7lgfRZAdhKY6PSKvcIK9nFmuOUzAgB5+x/XWcroZ7Zg==",
        )
        self.assertEqual(
            provenance["compatible_cli"]["registry_tarball"],
            "https://registry.npmjs.org/@mermaid-js/mermaid-cli/-/mermaid-cli-11.16.0.tgz",
        )

    def test_absent_mmdc_uses_one_verified_fallback_and_names_reason(self) -> None:
        with mock.patch.object(mermaid_render.shutil, "which", return_value=None), mock.patch.object(
            mermaid_render.subprocess, "run"
        ) as run:
            results, runtime, warnings = mermaid_render.prepare_diagrams([self.diagram, self.diagram])
        run.assert_not_called()
        self.assertEqual([result.state for result in results], ["browser-fallback", "browser-fallback"])
        self.assertTrue(all("mmdc not found on PATH" in warning for warning in warnings))
        self.assertEqual(runtime.count("data:text/javascript;base64,"), 1)
        self.assertEqual(runtime.count('data-mermaid-runtime="11.16.0"'), 1)
        self.assertIn('securityLevel:"strict"', runtime)
        self.assertIn("startOnLoad:true", runtime)
        self.assertIn("deterministicIds:true", runtime)
        self.assertNotIn("<iframe", runtime.lower())
        self.assertNotRegex(runtime, r"https?://")

    def test_version_failure_timeout_and_mismatch_never_render(self) -> None:
        rows = [
            (types.SimpleNamespace(returncode=1, stdout="", stderr="bad"), "version check failed"),
            (types.SimpleNamespace(returncode=0, stdout="11.15.0\n", stderr=""), "expected 11.16.0, got 11.15.0"),
            (subprocess.TimeoutExpired(["mmdc", "--version"], 5), "version check timed out"),
        ]
        for outcome, expected in rows:
            with self.subTest(expected=expected), mock.patch.object(
                mermaid_render.shutil, "which", return_value="C:/tools/mmdc.cmd"
            ), mock.patch.object(mermaid_render.subprocess, "run", side_effect=[outcome] if isinstance(outcome, Exception) else None) as run:
                if not isinstance(outcome, Exception):
                    run.return_value = outcome
                results, runtime, warnings = mermaid_render.prepare_diagrams([self.diagram])
            self.assertEqual(run.call_count, 1)
            self.assertEqual(results[0].state, "browser-fallback")
            self.assertIn(expected, warnings[0])
            self.assertIn("data:text/javascript;base64,", runtime)

    def test_exact_mmdc_uses_contained_argv_config_and_is_deterministic(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []
        configs: list[dict[str, object]] = []

        def fake_run(argv: list[str], **kwargs: object) -> types.SimpleNamespace:
            calls.append((argv, kwargs))
            if argv[1:] == ["--version"]:
                return types.SimpleNamespace(returncode=0, stdout="11.16.0\n", stderr="")
            config_path = Path(argv[argv.index("-c") + 1])
            output_path = Path(argv[argv.index("-o") + 1])
            configs.append(json.loads(config_path.read_text(encoding="utf-8")))
            output_path.write_text(REALISTIC_MERMAID_SVG, encoding="utf-8")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        with mock.patch.object(mermaid_render.shutil, "which", return_value="C:/tools/mmdc.cmd"), mock.patch.object(
            mermaid_render.subprocess, "run", side_effect=fake_run
        ):
            first = mermaid_render.prepare_diagrams([self.diagram])
            second = mermaid_render.prepare_diagrams([self.diagram])

        self.assertEqual(first, second)
        self.assertEqual(first[0][0].state, "static-svg")
        self.assertEqual(first[1], "")
        expected_seed = hashlib.sha256(b"Flow\0flowchart LR\nA-->B").hexdigest()
        expected_config = {
            "deterministicIDSeed": expected_seed,
            "deterministicIds": True,
            "flowchart": {"htmlLabels": False},
            "htmlLabels": False,
            "securityLevel": "strict",
            "theme": "neutral",
        }
        self.assertEqual(configs, [expected_config, expected_config])
        for argv, kwargs in calls:
            self.assertIsInstance(argv, list)
            self.assertFalse(kwargs["shell"])
            self.assertTrue(str(kwargs["cwd"]).startswith(tempfile.gettempdir()))
            if argv[1:] != ["--version"]:
                self.assertEqual([Path(value).name if index in {2, 4, 6} else value for index, value in enumerate(argv)], [
                    "C:/tools/mmdc.cmd", "-i", "input.mmd", "-o", "output.svg", "-c", "config.json"
                ])
                self.assertTrue(all(str(Path(value).parent) == str(kwargs["cwd"]) for value in (argv[2], argv[4], argv[6])))

    def test_mixed_static_and_unsafe_output_uses_runtime_once(self) -> None:
        render_count = 0

        def fake_run(argv: list[str], **kwargs: object) -> types.SimpleNamespace:
            nonlocal render_count
            if argv[1:] == ["--version"]:
                return types.SimpleNamespace(returncode=0, stdout="11.16.0", stderr="")
            render_count += 1
            output = Path(argv[argv.index("-o") + 1])
            svg = '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>' if render_count == 1 else '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
            output.write_text(svg, encoding="utf-8")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        second = {**self.diagram, "title": "Unsafe"}
        with mock.patch.object(mermaid_render.shutil, "which", return_value="mmdc"), mock.patch.object(
            mermaid_render.subprocess, "run", side_effect=fake_run
        ):
            results, runtime, warnings = mermaid_render.prepare_diagrams([self.diagram, second])
        self.assertEqual([result.state for result in results], ["static-svg", "browser-fallback"])
        self.assertEqual(runtime.count("data:text/javascript;base64,"), 1)
        self.assertEqual(len(warnings), 1)
        self.assertIn("script element is forbidden", warnings[0])

    def test_adversarial_svg_is_rejected_before_inlining(self) -> None:
        unsafe = {
            "doctype": '<!DOCTYPE svg><svg xmlns="http://www.w3.org/2000/svg"/>',
            "entity": '<!ENTITY x "y"><svg xmlns="http://www.w3.org/2000/svg"/>',
            "root": '<html/>',
            "script": '<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>',
            "foreignObject": '<svg xmlns="http://www.w3.org/2000/svg"><foreignObject/></svg>',
            "event": '<svg xmlns="http://www.w3.org/2000/svg" onload="x"/>',
            "external href": '<svg xmlns="http://www.w3.org/2000/svg"><a href="https://example.com"/></svg>',
            "data href": '<svg xmlns="http://www.w3.org/2000/svg"><a href="data:text/html,x"/></svg>',
            "file href": '<svg xmlns="http://www.w3.org/2000/svg"><a href="file:///tmp/x"/></svg>',
            "css url": '<svg xmlns="http://www.w3.org/2000/svg"><style>.x{fill:url(https://example.com/x)}</style></svg>',
            "xml stylesheet": '<?xml-stylesheet href="https://evil.example/x.css"?><svg xmlns="http://www.w3.org/2000/svg"><path/></svg>',
            "xml base": '<svg xmlns="http://www.w3.org/2000/svg" xml:base="https://evil.example/"><use href="#p"/></svg>',
            "external src": '<svg xmlns="http://www.w3.org/2000/svg"><image src="https://evil.example/x.png"/></svg>',
            "style tail css url": '<svg xmlns="http://www.w3.org/2000/svg"><style><a/>body{background-image:url(http://127.0.0.1:8931/tail.png)}</style></svg>',
            "mixed content tail css import": '<svg xmlns="http://www.w3.org/2000/svg"><g><path/>@import "https://evil.example/tail.css"</g></svg>',
        }
        for label, svg in unsafe.items():
            with self.subTest(label=label):
                clean, reason = mermaid_render.sanitize_svg(svg.encode("utf-8"))
                self.assertIsNone(clean)
                self.assertTrue(reason)
        safe, reason = mermaid_render.sanitize_svg(
            b'<?xml version="1.0" encoding="UTF-8"?><svg xmlns="http://www.w3.org/2000/svg"><defs><path id="p"/></defs><use href="#p" style="fill:url(#p)"/><image src="#p"/></svg>'
        )
        self.assertEqual(reason, "")
        self.assertIn("<svg", safe)
        self.assertNotIn("<?xml", safe)

    def test_corrupt_runtime_degrades_to_visible_source_only(self) -> None:
        corrupt = self.tmp_path / "mermaid.min.js"
        corrupt.write_bytes(b"corrupt")
        with mock.patch.object(mermaid_render.shutil, "which", return_value=None), mock.patch.object(
            mermaid_render, "RUNTIME_PATH", corrupt
        ):
            results, runtime, warnings = mermaid_render.prepare_diagrams([self.diagram])
        self.assertEqual(results[0].state, "source-only")
        self.assertEqual(runtime, "")
        self.assertIn("SHA-256 mismatch", warnings[0])
        rendered = renderer.render_diagrams(results)
        self.assertIn('data-diagram-state="source-only"', rendered)
        self.assertIn("flowchart LR", rendered)
        self.assertNotIn('<pre class="mermaid">', rendered)

    def test_no_diagram_report_matches_golden_without_runtime(self) -> None:
        source = FIXTURE_DIR / "no-diagram.spec.md"
        golden = (FIXTURE_DIR / "no-diagram.golden.html").read_text(encoding="utf-8")
        output = self.tmp_path / "no-diagram.html"
        with mock.patch.object(mermaid_render.shutil, "which") as which:
            _, sections, warnings = build_report(source, output)
        which.assert_not_called()
        self.assertEqual(output.read_text(encoding="utf-8"), golden)
        # No "metadata" section: every metadata fact now renders once in the head strip and the
        # rail, and the section that used to repeat them carries warnings only -- so with a clean
        # build it is dropped entirely rather than rendering an empty card.
        self.assertEqual(sections, ["overview", "qa"])
        self.assertEqual(warnings, [])
        self.assertNotIn("data-mermaid-runtime", golden)
        self.assertNotIn("data-diagram-state", golden)

    def test_all_static_report_matches_golden_without_runtime(self) -> None:
        source = FIXTURE_DIR / "static-report.spec.md"
        golden = (FIXTURE_DIR / "static-report.static-svg.golden.html").read_text(encoding="utf-8")
        output = self.tmp_path / "static-svg.html"

        def fake_run(argv: list[str], **kwargs: object) -> types.SimpleNamespace:
            if argv[1:] == ["--version"]:
                return types.SimpleNamespace(returncode=0, stdout="11.16.0\n", stderr="")
            Path(argv[argv.index("-o") + 1]).write_text(REALISTIC_MERMAID_SVG, encoding="utf-8")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        with mock.patch.object(mermaid_render.shutil, "which", return_value="mmdc"), mock.patch.object(
            mermaid_render.subprocess, "run", side_effect=fake_run
        ):
            _, _, warnings = build_report(source, output)
        self.assertEqual(output.read_text(encoding="utf-8"), golden)
        self.assertEqual(len(warnings), 1)
        self.assertIn("evidence/missing.png", warnings[0])
        self.assertIn('data-diagram-state="static-svg"', golden)
        self.assertIn("flowchart-static-pointEnd", golden)
        self.assertIn("Client", golden)
        self.assertIn("Report", golden)
        self.assertNotIn("foreignObject", golden)
        self.assertNotIn("data-mermaid-runtime", golden)
        self.assertNotIn('<pre class="mermaid">', golden)
    def test_no_diagram_requires_no_runtime(self) -> None:
        results, runtime, warnings = mermaid_render.prepare_diagrams([])
        self.assertEqual((results, runtime, warnings), ([], "", []))

if __name__ == "__main__":
    unittest.main()
