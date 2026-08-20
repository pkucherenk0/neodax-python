// TIER 1 -- safe: public market-data reads, no auth, no state change. Confirms the target is
// reachable and the script itself is valid before running anything bigger.
// Run: k6 run perf/scripts/market-data/smoke.js
import http from 'k6/http';
import { check, group, sleep } from 'k6';

const BASE = __ENV.NIMBUS_UAT_TRADING_BASE;
const MARKET = __ENV.PERF_MARKET || 'BTCUSDT-PERP';
const HEADERS = { headers: { 'User-Agent': 'Mozilla/5.0' } }; // WAF 403s requests with none

export const options = {
  vus: 1,
  duration: '30s',
  thresholds: {
    http_req_duration: ['p(95)<300'],
    http_req_failed: ['rate<0.01'],
  },
};

export default function () {
  if (!BASE) throw new Error('NIMBUS_UAT_TRADING_BASE not set -- see ../../README.md');

  group('exchangeInfo', function () {
    const res = http.get(`${BASE}/perpetual/exchangeInfo`, HEADERS);
    check(res, { 'exchangeInfo: 200': (r) => r.status === 200 });
  });

  group('market-risk-tiers', function () {
    const res = http.get(`${BASE}/perpetual/market-risk-tiers?symbol=${MARKET}`, HEADERS);
    check(res, { 'market-risk-tiers: 200': (r) => r.status === 200 });
  });

  group('orderbook', function () {
    const res = http.get(`${BASE}/orderbook?symbol=${MARKET}`, HEADERS);
    check(res, { 'orderbook: 200': (r) => r.status === 200 });
  });

  group('funding-rates', function () {
    const res = http.get(`${BASE}/perpetual/funding-rates/current?symbols=${MARKET}`, HEADERS);
    check(res, { 'funding-rates: 200': (r) => r.status === 200 });
  });

  sleep(1);
}
