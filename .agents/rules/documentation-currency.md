---
trigger: always_on
description: "Rule: Documentation Currency. Docs describe the project as it is today, for a human reader: compact, publishable, never a changelog. History lives in task records."
---
<!-- GENERATED FROM .ai/. DO NOT EDIT DIRECTLY. Run `ai sync`. -->

# Rule: Documentation Currency

Docs describe the project as it is today, for a human reader: compact, publishable, never a changelog. History lives in task records.

`project/docs/` is not a changelog, not a spec dump, and not a task ledger. A doc that still promises
something a later decision dropped is a defect — not history. Docs are potentially published (the
Orbital / Triagain public product lane), so the bar is compact and clear with enough information to
be useful — never sprawl.

## Where each kind of truth lives

| Truth | Home | Never |
|---|---|---|
| How the system works today | `project/docs/` architecture docs | A running history of how it got there |
| What a feature will be | Summarized into its architecture doc | A full spec pasted into the doc |
| Why/when something changed | `.ai/tasks/done/` + `archive/` records | `project/docs/` |
| A changelog or dev log | **Derived on demand** from task records | Hand-maintained prose in a doc |
| Reusable traps, decisions, lessons | `.ai/memory/` (see `.ai/rules/knowledge-capture.md`) | `project/docs/` |

Task folders and archives already carry the full history with receipts and evidence. When a changelog
or dev log is wanted, generate it from those — do not grow one inside a doc.

## New product-document authoring gate

Every new, moved, or content-changed `project/docs/*.md` file must start from the matching
`.ai/templates/project-doc/*.md.tmpl` variant and satisfy schema version 2 in
`.ai/project/doc-schema.md`. Required metadata is authored truth. A summary extracted from prose,
an inferred Markdown link, a filename, or a renderer default must never be promoted into authored
audience, authority, maturity, visibility, navigation, relation, or subject metadata.

The checked-in legacy baseline exempts only an exact repository-relative path and SHA-256 content
hash. An exemption labels the document `legacy-untyped`, internal, and unclassified; it does not
make the document canonical, normative, public, or publication-ready. Editing or moving the file
ends the exemption and requires complete schema-v2 metadata.

## Deep dives

Reserved for **important or complex features** that a reader cannot understand from the architecture
doc alone. Not every feature earns one. Prefer durable content (invariants, conventions, math,
contracts) over anything that restates current code, which drifts the moment the code moves.

## The gates

1. **Feature-close breakpoint (primary).** Tasks declare their doc in the `feature:` field
   (`feature: "Viewport identity (docs/025 §2.5)"`). When the LAST task carrying `feature: docs/NNN`
   reaches `done/`, that doc must be reconciled before the feature is closed. This is checkable:
   a doc whose tasks are all done, carrying no reconciliation, is drift.
2. **Per-task, only on an architecture shift.** A task that *changes the shape of the system*
   reconciles the affected doc within that task. Ordinary tasks do not touch docs — per-task doc
   edits are the exception, not the tax.

## What reconciliation means

**Rewrite to current truth. Do not append.**

- **Delete** what a later decision dropped. Docs accumulate promises that analysis quietly killed;
  removing them is the point of the pass, not a side effect.
- **Correct** what changed shape.
- **Summarize** what landed, compactly, in the doc's own voice — not as "task_12 delivered X".
- **Do not** add a history/changelog section, a task-number narrative, or per-round receipts.
- Deviations, defects, and rationale stay in the task records where they already live. A doc may
  state a current limitation (it is true today); it must not narrate how the limitation arose.

## Why this rule exists

Documentation was in no rule and no workflow, so it was nobody's job — it surfaced only in
retrospective sweeps. At the time of writing, `docs/024` had **7 tasks done and no record that any
of them shipped**, and `docs/025` had 3. The link needed to prevent that already existed: every task
names its doc in `feature:`. Nothing read it.
