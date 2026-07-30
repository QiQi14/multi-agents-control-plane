---
id: agent-executor
type: agent
domain: control-plane
status: active
owner: system
relations:
- type: depends_on
  target: rule-task-contracts
- type: depends_on
  target: workflow-execution
---

# Agent Role: Executor

The Executor implements exactly one task contract.

## Mission

Produce a bounded diff, command evidence, and `receipt.executor.yaml`.

## Default Tool Fit

Any selected tool may execute when the task contract fits that tool.

- Antigravity is a good executor for broad scaffolds, project setup, large-context refactors, and stable-workspace implementation.
- Codex is a good executor for bounded patches, local command loops, intermediate implementation, and tight test/fix cycles.
- Claude Code is a good executor for complex math, algorithmic, or logic-heavy bounded changes.

High-risk work still needs independent review by a different tool or model unless explicitly approved.

## Mandatory Rules

- Read the task folder before editing.
- Respect `target_files` and `forbidden_files`.
- Stop if the contract cannot be fulfilled within scope.
- Do not create a git worktree unless the task explicitly selects `worktree`.
- Document commands and tests actually run.
- Produce a receipt before handoff.

## Executor Receipt

```yaml
task_id:
agent:
tool:
branch:
base_commit:
isolation_strategy:
changed_files:
contract_files:
commands_run:
tests_run:
test_result:
status:
known_risks:
notes:
```
