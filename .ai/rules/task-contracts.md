---
id: rule-task-contracts
type: rule
domain: control-plane
status: active
owner: system
updated: 2026-07-30
relations:
- type: enforced_by
  target: workflow-execution
- type: enforced_by
  target: workflow-qa
---

# Rule: Task Contracts

The task folder is the contract.

Executors may edit only `target_files`. They must not edit `forbidden_files`.

If implementation requires files outside `target_files`, the executor must stop and request scope approval instead of expanding the task.

## Target Files: Bounded Areas and Invariants, Not File Enumeration

`target_files` names bounded writable **areas** — glob patterns — plus explicit `forbidden_files`,
not an exhaustive list of exact paths, whenever the task's scope could plausibly grow, split, or
reorganize files during implementation. Inside its declared area, the executor may create, split,
merge, rename, and reorganize files freely; file-level decomposition inside the area is the
executor's engineering judgment, not a contract matter. `rust_verify`'s contract check already
accepts `**` glob patterns in `target_files`, so area-based
contracts require no verifier change.

Exact-file enumeration is still the right shape for a bounded single-file surgical edit where no
plausible split exists — for example, a one-line fix or a change confined to a single small file.
When in doubt about whether growth is plausible, prefer an area.

**Recorded rationale:** a contract enumerated five exact file paths for a module. The
executor found that one of those files exceeded 1,000 lines with cleanly separable
responsibilities, but the contract's exact file list — not engineering judgment — forbade splitting
it further; creating a submodule directory would have failed contract verification. No planner,
frontier models included, can predict actual code growth at execution time, so exact-file
enumeration systematically converts good decomposition into a contract violation.

The load-bearing contract content moves to **invariants**: which responsibilities must stay
separated, which behavior must remain byte- or semantics-preserving, which gates must pass, and
what evidence proves each. This does not weaken scope authority: `forbidden_files` stays absolute
regardless of how `target_files` is expressed, scope expansion always requires owner approval, and
contract verification still fails closed on any out-of-area write. A glob is an authorization to
reorganize files that serve the task's stated purpose inside that area — it is never license to
touch a file the invariants and forbidden_files together mark as out of bounds, even if its path
happens to match the glob syntactically.

A queued contract that already enumerates exact files is not bulk-rewritten under this law; it
applies to new contracts. An existing contract's file list may be relaxed only through an explicit
owner-approved `task.yaml` amendment committed before dispatch, and the dispatch record must cite
that commit and decision. A prompt, handoff note, or dispatch-time annotation can never expand
writable authority.

## Generated-File Contract Exemption

`.ai/_manifest.json` records generated repository files by relative path, SHA-256, and the exact
`ai sync`, `ai dispatch`, or `ai review` command that produced them. The verifier may exempt a
changed generated file from `target_files` and `forbidden_files` only when its bytes exactly match
an entry in the manifest from the selected base commit. The current manifest must also parse and
validate; a missing, malformed, self-referential, or schema-invalid manifest disables all
exemptions.

A manifest changed in the task diff cannot authorize its own entries. The only in-diff exception
is a newly generated dispatch or review prompt pair: the task-local prompt and configured adapter
copy must be byte-identical, and the current manifest must equal the base manifest plus exactly
those two path/hash/command entries. Any extra entry or altered field fails closed.

All other contract checks remain authoritative. A manifest exemption applies only to the exact
generated path whose trusted hash matches; it does not weaken non-manifest scope, forbidden-file,
or task identity checks.

## Portable Repository Paths

Repository-local paths in project documentation, task contracts, briefs, contexts, generated
prompts, and dispatch/review handoffs must be relative to the repository root. Use `.` for the
repository root and forward slashes for repository paths, including on Windows. Never embed a
developer's drive letter, home directory, checkout location, `file:///` URI, or tool-managed
worktree location as an actionable repository path.

Commands may use platform-specific launcher syntax such as `.\ai.cmd` when the command itself
requires it, but their project file arguments must remain repository-relative. Paths that are
inherently external may use environment variables or explicit placeholders; do not substitute one
developer's installed SDK, executable, or home-directory location. Historical receipts may retain
the exact paths actually observed as command evidence, and filesystem tests may retain deliberately
absolute hostile-path fixtures when the path semantics are under test.

Reviewers must reject an actionable task packet or project document that depends on a
machine-specific checkout path.

## Human Presentation Contract

Execution contracts and human-reader contracts are separate interfaces. Exact repository paths,
globs, symbols, commands, revisions, and evidence locations belong in `target_files`,
`forbidden_files`, `commands`, typed evidence, task context, Source, and Git. They must not be used
as the prose shown to developers, product owners, or QA in the task reader.

New tasks declare one complete, flat presentation namespace:

- `presentation_schema_version: "1"`;
- `presentation_purpose`: why the task matters to the product or workflow;
- `presentation_outcome`: the stakeholder-visible behavior or capability it must produce;
- `presentation_scope`: affected features, user journeys, or team workflows;
- `presentation_out_of_scope`: optional, explicit stakeholder boundaries; and
- `presentation_acceptance`: observable acceptance statements understandable without repository
  layout knowledge.

The `presentation_*` namespace is all-or-nothing. Declaring any presentation key reserves the whole
prefix and requires every core field. Values are authored plain language, not Markdown, and must
not contain repository locators, commands, or revision expressions. Validation fails closed on a
partial, misspelled, or locator-bearing presentation contract; it never rewrites or redacts the
author's words.

The task `title` and `feature` label are also global reader metadata. On a new task that activates
the presentation namespace, both must be locator-free. A legacy locator-bearing title is replaced
outside Source by a humanized task-ID label and marked source-only; a legacy locator-bearing feature
keeps only an opaque grouping identity and an explicit source-only label. The original values remain
unchanged in Source.

A legacy task with no `presentation_*` key remains valid and byte-preserved. Human views label its
presentation as unavailable and direct readers to Source for the historical contract. They never
promote `input_contract`, `output_contract`, `acceptance_tests`, `known_risks`, receipts, filenames,
or inferred technical areas into a guessed stakeholder summary. A separately labelled technical
footprint may be derived from exact contract fields, but it is not feature scope.

Every task must specify:

- Inputs.
- Outputs.
- A complete human presentation contract for new tasks.
- Target files.
- Forbidden files.
- Acceptance tests.
- Commands.
- Known risks.
- Isolation strategy.
- Preferred implementation tool.
- Review tool.
- Verification scope.

## Routing Metadata

`risk` selects impact evidence, approval, and review gates. It does not select a model or reasoning
profile. The task zone and the derived complexity band select the executor's execution profile
(`.ai/project/routing-taxonomy.md`). A high-impact task may impose a review-depth floor on the
required independent review, but never upgrades the executor's complexity band.

The `routing_*` contract fields are **optional**. Because the task-contract YAML subset is top-level
only, the routing vector is expressed as flat keys — `routing_policy_version`, `routing_zone`,
`routing_axes` (one `<axis>=<level>` entry per declared axis), the optional
`routing_complexity_band`, and the selected-profile group `routing_profile_tool`,
`routing_profile`, `routing_reasoning_level`, `routing_provenance`, `routing_rationale`.
`preferred_tool` and `review_tool` stay canonical; routing metadata describes and explains an
assignment, it never replaces one.

**Compatibility rule.** A contract declaring no `routing_`-prefixed key is legacy: it stays fully
readable, and no zone, axis, band, or profile is guessed on its behalf. The whole `routing_` prefix
is reserved, and presence is decided by key membership rather than by a value being non-empty, so a
misspelled key (`routing_zome`) or a key left blank activates strict validation instead of passing
as silence. Declaring any key makes the core vector required; declaring any selected-profile key
makes that whole group required. Any unknown, missing, duplicated, or malformed value fails closed
with the declared vocabulary named in the error, and a recorded `routing_complexity_band` that
disagrees with its own vector fails closed rather than outranking the derivation.

A task contract must declare each top-level key exactly once. The contract parser keeps the last
occurrence silently, so a duplicated key could override a reviewed value invisibly; the contract
gate rejects duplicates before any semantic validation.

**The selected profile explains an assignment; it never makes one.** `routing_profile_tool` must
equal `preferred_tool` — a contract cannot assign one executor and select another's profile — and
the selected profile's effective capabilities (its tool's surface capabilities plus its own model
capabilities) must cover every capability its zone requires. That is hard-filter validation of a
declared vector against a declared catalog, not tool ranking.

## Verification Scope

`verification_scope` is part of the contract vocabulary. The core scope values are
`control-plane` (no build gate; docs/framework-only), `affected` (changed package owners only),
`affected-plus-neighbors` (owners plus direct workspace reverse dependents — the ordinary product
handoff gate), and `workspace` (every package; escalation or milestone tasks only). A registered
evidence gate may extend this vocabulary through its manifest; the effective set is the core values
plus every enabled gate's additions. A missing legacy scope defaults to `affected-plus-neighbors`;
an unknown value (outside the effective vocabulary) fails closed. `ai verify` runs the project's
registered evidence gate, or — with no gate registered — the control-plane contract check only,
exiting 0 for docs-only work without claiming build/test evidence. Any explicit gate command in a
contract routes through the gate's wrapped runner (for the Rust gate, `ai cargo`); raw gate output
is never canonical evidence. Semantics: `.ai/rules/rust-verification.md`; the generic substrate and
extension registry: `scripts/verify_core.py` and `scripts/extension_registry.py`.
## Versioned Task Artifacts

New executor/QA receipts, evidence sets, and closeouts follow
`.ai/project/task-evidence-schema.md`. Receipt events are immutable: preserve every attempt and round,
and never overwrite an event present at the selected base. `ai verify` fails closed on a new or
changed untyped receipt. Historical receipts remain byte-preserved behind the exact legacy baseline
and are labelled `legacy-untyped-context`; their free text is never promoted into typed findings.
A versioned task cannot pass the merge gate without a complete `task-closeout.yaml` fold.
