<!-- GENERATED FROM .ai/. DO NOT EDIT DIRECTLY. Run `ai sync`. -->

# Workflow: Review

Review is independent, feature-focused, and evidence-based.

## Steps

1. Read the task contract.
2. Read executor receipt.
3. Inspect the diff.
4. Verify scope against `target_files` and `forbidden_files`.
5. Optionally query `ai impact` for exact changed symbols that verify against the index and
   evaluate high-importance callers outside the declared scope as potential scope gaps.
   This optional blast-radius step is ADVISORY ONLY and must NEVER auto-expand `target_files`.
   A flagged caller is never an automatic finding, block, or gate; incomplete, ambiguous, stale, missing, or
   unavailable advice is omitted and never fails review.
6. Verify contract and acceptance criteria.
7. Verify tests and command evidence.
8. For a claimed fix to an unknown-cause failure, inspect the prerequisite diagnostic evidence and
   reproduce the original failing condition under the declared observation window. Revise if only
   the baseline or growth rate improved while the original failure still occurs.
9. If the task painted anything, render its preview variant and LOOK at it
   (`.ai/rules/visual-evidence.md`). Reading a painter does not prove what it draws.
10. Record issues and decision in `receipt.qa.yaml`.

Reviewers should bias toward catching subtle edge cases, missing negative paths, and accidental scope expansion.

Review the behavior and evidence declared by the task contract before broader concerns. A platform,
operating-system, abuse-case, or security concern outside that acceptance boundary is recorded once
as a concise nonblocking follow-up and review continues. It becomes blocking only when it directly
violates an in-scope acceptance criterion, crosses a destructive boundary owned by the task, or
demonstrates concrete data loss or corruption.

## Proving a regression test

A regression test that passes against the unfixed code proves nothing. Before accepting a fix,
revert it in isolation and confirm its test actually fails. This is cheap and it is what separates a
real pin from a tautology.
