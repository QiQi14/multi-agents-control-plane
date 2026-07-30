---
id: project-commands
type: project-doc
domain: control-plane
status: active
owner: system
updated: 2026-07-24
---

# Commands

Use the unified CLI from the repository root. The supported-tool roster, command spellings, adapter paths, formats, and shared defaults come only from `.ai/config.yaml`; roster-consuming commands fail closed when that file is missing, unreadable, or invalid.
Canonical command examples use neutral tokens and `__AI_COMMAND_SYNC__` resolves them while generating each adapter.

```bash
__AI_COMMAND_INIT__
__AI_COMMAND_SYNC__

__AI_COMMAND_AUDIT_FRAMEWORK__
python scripts/ai_cli.py tools list
python scripts/ai_cli.py tools status --detect
python scripts/ai_cli.py tools configure --enable codex --default-tool codex

__AI_COMMAND_FEATURE__ new "Auth login/register/password reset"
__AI_COMMAND_RESEARCH__ auth --tool antigravity
__AI_COMMAND_PLAN__ auth

__AI_COMMAND_TASKS__
__AI_COMMAND_TASK__ show task_03_login_ui
__AI_COMMAND_DISPATCH__ task_03_login_ui --tool codex
__AI_COMMAND_DISPATCH__ task_04_dependency_map --tool antigravity
__AI_COMMAND_DISPATCH__ task_03_login_ui --tool codex --auto
__AI_COMMAND_REVIEW__ task_03_login_ui --tool claude
__AI_COMMAND_QA__ task_03_login_ui
__AI_COMMAND_MERGE__ task_03_login_ui
__AI_COMMAND_LEARN__ task_03_login_ui
__AI_COMMAND_ARCHIVE__ auth

__AI_COMMAND_BLUEPRINT__ init "Auth Flow" --preset backend
__AI_COMMAND_BLUEPRINT__ init --from-task task_07_payment_retry_backoff --base HEAD
__AI_COMMAND_BLUEPRINT__ build docs/blueprints/auth_flow.spec.md

python scripts/ai_cli.py docs build
python scripts/ai_cli.py docs lint
python scripts/ai_cli.py docs search "auth"
python scripts/ai_cli.py docs stats
python scripts/ai_cli.py docs graph
```

## Blast-Radius Advisory

`ai impact` is an optional thin bridge to the already-built `tools/ai-impact` binary:

```bash
python scripts/ai_cli.py impact exact::qualified::symbol
python scripts/ai_cli.py impact exact::qualified::symbol --manifest project/Cargo.toml --database .ai/.local/ai-impact.sqlite --content-audit
```

The default database is `.ai/.local/ai-impact.sqlite`. The bridge never builds the binary, creates
an index, discovers changed files, interprets graph results, runs tests, or expands task scope. A
missing binary/index or failed launch/query exits 0 with the concrete build, initialize, refresh,
or retry action. Successful output is passed through unchanged, including its staleness banner,
complete blast radius, explicit no-covering-tests warning, or advisory-silent reason.

This optional blast-radius step is ADVISORY ONLY and must NEVER auto-expand `target_files`.
Planners and reviewers may use a complete exact-symbol answer as context; they retain all scope,
verification, finding, and acceptance authority. Missing, ambiguous, partial, stale, or unavailable
advice is silence, never a workflow failure.

## Supported Tools and Local Enablement

`.ai/config.yaml` is the committed catalog of tools this repository **supports**. It does not claim
that a public clone can use any provider. Each checkout records explicit user choice in
`.ai/.local/tools.json` through `ai tools configure`; that versioned JSON is Git-ignored,
noncanonical, and contains enabled tool IDs, role defaults, logical-profile selections, and
normalized integration-owned catalog snapshots. It contains no
credentials, executable arguments, paths, account data, or billing data.

The same boundary applies to execution profiles. `.ai/config.yaml` declares stable logical
profiles and reasoning levels a tool **supports** (`.ai/project/routing-taxonomy.md`), never
exact model inventory. It never claims a profile
is installed, enabled, authenticated, paid for, or usable in this checkout. A tool may declare no
profile at all, in which case its model and reasoning profile are reported as *undeclared* rather
than guessed. Local profile availability is a separate, explicit, checkout-local concern.

With no local profile, `ai tools list` reports zero enabled tools. Deterministic and read-only
commands still work. Commands needing an implicit provider fail with `tool-profile-required`; a
configured but disabled task tool fails with `tool-not-enabled`. Configuration is deterministic
and noninteractive; use explicit flags:

```bash
python scripts/ai_cli.py tools configure --enable codex --default-tool codex
python scripts/ai_cli.py tools configure --enable codex --enable claude --research-tool codex --planning-tool codex --implementation-tool codex --review-tool claude
python scripts/ai_cli.py tools configure --reset
```

An explicit `--tool` is a one-command manual prompt choice. It does not enable that tool or
authorize `--auto`. Local state never changes sync, adapters, registry, manifest, task vocabulary,
or committed defaults.

Use `python scripts/ai_cli.py route explain <task_id>` for deterministic read-only human output,
or add `--json` for stable machine output. Version-1 profiles resolve to unknown catalog state and a
symbolic selector; exact identities come only from ignored normalized catalog observations.

Task-creating `research`, `plan`, and `feature new` commands never infer routing from titles or
brief text. Supply the complete explicit set (`--tool`, `--review-tool`, `--routing-zone`,
`--routing-rationale TEXT`, and one `--routing-axis AXIS=LEVEL` for every declared axis), or omit
all of it: the command writes
`preferred_tool: pending` and `review_tool: pending` with exact explain/apply guidance. Partial
routing input fails without creating a partial assignment.

`python scripts/ai_cli.py route explain <task_id>` and ordinary
`python scripts/ai_cli.py route apply <task_id>` evaluate identical assignment constraints for the
same contract. Apply atomically fills only unset or `pending` router-owned executor, profile,
reasoning, reviewer, policy,
rationale, and availability-provenance fields. A second application is byte-idempotent. It never
launches, submits, detects, enables, probes, or dispatches. Existing owner/planner assignments are
hard constraints. Replacing them requires the separately named `--replace --reconciliation TEXT`
path, which writes round-trip-safe owner prose plus the previous executor, reviewer, profile, and
reasoning values into the task contract. `--reconciliation` without `--replace` is invalid.

## Advisory Provider Detection

`python scripts/ai_cli.py tools status --detect` performs an explicit, read-only environmental
check. Results are `present`, `absent`, `unknown`, or `error`, each with a detector kind and reason:

- `exec` descriptors inspect only whether their fixed bare `argv[0]` resolves through
  `PATH`/`PATHEXT`. Other arguments are never evaluated and the executable is never started.
- `deeplink` descriptors inspect only a fixed URI scheme through a contained, read-only Windows
  registry adapter. Unsupported platforms and ambiguous registrations are `unknown`; no URI opens.
- Tools without a descriptor are `unknown: no configured transport`.

Detection is a hint, never consent or routing authority. It does not write the local profile,
enable a tool, select a default, reassign a task, access credentials/accounts/billing, contact a
network, prove authentication or quota, launch a provider, submit a prompt, or prove a task is
running. A present-but-disabled tool remains ineligible for implicit routing and `--auto`; an
enabled manual-only tool remains available for manual handoff.

Doctor reports the same evidence layers as supported, enabled, detected, launch-attempted, and
submitted/running. A missing configured reviewer lists enabled alternatives but substitutes
nothing. Only the owner can authorize a disclosed fresh substitute; a same-family substitute does
not satisfy independent review unless the owner explicitly waives that merge gate.

## Blueprint Task Extraction

`__AI_COMMAND_BLUEPRINT__ init --from-task <task-id> [--base <commit>]` resolves an exact task across
queue, active, and done, then deterministically pre-fills Overview, QA, Risks, Execution Summary,
Decisions, and File Inventory from task/receipt/Git facts. It invokes Git only with argv arrays for
read-only name inventories, never calls a model or network, never mutates source task records, and
never overwrites an existing spec without `--force`. Missing optional sources produce explicit
needs-judgment markers and notes.

## Blueprint Reports

`__AI_COMMAND_BLUEPRINT__ build` is self-contained and offline. For Mermaid sections it uses only an
exact `mmdc` 11.16.0 already on `PATH`; safe output becomes deterministic inline SVG. Every absent,
incompatible, failed, timed-out, or unsafe CLI outcome is a visible warning and falls back to the
hash-verified vendored `mermaid` 11.16.0 runtime once per report. Missing or corrupt vendor bytes
remain inert source-only. The command never installs Node packages, invokes `npx`, contacts a CDN,
or performs a build-time network request. `__AI_COMMAND_SYNC__` copies the canonical template to
`.agents/skills/pr-blueprint/template/`.

## Brief-Only Kickoff

When you want to provide only the requirement and let the agents split the work:

```bash
__AI_COMMAND_FEATURE__ new "Add team billing with invoices, seat limits, and admin UI"
__AI_COMMAND_PLAN__ team-billing
__AI_COMMAND_TASKS__
```

Then dispatch the generated task contracts:

```bash
__AI_COMMAND_DISPATCH__ task_03_billing_api --tool antigravity
__AI_COMMAND_DISPATCH__ task_04_billing_ui --tool codex
__AI_COMMAND_REVIEW__ task_04_billing_ui --tool claude
```

The first implementation of the CLI focuses on safe file generation and handoff prompts. It does not try to automate opening every external application.

## Generated-File Manifest

`ai sync`, `ai dispatch`, and `ai review` record every generated output in `.ai/_manifest.json`
with its repository-relative path, SHA-256, and exact generating command. Manifest writes are
deterministic and atomic. `ai sync` replaces its own entries and prunes stale sync outputs while
preserving dispatch/review history; the manifest never lists itself.

Generation is customization-safe. If an existing generated path no longer matches its recorded
hash, the CLI preserves the file and warns instead of overwriting it. Review the warning and
reconcile the customization deliberately before expecting the path to regain manifest authority.

## Automatic Dispatch Lane

`--auto` on `__AI_COMMAND_DISPATCH__` renders the prompt exactly as the manual lane, then also
launches the assigned tool via an optional per-tool descriptor in `.ai/config.yaml`
(`tools.<name>.dispatch`, an argv-array `exec` form and/or a percent-encoded `deeplink` form). It
is off by default: launch requires `defaults.auto_dispatch: true`, exact local enablement, and explicit `--auto`; the selected tool must
carry a descriptor, or `--auto` falls back to the same manual guidance with an explanatory message
— it never overrides the project gate and never loses a dispatch, since the prompt files are
always written first. Each `--auto` attempt records which lane it used in the task folder's
`dispatch-record.yaml`. See `.ai/workflows/dispatch.md` for the full descriptor schema, the known
placeholder set, and the safety invariants.


A Codex deeplink opens a new chat and prefills the composer, but does **not** submit the prompt:
the user must press Send. `auto-deeplink` is transport evidence, never evidence that a task started
or is running.

## Verification and Cargo Cache

```bash
__AI_COMMAND_VERIFY__ <task-id> --base <commit> --plan   # show diff scope and any evidence-gate selection; no build, no lock
__AI_COMMAND_VERIFY__ <task-id> --base <commit> --run    # resolve the registered evidence gate and run it as the contract gate
__AI_COMMAND_CARGO__ <task-id> --base <commit> [--label <evidence-label>] -- <cargo-subcommand argv...>
__AI_COMMAND_CARGO_CACHE__ inspect                       # read-only cache, scratch-root, and lock report
__AI_COMMAND_CARGO_CACHE__ clean --scratch --yes         # delete only marker-proven repository-owned temp targets
__AI_COMMAND_CARGO_CACHE__ clean --workspace --yes       # locked cargo clean against the canonical manifest
```

`__AI_COMMAND_VERIFY__ --run` resolves the project's registered evidence gate through the extension
registry (`.ai/config.yaml` `extensions.enabled`, manifests under `scripts/extensions/`). With no
gate registered it degrades to the control-plane contract check, prints that no build/test evidence
was produced, and exits 0 for docs-only work. `__AI_COMMAND_CARGO__` and `__AI_COMMAND_CARGO_CACHE__`
are the Rust gate's wrapped commands and require the `rust` extension enabled; `__AI_COMMAND_CARGO__`
runs under the shared verification lock, rejects `clean`, and appends exact argv and result to the
task's evidence record. See `.ai/rules/rust-verification.md` and the reusable-core/extension section
of `.ai/project/architecture.md`.

## Extensions

```bash
python scripts/ai_cli.py ext list              # explain the resolved extension composition (order, scopes, commands, capabilities, origins)
python scripts/ai_cli.py ext run <name>        # run a registered command capability via argv (shell disabled)
```

A command's declared `args` are its complete invocation convention (with typed `{config:<key>}`
values expanded from the extension's effective configuration); caller argv is rejected with
`unexpected-command-arguments`, and the command's declared input/output/write authority is
re-checked at dispatch.

Extensions are enabled only in `.ai/config.yaml` `extensions.enabled` (see the Extension registry
comment there). `ai sync` composes enabled **pack**/**integration** `contributes.files` into their
generated destinations through the hash manifest, so enabling an extension adds its generated output
and disabling it prunes that output on the next sync. **Gate** extensions are resolved by `ai verify`;
**command** extensions are invokable through `ai ext run`. See the reusable-core/extension section of
`.ai/project/architecture.md`.

## Environment Doctor

Run the read-only environment check from any repository directory:

```bash
python scripts/ai_cli.py doctor
```

`ai doctor` reports the supported Python version, Git discovery, task-state scaffold, authoritative
config parsing, local tool-profile state, registry and generated-file manifest freshness, queue
state, and verification-lock
holder/heartbeat state. Expected gaps such as stale generated files, an empty queue, or an idle/stale
lock remain exit `0` and print the exact follow-up command. Missing or unreadable scaffold state,
an invalid config, an unsupported interpreter, or missing Git exits nonzero. Doctor never repairs,
deletes, or rewrites repository state.

A missing, malformed, or catalog-stale profile is a WARN with exact `tools configure` remediation;
a valid profile is PASS. Doctor reads but never creates, repairs, resets, or mutates the profile.
`tools list` instead fails closed for a malformed or catalog-stale profile.

On Windows, `ai.cmd doctor` probes `python`, `py -3`, and `python3` by executing real Python code.
Candidates are resolved through `PATH`/`PATHEXT`; Microsoft Store App Execution Alias stubs and other
failed probes are skipped with a reason. If no candidate works, disable the aliases or install Python
3.10+, reopen the terminal, and run `python scripts/ai_cli.py doctor` explicitly.

## Compatibility

On Windows, use `ai.cmd` if the extensionless `ai` launcher is not executable in the current shell.
