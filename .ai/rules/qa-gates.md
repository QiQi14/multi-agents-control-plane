---
id: rule-qa-gates
type: rule
domain: control-plane
status: active
owner: system
updated: 2026-07-24
---

# Rule: QA Gates

QA reviews evidence, not trust-based prose.

Validate:

- Did the executor modify only allowed target files?
- Did it touch forbidden files?
- Does the diff match the contract? When `target_files` names an area (glob) rather than exact
  paths, "matches the contract" means every changed path falls inside a declared area and outside
  `forbidden_files`, and the invariants and evidence the contract states are actually satisfied —
  not that the executor touched a predicted file list. Attempt to construct a contract-compliant
  but unsafe write under the stated invariants and forbidden_files; if one exists, the contract
  text is the defect, not the diff.
- For any generated-file contract exemption, did the verifier use the selected base commit's exact
  manifest hash rather than trusting an entry added by the task? For a new dispatch/review pair,
  confirm the task prompt, adapter copy, and reconstructed manifest delta are exact and contain no
  extra entry or changed field. A missing or invalid current manifest must fail closed.
- Were required commands or tests run?
- **Does what the task PRODUCED match what it DECLARED?** Count the files in the task's `evidence/`
  directory against the artifacts `evidence.yaml` declares. Undeclared files are the single most
  common way a task ships with no usable evidence: they pass every gate, appear in a directory
  listing as diligence, and are visible to nobody. A gap is a finding, not a formatting nit — and
  the remedy is to declare them or delete them, never to change the reader.
- Does the versioned verification evidence record match the contract (identity, argv, scope,
  escalation reasons)? That record is the canonical evidence-gate result; raw gate logs (e.g. Cargo
  output) are diagnostic only. When no evidence gate is registered, `ai verify` degrades to the
  control-plane contract check — confirm the degraded run claimed no build/test evidence rather than
  masking a missing gate. Task-specific named commands (adversarial, platform, hardware, preview,
  visual) remain explicit contract gates and are not replaced by the generic verifier.
- **Which development environment produced the evidence (OS / arch / device), and does it match the
  environment this review runs in?** Evidence is environment-scoped — a test count, screenshot, or
  hardware-gated result from another machine is not evidence on this one.
- For an unknown-cause remediation, does the evidence satisfy
  .ai/rules/diagnostic-isolation.md: failing and control environments, measurement window,
  stopping condition, falsifiable hypothesis, and a rerun of the original failure? A lower
  baseline or slower unbounded slope is not a fix.
- Are risks documented?
- Is the implementation ready to merge?
- Should the task be rejected, revised, or accepted?

## Environment-mismatch handling (executor device ≠ review device)

When the executor generated evidence on one environment and this review runs on another (e.g. a
macOS executor and a Windows reviewer), hardware/OS-gated evidence does not travel. Do **not** issue
a vague "missing evidence" reject. Instead:

- **Name the environment mismatch as the cause** in the QA receipt.
- **Explicitly instruct that the acceptance evidence be regenerated on the review device** — list the
  exact commands to re-run and record in the receipt — or that the acceptance line be reconciled to
  the checks that actually run cross-platform.
- Confirm hardware/OS-gated tests **self-skip cleanly** on this host and are recorded as "skipped
  (reason)", not "missing".

A `revise` must always state *what* evidence is missing and *how* to produce it; an unclear reject
that only says evidence is absent is itself a QA defect.

## Acceptance criteria must name a real check

If an acceptance line names an artifact the codebase does not actually produce (e.g. a
"golden/snapshot PNG" harness that does not exist), the reviewer must require the receipt to
reconcile the gap with reproduced evidence for the check that *does* run — and flag the acceptance
line for correction — rather than pass on prose or reject without direction.

Risk gates:

- `low`: scope check.
- `medium`: scope check, tests, QA receipt.
- `high`: research digest, implementation receipt, tests, independent review, explicit merge approval.

## Reviewer profile proportionality

Maximum reasoning is not the default high-risk gate. For bounded feature work, use an eligible
cross-family logical review profile at the configured review-depth floor, chosen to preserve
independence from the executor. Prefer cross-family review; if an adequate cross-family reviewer is
unavailable, an owner may authorize a fresh substitute to keep diagnosis moving. The receipt must
disclose that it is not the normal independent pairing, and it does not satisfy the independent
merge gate without an explicit owner waiver.

Reserve reasoning above the configured escalation threshold for a task-specific written reason: native unsafe/ABI work,
cryptography, irreversible destructive mutation, unusually hard math/algorithm work, or a concrete
unresolved escalation after a High review. A platform or security concern outside the task's
acceptance boundary is a concise nonblocking follow-up unless it directly violates an in-scope
criterion or demonstrates concrete data loss/corruption.
## Context Retention and Closeout

A QA receipt is an immutable schema-versioned event. Record every finding, risk, limitation,
observation, and follow-up as a typed context item, including nonblocking items on an accepted
round. A later acceptance may resolve a prior finding but never overwrites its event.

Before versioned work closes, inspect `task-closeout.yaml`: every context item from every attempt and
round must have exactly one allowed disposition. Accepted risk and deferred work require rationale
and owner; a transfer requires a real target task and a reciprocal typed context relation. Missing
or one-sided dispositions are gate failures, not optional bookkeeping.
