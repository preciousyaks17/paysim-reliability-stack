# PaySim Reliability Stack

A simulated instant-transfer (payments) service, built and operated the way an
SRE would run a real financial system: with tracing, SLOs, load testing, and a
deliberately reproducible incident to practice detection and postmortem writing.

This project exists to demonstrate SRE fundamentals applied specifically to a
payments/fintech context — SLIs/SLOs for transaction reliability, idempotency
and retry-safety, and incident response — without requiring prior employment
at a bank or fintech company.

## Architecture

```
client -> transfer-service (FastAPI) -> Postgres/SQLite
              |
              +--> OpenTelemetry traces --> OTel Collector --> Elastic / Grafana
```

Currently implemented: **transfer-service** (core debit/credit logic with
idempotency handling and OTel instrumentation).

Planned/optional: gateway-service (routing + rate limiting), notification-service
(simulated SMS/email confirmation with injectable failure).

## Why this project

Financial transfer systems have a small number of failure modes that show up
constantly in SRE interviews:

- **Duplicate debits from retried requests** (idempotency)
- **Stuck/ambiguous transactions** when a downstream step (e.g. notification)
  fails after the debit succeeds
- **Latency SLO breaches** under load
- **Reconciliation drift** between what the ledger says and what actually happened

This project reproduces the first two on purpose so they can be observed,
measured, and written up — rather than described hypothetically.

## Running locally

```bash
cd transfer-service
pip install -r requirements.txt
uvicorn main:app --reload --port 8001
```

Seed accounts (`acct_alice`, `acct_bob`) are created automatically on startup.

Try a transfer:
```bash
curl -X POST http://localhost:8001/transfers \
  -H "Content-Type: application/json" \
  -d '{"from_account": "acct_alice", "to_account": "acct_bob", "amount": 10, "idempotency_key": "abc-123"}'
```

Send the same request again with the same `idempotency_key` — it should
return the *same* transfer, not create a new one.

## Reproducing the double-debit incident

```bash
SIMULATE_DOUBLE_DEBIT_BUG=true uvicorn main:app --reload --port 8001
```

With this flag on, the idempotency check is skipped, so replaying the same
request (as a client would after a timeout) causes a second debit. Run the
k6 load test in `dashboards/load-test.js` against this mode to generate the
incident, then observe it in your traces/dashboard, and write it up using
`docs/postmortem-template.md`.

## SLOs (see `docs/slos.md`)

- 99.5% of transfers complete in under 2s (rolling 30-day window)
- 99.9% of transfers do not duplicate (measured via idempotency-key collision rate)
- Error budget and burn-rate alerting defined in `dashboards/`

## Load testing

```bash
k6 run dashboards/load-test.js
```

This script intentionally replays 10% of requests with the same idempotency
key to simulate client retries under load.

## Repo structure

```
transfer-service/       Core transfer API with OTel instrumentation
gateway-service/         (planned) API gateway / rate limiting
notification-service/    (planned) simulated notification with injectable failure
k8s/                      Kubernetes manifests for deployment
dashboards/               Grafana dashboard JSON + k6 load test script
docs/                     SLOs, incident postmortem, decisions log
```

## Status

- [x] Transfer service with OTel tracing and togglable idempotency bug
- [x] k6 load test with retry simulation
- [ ] Kubernetes manifests
- [ ] Grafana/Elastic dashboards
- [ ] Postmortem writeup from a real reproduced incident
- [ ] Gateway + notification services
