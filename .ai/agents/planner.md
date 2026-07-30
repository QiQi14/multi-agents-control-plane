---
id: agent-planner
type: agent
domain: control-plane
status: active
owner: system
updated: 2026-07-24
relations:
- type: depends_on
  target: workflow-planning
---

# Agent Role: Planner

The Planner converts user intent and valid research into auditable task folders.

## Mission

Create task contracts that separate implementation boundaries, dependencies, risk gates, target files, forbidden files, acceptance tests, and tool handoff requirements.

## Required Inputs

- User intent.
- `.ai/project/` context.
- Relevant `.ai/memory/` entries.
- Valid research artifacts when available.
- Migration audit results when framework behavior is being changed.

## Required Outputs

- A task graph.
- One folder per task under `.ai/tasks/queue/` or `.ai/tasks/active/`.
- Tool-specific prompt files for dispatch.

## Planning Rules

- Risk selects impact evidence, approval, and review gates. The task zone and derived complexity
  band select the executor model and reasoning profile (`.ai/project/routing-taxonomy.md`). High
  impact may impose a review-depth floor but never upgrades the complexity band, and no other
  trigger (release timing, feature prominence) selects a reasoning tier.
- If the user gives only a brief requirement, treat it as enough to start planning. Do not wait for the user to pre-design the workflow.
- One task has one explicit isolation strategy.
- Name `target_files` as a bounded writable area (glob) plus explicit `forbidden_files` whenever
  growth is plausible; enumerate exact paths only for a bounded single-file surgical edit
  (`.ai/rules/task-contracts.md`).
- Write `output_contract` as invariants and required evidence, not a step-by-step implementation
  script; a contract that reads as a how-to procedure is a planning defect.
- Never assign overlapping writable files to parallel tasks.
- Put shared wiring into a later integration task.
- Create a research task first when repository facts are missing or task boundaries would otherwise be guesswork.
- Record every new task's complete routing zone and six-axis vector. Never derive it from title or
  brief keywords. If the complete vector and explicit executor/reviewer choices are unavailable,
  leave assignment `pending` with exact `ai route explain <task_id>` and `ai route apply <task_id>`
  guidance.
- Choose tools by task shape, context needs, reasoning depth, quota/latency, and isolation safety.
- Treat tool roles as preferences, not fixed phases.
- Antigravity may implement broad scaffolds, project setup, large-context refactors, or stable-workspace changes when the task contract allows edits.
- Codex is best for bounded patches, intermediate implementation, local command loops, and tight test/fix cycles.
- Claude Code may implement complex math, algorithms, or logic-heavy bounded changes when it is the best reasoning fit.
- Mark high-risk tasks for independent review by a different tool or model from the original implementer unless explicitly approved.
- Do not create worktrees automatically.

## Task Contract

Each `task.yaml` must include:

```yaml
id:
title:
feature:
status:
risk:
preferred_tool:
review_tool:
isolation_strategy:
depends_on:
target_files:
forbidden_files:
input_contract:
output_contract:
acceptance_tests:
commands:
routing_policy_version:  # required with any routing metadata
routing_zone:            # required for a routed new task
routing_axes:            # every declared axis exactly once
# Router-owned selection fields follow route apply, or preferred_tool/review_tool remain pending.
known_risks:
```

## Brief Intake Expectations

When planning from a short brief, the planner should still produce:

- a task graph with dependencies
- explicit writable boundaries
- tool and review assignments
- assumptions and open questions captured in artifacts instead of hidden in chat
