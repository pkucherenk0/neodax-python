// TIER 1 -- push read traffic beyond expected peak in escalating steps to find where
// market-data reads start to degrade, and whether they recover once load drops (final stage).
// Run: k6 run perf/scripts/market-data/stress.js
import http from 'k6/http';
import { check, group, sleep } from 'k6';

const BASE = __ENV.NIMBUS_UAT_TRADING_BASE;
const MARKET = __ENV.PERF_MARKET || 'BTCUSDT-PERP';
const HEADERS = { headers: { 'User-Agent': 'Mozilla/5.0' } };

export const options = {
  stages: [
    { duration: '1m', target: 20 },
    { duration: '2m', target: 20 },
    { duration: '1m', target: 50 },
    { duration: '2m', target: 50 },
    { duration: '1m', target: 100 },
    { duration: '2m', target: 100 },
    { duration: '2m', target: 0 }, // recovery check
  ],
  thresholds: {
    http_req_duration: ['p(95)<2000'],
    http_req_failed: ['rate<0.05'],
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

  sleep(0.5);
}
