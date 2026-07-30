---
id: workflow-brief-intake
type: workflow
domain: control-plane
status: active
owner: system
---

# Workflow: Brief Intake

Use this workflow when the user wants to provide a short requirement and have the system figure out the rest.

## Goal

Turn one brief into a safe task graph that can be dispatched across multiple tools without overlapping edits.

## Steps

1. Capture the brief as a feature-level planning task.
2. Read `.ai/project/`, relevant `.ai/memory/`, and existing research artifacts.
3. Identify likely repository surfaces, dependencies, and unknowns.
4. Decide whether the brief is ready for direct planning or needs a preliminary research task.
5. Create a task graph with explicit sequencing and non-overlapping writable scopes.
6. Assign each task a preferred tool, review tool, risk tier, and isolation strategy.
7. Reserve shared wiring for a later integration task when parallel work would collide.
8. Dispatch only after the task contracts are explicit enough to execute safely.

## Minimum Brief Format

The brief may be as small as:

```text
Goal: add team billing with invoices and seat limits
Constraints: no worktrees, keep backend API stable
Do not touch: mobile app
```

## Expected Output

- one planning task folder for the feature intake
- zero or more research tasks when facts are missing
- one or more implementation or review tasks with clear boundaries
