---
id: project-infrastructure
type: project-doc
domain: control-plane
status: active
owner: system
---

# Infrastructure

The framework intentionally uses only repository files and the Python standard library.

## Requirements

- Python 3.10 or newer is recommended.
- No package installation is required for the control-plane CLI.
- External coding tools remain manually operated unless a future adapter explicitly adds automation.

## Generated Outputs

`ai sync` writes:

- `AGENTS.md`
- `GEMINI.md`
- `CLAUDE.md`
- `.claude/`
- `.agents/rules/`
- `.agents/skills/`

Generated files include a warning and should be treated as build outputs from `.ai/`.

Antigravity receives the always-loaded project prompt through root `GEMINI.md`.
The generated `.agents/` tree is intentionally limited to conditional rules and
skills so ordinary Antigravity chat is not dependent on slash-command workflow
loading.

## Workspace Stability

Antigravity should use the stable current workspace by default. Do not create git worktrees for Antigravity-targeted work unless the user explicitly approves a topology change.
