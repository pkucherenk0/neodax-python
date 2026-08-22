// TIER 3 -- REAL MONEY. Same never-filling resting-order pattern as smoke.js, pushed past peak in escalating steps -- see ../../README.md for why this stays safe to escalate.
// VU count MUST NOT exceed provisioned accounts. Provision at least as many as the highest `target` below (60s token TTL -- do this immediately before running):
//   python3 tools/arrange_perf_accounts.py --count 15
// Run: k6 run perf/scripts/order-placement/stress.js
import { sleep } from 'k6';
import { env, market, perVUAccount } from '../../lib/accounts.js';
import { authHeaders } from '../../lib/auth.js';
import { placeAndCancelRestingOrder } from '../../lib/orders.js';

export const options = {
  stages: [
    { duration: '1m', target: 5 },
    { duration: '2m', target: 5 },
    { duration: '1m', target: 10 },
    { duration: '2m', target: 10 },
    { duration: '1m', target: 15 },
    { duration: '2m', target: 15 },
    { duration: '2m', target: 0 }, // recovery check
  ],
  thresholds: {
    http_req_duration: ['p(95)<2500'],
    http_req_failed: ['rate<0.05'],
  },
};

const account = perVUAccount();

export default function () {
  placeAndCancelRestingOrder(env, market, account, authHeaders(account));
  sleep(1);
}
