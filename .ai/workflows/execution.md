---
id: workflow-execution
type: workflow
domain: control-plane
status: active
owner: system
updated: 2026-07-24
relations:
- type: depends_on
  target: rule-task-contracts
---

# Workflow: Execution

The selected executor implements one task. The executor can be Codex, Antigravity, Claude Code, or another configured tool when the task contract justifies that choice.

## Steps

1. Read `task.yaml`, `brief.md`, `context.md`, and the selected tool prompt.
2. Confirm isolation strategy.
3. Check current branch and base commit when applicable.
4. If the task claims to remediate an unknown-cause failure, confirm its prerequisite diagnostic
   evidence names the failing case, a control, measurement window, stopping condition, and
   falsifiable hypothesis under .ai/rules/diagnostic-isolation.md. Stop if it does not.
5. Edit only `target_files`.
6. Run `__AI_COMMAND_VERIFY__ <task-id> --base <base> --plan` before implementing — at the latest
   before verification — to confirm diff scope and any evidence-gate selection. Gate with
   `__AI_COMMAND_VERIFY__ <task-id> --base <base> --run`: this resolves the project's registered
   evidence gate through the extension registry, and with no gate registered it runs the
   control-plane contract check only and says so (exit 0 for docs/framework-only work). When the
   registered gate exposes a wrapped command runner, route every explicit gate command through it —
   for the Rust gate that is `__AI_COMMAND_CARGO__ <task-id> --base <base> [--label <evidence-label>] -- <argv...>`.
   Run the remaining required commands or document why they could not run.
7. Capture diff evidence.
8. If the task painted anything, add or refresh its preview variant, render it, and record the exact
   variant command in the receipt — see `.ai/rules/visual-evidence.md`. Tests and clippy are not
   visual evidence. If the feature has no production trigger yet, the harness is the only way to
   prove it renders at all, so it is required, not optional.
9. If this task SHIFTS THE ARCHITECTURE, reconcile its `feature:` doc in the same task — rewrite to
   current truth, never append a changelog (`.ai/rules/documentation-currency.md`). An ordinary task
   touches no docs; if you are unsure whether the shape of the system changed, say so in the receipt
   and let QA adjudicate rather than editing a doc speculatively.
10. Fill `receipt.executor.yaml`, recording the verification evidence record.
11. Hand off to review or QA.

## Stop Conditions

- Missing contract.
- An unknown-cause remediation task lacks prerequisite diagnostic-isolation evidence.
- Required file is outside `target_files`.
- A forbidden file must be touched.
- Tests cannot be run and no acceptable alternate evidence exists.
- The task risk tier requires review not yet planned.
## Versioned Evidence Procedure

For new schema-versioned work, write one immutable executor receipt per attempt. Record actual
actor/model/reasoning, exact base/head/diff, environment, commands as gates, evidence IDs, and every
typed context item. Keep visual or other claim-bearing artifacts in `evidence.yaml`; the receipt
references their stable IDs. A revision appends a new attempt rather than rewriting a committed or
frozen event. Closeout happens only after accepting QA and folds every context item.
