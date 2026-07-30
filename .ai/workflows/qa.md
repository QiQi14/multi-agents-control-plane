---
id: workflow-qa
type: workflow
domain: control-plane
status: active
owner: system
updated: 2026-07-24
relations:
- type: depends_on
  target: rule-qa-gates
---

# Workflow: QA

QA validates diff, receipt, contract, and gates.

## Required Checks

- Scope check.
- Contract check.
- Optional blast-radius check — for exact changed symbols that verify against the index, QA may
  query `ai impact` and evaluate high-importance callers outside the declared scope as potential
  scope gaps.
  This optional blast-radius step is ADVISORY ONLY and must NEVER auto-expand `target_files`.
  A flagged caller is never an automatic finding, block, or gate; incomplete,
  ambiguous, stale, missing, or unavailable advice is omitted and never fails QA.
- Test evidence check.
- Verification evidence check — validate the task's versioned evidence record against the
  contract: scope, any gate selection, escalation reasons, argv, exit status, and diff identity.
  That record is the canonical evidence-gate result; QA does not re-run the gate's raw tool
  output to substitute for it, and raw gate logs (e.g. Cargo output) are diagnostic only. When no
  evidence gate is registered, verification degrades to the control-plane contract check and the
  evidence is that contract result — QA confirms the degraded message was honest (no build/test
  evidence claimed) rather than treating a missing gate as a pass for stack-relevant work.
- Visual evidence check — if the task changed a painted surface, a preview variant must exist and
  the reviewer must OPEN the image, not just read the painter. See `.ai/rules/visual-evidence.md`.
  Tests and clippy passing is not visual evidence; a console has cleared both and still shipped
  a visual regression that only rendering caught.
- Environment check — record the OS/arch/device that produced the evidence and the one this review
  runs in. On a mismatch, follow the environment-mismatch handling in `.ai/rules/qa-gates.md`
  (name the mismatch, instruct regeneration on the review device — do not reject unclearly).
- Diagnostic-isolation check — when root cause or trigger environment was initially unknown,
  require the failing/control matrix, measurement window, stopping condition, and reproduction of
  the original symptom. Reduced cost or slower growth is not fix evidence if the failure persists.
- Risk documentation check.
- Knowledge capture check.
- Documentation check (`.ai/rules/documentation-currency.md`) — two narrow cases, not a per-task tax:
  (a) if this task SHIFTS THE ARCHITECTURE, its doc must be reconciled within the task;
  (b) if this task is the last one carrying its `feature: docs/NNN`, the feature-close breakpoint
  applies and the doc must be reconciled before the feature closes. Ordinary tasks touch no docs.
  Reject a doc edit that appends a changelog or task-number narrative — that history belongs to the
  task records. When the diff creates, moves, or changes `project/docs/*.md`, require the matching
  project-document template, complete schema-v2 metadata, and a green `ai docs lint`. Reject any
  inferred authority, maturity, visibility, navigation, relation, or subject value presented as
  authored truth.

## Decisions

- `accept`: all required gates pass.
- `revise`: bounded fixes needed. Always state *what* is missing and *how* to produce it — for a
  missing-evidence revise, list the exact commands to re-run on the review device. A reject/revise
  that only says "evidence is missing" without direction is itself a QA defect.
- `reject`: contract, scope, or safety failure.

High-risk work requires independent review by a different tool/model and explicit merge approval.
The reviewer profile is proportional to task shape; high risk alone does not require reasoning
above the configured review-depth floor.
## Versioned QA Events

Write one immutable QA event per round with the exact reviewed revision. On `revise`, preserve that
event under a round-specific filename before the executor changes the implementation; a wholly new
review writes the next event. On `accept`, retain nonblocking context as typed items. Acceptance is
not closeout: the coordinator must create a valid fold that dispositions every item before merge.
