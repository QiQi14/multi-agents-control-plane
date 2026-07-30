<!-- GENERATED FROM .ai/. DO NOT EDIT DIRECTLY. Run `ai sync`. -->

# PR Blueprint

This skill points to the spec-first PR Blueprint generator.

## Canonical Source

The canonical implementation lives in `.ai/templates/pr-blueprint/`.

## Compatibility Output

`ai sync` copies the template system to `.agents/skills/pr-blueprint/template/` for Antigravity compatibility.

## Mermaid Behavior

Builds are offline and succeed without Mermaid tooling. Exact `mmdc` 11.16.0 may produce sanitized,
deterministic static SVG; otherwise the report names the reason and uses the exact hash-verified
vendored `mermaid` 11.16.0 runtime once. Missing/corrupt runtime bytes degrade to escaped source-only.
Never install Mermaid, run `npx`, or add a build-time network/CDN path.

## Usage

```bash
ai blueprint init --from-task task_07_payment_retry_backoff --base HEAD
ai blueprint build docs/blueprints/deterministic_spec_extractors_ai_blueprint_init_from_task.spec.md
```

Generated blueprint reports are supporting artifacts. They do not replace task contracts, receipts, or QA gates.
