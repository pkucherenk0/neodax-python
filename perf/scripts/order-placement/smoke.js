// TIER 3 -- REAL MONEY. Rests a GTC limit buy 10% below mark (never fills, no position/PnL
// risk -- mirrors suites/nimbus/perp/test_orders.py's non-filling pattern), then cancels it.
// Smoke-only ON PURPOSE: no load/stress/soak variant exists for this tier -- see ../../README.md.
// Provision fresh accounts first (60s token TTL -- do this immediately before running):
//   python3 tools/arrange_perf_accounts.py --count 2
// Run: k6 run perf/scripts/order-placement/smoke.js
import http from 'k6/http';
import { check, group, sleep } from 'k6';
import { env, market, perVUAccount } from '../../lib/accounts.js';
import { authHeaders } from '../../lib/auth.js';

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
  const headers = authHeaders(account);
  let orderUuid;

  group('place resting limit buy (10% below mark, never fills)', function () {
    const markRes = http.get(
      `${env.trading_base}/perpetual/funding-rates/current?symbols=${market}`,
      { headers: { 'User-Agent': 'Mozilla/5.0' } },
    );
    const okMark = check(markRes, { 'mark price: 200': (r) => r.status === 200 });
    if (!okMark) return;
    const mark = parseFloat(JSON.parse(markRes.body).funding_rates[0].mark_price);
    const price = (mark * 0.9).toFixed(2);

    const res = http.post(`${env.trading_base}/perpetual/order`, JSON.stringify({
      app_session_id: account.address,
      market,
      side: 'buy',
      direction: 'long',
      type: 'limit',
      amount: '0.01',
      price,
      time_in_force: 'gtc',
      reduce_only: false,
      leverage: '5',
    }), headers);
    const ok = check(res, { 'order placed: 200': (r) => r.status === 200 });
    if (ok) orderUuid = JSON.parse(res.body).order_uuid;
  });

  group('cancel it', function () {
    if (!orderUuid) return; // placement failed -- nothing to cancel
    const res = http.del(`${env.trading_base}/perpetual/order`, JSON.stringify({
      app_session_id: account.address,
      market,
      order_uuid: orderUuid,
    }), headers);
    check(res, { 'cancel: 200': (r) => r.status === 200 });
  });

  sleep(2);
}
