// k6 load test for the Transfer Service
// Run: k6 run load-test.js
//
// This script sends transfer requests and occasionally REPLAYS the same
// idempotency key to simulate a client retry after a timeout — the exact
// scenario that exposes the double-debit bug when SIMULATE_DOUBLE_DEBIT_BUG=true.

import http from 'k6/http';
import { check, sleep } from 'k6';
import { uuidv4 } from 'https://jslib.k6.io/k6-utils/1.4.0/index.js';

export const options = {
  stages: [
    { duration: '30s', target: 10 },
    { duration: '1m', target: 25 },
    { duration: '30s', target: 0 },
  ],
  thresholds: {
    // This is your SLO expressed as a k6 threshold.
    http_req_duration: ['p(99)<2000'],
    http_req_failed: ['rate<0.005'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8001';

export default function () {
  const idempotencyKey = uuidv4();

  const payload = JSON.stringify({
    from_account: 'acct_alice',
    to_account: 'acct_bob',
    amount: 10.0,
    idempotency_key: idempotencyKey,
  });

  const params = { headers: { 'Content-Type': 'application/json' } };

  const res = http.post(`${BASE_URL}/transfers`, payload, params);

  check(res, {
    'status is 200': (r) => r.status === 200,
    'latency < 2s': (r) => r.timings.duration < 2000,
  });

  // 10% of requests simulate a client retry with the SAME idempotency key
  // (e.g. client timed out and resent). This is your reproduction case.
  if (Math.random() < 0.1) {
    sleep(0.2);
    http.post(`${BASE_URL}/transfers`, payload, params);
  }

  sleep(0.5);
}
