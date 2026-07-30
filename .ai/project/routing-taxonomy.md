---
id: project-routing-taxonomy
type: project-doc
domain: control-plane
status: active
owner: system
created: 2026-07-26
updated: 2026-07-27
tags: [routing, taxonomy, capabilities, profiles, governance]
relations:
  - type: relates_to
    target: project-principles
  - type: relates_to
    target: rule-task-contracts
  - type: informs
    target: workflow-planning
  - type: informs
    target: agent-planner
---
# Routing Taxonomy and Execution Profiles

**Routing policy version:** 2

The machine-readable authority is `.ai/config.yaml`: `routing_taxonomy` defines task shape,
capabilities, policy, catalog provenance, and symbolic selectors; each tool declares stable logical
profiles. Exact vendor model identities are deliberately absent. Python validates and evaluates
the data generically and contains no branch on a zone, capability, tool, provider, or model name.

## 1. Routing Law

| Question | Selector | Governs |
|---|---|---|
| How much damage can this change do? | `risk` | Evidence, approval, and review gates |
| How hard is this work to do well? | Zone + axes → complexity band | Logical executor profile and reasoning level |

`risk` remains exactly `low`, `medium`, or `high`. It selects impact gates; high risk still
requires the declared evidence, independent review, and owner approval. Task shape and the derived
complexity band select the least-costly compatible logical profile. A high-risk review floor raises
review depth only, never executor complexity.

Selecting reasoning above `reasoning_escalation_threshold`, or a profile marked
`requires_rationale`, needs a task-specific written reason. Release timing, prominence, and
advisory environment detection are never routing weights.

This policy **supersedes the Task 184 sentence "Risk is the only reasoning-tier selector"** while
preserving its scope authority, independent-review, disclosure, and owner-approval gates.

## 2. Task Shape

Exactly one zone is declared. Its `required_capabilities` are a hard filter, never a score.

| Zone | Required capabilities |
|---|---|
| `mass_digest` | `large_context_ingestion` |
| `visual_design` | `visual_iteration` |
| `bounded_implementation` | `bounded_patch_loop` |
| `horizontal_refactor` | `multi_file_refactor`, `large_context_ingestion` |
| `deep_logic` | `deep_reasoning` |
| `research_diagnosis` | `repository_research` |
| `review` | `independent_review` |

Six orthogonal axes—`context_breadth`, `logic_depth`, `visual_interaction`, `change_breadth`,
`uncertainty`, and `feedback_loop_intensity`—each take one declared ordinal level. Levels are named
labels, never summed or averaged.

The configured band rules derive `routine`, `substantial`, or `demanding`. Rules are evaluated
independently; the highest matching band wins and the default applies when none match. The
explanation records every matching rule and axis. A recorded `routing_complexity_band` must equal
the derivation.

## 3. Logical Profiles

The effective capability set of a `(tool, logical profile)` pair is the union of:

- tool-surface capabilities in `tools.<tool>.capabilities`; and
- logical-profile capabilities in `tools.<tool>.profiles.<profile>.capabilities`.

A logical profile declares `reasoning_levels`, `complexity_bands`, `preference_rank`, optional
capabilities, and optional `requires_rationale`. It declares no exact model ID or vendor reasoning
label. `profile_preferences.complexity_reasoning` maps each complexity band to its minimum
reasoning level. Candidate ranking uses only the declared preference rank followed by stable tool
and profile declaration order.

`family`, not tool ID, governs review independence because several surfaces may front one family.
An eligible reviewer must be enabled, support `independent_review`, meet the review-depth floor,
and differ from the selected executor family. If none exists, the explanation gives explicit
owner-waiver guidance and substitutes nothing.

No tool currently declares `visual_iteration`. A `visual_design` route therefore fails closed
until an owner makes that capability declaration.

## 4. Local Enablement and Catalog Boundary

Committed config describes support only. `.ai/.local/tools.json` is ignored, checkout-local,
atomic, and explicit. Version 2 records:

- enabled tools and role defaults;
- enabled logical profiles per tool; and
- one normalized integration-owned catalog snapshot per enabled tool.

Version-1 profiles remain readable. They produce `unknown` catalog state and a symbolic
recommendation; they never imply an exact model.

A normalized catalog retains:

- provenance: `api_account`, `desktop_app`, `bundled`, `manual`, or `unknown`;
- integration source;
- observed and fetched timestamps;
- explicit evaluation time and TTL;
- computed freshness;
- exact observations with capabilities, reasoning support, logical-profile compatibility, and
  deterministic preference;
- symbolic selector and optional exact owner pin.

Provenance is authority, not decoration. The same model string under `api_account` says nothing
about `desktop_app` entitlement. Stale or unknown evidence remains visible and cannot prove exact
availability. Evaluation reads no wall clock: its time is normalized input.

`app_default` delegates identity to the execution surface. `latest_compatible` may late-bind an
exact identity only from a fresh compatible observation under the selected provenance. Without
trustworthy evidence the result stays symbolic. Every executor receipt records the actual model
reported by the execution surface.

An explicit exact pin is a hard constraint. Missing, stale, provenance-mismatched, or incompatible
evidence produces `exact-pin-unproven`; no symbolic or different-model fallback is attempted.

## 5. Task Metadata

The task-contract vector remains flat because the YAML subset is top-level:

```yaml
routing_policy_version: 2
routing_zone: "deep_logic"
routing_axes:
  - "context_breadth=moderate"
  - "logic_depth=high"
  - "visual_interaction=low"
  - "change_breadth=moderate"
  - "uncertainty=moderate"
  - "feedback_loop_intensity=low"
routing_complexity_band: "demanding"
routing_profile_tool: "claude"
routing_profile: "intensive_reasoning"
routing_reasoning_level: "high"
routing_provenance: "explicit_owner"
routing_rationale: "Dense protocol invariants require the demanding logical profile."
```

No `routing_` key means a legacy task: no shape is guessed. Any routing key activates strict
validation; partial, misspelled, duplicated, unknown, or inconsistent values fail closed.
Selected-profile metadata explains an assignment and must agree with `preferred_tool`; it never
reassigns work.

## 6. Route Explain

`python scripts/ai_cli.py route explain <task_id>` is deterministic and read-only. Add `--json` for
the stable machine form. The explanation includes normalized inputs, complexity derivation,
selected and rejected candidates, reviewer candidates, every reason, provenance, freshness, and
symbolic or exact resolution.

The engine never edits a task, enables a tool or profile, changes defaults, invokes a provider,
opens a URI, starts a process, reads credentials, or touches network, authentication, account,
quota, or payment state. Advisory detection is accepted only as diagnostic input and cannot alter
eligibility, ranking, resolution, or output bytes.

## 7. Route Apply and Planning Integration

`python scripts/ai_cli.py route apply <task_id>` is the bounded write companion to explain. It uses
only the explicit task vector and Git-ignored checkout-local profile. Ordinary explain and apply
share the same assignment constraints; only explicit replacement evaluates without them. Apply then
atomically fills unset or `pending` router-owned assignment fields. It records the policy version, `router` provenance, rationale,
complexity band, availability provenance, and symbolic/exact selector. Repeating it against the
result changes no bytes.

Owner and planner assignments are constraints, not suggestions. Apply preserves them by default;
`--replace` is a separately named owner-authorized path and requires nonempty
`--reconciliation TEXT` evidence. Replacement records both round-trip-safe owner prose and the
prior executor, reviewer, profile, and reasoning values. Neither path dispatches, launches,
submits, detects, enables, or probes a provider.

New research, planning, and feature-task creation either receives the complete explicit zone,
axis vector, executor, and reviewer, or writes both assignments as `pending` with exact explain/apply
guidance. Partial input fails closed and free text is never converted into routing metadata.

## 8. Versioning

Version 2 replaces exact model inventory with stable logical profiles and the normalized
integration-owned catalog boundary. A catalog-only monthly identity change requires no edit to
route-core Python or logical-profile policy. Future changes to closed routing vocabulary or
evaluation semantics increment `routing_taxonomy.version`; prose-only clarification does not.
