---
id: workflow-learn
type: workflow
domain: control-plane
status: active
owner: system
---

# Workflow: Learn

After QA, capture durable knowledge in typed memory — and, at a feature close, reconcile the doc.

## Steps

1. Read `receipt.qa.yaml`.
2. Identify reusable facts, traps, deprecations, decisions, or lessons.
3. Write to the correct `.ai/memory/` file.
4. Keep entries concise and dated.
5. Do not paste raw logs unless the exact log is the durable lesson.
6. **Feature-close doc breakpoint.** Check whether this task was the LAST one carrying its
   `feature: docs/NNN`. If every task for that doc is now in `done/`, the doc must be reconciled to
   current truth before the feature closes — see `.ai/rules/documentation-currency.md`. Reconcile
   means rewrite (delete dropped promises, correct what changed, summarize compactly), NOT append a
   history section: the history already lives in the task records you just read.

7. When reconciliation creates, moves, or changes a product document, copy the matching
   `.ai/templates/project-doc/*.md.tmpl` variant and author every schema-v2 field. Do not convert
   inferred prose, links, or filenames into authority or publication metadata.

## Where knowledge goes

`.ai/memory/` is for the agents (traps, decisions, lessons). `project/docs/` is for humans and may
be published. They are not the same audience and must not be filled with the same text.
## Learn From the Fold

Read all immutable QA events and `task-closeout.yaml`, not only the final acceptance. Capture a
durable lesson only when it belongs in typed memory. Do not delete, paraphrase away, or silently
promote receipt context; its authoritative state and disposition remain in the task folder.
