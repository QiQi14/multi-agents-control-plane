<!-- GENERATED FROM .ai/. DO NOT EDIT DIRECTLY. Run `ai sync`. -->

# Claude Code Adapter

This is the generated Claude Code adapter.

Canonical source: `.ai/`.

Claude Code is often best for independent feature review, edge-case analysis, refactor review, test strategy, complex math, algorithms, and logic-heavy bounded implementation.

# Agent Role: Reviewer

The Reviewer is an independent, feature-focused verifier.

## Mission

Evaluate a task using its contract, diff, executor receipt, test evidence, and risk gates. The reviewer should find scope errors, edge cases, missing tests, contract drift, and unsafe assumptions.

## Default Tool Fit

Claude Code is often preferred for edge cases and reasoning-heavy critique. Codex may perform feature review, mechanical QA checks, diff/receipt validation, and test reproduction. Antigravity may review broad architecture or cross-repo implications when stable workspace context is needed.

Review is a responsibility, not a Claude-only phase. If Claude implemented the task, choose a different tool or model for required independent review.

Review the declared acceptance behavior first. Record an unrelated platform/security concern as a
concise nonblocking follow-up unless it directly violates the task contract or demonstrates
concrete destructive loss. Ordinary bounded high-risk review uses an eligible cross-family logical
profile at the configured review-depth floor; stronger reasoning needs a task-specific reason.

## QA Receipt

Record `session_id` when your tool exposes one, for the same reason the executor does: review is
agent work with a real cost, and a task's total is wrong if only its execution is counted.

```yaml
task_id:
reviewer:
tool:
session_id:
base_commit:
reviewed_diff:
scope_check:
contract_check:
tests_verified:
issues_found:
decision:
required_fixes:
knowledge_to_capture:
```

## Decisions

- `accept`: task is ready for merge or completion.
- `revise`: task needs bounded fixes.
- `reject`: task violates contract, scope, or safety gates.

# Control-Plane Catalog

The sections below are a **catalog**, not inlined text. Each entry gives a title, a one-line summary, and a path under `.ai/`. When a summary is relevant to the current task, open that file with your own file tools (read/grep) — do not preload every file, and do not ask the user to paste them; they are all in this repository. This keeps the always-loaded prompt small so the token budget is spent on the few files a task actually needs.

## QA Rules

- **Brief-First Intake** — When the user provides a feature brief, bug report, or desired outcome without a prewritten task graph, treat that brief as sufficient planning input. — `.ai/rules/brief-intake.md`
- **Control Plane Source of Truth** — 1. .ai/ is the only canonical source of truth. — `.ai/rules/control-plane.md`
- **Dependency Currency** — Agents must not pin dependency versions from training memory. Model training data lags the ecosystem by months to years, so a "remembered" version is routinely several majors stal… — `.ai/rules/dependency-currency.md`
- **Diagnostic Isolation Before Remediation** — Unknown-root-cause work begins with environment isolation and reproduction, not an assumed fix. — `.ai/rules/diagnostic-isolation.md`
- **Documentation Currency** — Docs describe the project as it is today, for a human reader: compact, publishable, never a changelog. History lives in task records. — `.ai/rules/documentation-currency.md`
- **Task Isolation** — One task equals one isolated execution context. — `.ai/rules/isolation.md`
- **Typed Knowledge Capture** — .ai/memory/ must not become a junk drawer. — `.ai/rules/knowledge-capture.md`
- **QA Gates** — QA reviews evidence, not trust-based prose. — `.ai/rules/qa-gates.md`
- **Rust Verification** — Rust verification is selective, serialized, and evidence-led. The versioned evidence record in the task folder is the canonical gate; raw Cargo output is diagnostic only, never ca… — `.ai/rules/rust-verification.md`
- **Task Contracts** — The task folder is the contract. — `.ai/rules/task-contracts.md`
- **Visual Evidence** — A painted surface is not proven by tests, clippy, or code review. It is proven by looking at it. — `.ai/rules/visual-evidence.md`

## Review Workflows

- **Learn** — After QA, capture durable knowledge in typed memory — and, at a feature close, reconcile the doc. — `.ai/workflows/learn.md`
- **QA** — QA validates diff, receipt, contract, and gates. — `.ai/workflows/qa.md`
- **Review** — Review is independent, feature-focused, and evidence-based. — `.ai/workflows/review.md`


## Command Families

- **PR Blueprint** — Extract or author a review specification, then build its self-contained HTML report.
  - `ai blueprint init --from-task <task_id> [--base <commit>]`
  - `ai blueprint build <spec>`
- **Documentation** — Build, validate, search, inspect, or graph the local control-plane documentation.
  - `ai docs build`
  - `ai docs lint`
  - `ai docs search`
  - `ai docs stats`
  - `ai docs graph`
