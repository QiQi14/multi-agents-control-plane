---
trigger: always_on
description: "Rule: Brief-First Intake. When the user provides a feature brief, bug report, or desired outcome without a prewritten task graph, treat that brief as sufficient planning input."
---
<!-- GENERATED FROM .ai/. DO NOT EDIT DIRECTLY. Run `ai sync`. -->

# Rule: Brief-First Intake

When the user provides a feature brief, bug report, or desired outcome without a prewritten task graph, treat that brief as sufficient planning input.

## Trigger

Use this rule when the request names the goal but does not already define:

- task boundaries
- file ownership
- tool assignments
- execution order

## Required Planner Behavior

1. Convert the brief into a feature-level planning task instead of waiting for a detailed workflow from the user.
2. Brainstorm likely workstreams, dependencies, open questions, and repository touch points.
3. Split the work into auditable task folders with non-overlapping `target_files` and explicit `forbidden_files`.
4. Assign `preferred_tool`, `review_tool`, `risk`, and `isolation_strategy` per task.
5. Put shared wiring and cross-cutting edits into a later integration task instead of parallel tasks.
6. If repository understanding is insufficient, create a bounded research task first rather than guessing.
7. If the root cause or triggering environment is unknown, create a diagnostic/reproduction task
   under `.ai/rules/diagnostic-isolation.md` before any remediation task. Do not let an assumed fix
   substitute for environment isolation.
8. Record assumptions in planning artifacts so execution can proceed without re-briefing the whole workflow in chat.

## User Input Expectation

The user should only need to provide:

- the requirement or problem statement
- hard constraints
- deadlines or risk sensitivity when relevant
- optional do-not-touch files or areas

The user should not need to pre-assign agents or design the task graph unless they want to.
