# Context — invariants that must survive

## Where the code lives

| Path | Role |
| --- | --- |
| `src/workers/settlement/worker.py` | Pulls jobs, calls the gateway, handles the response |
| `src/workers/settlement/retry.py` | The retry loop being replaced |
| `src/workers/settlement/keys.py` | Idempotency key derivation |
| `tests/workers/settlement/` | Fake gateway, injected clock, the two failing tests |

## Invariants

1. **One authorization per job, ever.** The idempotency key is derived once from the job id and
   reused for every attempt, including attempts that time out with no response. A timeout is not
   evidence that the gateway did nothing.
2. **One attempt in flight.** A retry is dispatched only after the previous attempt resolves or its
   timeout elapses. Overlapping attempts are what turned an outage into a rate-limit ban.
3. **Backoff is bounded on both ends.** Strictly increasing gaps, non-zero jitter, and a hard cap.
   A clock that jumps backwards must not produce a zero or negative delay.
4. **Exhaustion is observable.** A job that runs out of attempts lands in the dead-letter queue with
   the provider's last response body attached. Silently dropping it is a failure of this contract.
5. **The ledger is not this task's business.** `src/ledger/**` and `migrations/**` are forbidden.
   If the fix appears to require a ledger change, stop and return the contract rather than widening
   the diff.

## Testing notes

The fake gateway can be told to fail with a status, to hang past the timeout, or to succeed slowly.
Use the injected clock for every timing assertion — a test that sleeps for real will be flaky in CI
and will not prove the cap.

The concurrency test needs two workers against one job. It is the only test that can catch the
double-authorization failure mode, so it is a gate rather than a nice-to-have.

## Review notes

High risk applies in full: independent review by a different model family, then explicit owner
approval before merge. The reviewer should assume the aggregate metrics are blind to the failure
this task exists to prevent, and read the idempotency and concurrency tests first.
