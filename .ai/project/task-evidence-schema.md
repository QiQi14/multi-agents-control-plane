---
id: project-task-evidence-schema
type: spec
domain: control-plane
status: active
owner: system
updated: 2026-07-30
relations:
- type: enforced_by
  target: rule-task-contracts
- type: enforced_by
  target: rule-qa-gates
- type: referenced_by
  target: workflow-execution
- type: referenced_by
  target: workflow-qa
- type: referenced_by
  target: workflow-learn
---

# Versioned Task Evidence and Closeout

Task history is an ordered event stream. A QA acceptance event never overwrites an earlier revise
round and never makes a nonblocking finding disappear. `task-closeout.yaml` is the authoritative
fold over those immutable events.

## Serialization

New task evidence uses schema version `1` in files named `receipt*.yaml`, `evidence.yaml`, and
`task-closeout.yaml`. These YAML files use the strict JSON-compatible subset of YAML 1.2 so the
standard-library control plane can parse nested values without a dependency or ambiguous YAML
coercion. Objects reject unknown fields; required fields do not receive inferred defaults.

Historical free-form receipts are not parsed into the new vocabulary. They are labelled
`legacy-untyped-context`, exposed as incomplete with their raw file available, and protected by the
exact hashes in `task-artifact-legacy-baseline.json`. Baseline hashes come from the exact Git blob
bytes at the declared base, never checkout-dependent bytes; audit recognizes only Git's CRLF/LF
checkout normalization as equivalent. Once the baseline exists at a verifier base, the baseline
itself is immutable, so a same-diff hash rewrite cannot authorize a historical mutation. A new or
changed receipt that lacks schema version `1` fails closed. Moving an unchanged legacy receipt
between task states is allowed because its baseline identity excludes the state directory;
changing, deleting, or imitating it is not.

## Immutable receipt events

Every executor attempt and QA round is one receipt event with:

- `schema_version`, stable `receipt_id`, `task_id`, and `role` (`executor` or `qa`);
- role-specific `sequence.attempt` or `sequence.round`;
- `actor` name, family, tool, actual model, and reasoning;
- `revision` base, head, exact diff identity, and optional SHA-256 diff fingerprint;
- `environment` OS, architecture, device, and optional toolchain;
- role-specific `decision.status` plus an outcome;
- ordered `gates` with exact command, result, and evidence references;
- task-level `evidence_refs`;
- zero or more typed `context_items`;
- optional notes and knowledge-capture candidates.

A receipt already present at the selected base may move unchanged but may not be edited or deleted.
For a revise loop, preserve the prior event under a round-specific filename, then let the wholly
fresh reviewer create the next `receipt.qa.yaml`. Final acceptance names the exact accepted event;
it does not replace the earlier one.

## Typed context

Every context item has a stable `context_item_id`, type, blocking flag, severity, summary, and event
state. Types are `finding`, `risk`, `limitation`, `observation`, `follow-up`, or `decision`. Optional
fields carry source receipt, source locations, evidence references, resolution text, owner, target
task, and a reciprocal context relation.

Blocking controls the current decision only. Both blocking and nonblocking items participate in the
closeout fold. Free text such as `known_risks`, `issues_found`, or `notes` in a legacy receipt is not
silently interpreted as a complete typed item.

## Evidence sets

`evidence.yaml` is a versioned set of claim-bearing evidence items. Each item records:

- stable evidence ID;
- kind: `generated-result`, `expected-reference`, `golden`, or `comparison-diff`;
- role: acceptance, supporting, or diagnostic;
- storage convention: committed, regenerable, or external;
- availability;
- optional artifact path, media type, SHA-256, dimensions, variant, theme, locale, and route;
- exact producer command and producer environment;
- claim and acceptance linkage;
- optional inspection and coverage;
- a meaningful accessibility text alternative.

An expected reference is never delivered-result evidence. A comparison is never silently treated as
a golden. A regenerable preview is valid without a committed PNG when its exact command,
availability, claim/linkage, environment, and accessibility text are recorded. An unavailable item
stays visible as unavailable instead of rendering as a missing committed file.

## Reader-safe evidence and receipt fields

The evidence schema is lossless, but the default task reader is not a raw-record dump. Source keeps
the exact receipt and evidence artifacts. Human-facing Evidence and Review views may expose
structural facts such as role, sequence, decision, result, severity, blocking state, disposition,
counts, availability, media type, dimensions, and verified media previews.

Human-facing narrative is eligible only when it is independently authored without repository
locators, commands, or revision expressions. This applies across receipt outcomes and notes,
context summaries and resolutions, evidence claims, inspection prose, and accessibility text.
Unsafe narrative is labelled `source-only`; it is never partially redacted, rewritten, or replaced
with a guess. Exact commands, artifact paths, source locations, revisions, hashes, producers, and
environment details remain Source-only even when technically valid.

Verified committed media may be copied into a deterministic, noncanonical reader asset alias. The
reader displays the media and its safe caption without exposing the canonical repository location.
The alias is a generated presentation artifact, not evidence authority; Source retains the
canonical record.

## Disposition-gated closeout

`task-closeout.yaml` lists every immutable receipt ID in causal order, names the accepting QA
receipt, and dispositions every context item exactly once as:

- `resolved` — the item is closed with rationale;
- `accepted-risk` — rationale and owner explicitly accept the residual risk;
- `deferred` — rationale and owner retain the work for later;
- `transferred` — owner, real target task, and target context item are named, and the target carries
  a reciprocal relation back to `<source-task>/<source-context-item>`;
- `superseded` — rationale and the replacing context item are named.

Closeout fails on a missing or duplicate item, an unknown receipt, an accepting receipt that is not
an accepting QA event, incomplete accepted-risk/deferred ownership, or a one-sided transfer. The
merge gate requires both a valid closeout and the complete task-evidence audit for versioned
tasks, including task identity and every receipt/gate/context evidence reference. Legacy tasks remain
mergeable through the legacy gate and are always presented as incomplete.

## Validation surfaces

- `ai verify ... --plan|--run` checks changed/new task evidence and immutable receipts.
- `ai audit-framework` checks the complete legacy baseline plus every schema-versioned artifact.
- `ai qa` creates a schema-v1 QA receipt template and refuses to force-overwrite a versioned event.
- `ai sync` republishes the rules and workflow guidance into generated adapters.
- The production read model consumes `read_task_evidence`; it receives typed data for versioned
  tasks and only the explicit incomplete label/raw-availability bridge for legacy tasks.