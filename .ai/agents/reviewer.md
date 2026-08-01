---
id: agent-reviewer
type: agent
domain: control-plane
status: active
owner: system
relations:
- type: depends_on
  target: workflow-review
---

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
