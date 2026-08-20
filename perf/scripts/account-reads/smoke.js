// TIER 2 -- authenticated reads (account/balance/positions), one dedicated account per VU.
// Provision fresh accounts first (60s token TTL -- do this immediately before running):
//   python3 tools/arrange_perf_accounts.py
// Run: k6 run perf/scripts/account-reads/smoke.js
import http from 'k6/http';
import { check, group, sleep } from 'k6';
import { env, perVUAccount } from '../../lib/accounts.js';
import { authHeaders } from '../../lib/auth.js';

export const options = {
  vus: 1,
  duration: '1m',
  thresholds: {
    http_req_duration: ['p(95)<400'],
    http_req_failed: ['rate<0.01'],
    checks: ['rate>0.99'],
  },
};

const account = perVUAccount(); // set up once per VU lifetime, refreshed in place by auth.js

export default function () {
  const headers = authHeaders(account);

  group('perp account', function () {
    const res = http.get(`${env.trading_base}/perpetual/account?app_session_id=${account.address}`, headers);
    check(res, { 'account: 200': (r) => r.status === 200 });
  });

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
