---
trigger: always_on
description: "Rule: Diagnostic Isolation Before Remediation. Unknown-root-cause work begins with environment isolation and reproduction, not an assumed fix."
---
<!-- GENERATED FROM .ai/. DO NOT EDIT DIRECTLY. Run `ai sync`. -->

# Rule: Diagnostic Isolation Before Remediation

Unknown-root-cause work begins with environment isolation and reproduction, not an assumed fix.

This rule is about isolating the **failure environment and mechanism**. It is separate from the
Git/workspace isolation strategy in `.ai/rules/isolation.md`.

## Trigger

Apply this rule when any of the following is unknown:

- the root cause;
- the environment or mode that triggers the failure;
- whether the symptom is deterministic or intermittent;
- whether a crash, runaway resource, corruption, or performance regression belongs to product
  code, development tooling, build tooling, a dependency, or the host environment.

## Required Diagnostic Slice

Before a remediation task is authorized, create or complete a bounded diagnostic/reproduction
slice that records:

1. The original symptom signature and failure threshold.
2. The measurement method, baseline, observation window, sampling interval, and stopping condition.
3. An environment matrix containing the suspected failing case and at least one control when one is
   available. Relevant axes include development versus production, debug versus release, cold
   versus warm, tool server versus built artifact, feature flags, platform, and dependency version.
4. A minimal reproducer or the narrowest repeatable repository scenario available.
5. One-variable-at-a-time comparisons that distinguish the product from its surrounding toolchain.
6. A result classified as `reproduced`, `narrowed`, or `not-reproduced`, with raw observations and
   remaining uncertainty.

Do not combine an open-ended investigation and its assumed fix into one implementation contract.
When the boundary is still unknown, the next task is diagnostic research with
`readonly-research` or another non-mutating strategy.

## Remediation Authorization

Implementation may begin when evidence identifies a falsifiable boundary or mechanism: a failing
case, a control case, and a prediction the proposed change must satisfy. Exact causal certainty is
not required, but the task must state what observation would disprove its hypothesis.

An urgent mitigation may proceed with explicit owner authorization, but it must be named
`mitigation`, preserve the unresolved diagnosis, and must not close the original defect.

## Fix Acceptance

A fix is accepted only when:

- the original failing condition no longer reproduces for the declared observation window or
  stopping condition;
- the relevant control environments remain bounded and functional; and
- the receipt distinguishes symptom removal from incidental optimization.

A smaller starting footprint, lower average usage, or slower growth rate is not a fix when the
original resource still grows without a cap and eventually reaches OOM. Conversely, a high but
stable plateau is not evidence of an unbounded leak. Report these as different behaviors.

If the original condition cannot be reproduced, the task may report a narrowed or not-reproduced
diagnostic result, but it may not claim root cause or remediation.
