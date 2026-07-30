<!-- GENERATED FROM .ai/. DO NOT EDIT DIRECTLY. Run `ai sync`. -->

# Workflow: Planning

Planning is normative: it decides what should be done.

## Steps

1. Read user intent and `.ai/project/`.
2. Load relevant `.ai/memory/` entries.
3. Load valid research artifacts, if present.
4. If the input is only a brief requirement, brainstorm likely workstreams and unknowns before asking the user to supply a workflow.
5. Identify risk tier.
6. Determine whether the root cause and triggering environment are known. If either is unknown,
   place a bounded diagnostic/reproduction task before remediation and apply
   `.ai/rules/diagnostic-isolation.md`.
7. Define task graph and dependencies.
8. Author the complete human presentation contract: purpose, required outcome, feature/workflow
   scope, optional out-of-scope boundaries, and observable acceptance. Write for developers,
   product owners, and QA; keep the globally rendered title and feature label locator-free too.
   Keep paths, symbols, commands, revisions, and evidence locations in the
   execution contract and Source; never use them as reader prose.
9. Assign target and forbidden files. Name a bounded writable area (glob pattern) plus explicit
   forbidden_files whenever growth is plausible; enumerate exact paths only for a bounded
   single-file surgical edit. See `.ai/rules/task-contracts.md`.
10. Optionally, when the task names changed code symbols, query `ai impact` for those exact symbols.
   Include only a complete answer whose symbols verify against the index, preserving its callers,
   caller files, covering tests (or exact no-covering-tests warning), and staleness banner.
   This optional blast-radius step is ADVISORY ONLY and must NEVER auto-expand `target_files`.
   Missing, ambiguous, incomplete, stale, or unavailable advice is omitted without blocking
   planning; the planner remains responsible for scope.
11. Choose preferred tool, review tool, and isolation strategy by task shape, context window, reasoning depth, workspace stability, edit scope, and required review independence.
12. Write task folders under `.ai/tasks/queue/` or `.ai/tasks/active/`.
13. Generate tool prompts with `ai dispatch` when ready.

When a planned task creates, moves, or changes a product document under `project/docs/`, include the
matching `.ai/templates/project-doc/*.md.tmpl` file and `.ai/project/doc-schema.md` in its inputs.
The contract must require complete authored schema-v2 metadata and must not authorize inference of
authority, visibility, maturity, navigation, relations, or subjects from filenames or body prose.

## Contract Shape: Invariants and Evidence, Not Procedure

`output_contract` states invariants (what must remain true), required evidence, and gates to pass —
never a step-by-step implementation script. A contract that reads as a how-to procedure is a
planning defect: it substitutes the planner's guess at implementation steps for the executor's
engineering judgment, and it goes stale the moment execution reveals a better structure. State the
constraint; let the executor choose how to satisfy it.

`presentation_*` is a second, reader-facing contract, not a friendlier rendering of
`output_contract`. It states stable stakeholder meaning and observable behavior. The planner must
author it directly; projection code must not derive it from implementation paths, receipts, or raw
execution prose.

## Collision Avoidance

Parallel tasks must not write the same file. Shared wiring belongs in a later integration task.

## Verification Scope

Set `verification_scope` on every task: `control-plane` for docs/framework-only work,
`affected-plus-neighbors` as the ordinary product gate, and `workspace` only for
milestone/integration tasks with a recorded justification. See `.ai/rules/rust-verification.md`.

## Brief-Only Trigger

When the user provides only the requirement or desired outcome, planning must still proceed. The planner should treat the brief as the intake artifact, create research tasks when facts are missing, and split the implementation into explicit task contracts without asking the user to pre-assign agents.

For unknown-cause failures, the split is mandatory: diagnostic isolation first, remediation only
after a failing/control boundary and falsifiable hypothesis exist. Do not label an optimization as
a fix merely because a metric improved while the original failure still reproduces.

## Tool Routing

- Antigravity can implement broad scaffolds, project setup, large-context refactors, and stable-workspace changes.
- Codex fits bounded patches, intermediate implementation, command/test loops, and tight diffs.
- Claude Code can implement complex math, algorithms, and logic-heavy bounded tasks.
- Review should be independent for high-risk work; do not require one permanent reviewer tool if another tool/model gives better independence.

## Routing: Impact Risk Versus Task Complexity

Two different questions, two different selectors (`.ai/project/routing-taxonomy.md`):

- `risk` selects impact evidence, approval, and review gates. It never selects a model or reasoning
  tier on its own, and it does not automatically choose a maximum-reasoning model.
- The task zone and the derived complexity band select the executor model and reasoning profile.
- A high-impact task may impose a review-depth floor (`routing_taxonomy.review_depth_floor`) on the
  required independent review, but high impact never upgrades the executor's complexity band: it
  does not make simple work cognitively complex.
- Nothing outside task shape and complexity (release timing, feature prominence, and similar)
  selects a reasoning tier.
- This supersedes the earlier "risk is the only reasoning-tier selector" rule; every scope, review
  independence, and owner-approval gate is unchanged.

Every newly planned task records the complete explicit `routing_zone`, `routing_axes`, and assignment rationale.
Choose those values from the contracted task shape, never from keyword inference. If task creation
has not collected the whole vector plus explicit executor and reviewer choices, leave both
assignments as `pending` and record the exact `route explain` then `route apply` guidance. Legacy
contracts with no routing keys remain readable and are never backfilled by guessing.

## Profile Proportionality

- Pick the least costly profile whose declared capabilities and supported reasoning levels satisfy
  the task's zone and complexity band. A `routine` band never justifies an escalated profile, and no
  risk tier raises or lowers the executor profile.
- An eligible cross-family logical profile at the configured review-depth floor is sufficient for ordinary bounded high-risk feature review.
- Prefer cross-family independence. If it is unavailable, an owner-authorized substitute must be fresh and disclosed; do not call same-family review independent or use it to satisfy the independent merge gate without an explicit owner waiver.
- Use reasoning above the configured escalation threshold only when the task records a concrete reason such as native unsafe/ABI work, cryptography, irreversible destructive mutation, unusually hard math/algorithm work, or an unresolved threshold-level review.
- Do not stop dispatch merely because one premium profile is unavailable when an adequate independent profile can perform the contracted review.
