# Security Policy

This project decides what an AI agent is allowed to write, what counts as proof that it did the
work, and who may approve a merge. A defect in any of that is a security defect, not a bug report.

## Supported versions

| Version | Supported |
| --- | --- |
| 0.2.x | Yes |
| < 0.2 | No |

Pre-1.0, fixes land on the latest release. There is no long-term support branch yet.

## Reporting a vulnerability

**Use GitHub's private vulnerability reporting**, not a public issue:

<https://github.com/QiQi14/multi-agents-control-plane/security/advisories/new>

Please include the version or commit, what an attacker gains, and the smallest reproduction you
have — a spec, task contract, config, or repository layout that triggers it.

This is a small project. Reports are handled as promptly as is practical, but no response time is
guaranteed and it would be dishonest to publish an SLA that cannot be met. You will get an
acknowledgement, and credit in the fix unless you ask otherwise.

## Report privately, not in a public issue

These classes are security-sensitive because they defeat a boundary the plane exists to enforce:

- **Scope bypass** — writing outside a contract's `target_files`, or into `forbidden_files`,
  without the verifier failing.
- **Path traversal or escape** — any input (spec, task id, evidence path, extension manifest,
  pack directory) that causes a read or write outside the repository.
- **Arbitrary execution** — a spec, contract, extension, or pack that causes code to run that the
  project did not explicitly enable, including argument injection into a registered command.
- **Evidence forgery** — producing a verification record, receipt, or closeout that a reviewer
  would accept for work that did not happen, or mutating a frozen evidence artifact.
- **Approval bypass** — reaching a merge-approved state without the review and human approval a
  risk tier requires.
- **Manifest or hash weakness** — making a generated or vendored file pass its recorded hash while
  differing in content, including the vendored Mermaid asset.
- **Adapter injection** — content in `.ai/` that renders into a generated adapter in a way that
  smuggles instructions past the boundary the plane documents.

## Reasonable to open publicly

Crashes, confusing errors, doc mistakes, false positives in a gate, and platform quirks. If you are
unsure which side a finding falls on, report it privately and we will move it if it is ordinary.

## What this project does not defend against

Stated plainly, so nobody relies on a boundary that does not exist:

- **A hostile agent with shell access.** The plane structures and records agent work; it is not a
  sandbox. If a tool can run arbitrary commands in your repository, the plane cannot contain it.
  Isolation strategies bound *intent*, not process capability.
- **Extensions you enable.** Enabling an extension grants it the read and write roots its manifest
  declares. Review one before enabling it, exactly as you would a dependency.
- **A malicious contributor with merge rights.** Contracts and receipts create an audit trail; they
  do not prevent someone who can approve their own merge.
- **Secrets in your repository.** The plane reads repository files and renders some into generated
  adapters and reports. Do not put secrets in `.ai/`, in a task contract, or in evidence.

## Third-party components

One component is vendored: Mermaid 11.16.0 (MIT), pinned by sha256 with its provenance recorded in
`.ai/templates/pr-blueprint/vendor/mermaid-11.16.0/provenance.json`. Report upstream Mermaid
vulnerabilities to that project; report a *provenance or integrity* failure here.
