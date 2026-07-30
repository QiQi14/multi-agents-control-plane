---
trigger: always_on
description: "Rule: Visual Evidence. A painted surface is not proven by tests, clippy, or code review. It is proven by looking at it."
---
<!-- GENERATED FROM .ai/. DO NOT EDIT DIRECTLY. Run `ai sync`. -->

# Rule: Visual Evidence

A painted surface is not proven by tests, clippy, or code review. It is proven by looking at it.

The project's registered headless preview harness is the only mechanism an executor or
reviewer has to prove visual work. Treat it as pipeline infrastructure, not a side artifact: it must
grow a variant for every painted feature and stay current as those surfaces change.

## Why this rule exists

One build console passed 226 tests, workspace clippy with `-D warnings`, and an adversarial
code review that specifically inspected clipping, layering, and geometry by reading. The first PNG
ever rendered showed the compact line's opaque panel overdrawing the status bar's own message,
cutting it mid-token with no ellipsis. Reading found two real defects; only looking found the third.

## Requirements

1. **Every task that changes a painted surface adds or refreshes a preview variant.** If the feature
   has no production trigger yet (a model landing before its first consumer), the harness is not
   optional — it is the ONLY route to visual evidence, because the surface is otherwise unreachable
   by launching the app and clicking.
2. **The variant must drive the real painters** on real state — a real `App`, a real model fed the
   way production feeds it (worker thread, real project). A hand-built struct passed to a painter
   proves the painter compiles, not that the feature renders.
3. **Reviewers must open the image.** A visual acceptance line is not satisfied by a passing test or
   by reading the painter. Attach the variant command to the receipt and look at what it produces.
   Then read the source behind anything that looks wrong: an image alone misleads too. That
   console's overdraw was invisible to reading; a "Spinner 12 / 16 / 24" row looks like a bug in the
   image and is correct in the source. Looking and reading cover different blind spots — do both.
   Verify a receipt's pixel claims by sampling yourself; a claim you did not reproduce is prose.
4. **Sample pixels for any color claim.** Eyeballing a rendered token is unreliable — surrounding
   surface tints will fool you. Assert against the theme token's actual hex.
5. **Render every theme the surface supports.** A token pairing that works in light can fail in dark.
6. **A preview run must never touch the real user profile.** Constructing an `App` outside
   `#[cfg(test)]` resolves `AppPaths::from_environment()`; route through `new_with_paths` with a
   scratch root (the X2 isolation law).

## Evidence hygiene

- **The command is the durable artifact.** Record the exact variant command in the receipt so any
  reviewer on any device can regenerate the image. An image pasted into a receipt is unverifiable;
  a command is reproducible. Verify the command actually reproduces the delivered artifact — a
  checked-in image that no longer regenerates is a claim, not evidence.

### Two artifact conventions — do not "clean up" one into the other

1. **Committed golden** (a checked-in reference image): tracked in git AND
   pinned by a hash test. Prefer this where a golden
   test exists: an unintended visual change then fails a test loudly *and* shows up as a reviewable
   image diff. A deliberate change updates the hash in the same commit.
2. **Gitignored regenerable preview** (the registered preview harness, matching
   `ui-*preview*.png`): never committed; regenerated on demand from the recorded command. Use where
   no golden test backs the image and binary churn would outweigh the benefit.

Neither is universal. Check which convention the crate already uses before adding or removing an
image. (This rule originally said "PNGs are gitignored, never commit them" — generalized from the
preview case alone. The review that found it would have condemned a committed golden
that is doing its job.)
- Visual evidence is environment-scoped like any other evidence — see `.ai/rules/qa-gates.md`. A PNG
  rendered on another device is a claim, not a proof; regenerate it on the review device.
- If a preview variant goes stale (a painter changes and the variant no longer compiles or no longer
  shows the feature), fixing it is in scope for the task that broke it — not a follow-up.

## Limits

A headless PNG proves **paint**, not input. Native pointer/wheel routing against a real window is a
separate, live check the harness cannot stand in for; do not record a headless pass as a live one.
## Typed Evidence Identity

For schema-versioned tasks, record visual evidence in `evidence.yaml`. Keep `generated-result`,
`expected-reference`, `golden`, and `comparison-diff` distinct, with storage convention,
availability, artifact identity, producer command/environment, claim or acceptance linkage,
inspection/coverage, and accessibility text. A regenerable preview needs no committed PNG when the
exact command and availability are present; do not render it as a missing committed artifact.
