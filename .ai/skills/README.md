---
id: skill-index
type: skill
domain: control-plane
status: active
owner: system
---

# Skills

This directory holds the skill content **this project has installed** — not the full library.

Stack packs are optional and selective. A Rust workspace should not carry React guidance, and a
Swift project should not carry Rust guidance: every installed pack is composed into the generated
adapters and becomes context every agent has to read. Install only what the project actually is.

The shipped catalog lives in `packs/` at the repository root. Nothing there is active until it is
installed here.

```text
__AI_COMMAND_SKILLS_LIST__            list the catalog and what is installed
__AI_COMMAND_SKILLS_ADD__ <name>...   install one or more packs
__AI_COMMAND_SKILLS_REMOVE__ <name>   uninstall
__AI_COMMAND_SYNC__                   regenerate adapters after either
```

## Already present

- `pr-blueprint/` — not an optional stack pack. It is the agent-facing surface of the first-class
  blueprint command, and the generated adapters require its command catalog. Leave it in place.

Anything else here was installed by the skills-add command and can be removed the same way.

Do not treat generated adapter folders as skill source. `.ai/` is canonical.
