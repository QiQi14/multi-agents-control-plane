# PR Blueprint

PR Blueprint is a spec-first report generator for human-readable feature, API, tool, and engine review documents.

The source of truth is a compact spec file. Generated HTML is build output.

## When To Create One

Create a blueprint when a change benefits from a structured review artifact:

- API or WebSocket contract.
- UI states or validation behavior.
- Tool workflows or command behavior.
- Game engine, canvas, or runtime state transitions.
- Complex QA checklist or risk map.
- Cross-functional feature where planner, executor, and QA need the same contract.

## When Not To Create One

Skip PR Blueprint for:

- tiny single-file tasks
- pure refactors with no user-facing behavior
- research-only work
- changes already covered by a clearer task contract

## Core Rule

Do not manually edit generated HTML.

```text
edit spec -> run build -> view HTML
```

If the report is wrong, fix the source spec or renderer.

## Create A Spec

```bash
ai blueprint init "Auth Flow" --preset backend
```

This writes:

```text
docs/blueprints/auth_flow.spec.md
```

To pre-fill a spec from an exact task contract and its available receipts:

```bash
ai blueprint init --from-task task_07_payment_retry_backoff --base HEAD
```

Task extraction is deterministic and read-only over queue/active/done task records plus
non-mutating Git name inventory. Source comments distinguish machine facts from sections that still
need judgment. Missing receipts or `--base` produce explicit notes instead of fabricated facts.

## Build A Report

```bash
ai blueprint build docs/blueprints/auth_flow.spec.md
```

This writes:

```text
docs/reports/pr-blueprint-auth-flow.html
```

Direct renderer command:

```bash
python .ai/templates/pr-blueprint/renderer/build_report.py --spec docs/blueprints/auth_flow.spec.md
```

## Mermaid Diagrams

Diagram rendering is optional and offline. A build probes only `mmdc` on `PATH`, accepts exact
`@mermaid-js/mermaid-cli` 11.16.0, and runs it with strict deterministic configuration, neutral theme, and HTML labels disabled in an
OS-temporary directory. A safe result is sanitized and embedded as labeled static SVG.

If the CLI is absent, incompatible, times out, fails, or returns unsafe SVG, the report still
builds: it preserves escaped source and loads one hash-verified copy of the vendored `mermaid`
11.16.0 browser runtime as a self-contained base64 data script.

**A missing CLI is not a warning.** Nothing needs installing: the vendored runtime renders the
diagram in the browser with no network. A CLI that IS present and unusable -- wrong version, timeout,
failure, unsafe output -- names the reason in the command output and report, because that is a real
misconfiguration.

The CLI is a size optimisation, not a requirement. Pre-rendered static SVG makes a report with
diagrams roughly 200x smaller (~25 KB against ~4.8 MB) because the browser runtime no longer has to
be inlined. Install `@mermaid-js/mermaid-cli@11.16.0` if that matters for how you distribute
reports; ignore it otherwise. If that asset or its provenance is missing or corrupt, the report degrades visibly to
source-only. Normal init/build never installs packages, runs `npx`, calls a CDN, or accesses the
network. Vendored provenance, the upstream MIT license, and exact bytes live under
`vendor/mermaid-11.16.0/`.

## Fix Validation Errors

The build command validates before writing HTML. Errors name the broken section and expected format. Fix the spec and rebuild.

Examples:

- `metadata: missing required 'title'`
- `api line 12: endpoint heading must look like '## GET /path'`
- `architecture diagram 1: invalid Mermaid start`

## How Agents Use It

Planner:

- Prefer the source spec for structured details.
- Reference generated HTML only for human-facing review.
- Use sections as task context bindings when useful.

Executor:

- Use the spec to confirm exact fields, states, endpoints, and acceptance behavior.
- Do not edit generated HTML.

QA:

- Review the generated report for readability.
- Verify implementation against the source spec.
- Use the QA checklist and risks as evidence prompts.

## Integration With `.ai/`

Canonical source:

```text
.ai/templates/pr-blueprint/
```

Antigravity adapter output:

```text
.agents/skills/pr-blueprint/template/
```

Run `ai sync` after changing the template system.
