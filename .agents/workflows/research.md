<!-- GENERATED FROM .ai/. DO NOT EDIT DIRECTLY. Run `ai sync`. -->

# Workflow: Research

Research is factual.

## Steps

1. Identify the question and repository area.
2. Choose the tool by context need and reasoning fit. Antigravity is preferred for broad repository mapping, but research is not Antigravity-only.
3. Use `readonly-research` unless mutation is explicitly required.
4. For an unknown root cause, apply `.ai/rules/diagnostic-isolation.md`: reproduce the original
   symptom, compare the suspected environment with a control, vary one axis at a time, and record
   the measurement window and stopping condition before recommending remediation.
5. Produce factual artifacts:
   - `research_digest.md`
   - `api_surface.md`
   - `dependency_map.md`
   - `risk_map.md`
5. Separate recommendations into planner notes.

## Artifact Rules

- Include exact files and symbols.
- Mark assumptions.
- Do not propose broad redesigns inside factual artifacts.
- Classify unknown-cause results as `reproduced`, `narrowed`, or `not-reproduced`. A lower baseline
  or slower failure is an observation, not proof that the original failure is fixed.
