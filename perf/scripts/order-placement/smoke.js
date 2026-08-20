// TIER 3 -- REAL MONEY. Rests a GTC limit buy 10% below mark (never fills, no position/PnL
// risk -- mirrors suites/nimbus/perp/test_orders.py's non-filling pattern), then cancels it.
// Provision fresh accounts first (60s token TTL -- do this immediately before running):
//   python3 tools/arrange_perf_accounts.py --count 2
// Run: k6 run perf/scripts/order-placement/smoke.js
import { sleep } from 'k6';
import { env, market, perVUAccount } from '../../lib/accounts.js';
import { authHeaders } from '../../lib/auth.js';
import { placeAndCancelRestingOrder } from '../../lib/orders.js';

export const options = {
  vus: 1,
  iterations: 3,
  thresholds: {
    http_req_duration: ['p(95)<800'],
    http_req_failed: ['rate<0.01'],
    checks: ['rate>0.99'],
  },
};

const account = perVUAccount();

export default function () {
  placeAndCancelRestingOrder(env, market, account, authHeaders(account));
  sleep(2);
}
