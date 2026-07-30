---
id: rule-dependency-currency
type: rule
domain: control-plane
status: active
owner: system
---

# Rule: Dependency Currency

Agents must not pin dependency versions from training memory. Model training data
lags the ecosystem by months to years, so a "remembered" version is routinely
several majors stale (this is exactly how `wgpu 0.16` — three years old — entered
this repo). Versions are facts that must be verified against the live registry at
the time of the change.

## When This Rule Applies

Any time a task adds a dependency, bumps one, or scaffolds a new manifest
(`Cargo.toml`, `package.json`, `tauri.conf.json`, `pyproject.toml`, etc.).

## Required Behavior

1. **Verify latest stable from the source registry before writing a version.**
   - Rust: `https://crates.io/api/v1/crates/<name>` → `max_stable_version`.
   - npm: `https://registry.npmjs.org/<name>/latest` → `version`.
   - Do not infer the latest version from memory, blog posts, or other repos.
2. **Check for deprecation / rename.**
   - npm: read the `deprecated` field on the resolved version (history-wide greps
     give false positives — check the specific `latest` release).
   - crates.io: a `+deprecated` build-metadata tag or an unmaintained/yanked
     status means find the maintained successor (e.g. `serde_yaml` →
     `serde_yml` / `serde_yaml_ng`; `reactflow` → `@xyflow/react`).
3. **Record the check.** In the executor receipt (or a short manifest comment),
   note the registry-confirmed latest and the date checked, so review can audit it.
4. **Do not silently perform breaking major bumps.** If the latest stable is one
   or more majors ahead, treat the upgrade as its own bounded task with the
   appropriate risk tier; do not fold a framework migration into an unrelated slice.
5. **Respect intentional pins.** If a manifest comment or `decisions.md` records a
   deliberate hold-back, keep it and surface the gap rather than overriding it.

## Review Hook

Reviewers verify that any new or changed dependency version was registry-checked
(receipt evidence) and that no deprecated/renamed package was introduced. A pin
that is several majors behind latest without a recorded reason is a `revise`.

## Reference

Standing audit and methodology: `project/docs/009-dependency-audit.md`.
</content>
