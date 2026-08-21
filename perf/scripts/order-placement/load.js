// TIER 3 -- REAL MONEY. Same never-filling resting-order pattern as smoke.js, at realistic concurrent load. VU count MUST NOT exceed provisioned accounts (see ../../README.md).
// Provision at least as many accounts as `target` below (60s token TTL -- do this immediately before running):
//   python3 tools/arrange_perf_accounts.py --count 5
// Run: k6 run perf/scripts/order-placement/load.js
import { sleep } from 'k6';
import { env, market, perVUAccount } from '../../lib/accounts.js';
import { authHeaders } from '../../lib/auth.js';
import { placeAndCancelRestingOrder } from '../../lib/orders.js';

export const options = {
  stages: [
    { duration: '30s', target: 5 },
    { duration: '2m', target: 5 },
    { duration: '30s', target: 0 },
  ],
  thresholds: {
    http_req_duration: ['p(95)<1200'],
    http_req_failed: ['rate<0.02'],
    checks: ['rate>0.98'],
  },
};

const account = perVUAccount();

export default function () {
  placeAndCancelRestingOrder(env, market, account, authHeaders(account));
  sleep(2);
}
