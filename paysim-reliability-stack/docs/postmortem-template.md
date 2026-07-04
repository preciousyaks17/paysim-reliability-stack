# Postmortem: Duplicate Debit from Retried Transfer Request

**Status:** Draft — fill in after running the reproduction steps in the README.

## Summary
_One or two sentences: what happened, and the customer/business impact._

Example: "Between [time] and [time], a subset of transfer requests were
debited twice from the source account due to a missing idempotency check.
Affected transfers: N. Total over-debited amount: $X."

## Impact
- Number of affected transfers:
- Total financial impact (simulated):
- Customer-facing symptom (what would the user have seen):
- Duration:

## Detection
- How was this detected? (dashboard alert, manual trace inspection, load test failure)
- Time to detect from first occurrence:
- What signal fired first — latency, error rate, or a business metric (duplicate-transfer rate)?
- What would have caught this *faster*? (e.g. an alert on idempotency-key collision rate)

## Timeline
| Time | Event |
|------|-------|
|      | Load test / traffic pattern begins retrying requests |
|      | First duplicate debit occurs |
|      | Detected via [dashboard/trace/alert] |
|      | Root cause identified |
|      | Fix applied / flag reverted |

## Root Cause
The idempotency check (`if req.idempotency_key and not SIMULATE_DOUBLE_DEBIT_BUG`)
was bypassed, meaning a client retry with the same idempotency key was treated
as a brand-new transfer rather than being recognized as a replay. This is
functionally identical to a real bug where an idempotency check is missing,
misconfigured, or bypassed under a specific code path (e.g. a race condition
between the check and the write).

## Contributing Factors
- Client-side retry behavior on timeout (expected and correct client behavior —
  the server should have been safe against it)
- No uniqueness constraint enforced at the database level on `idempotency_key`
  independent of application logic (defense in depth was missing)
- No alert on "idempotency key seen more than once resulting in distinct transfers"

## What Went Well
- Tracing captured both the original and duplicate request clearly, including
  the idempotency key attribute, making root cause identification straightforward
- Load test surfaced the issue before it would have reached production traffic

## Remediation / Action Items
| Action | Owner | Priority |
|--------|-------|----------|
| Re-enable idempotency check as a hard requirement, not a feature flag | | P0 |
| Add DB-level unique constraint on `idempotency_key` as defense in depth | | P0 |
| Add alert: distinct transfer IDs sharing an idempotency key > 0 | | P1 |
| Add contract test verifying idempotent replay behavior | | P1 |
| Add reconciliation job comparing debit count to credit count per account | | P2 |

## Lessons Learned
_What does this incident teach about how the system should be designed
differently — not just "fix the bug" but "what pattern prevents this class
of bug in future"?_

Example takeaway: idempotency should be enforced at the data layer (unique
constraint) as well as the application layer, so a code regression can't
silently remove the protection.
