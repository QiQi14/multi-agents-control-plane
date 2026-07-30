# Contributing

This repository governs how AI agents are allowed to change code, so its own contribution process
holds to the same standard: bounded scope, evidence over assertion, and an independent reader.

## Development setup

Python 3.10+ and git. There is nothing to install.

```bash
git clone https://github.com/QiQi14/multi-agents-control-plane
```

```bash
cd multi-agents-control-plane
```

```bash
python -m unittest discover -s scripts/tests -t .
```

The suite is stdlib `unittest` with no third-party dependency. It should be green before you start,
so a failure later is yours.

## The three gates

Run all three before opening a pull request. They are what CI runs.

```bash
python -m unittest discover -s scripts/tests -t .
```

```bash
python scripts/ai_cli.py doctor
```

```bash
python scripts/ai_cli.py docs lint
```

`doctor` checks scaffold integrity, config, registry freshness, and the generated-file manifest.
`docs lint` fails on a relation whose target does not exist, which is what stops the knowledge graph
rotting as documents are renamed.

If you changed anything under `.ai/`, also run `ai sync` and commit the regenerated adapters in the
same change. A pull request whose `.ai/` and adapters disagree will fail `doctor`.

## Generated versus canonical

`.ai/` is canonical. These are generated and must never be hand-edited:

```text
AGENTS.md    CLAUDE.md    GEMINI.md    .claude/    .agents/    .ai/_registry.json    .ai/_manifest.json
```

Every generated file carries a warning header and a sha256 in `.ai/_manifest.json`. Editing one
directly means your change is lost on the next `ai sync`, and `doctor` will report the drift. Change
the canonical source in `.ai/` and regenerate.

## Repository architecture

| Path | Role |
| --- | --- |
| `.ai/rules`, `.ai/workflows`, `.ai/agents` | The governing content, rendered into every adapter |
| `.ai/project` | Framework documentation and schemas |
| `.ai/config.yaml` | Tool roster, routing taxonomy, risk gates, enabled extensions |
| `scripts/ai_cli.py` | Thin CLI facade, capped at 400 lines |
| `scripts/ai_plane/` | The plane's modules, each capped at 600 lines |
| `scripts/extension_registry.py` | Extension resolution; fails closed |
| `scripts/extensions/` | Shipped integrations and the reference Rust gate |
| `scripts/tests/` | The suite; `test_ai_*.py` capped at 700 lines |

Those ceilings are enforced by `test_ai_architecture.py`, along with import-cycle and
facade-dependency checks. They are a ratchet: if your change pushes a module over, split it rather
than raising the limit.

## Adding an adapter for a new tool

Adding a tool is **data, not code**. You should not need to touch the renderer.

1. Create `scripts/extensions/<tool>/extension.json` declaring `types: ["integration"]`, its
   `read_roots` and `write_roots`, its command map, and its render artifacts.
2. Add templates under `scripts/extensions/<tool>/templates/`.
3. Name the tool in `.ai/config.yaml` under `tools:` and `extensions.enabled`.
4. Run `ai sync` and commit the generated output.

If the tool decides rule activation from frontmatter, declare `rule_frontmatter` on its `rules_tree`
artifact. Omitting it is how rules once reached a tool with no declared activation and were silently
ignored — see `.ai/project/adapter-design.md`.

Use neutral `__AI_COMMAND_<NAME>__` tokens in canonical content. The renderer resolves them per tool
and fails closed on an unknown or surviving token.

## Adding an extension

Extensions are `integration`, `pack`, `gate`, or `command`. Nothing is enabled by being present on
disk — only by being named in `.ai/config.yaml`. Keep it that way: a change that activates behaviour
through filesystem discovery will be rejected.

The core carries no language, stack, or vendor policy. If your change puts a stack name, a tool name,
or a cargo/npm/gradle assumption into `scripts/ai_plane/` or `scripts/extension_registry.py`, it
belongs in an extension instead. `test_ai_maw_core_boundary.py` enforces this.

## Skill packs

No packs ship. Stack conventions age quickly and a pack nobody has exercised is a liability, so the
plane installs yours from wherever you keep them:

```bash
ai skills add house-style --from ../our-shared-packs
```

Pull requests adding a pack to this repository will generally be declined. Publish it in your own
repository instead.

## Changing a schema

Schemas are consumed by other people's task history, so a change is a versioned event, not an edit:

1. Bump the `schema_version` rather than redefining the current one.
2. Keep existing artifacts readable, or provide an explicit migration.
3. Update the schema document under `.ai/project/`.
4. Add a fixture proving an artifact written under the old version still loads.

Silently widening a schema is the failure mode here — it makes old evidence mean something it did
not mean when it was recorded.

## Tests

New behaviour needs a test that can fail. The house habit is to **mutation-test your own guard**:
break the code the test is meant to protect and confirm the test goes red. A test that stays green
with the defect reintroduced is not a gate, and several have shipped that way.

Prefer a test that pins observable behaviour over one that pins an implementation detail.

## Design proposals

For anything that changes a contract, a schema, an authority boundary, or the CLI surface, open an
issue describing the problem before writing code — what breaks today, what the change makes possible,
and what it costs. Small fixes do not need this.

## Pull requests

- One bounded change. If you find an unrelated defect, note it and open a separate issue.
- State what you ran, and paste the result rather than describing it.
- Say plainly what you did *not* verify. An honest gap is far more useful than an implied claim.
- Commit messages explain **why**; the diff already shows what.

## Agent-assisted contributions

Agent-written code is welcome — this project exists to make it reviewable. Two conditions:

- **You are the author.** Review it as your own work before opening the PR. "The agent wrote it" is
  not an explanation for a defect.
- **Do not paste unverified claims.** If a receipt or summary says tests passed, run them yourself.
  Receipts written ahead of the evidence are a recurring failure this project was built to catch.

## Third-party provenance

One component is vendored: Mermaid 11.16.0 (MIT), pinned by sha256 with its provenance recorded
beside it. Adding another vendored asset requires its license, upstream source, exact version, and a
recorded hash, plus an entry in `NOTICE`. Runtime dependencies on a CDN will be declined: reports
must render offline years after they were sent.

## License

Contributions are accepted under the Apache License 2.0. See [LICENSE](LICENSE).
