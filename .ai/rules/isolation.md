---
id: rule-isolation
type: rule
domain: control-plane
status: active
owner: system
---

# Rule: Task Isolation

One task equals one isolated execution context.

Supported strategies:

- `branch`
- `patch`
- `shadow-copy`
- `worktree`
- `readonly-research`
- `manual`
- `branch_or_patch`
- `patch_review`

## Defaults

- Default isolation: `branch_or_patch`
- Antigravity default for research: `readonly-research`
- Antigravity default for implementation/scaffold: `branch_or_patch`
- Claude default for review: `patch_review`
- Claude default for implementation: `branch_or_patch` or task-selected patch mode
- Codex default: `branch_or_patch`

## Worktree Safety

Worktrees are optional. They are never the default. If a task targets Antigravity or requires Antigravity to keep project-wide indexing stable, do not create a git worktree automatically.

Tool choice does not determine isolation by itself. The task contract must name the isolation strategy.
