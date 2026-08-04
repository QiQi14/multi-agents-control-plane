---
id: rule-visual-evidence
type: rule
domain: control-plane
status: active
owner: system
---

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
inspection/coverage, and accessibility text.

### A file on disk is not evidence

`evidence.yaml` is the evidence. The `evidence/` directory is a byproduct of producing it. A file
sitting there that no item declares proves nothing, is visible to nobody, and costs the repository
its bytes — while looking, in a directory listing, exactly like diligence.

This is the most common way a task ships with no evidence at all. Three real tasks captured 31, 23,
and 24 images between them and declared 1, 1, and 0. Every gate passed. The reader showed "No
verified media is available" on all three, and the reviewer who asked why went looking for a bug in
the reader — twice — because the rule pointed nowhere else.

**Decide before you write the file:**

- **A human must look at it to accept the task.** Then it has to be *visible*, which means declared
  and committed. A `regenerable` item is invisible to everyone who has not run the command
  themselves, so it cannot carry an acceptance line that depends on looking.
- **Nobody needs to look.** Then do not write the file. Record the command as `regenerable` and
  stop. Writing an image you do not declare is strictly worse than not producing it.

Never the third thing: producing files and declaring none of them.

### What makes an artifact reach a human

The reader shows an item under *Representative media* only when all four hold. Fewer than four and
it renders nothing — not an error, just an empty panel, which is why this fails silently:

1. `availability: available`
2. `storage: committed` — the bytes are in git
3. `artifact.media_type` is `image/*`, `audio/*`, or `video/*`, with `artifact.path` and
   `artifact.sha256`
4. The recorded path resolves to a real file inside the repository (lifecycle moves are handled;
   a wrong or deleted path is not)

Give every one an `accessibility_text`: it is the alt text, and the only description a reviewer on a
text-only surface will ever get.

### The sentence this replaces

This rule used to say a regenerable preview "needs no committed PNG when the exact command and
availability are present". That is true of *validation* and false of *review*: it answers whether an
item is well-formed, not whether anyone can see it. Read as guidance about what to produce — which
is how an executor reads a rule — it says record commands and skip the artifact, and then the
acceptance line that says "look at this" has nothing to look at. Both halves are needed: the command
makes the image reproducible, the declared committed artifact makes it visible.
