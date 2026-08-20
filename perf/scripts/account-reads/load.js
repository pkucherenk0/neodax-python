// TIER 2 -- same reads as smoke.js, run at expected-peak concurrency.
// VU count MUST NOT exceed the number of provisioned accounts (see ../../README.md) -- two
// VUs sharing one account race on refresh_token rotation and break each other's auth.
// Provision at least as many accounts as `target` below (60s token TTL -- do this immediately
// before running):
//   python3 tools/arrange_perf_accounts.py --count 10
// Run: k6 run perf/scripts/account-reads/load.js
import http from 'k6/http';
import { check, group, sleep } from 'k6';
import { env, perVUAccount } from '../../lib/accounts.js';
import { authHeaders } from '../../lib/auth.js';

export const options = {
  stages: [
    { duration: '30s', target: 10 },
    { duration: '2m', target: 10 },
    { duration: '30s', target: 0 },
  ],
  thresholds: {
    http_req_duration: ['p(95)<600', 'p(99)<1200'],
    http_req_failed: ['rate<0.01'],
    checks: ['rate>0.99'],
  },
};

const account = perVUAccount();

export default function () {
  const headers = authHeaders(account);

  group('perp balance', function () {
    const res = http.get(`${env.trading_base}/perpetual/balance?app_session_id=${account.address}`, headers);
    check(res, { 'balance: 200': (r) => r.status === 200 });
  });

  group('perp positions', function () {
    const res = http.get(`${env.trading_base}/perpetual/positions?app_session_id=${account.address}`, headers);
    check(res, { 'positions: 200': (r) => r.status === 200 });
  });

  sleep(1);
}
