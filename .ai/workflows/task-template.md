---
id: workflow-task-template
type: workflow
domain: control-plane
status: active
owner: system
updated: 2026-07-30
---

# Task Folder Template

```text
.ai/tasks/active/task_03_login_ui/
  task.yaml
  brief.md
  context.md
  prompt.codex.md
  prompt.claude.md
  prompt.antigravity.md
  receipt.executor.yaml
  receipt.qa.yaml
  diff.patch
  test-output.txt
  notes.md
```

## task.yaml

```yaml
id:
title:
feature:
status:
risk:
preferred_tool:
review_tool:
isolation_strategy:
verification_scope:
depends_on:
presentation_schema_version: "1"
presentation_purpose:
presentation_outcome:
presentation_scope:
presentation_out_of_scope:
presentation_acceptance:
target_files:
forbidden_files:
input_contract:
output_contract:
acceptance_tests:
commands:
known_risks:
```

## Human Presentation Contract

Author the `presentation_*` fields before dispatch. They describe product or workflow impact for
developers, product owners, and QA; they do not restate the implementation map.

- `presentation_purpose` explains why the task is needed.
- `presentation_outcome` states the stakeholder-visible result.
- `presentation_scope` names affected features, user journeys, or team workflows.
- `presentation_out_of_scope` records meaningful stakeholder boundaries and may be empty.
- `presentation_acceptance` lists observable outcomes in reader language.

Because the title and feature tag also appear across Overview, search, relationships, and the task
catalog, author them as locator-free stakeholder labels too.

Keep repository paths, file lists, commands, symbols, revision IDs, and evidence locations in the
execution contract, task context, Source, and Git. Do not copy them into presentation prose. The
namespace is complete-or-absent: historical tasks without it remain readable as
`legacy-unavailable`, while a new partial or locator-bearing namespace fails validation.

Optional routing metadata (`.ai/project/routing-taxonomy.md`), all-or-nothing per group:

```yaml
routing_policy_version:
routing_zone:
routing_axes:            # one "<axis>=<level>" entry per declared axis
routing_complexity_band: # optional; must equal the derived band
routing_profile_tool:
routing_profile:
routing_reasoning_level:
routing_provenance:      # explicit_owner | planner | router
routing_rationale:
```

Keep `preferred_tool` and `review_tool` to the canonical tool values; they remain the assignment
fields. `risk` selects impact evidence, approval, and review gates, while the task zone and derived
complexity band select the executor model and reasoning profile. Low- and medium-risk tasks need no
per-task model prose. For ordinary bounded high-risk work, choose an eligible independent cross-family logical profile at the
configured review-depth floor; document why stronger reasoning is necessary instead of inferring it from
`risk: high`. A task that records no `routing_*` field is legacy and stays readable — nothing is
guessed on its behalf.

## Target Files: Areas, Not Enumeration

`target_files` names a bounded writable area (glob pattern) plus explicit `forbidden_files`
whenever the task's scope could plausibly grow or be reorganized during implementation; enumerate
exact paths only for a bounded single-file surgical edit. See `.ai/rules/task-contracts.md` for the
full law and its recorded rationale.

`output_contract` states invariants — what must remain true, which responsibilities must stay
separated, which gates must pass, and what evidence proves each — plus required evidence. It never
prescribes implementation steps.

**Anti-pattern:** an `output_contract` written as a numbered how-to ("first create file X, then
move function Y into it, then update the import in Z") is a planning defect even when every step is
correct, because it converts the executor's engineering judgment into a contract the executor
cannot legally deviate from. Write the constraint the steps were meant to satisfy instead.

`verification_scope` chooses the Cargo gate: `control-plane` for docs/framework-only work,
`affected-plus-neighbors` for ordinary product tasks (the default when omitted), and
`workspace` only for escalation or milestone tasks (`.ai/rules/rust-verification.md`).

For unknown-root-cause work, create a diagnostic/reproduction task before remediation and record
the symptom signature, environment matrix, measurement method, observation window, stopping
condition, reproduction classification, and remaining uncertainty. A later remediation contract
must depend on that evidence and state its falsifiable hypothesis. See
`.ai/rules/diagnostic-isolation.md`.
## Schema-Versioned Evidence Files

New execution uses JSON-compatible YAML 1.2 for nested schema-versioned artifacts:

```text
receipt.executor.yaml       immutable executor attempt event
receipt.executor.attempt-2.yaml
receipt.qa.round-1.yaml     preserved revise/reject round
receipt.qa.yaml             current/final immutable QA round
evidence.yaml               typed claim-bearing evidence set
task-closeout.yaml          authoritative disposition fold
```

Use `.ai/project/task-evidence-schema.md`; do not copy historical free-text receipt shapes into a
new task. `ai qa <task-id>` creates the current QA schema template. Never use `--force` to overwrite
a versioned event; preserve the earlier round first.
