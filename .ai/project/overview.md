---
id: project-overview
type: project-doc
domain: control-plane
status: active
owner: system
---

# Project Overview

This repository contains the Agent Control Plane: a repo-native coordination framework for using separate AI coding tools against one project without assuming a shared runtime.

`.ai/` is the canonical source of truth. Tool-specific files such as `AGENTS.md`, `GEMINI.md`, `CLAUDE.md`, `.claude/`, `.agents/rules/`, and `.agents/skills/` are generated adapters.

## Purpose

- Coordinate research, planning, implementation, review, QA, and learning across Codex, Claude Code, Google Antigravity, and generic coding agents.
- Keep task contracts, receipts, and memory in repository files instead of chat state.
- Protect tools with incompatible workspace assumptions, especially Antigravity workspace indexing.

## Operating Model

One task equals one isolated execution context. The isolation strategy is explicit per task and may be `branch`, `patch`, `shadow-copy`, `worktree`, `readonly-research`, or `manual`.

The default isolation is conservative: `branch_or_patch`. Worktrees are optional and never universal.

## Child Product Control Lanes

- **Orbital / Triagain public launch product** is an independent child Git repository at
  [`orbital/`](../../orbital/). Its canonical task, context, and documentation lane starts at
  [`orbital/.ai/README.md`](../../orbital/.ai/README.md). When working inside that repository,
  follow `orbital/AGENTS.md` and the inner active task rather than copying its contracts into the
  root control plane.

## Canonical vs Generated

- Editable source: `.ai/`
- Generated adapters: `AGENTS.md`, `GEMINI.md`, `CLAUDE.md`, `.claude/`, `.agents/rules/`, `.agents/skills/`
- Generated adapter edits are disposable unless manually ported back into `.ai/`.
