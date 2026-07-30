---
id: project-principles
type: project-doc
domain: control-plane
status: active
owner: system
created: 2026-07-24
updated: 2026-07-24
tags: [principles, governance, control-plane, semver]
relations:
  - type: relates_to
    target: rule-control-plane
---
# Control Plane Governed Principles

**Version:** 1.1.0

---

## 1. Overview

This document defines the non-negotiable foundational principles governing the Agent Control Plane. All control-plane rules, workflows, agent roles, and task contracts operate under these principles. Reviewers and executors must treat these principles as binding authority.

---

## 2. Core Principles

### Principle 1: Single Source of Truth

The `.ai/` directory is the sole canonical source of truth for all agent control-plane instructions, rules, workflows, and configurations. External files, generated adapters (`AGENTS.md`, `GEMINI.md`, `CLAUDE.md`), and IDE integrations are build outputs derived from `.ai/`.

### Principle 2: Task Contract Isolation & Bounded Scope

Every implementation task operates within a folder-based task contract containing explicit `target_files`, `forbidden_files`, risk tier, and isolation strategy. Executors may edit only target files and must strictly preserve forbidden files. `target_files` names a bounded writable area (glob) plus `forbidden_files` when growth is plausible, not an exhaustive file enumeration; exact-file decomposition inside a declared area is the executor's engineering judgment, never a contract violation (`.ai/rules/task-contracts.md`).

### Principle 3: Evidence-Led Verification

No task is complete until concrete runtime verification evidence is produced and recorded in a structured receipt (`receipt.executor.yaml` / `receipt.qa.yaml`). Prose claims without command/test evidence are insufficient for acceptance.

### Principle 4: Visual Evidence for Painted Surfaces

Code tests and static checks do not prove visual correctness. Any change to a painted surface or visual user interface requires rendering a preview variant and inspecting the rendered visual artifact (`.ai/rules/visual-evidence.md`).

### Principle 5: Diagnostic Isolation Before Remediation

Unknown-root-cause defect remediation must begin with environment isolation, reproduction, and falsifiable hypotheses before applying code modifications (`.ai/rules/diagnostic-isolation.md`).

### Principle 6: Impact Risk and Task Complexity Are Separate Selectors

`risk` selects impact evidence, approval, and review gates and nothing else. The task zone and its
derived complexity band select the executor's model and reasoning profile. A high-impact task may
impose a review-depth floor on the required independent review, but high impact never upgrades the
executor's complexity band — it does not make simple work cognitively complex. Routing vocabulary is
declared configuration data, never a provider, model, or zone branch in code
(`.ai/project/routing-taxonomy.md`). This separation adds a selector boundary; it does not relax any
gate: high-risk work still requires independent review and explicit owner merge approval.

---

## 3. SemVer Governance Contract

Modifications to this principles document follow strict Semantic Versioning (SemVer):

- **MAJOR version change (e.g. 1.0.0 -> 2.0.0):** Required when a principle is removed, fundamentally redefined, or when an existing principle's enforcement contract is weakened or structurally broken.
- **MINOR version change (e.g. 1.0.0 -> 1.1.0):** Required when adding a new principle or extending the governance scope without altering or removing existing principles.
- **PATCH version change (e.g. 1.0.0 -> 1.0.1):** Permitted for minor wording, typographical, formatting, or clarity adjustments that do not change normative principle meaning.

---

## 4. Rule Changes & Sync Impact Reports

Any proposed change to standing control-plane rules (`.ai/rules/**`) or governed principles must include an explicit **Sync Impact Report** note in the task contract and review prompt. The Sync Impact Report must detail:

1. The exact rule or principle being modified.
2. Anticipated side effects on existing workflows, generated adapters, or agent roles.
3. Required adapter regeneration steps (`python scripts/ai_cli.py sync`) and verification commands.

---

## 5. Reviewer Authority & Landing Discipline

- **Non-Negotiable Status:** Reviewers treat these principles as non-negotiable. Any change in an implementation PR or task diff that conflicts with a governed principle is automatically blocking and requires a `revise` decision.
- **Separate Governance Tasks:** Principle changes must land **only** through separate, dedicated governance tasks. Principle modifications may never be diluted or smuggled inside an unrelated feature, bugfix, or refactoring review.

---

## 6. Revision History

- **1.1.0** (2026-07-26): MINOR — added Principle 6 separating impact risk from task
  complexity as routing selectors. No existing principle is removed, redefined, or weakened, and no
  enforcement contract is relaxed. Supersedes the Task 184 sentence "risk is the only reasoning-tier
  selector" while preserving Task 184's intent (no ad-hoc second selector such as release timing or
  feature prominence) and every gate it established. **Sync Impact Report:** (1) modified rules —
  `.ai/rules/task-contracts.md` gained a Routing Metadata section defining the optional `routing_*`
  contract fields and their fail-closed compatibility rule; (2) anticipated side effects — the
  reconciled law is restated in `.ai/config.yaml` (`routing_principles`, new `routing_taxonomy`
  block, per-tool `family`/`capabilities`/`profiles`), `.ai/workflows/planning.md`,
  `.ai/workflows/task-template.md`, and `.ai/agents/planner.md`; the new canonical vocabulary lives
  in `.ai/project/routing-taxonomy.md`; risk tiers, `risk_gates`, high-risk independent review,
  cross-family independence, forbidden-file authority, and Task 191 enablement semantics are all
  unchanged; existing task contracts declare no routing metadata and remain valid under the
  compatibility rule; (3) regeneration and verification — `python scripts/ai_cli.py sync`,
  `python scripts/ai_cli.py doctor`, `python scripts/ai_cli.py audit-framework`, and
  `python -m unittest discover -s scripts/tests`. This slice defines and validates vocabulary only;
  it ranks no tool, mutates no assignment, and launches nothing.
- **1.0.1** (2026-07-24): PATCH — clarified Principle 2 to state that `target_files`
  names a bounded writable area (glob) plus `forbidden_files` rather than an exhaustive file
  enumeration when growth is plausible; enforcement (executors edit only target files, forbidden
  files stay absolute) is unchanged. **Sync Impact Report:** modified
  `.ai/rules/task-contracts.md`, `.ai/rules/qa-gates.md`, `.ai/workflows/planning.md`,
  `.ai/workflows/task-template.md`, `.ai/agents/planner.md`, and `.ai/config.yaml`
  (`routing_principles`) to establish the area-plus-invariants contract law and make risk the sole
  reasoning-tier selector; no verifier change required (`rust_verify` already accepts glob
  `target_files`); adapters regenerated via `python scripts/ai_cli.py sync`.
- **1.0.0** (2026-07-24): Initial governed principles document.
