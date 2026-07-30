---
trigger: always_on
description: "Rule: Rust Verification. Rust verification is selective, serialized, and evidence-led. The versioned evidence record in"
---
<!-- GENERATED FROM .ai/. DO NOT EDIT DIRECTLY. Run `ai sync`. -->

# Rule: Rust Verification

Rust verification is selective, serialized, and evidence-led. The versioned evidence record in
the task folder is the canonical gate; raw Cargo output is diagnostic only, never canonical
evidence.

## Generic substrate and the reference gate

Verification is split along a stack boundary. The reusable **substrate** (`scripts/verify_core.py`,
maw_core) owns the coordination primitives every evidence gate builds on — the cross-platform
advisory lock with heartbeat and holder metadata, the atomic versioned JSON evidence-record
writer, the argv-array-only run/git/process seams, and the fail-closed `VerifyError` taxonomy. The
sibling **`scripts/verify_contract.py`** layers the control-plane contract check (with generated-
file manifest exemptions), git change-set discovery, and the no-gate degradation path on top,
importing the substrate one-directionally. Both contain no language, stack, or vendor policy and no
runtime dependency on the tool roster (the tool-aware prompt-pair exemption is injected, not
imported). This rule describes the reference **`rust` gate** (`scripts/rust_verify.py`): Cargo
selection, escalation, and command construction composed onto that substrate. The substrate never
imports the gate.

## Registry, resolution, and degradation

Evidence gates are registered capabilities, not hardcoded commands. A gate is enabled only by an
explicit entry in `.ai/config.yaml` `extensions.enabled`; its manifest
(`scripts/extensions/<id>/extension.json`) declares its scope vocabulary, entrypoint, executables,
and read/write authority. `ai verify` resolves the project's single enabled gate through the
registry **generically — by config, never by a hardcoded id** (a custom non-Rust gate is resolved
the same way; more than one enabled gate fails closed). There is no import-time side effect or
ambient discovery, and the required `extensions` block fails closed when missing or invalid with a
named reason — the zero-extension form is an explicit `enabled: []`, and no default roster or scope
vocabulary is ever substituted.

**Degradation cannot mask a stack change.** With no gate registered, `ai verify` degrades to the
control-plane contract check, but first validates the task's `verification_scope`: an unknown scope
fails closed (`unknown-scope`), and any non-`control-plane` scope fails closed
(`missing-evidence-gate`) because that scope asserts a build gate is required. Only an explicit,
valid `control-plane` scope proceeds; it states plainly that no build/test evidence was produced
and exits 0 only for that docs/framework-only case.

**Execution authority is honest about in-process Python.** Only `gate` and `command` capabilities
execute, and those entrypoints are TRUSTED first-party code enabled solely by explicit owner
config — not sandboxed untrusted plugins. The registry keeps manifests as pure data, fails closed
on every declarative violation (unknown api version, duplicate id, missing dependency, conflict,
dependency/ordering cycle, capability-field spill, undeclared command executable, unsupported
platform, out-of-authority root, a config value mismatching its declared type token), and
path-contains each entrypoint to its declared root using resolved paths (no `..`/symlink escape). A
gate's `cmd_verify` is checked for the `(args, *, root, ai)` signature BEFORE `ai verify` routes to
it (a wrong-arity or wrong-keyword gate fails closed with `invalid-gate-interface`, not a late
`TypeError`), and a registered command's declared args are its complete invocation convention:
caller argv is rejected (`unexpected-command-arguments`), typed `{config:<key>}` values are expanded
from the extension's effective configuration, and the declared input/output/write authority is
re-checked from the resolved manifest at dispatch. It does not — and in-process cannot — jail a
trusted entrypoint's runtime authority; `read_roots`/`write_roots` are declared authority for audit,
not a runtime sandbox. A future task may add real OS/subprocess isolation if untrusted gates are
ever required.

## Verification scope

Every task contract carries `verification_scope`:

- `control-plane`: no Rust build; docs/framework-only work.
- `affected`: changed package owners only.
- `affected-plus-neighbors`: owners plus their direct workspace reverse dependents across
  normal, build, and dev edges. This is the ordinary product handoff gate.
- `workspace`: every package. Reserved for escalation and milestone/integration tasks.

The four scope values above are the **core** vocabulary (owned by the substrate, not this gate); a
registered gate may add scope values through its manifest, and the effective vocabulary is the core
set plus every enabled gate's additions. A missing legacy scope defaults to
`affected-plus-neighbors`; an unknown value — one outside the effective vocabulary — fails closed.
Contract target/forbidden matching extracts every path token even when one list item contains
conjunction prose. Exact extensionless repository-root paths are valid. A single `*` or `?` is
segment-local and never crosses `/`; only explicit `**` is recursive.

## Selection and escalation

Selection is derived from `cargo metadata --no-deps`; never maintain a crate table by hand.
Escalate to `workspace` with a recorded reason on: root workspace manifest/lock/toolchain/
Cargo-config change, membership/profile/resolver change, crate add/remove, an unknown
Rust-relevant path, or an affected set reaching at least half the workspace. The half-workspace
reason is recorded only when that escalation rule fires; an explicitly declared `workspace` scope
records no synthetic escalation. Metadata failure fails closed instead of escalating (see Fail
closed): without metadata the workspace package
set cannot be derived honestly. Docs/control-plane-only changes run no Cargo.

## Command shape

Run mode executes, as argv arrays with no shell interpolation: package-scoped non-mutating
`cargo fmt ... -- --check` (no `--locked`), one strict clippy command with `--locked`, and one
test command with `--locked`, all scoped to the selected packages. Task-specific topology,
adversarial, platform, hardware, and preview commands stay explicit in task contracts; the
generic verifier never claims them.

## One lock

All run-mode verification, `ai cargo` invocations, and cache mutations serialize through one
cross-platform advisory lock in the OS temporary directory, keyed by SHA-256 of the canonical
repository root (msvcrt on Windows, fcntl on POSIX). Plan mode is lock-free; waiters see holder
metadata and heartbeat.

## Evidence is authority

Run mode emits an atomic, versioned JSON evidence record in the task folder: repository-relative
paths, scope/packages/reasons, escalation, base/HEAD/diff identity, argv, UTC timing, duration,
exit status, toolchain, OS/arch, and lock identity. Toolchain probes contain captured versions or
the explicit `recorded-unavailable` marker, never ambiguous `unknown` placeholders. Never store a
checkout path in raw, slash-normalized, or JSON-escaped form. This record
satisfies the receipt gate; raw Cargo output cannot.

## The `ai cargo` wrapper

Every explicit Cargo gate runs through `ai cargo <task-id> --base <commit> -- <argv...>`. The
wrapper shares the verification lock, rejects `clean`, and appends exact argv and result to the
same task evidence. Never gate on a raw Cargo invocation.

## Cache lifecycle

Normal tasks reuse `project/target`. Isolated targets require explicit justification, live in
the OS temporary directory with a repository/schema marker, and are removed in a finally path.
`ai cargo-cache inspect` is read-only. `clean --scratch --yes` deletes only marker-proven
repository-owned temp roots. Every cleanup refuses with `process-listing-unavailable` when the
live-process listing cannot be obtained. `clean --workspace --yes` holds the lock, validates the
canonical manifest, refuses while another process owns the lock or a live unwrapped cargo/rustc
process is detected, and runs `cargo clean --manifest-path project/Cargo.toml` — never a hand-built
recursive deletion, never implicit.

## Fail closed

Unknown scope values, invalid metadata, unknown Rust-relevant paths, malformed or foreign cache
markers, unmarked cleanup targets, and missing or unlaunchable Cargo binaries all refuse rather
than guess. Launch failures are actionable `VerifyError` messages without tracebacks; when a run
has begun, its failed result and explicit unavailable toolchain identity are still evidenced.
