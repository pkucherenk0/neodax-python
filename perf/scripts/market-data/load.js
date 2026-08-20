// TIER 1 -- same reads as smoke.js, run at expected-peak concurrency. No account provisioning
// needed (public endpoints), so VU count isn't capped the way Tier 2/3 are.
// Run: k6 run perf/scripts/market-data/load.js
import http from 'k6/http';
import { check, group, sleep } from 'k6';

const BASE = __ENV.NIMBUS_UAT_TRADING_BASE;
const MARKET = __ENV.PERF_MARKET || 'BTCUSDT-PERP';
const HEADERS = { headers: { 'User-Agent': 'Mozilla/5.0' } };

export const options = {
  stages: [
    { duration: '30s', target: 20 }, // ramp-up to expected peak
    { duration: '2m', target: 20 },  // hold
    { duration: '30s', target: 0 },  // ramp-down
  ],
  thresholds: {
    http_req_duration: ['p(95)<500', 'p(99)<1000'],
    http_req_failed: ['rate<0.01'],
  },
};

export default function () {
  group('exchangeInfo', function () {
    const res = http.get(`${BASE}/perpetual/exchangeInfo`, HEADERS);
    check(res, { 'exchangeInfo: 200': (r) => r.status === 200 });
  });

  group('orderbook', function () {
    const res = http.get(`${BASE}/orderbook?symbol=${MARKET}`, HEADERS);
    check(res, { 'orderbook: 200': (r) => r.status === 200 });
  });

  sleep(1);
}
