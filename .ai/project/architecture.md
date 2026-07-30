---
id: project-architecture
type: project-doc
domain: control-plane
status: active
owner: system
updated: 2026-07-24
---

# Architecture

The Agent Control Plane is file-based and tool-agnostic.

## Canonical Layout

```text
.ai/
  project/      project facts, commands, infrastructure, decisions
  agents/       canonical role prompts
  rules/        standing control-plane rules
  workflows/    repeatable lifecycle instructions
  skills/       optional reusable domain capabilities
  memory/       typed durable knowledge
  tasks/        queue, active, done, archive
  adapters/     generated prompt and adapter outputs
  migration/    audit and migration notes
  config.yaml   tool capability matrix and gates
  .local/       ignored checkout-local state; never canonical or generated
```

## Supported Catalog Versus Checkout Enablement

The committed `tools` mapping in `.ai/config.yaml` is the complete **supported** catalog and the
only authority for adapter rendering, task vocabulary, descriptor forms, registry composition, and
shared defaults. Catalog membership never implies local provider usability.

`.ai/.local/tools.json` is the separate **enabled** authority for one checkout. It is explicit user
choice only: absence means zero enabled tools. Its closed version-1 schema contains declared tool
IDs and four role defaults that must name enabled IDs. `.ai/.local/` is ignored, excluded from
`_manifest.json` and `_registry.json`, and never read by sync or generation. It cannot store
credentials, argv, executable/home paths, account identity, or billing state.

A configured descriptor reports only a transport form. A successful Codex deeplink opens and
prefills a composer; it does not submit the prompt. Submitted/running state exists only after the
user presses Send and a provider task actually starts.

Advisory detection is a third, non-authoritative evidence layer derived generically from those
descriptors. An exec detector asks only whether a fixed bare executable token resolves through
`PATH`/`PATHEXT`. A deeplink detector asks only whether a fixed URI scheme has one unambiguous
registration through a read-only platform adapter. Neither launches anything. Each observation is
structured as `present`, `absent`, `unknown`, or `error`; it never implies usability,
authentication, account/billing context, submission, or a running task.

The authority chain remains one-way: catalog support defines valid IDs and descriptors; explicit
local enablement permits implicit routing; explicit `--auto` plus the project gate may request a
launch. Detection can explain the environment but cannot write or advance any authority layer.

## Adapter Layout

- `AGENTS.md`: generated Codex/generic adapter.
- `GEMINI.md`: generated Antigravity project prompt.
- `CLAUDE.md` and `.claude/`: generated Claude Code adapter.
- `.agents/rules/`: generated Antigravity conditional rules.
- `.agents/skills/`: generated Antigravity conditional skills and skill resources.

Adapters should be stable and readable, but they are not canonical.

### Adapters are catalogs, not inlined content

The three root prompts (`AGENTS.md`, `GEMINI.md`, `CLAUDE.md`) are always loaded by
their tools, so their size is a per-session token cost. `ai sync` therefore emits a
**catalog** — one line per source file (title, one-line summary, path under `.ai/`) —
plus each adapter's own always-on core (the operating contract and its agent role),
inlined. Bodies of project docs, rules, workflows, and memory are **not** inlined; the
agent opens the specific file on demand when its summary matches the task. This is
progressive disclosure: the always-loaded prompt stays small (tens of lines, not
thousands) and the token budget goes to the few files a task actually needs. Do not
reintroduce full-file inlining into the root adapters.

## Task Layout

Tasks are folder-based:

```text
.ai/tasks/active/task_03_login_ui/
  task.yaml
  brief.md
  context.md
  prompt.codex.md
  prompt.claude.md
  prompt.antigravity.md
  receipt.executor.yaml
  receipt.qa.yaml
  diff.patch
  test-output.txt
  notes.md
```

## Control Flow

1. Research produces factual artifacts.
2. Planning converts intent and facts into task folders.
3. Dispatch writes tool-specific prompts and human handoff instructions.
4. Execution produces a diff and executor receipt.
5. Review/QA evaluates diff, receipt, contract, tests, and risk gates.
6. Learn captures durable knowledge into typed memory files.

## Python Control-Plane Architecture

The CLI executable (`scripts/ai_cli.py`) is a thin facade (line ceiling <= 400 lines) delegating control-plane domains to modular packages under `scripts/ai_plane/`:
- `constants.py`: Repository root, `.ai` pathing, minimum Python version.
- `utils.py`: Exit/error handling, directory setup, timestamping, slugification.
- `config.py`: Tool registry parsing, configuration loading, runtime state initialization.
- `manifest.py`: Atomic manifest generation, tracking, and checksum verification.
- `frontmatter.py`: YAML frontmatter parsing, document summarization, catalog rendering.
- `registry.py`: Indexing `.ai/` domain documents into `_registry.json`.
- `tasks.py`: Task contract parsing, vocabulary validation, lifecycle creation/showing/merging.
- `prompts.py`: Prompt assembly for executor, reviewer, planner, strategist roles.
- `sync.py`: Sync engine coordinating adapter rendering and neutral command token replacement.
- `doctor.py`: System environment and lock-inspector diagnostic checks.
- `tool_detection.py`: Descriptor-derived, injected PATH and read-only URI-handler advisory checks.
- `tool_profile.py`: Strict ignored-profile schema, atomic configuration, reporting, and command-time enablement gates.
- `blueprint.py`: PR blueprint artifact generation.

`scripts/tests/` mirrors control-plane responsibilities across modular test suites (`test_ai_cli.py`, `test_ai_tools.py`, `test_ai_doctor.py`, `test_ai_contracts.py`, `test_ai_adapters.py`, `test_ai_registry.py`, `test_ai_rust_verify.py`, `test_ai_extensions.py`, `test_ai_gate_resolution.py`, `test_ai_maw_core_boundary.py`, `test_ai_architecture.py`).
Line-count ceilings (`ai_cli.py` <= 400 lines, `ai_plane/*.py` <= 600 lines, `scripts/tests/test_ai_*.py` <= 700 lines) and import graph acyclicity (no `ai_plane` module importing `ai_cli`) are enforced automatically in `test_ai_architecture.py`.

## Reusable Core and Extension Capabilities

Verification is split along a stack boundary so the reusable core can be extracted
without dragging language/stack policy with it:

- `scripts/verify_core.py` (**maw_core substrate**) owns the stack-agnostic coordination
  primitives: the cross-platform advisory lock with heartbeat and holder metadata, the atomic
  versioned JSON evidence-record writer, the argv-array-only run/git/process seams, the fail-closed
  `VerifyError` taxonomy, and repository-relative identity helpers. `scripts/verify_contract.py`
  layers control-plane contract checking (target/forbidden globs + generated-file manifest
  exemptions), git change-set discovery, and the no-gate control-plane degradation path on top of
  it; it imports the substrate one-directionally and the config-free `scripts/ai_plane/primitives.py`
  (never config/prompts/dispatch), so `import scripts.verify_core`/`verify_contract` pulls no tool
  or vendor graph (enforced by a transitive import-graph test) and neither imports an extension.
- `scripts/extension_registry.py` (**maw_core registry**) composes four capability types —
  `integration` (declarative agent-adapter — a render descriptor + templates that drive adapter
  generation), `pack` (declarative content — rules/workflows/skills/templates, scope
  vocabulary, cross-pack defaults, and relations), `gate` (executable evidence gate), and
  `command` (deterministic CLI subcommand). Each extension ships a versioned manifest (`scripts/extensions/<id>/extension.json`)
  declaring capabilities, dependencies, conflicts, composition order, scope vocabulary, and
  read/write/executable authority. Manifest fields are gated by capability type (a `pack` cannot
  spill a `command`), enablement is explicit-config only (a required `extensions` block; missing or
  invalid fails closed; the zero-extension form is `enabled: []`), and composition is deterministic
  `(priority, id)` refined by before/after and dependency edges. It fails closed with a named reason
  on duplicate ids, unsupported api versions, missing dependencies, conflicts, dependency/ordering
  cycles, unresolved constraints, capability spill, undeclared command executables, unsupported host
  platform, an entrypoint that resolves outside its declared root, a config value that mismatches
  its declared type token, a gate `cmd_verify` whose signature is not `(args, *, root, ai)`, a
  command input/output/caller-argv/write target outside its declared authority, a pack content id that
  collides across packs with no resolving replace, a content kind inconsistent with its frontmatter type,
  a cross-pack defaults conflict, or a relation that is malformed, targets a contribution no pack
  supplies, or is ambiguous. Execution authority is honest:
  gate/command entrypoints are TRUSTED first-party code enabled only by owner config and
  path-contained, not sandboxed untrusted plugins; read/write roots are declared authority for
  audit, not a runtime jail.
- `scripts/rust_verify.py` is the reference **`rust` gate**: Cargo selection, escalation, and
  command construction composed onto the substrate. `ai verify` resolves the project's single
  enabled gate generically by config (not a hardcoded id; a custom gate resolves identically, and
  >1 gate fails closed), replacing the former hardcoded loader. With no gate registered it degrades
  to a control-plane check that **classifies the actual changed paths** (never trusting the scope
  label): any product/stack/unclassifiable path, mixed change, or non-control-plane/unknown scope
  fails closed with a named `missing-evidence-gate` reason, so it cannot mask a stack change; only a
  change set entirely within the control-plane surface AND declaring control-plane scope exits 0.

All four capability types have a live consumer: a **gate** is resolved and routed by `ai verify`
(its cmd_verify presence AND `(args, *, root, ai)` signature validated before routing); a
**pack**/**integration** declares `contributes.files`, and `ai sync` — after resolving and
validating the whole composition fail-closed (a missing/invalid roster or duplicate destination
aborts before any write) — copies them into their generated destinations through the hash manifest
so **enabling adds them and disabling prunes them** (the manifest-safe enable/disable round trip). A
**pack** additionally composes DECLARATIVE CONTENT beyond raw copies: `contributes.content`
declares `{kind, from}`, and rules/workflows/skills are identified by their frontmatter `id`, indexed
into the generated `_registry.json` (participating in the registry, tagged with their pack origin) as
well as materialized, while `templates` are materialized with any `{default:<key>}` placeholder resolved
from the cross-pack **composed defaults** (every enabled pack's `defaults` merged into one namespace,
where a differing value is resolved by an explicit `before`/`after` precedence and only a genuinely
unordered clash fails closed). Declared **relations** (`replace`/`prepend`/`append`/`wrap`, ordered by
`before`/`after`) compose two packs contributing the same content `id` or defaults key deterministically
— a duplicate id with no resolving `replace`, an unordered relation race, or a relation targeting a
contribution no pack supplies fails closed — and the whole content/default/relation composition is
resolved and validated before any generation transaction, so a failure leaves generated files and
`_manifest.json` byte-identical. A
**command** is invokable via `ai ext run <name>` as an argv array with the shell disabled — its
declared args (with typed `{config:<key>}` values expanded from the extension's effective
configuration, defaults overlaid by project `extensions.config`) are the complete invocation
convention, so caller argv is rejected and the declared input/output/write authority is re-checked
at dispatch. `ai ext list` explains the resolved composition — order, scopes, commands, each
contribution's file origin, and the origin and relation precedence of every effective content and
default.

Adapter rendering is integration-driven: `scripts/ai_plane/adapter_render.py` is a
generic, vendor-free renderer that interprets an `integration` render descriptor (agent-adapter
format identity, neutral command-token mapping, detect marker, argument/invocation convention,
and an ordered list of render artifacts — marker/support/command/
settings documents and rules/skills trees — with prose supplied as contributed templates). A tool
selects its descriptor through `tools.<name>.adapter.render_source`: an omitted/null value renders
the vendor-free core neutral descriptor, and a non-empty value must resolve to exactly one enabled
integration or fail closed with a named `missing-render-source` reason (it never falls back to
neutral). `cmd_sync` renders the COMPLETE plan — every adapter, contribution, and index — to unique
`(path, bytes)` entries and collision-checks it BEFORE any filesystem mutation or manifest
transaction, so a render/roster/collision failure leaves generated files and `_manifest.json`
byte-identical. `config.py` and `sync.py` therefore own no tool roster and no per-vendor
adapter-format branch; the reference codex/claude/antigravity adapters ship as integrations under
`scripts/extensions/`, and their generated output is proven byte-identical to the pre-migration
renderer by a raw `git archive` A/B (`scripts/tests/test_ai_adapter_ab.py`).

Scope note: the **verification** substrate (verify_core/verify_contract/extension_registry) is
context-agnostic and cleanly extractable today. A later pass hardened the capability interfaces behind
the registry — typed `config_schema` value validation with a consumed configuration path (typed
`{config:<key>}` argv expansion), gate `cmd_verify` signature inspection before routing, and the
command input/output/caller-argv/write-authority contract enforced at manifest AND dispatch time.
**Pack content, defaults, and relations** are then consumed behind the registry:
`contributes.content` rules/workflows/skills indexed into the registry, `templates` materialized with
`{default:<key>}` resolved from the cross-pack composed defaults, and `replace`/`prepend`/`append`/`wrap`
plus before/after relations composing shared destinations deterministically — the whole composition
resolved and validated fail-closed before any generation transaction.

`test_ai_maw_core_boundary.py` enforces that the core modules never import an extension, a
tool/vendor/language module, or the ai_cli facade (statically AND transitively), and that the
evidence writer plus the full selection/escalation/contract orchestration are behavior-identical
across the split (both compared against the pinned dispatch base). `test_ai_ext_consumers.py`
proves the pack/integration round trip and the command dispatcher.
