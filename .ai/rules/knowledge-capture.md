---
id: rule-knowledge-capture
type: rule
domain: control-plane
status: active
owner: system
---

# Rule: Typed Knowledge Capture

`.ai/memory/` must not become a junk drawer.

Use the correct file:

- `api-surface.md`: factual API information.
- `gotchas.md`: mistakes and traps encountered.
- `decisions.md`: architectural decisions and rationale.
- `feature-ledger.md`: completed features and behavior.
- `deprecated.md`: deprecated APIs and patterns.
- `lessons.md`: durable process lessons learned.

Capture knowledge when QA finds a recurring trap, a deprecated API, a risky pattern, a tool mismatch, or a lesson that should change future planning.
## Closeout Is Not Memory

Typed task context stays in immutable receipts and `task-closeout.yaml`; it is not copied wholesale
into `.ai/memory/`. Capture only the durable reusable lesson after QA. A nonblocking item that is not
a durable lesson still requires a closeout disposition and remains visible in task history.
