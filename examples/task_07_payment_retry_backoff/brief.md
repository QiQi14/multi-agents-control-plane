# Brief — bounded backoff for the settlement worker

## What happened

On 2026-07-18 the payment gateway returned `503` for roughly nine minutes. The settlement worker
retried each failed call immediately, in a tight loop, with no ceiling. It produced **41,204
authorization attempts** against a provider whose per-account limit is 600/minute.

The provider rate-limited the whole account, not the tenant whose job was failing. Settlement
stopped for every tenant for a further 22 minutes after the gateway itself recovered.

## Why it is worse than it looks

The retry loop reuses the job's idempotency key *only when the previous attempt returned a
response*. A timed-out attempt generates a fresh key, so a slow gateway — not a failing one — can
produce two authorizations for one job. Nothing in the current dashboards would show this: the
duplicate lands as two successful authorizations under different keys.

That is the reason this task is high risk. The visible incident is availability; the latent one is
double-charging a customer.

## Reproduction

```text
tests/workers/settlement/test_retry.py::test_gateway_503_storm   (currently: 41k attempts)
tests/workers/settlement/test_retry.py::test_slow_gateway_key    (currently: two keys, FAILS)
```

Both reproduce against the fake gateway with an injected clock; neither needs the provider sandbox.

## What to build

Bounded exponential backoff with jitter and a hard attempt ceiling. On exhaustion the job moves to
the dead-letter queue carrying the provider's last response body, so an operator can see *why* it
died without re-running it.

One attempt is in flight at a time. The idempotency key is derived once, from the job, and is
identical across every attempt including timeouts.

## Out of scope

The ledger and the settled-transaction schema. This task changes when the worker calls the gateway,
never what it records afterwards.
