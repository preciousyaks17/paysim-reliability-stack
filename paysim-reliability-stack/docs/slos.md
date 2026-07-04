# Service Level Objectives — Transfer Service

## SLI 1: Transfer Latency
**Definition:** Time from request received to response returned for `POST /transfers`.
**SLO:** 99.5% of transfers complete in under 2000ms, measured over a rolling 30-day window.
**Why this threshold:** Instant-transfer systems (e.g. NIP-style rails) are
expected to confirm near-instantly; anything approaching multi-second latency
is user-visible and erodes trust in the "instant" promise.

## SLI 2: Transfer Success Rate
**Definition:** Proportion of transfer requests that complete with status
`completed` (excluding legitimate business-logic failures like insufficient funds).
**SLO:** 99.9% success rate over a rolling 30-day window.

## SLI 3: Idempotency Correctness (payments-specific SLI)
**Definition:** Proportion of transfer requests sharing an idempotency key
that resolve to the *same* underlying transfer, rather than creating duplicates.
**SLO:** 100% — this is a correctness guarantee, not a performance target.
A single violation should page, not just count against an error budget.
**Why this exists:** Generic SRE SLOs (latency, availability) don't capture
payment-specific correctness risk. This SLI is the one most relevant to
demonstrating fintech-domain reliability thinking.

## Error Budget Policy
- Latency/success-rate SLOs: standard 30-day error budget. If burn rate
  exceeds 2x expected over a 1-hour window, page on-call.
- Idempotency SLI: zero-tolerance. Any violation triggers immediate incident
  response regardless of remaining "budget" elsewhere — correctness bugs in
  a ledger don't average out over a month.

## Alerting Rules (to implement in Grafana/Alertmanager)
1. `transfer_request_duration_seconds{quantile="0.99"} > 2` for 5m → page
2. `rate(transfer_failures_total[5m]) / rate(transfer_requests_total[5m]) > 0.001` → page
3. `count by (idempotency_key) (transfer_ids) > 1` → page immediately (P0)
