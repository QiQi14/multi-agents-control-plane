---
id: project-doc-schema
type: project-doc
domain: control-plane
status: active
owner: system
created: 2026-07-24
updated: 2026-07-29
tags: [schema, documentation, frontmatter, registry]
relations:
  - type: relates_to
    target: project-principles
  - type: relates_to
    target: project-architecture
---
# Documentation Metadata & Relation Schema

**Status:** Active — canonical control-plane specification

## 1. Truth systems and corpora

The registry uses one transport envelope for two independent document corpora. Corpus is an
authority boundary, not a display category.

| Corpus | Path root | Truth carried |
|---|---|---|
| `control-plane` | `.ai/` | Agent rules, workflows, roles, project control-plane facts, memory, skills, migrations, and specs |
| `product` | `project/docs/` | Product, user, engineering, and research knowledge |

A shared registry does not merge their graphs or authority. Cross-corpus relations remain explicit
authored relations and readers must label them as bridges. Project Intelligence, task evidence, and
either document corpus are separate truth systems.

Registry `path` values are repository-relative, always beginning with `.ai/` or `project/docs/`.
Consumers may read schema-v1 `.ai`-relative paths for backward compatibility, but schema-v2 output
never relies on path guessing.

## 2. Schema-v2 product-document envelope

Every new, moved, or content-changed `project/docs/*.md` file requires all fields below. Existing
files are exempt only while their repository-relative path and SHA-256 content hash exactly match
`.ai/project/product-doc-legacy-baseline.json`.

```yaml
---
id: render-architecture
authority: canonical
corpus: product
type: architecture
domain: rendering
audiences: [engineering]
status: active
maturity: implemented
visibility: internal
summary: Current rendering architecture and its backend boundaries.
navigation: [rendering, architecture]
relations:
  - type: relates_to
    target: product-render-consistency
    note: Consistency requirements constrain backend behavior.
subjects:
  - type: crate
    target: render-core
---
```

Required fields are `id`, `corpus`, `type`, `domain`, `audiences`, `authority`, `status`,
`maturity`, `visibility`, `summary`, `navigation`, `relations`, and `subjects`.

- `id` is a repository-wide unique stable kebab-case slug.
- `domain` is a non-empty topical slug. It does not encode corpus, audience, or authority.
- `summary` is authored, compact current truth; a renderer must not manufacture it from body text.
- `navigation` is an authored list of stable navigation labels. An empty list is explicit.
- `relations` and `subjects` are authored lists. Empty lists are explicit, not missing data.

## 3. Closed vocabularies

### 3.1 Product document type

| Value | Purpose |
|---|---|
| `product-requirements` | Product outcomes and requirements |
| `tutorial` | Learning-oriented user journey |
| `how-to` | Task-oriented user guidance |
| `reference` | Precise user or engineering reference |
| `explanation` | Conceptual user explanation |
| `architecture` | Current engineering structure and invariants |
| `spec` | Normative bounded engineering contract |
| `runbook` | Operational procedure |
| `decision` | Adopted engineering decision and current consequence |
| `research` | Evidence and bounded findings |
| `proposal` | Proposed future change |

`legacy-untyped` is generated only by the compatibility adapter. Authors cannot select it.

### 3.2 Audience

`users`, `product`, `engineering`, `design`, `operations`, `contributors`, and `agents`.
At least one authored audience is required.

### 3.3 Authority

| Value | Meaning |
|---|---|
| `canonical` | Current source of truth for the named subject |
| `normative` | Requirements or rules that implementations must satisfy |
| `informative` | Guidance or explanation that does not establish requirements |
| `research` | Evidence or analysis without adopted product authority |
| `historical` | Retained context that is not current product truth |

### 3.4 Lifecycle status

`active`, `draft`, `deprecated`, `archived`, and `superseded`.

### 3.5 Product maturity

`proposed`, `adopted`, `partial`, and `implemented`.

### 3.6 Visibility

`internal` and `public`. New templates default to `internal`. Registration never publishes a
document, and no legacy document receives public visibility by inference.

## 4. Typed relations and subjects

A relation contains required `type` and `target` strings plus optional `note`. Relation types are:

`depends_on`, `enforced_by`, `informs`, `supersedes`, `relates_to`, `conflicts_with`, `implements`,
`part_of`, and `references`.

Every authored relation target must resolve to a registered document ID or a known task/decision ID.
Markdown links may produce inferred reader edges, but inferred edges never become authored metadata,
authority, corpus bridges, or publication decisions.

A subject contains required `type` and `target` strings. Subject types are `product`, `feature`,
`system`, `crate`, `module`, `task`, and `document`. Subjects label what a document is about; they do
not create document-to-document graph edges.

## 5. Control-plane compatibility adapter

Existing `.ai/**/*.md` frontmatter remains source-metadata version 1. Schema-v2 registry generation:

- preserves its `id`, `type`, `domain`, `status`, title fallback, tags, relations, and generated URL;
- adds `corpus: control-plane` and `source_metadata_version: 1` in the generated entry;
- emits a repository-relative `.ai/...` path; and
- keeps extension-composed control-plane content under the same adapter.

Control-plane document types remain `rule`, `workflow`, `agent`, `decision`, `project-doc`, `memory`,
`skill`, `migration`, `config`, and `spec`. Files without frontmatter remain excluded from the
control-plane registry for backward compatibility.

## 6. Legacy product-document baseline

The checked-in baseline is an exception ledger, not metadata authority. Each record contains only a
repository-relative `project/docs/*.md` path and exact SHA-256 content hash, plus a frozen source
commit at the file level.

An unchanged baseline match is indexed with:

- `corpus: product`;
- `type` and `status`: `legacy-untyped`;
- `authority`, `maturity`, and `domain`: `unclassified`;
- `visibility: internal`; and
- an explicit warning that no authored authority or public visibility was inferred.

Adding, moving, or changing a legacy product document invalidates the exemption. The document must
then use schema-v2 frontmatter. Deleting an obsolete baseline document does not resurrect it.

## 7. Registry payload and validation

Generated by `ai sync` at `.ai/_registry.json`:

```json
{
  "schema_version": 2,
  "generator": "ai sync",
  "corpora": [
    {"id": "control-plane", "path_root": ".ai/"},
    {"id": "product", "path_root": "project/docs/"}
  ],
  "documents": [],
  "unresolved_references": [],
  "warnings": [],
  "errors": []
}
```

`ai docs lint` exits nonzero when `errors` is non-empty. Errors include:

1. new, moved, or content-changed untyped product documents;
2. missing required product metadata;
3. unknown closed-vocabulary values;
4. malformed relation, navigation, audience, or subject values;
5. duplicate document IDs across either corpus; and
6. unresolved authored relations.

Warnings preserve visible non-authoritative states, including exact-match legacy documents. A
warning never grants canonical, normative, or public authority.

## 8. Authoring templates

Copy the matching file from `.ai/templates/project-doc/` when creating a product document. The
variants cover product requirements; user tutorial, how-to, reference, and explanation;
engineering architecture, spec, reference, runbook, and decision; and research/proposal.

Templates deliberately default to `status: draft` and `visibility: internal`. Authors must replace
the stable ID, topical domain, summary, title, and body, and must review every default before linting.