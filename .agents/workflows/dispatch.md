<!-- GENERATED FROM .ai/. DO NOT EDIT DIRECTLY. Run `ai sync`. -->

# Workflow: Dispatch

Dispatch creates clear handoff instructions for a separate application.

Dispatch does not imply fixed tool phases. The selected tool may be doing research, implementation, review, QA, or planning support when the task contract says so.

For every dispatch command, generate:

- Prompt for the selected tool.
- Expected working directory as repository-root-relative `.`; never capture the current checkout's
  absolute Windows, macOS, Linux, or tool-managed worktree path.
- Selected isolation strategy.
- Whether the tool should use main workspace, branch, patch mode, shadow copy, worktree, readonly research, or manual mode.
- Files the agent may edit.
- Files the agent must not edit.
- Commands to run.
- Required receipt format.
- Handoff instructions for the next stage.

The prompt is always rendered and written to disk first, whether or not the automatic lane runs.
Manual app switching remains the default: the CLI does not require opening external applications.

When `--tool` is omitted, dispatch preserves the task's `preferred_tool` exactly and requires local
enablement; it never substitutes another enabled tool. Explicit `--tool` authorizes one manual
prompt only and neither updates the profile nor authorizes launch. Review preserves the task's
`review_tool` under the same no-substitution rule.

## Advisory Detection

`ai tools status --detect` and doctor may report descriptor-derived environmental hints before a
handoff. Detection observes only a fixed exec `argv[0]` through `PATH`/`PATHEXT`, or a fixed URI
scheme through a contained read-only platform registry adapter. It never invokes an executable,
opens a URI, contacts a network, reads credentials/account/billing state, or changes the local
profile.

The four detection states (`present`, `absent`, `unknown`, `error`) are diagnostics only. They do
not enable a tool, authorize `--auto`, alter role defaults, select a fallback, prove submission, or
prove a running task. Unsupported platforms, ambiguous URI registration, non-fixed descriptor
tokens, and tools without a descriptor stay `unknown` with a reason.

If the configured reviewer is not enabled, report enabled alternatives and the existing
owner-waiver law, but substitute nothing. An owner-authorized substitute must be disclosed in the
handoff and receipt; a same-family substitute is not independent unless the owner explicitly
waives that merge gate.

## Automatic Dispatch Lane (optional, off by default)

`ai dispatch <task_id> --auto` renders the prompt exactly as the manual lane, then additionally
launches the assigned tool via an optional per-tool `dispatch` descriptor in `.ai/config.yaml`
(`tools.<name>.dispatch`): an `exec` form (an argv-array template) and/or a `deeplink` form (a
URL template with parameter encoding). A tool without a descriptor simply has no auto lane.

Safety invariants:

- The lane is **off by default**; launch requires `defaults.auto_dispatch: true`, exact local enablement, and explicit `--auto`.
  A `present` detection result satisfies none of these gates.
  Manual `--tool` does not grant launch authority, and enablement failure occurs before launch.
- The manual lane's own output stays byte-identical to today's — no `--auto` flag, no change.
- Launching uses **argv arrays only, never shell string interpolation**; deeplink parameters are
  percent-encoded. Only a fixed, known placeholder set (`{task_id}`, `{tool}`, `{prompt_path}`,
  `{prompt_text}`, `{prompt_encoded}`, `{prompt_path_encoded}`) may appear in a descriptor
  template; config loading fails closed on any other token or an unbalanced brace.
- A launch failure or absent tool binary produces success-shaped guidance falling back to the
  manual handoff — never a lost dispatch, because the prompt files are always written first.
- Auto-dispatch never bypasses a gate: it only changes how the executor's session is opened, not
  what the contract, receipts, or review require.
- A deeplink transports but does not submit a prompt. Codex deeplinks prefill a new-chat composer;
  the user must press Send. `auto-deeplink` success is not a submitted or running task.
- Each `--auto` attempt writes `dispatch-record.yaml` in the task folder naming the lane used
  (`manual`, `auto-exec`, or `auto-deeplink`), whether it succeeded, and the argv or URL involved.
  This is task evidence, not a generated/manifest-tracked file.
