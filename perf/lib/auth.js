// Proactive token refresh -- mirrors fixtures/accounts.py's auto_refreshing() pattern.
// access_token TTL is 60s (lib/auth.py); refresh comfortably before it expires, not on 401.
import http from 'k6/http';
import { check } from 'k6';
import { env } from './accounts.js';

const REFRESH_EVERY_S = 40;

export function authHeaders(account) {
  maybeRefresh(account);
  return { headers: { Authorization: `Bearer ${account.access_token}`, 'Content-Type': 'application/json' } };
}

export function maybeRefresh(account) {
  const now = Date.now() / 1000;
  if (account._mintedAt === undefined) account._mintedAt = now; // first use, treat as fresh
  if (now - account._mintedAt < REFRESH_EVERY_S) return;
  refresh(account);
}

export function refresh(account) {
  const res = http.post(
    `${env.auth_base}/auth/refresh`,
    JSON.stringify({ refresh_token: account.refresh_token }),
    { headers: { 'Content-Type': 'application/json' } },
  );
  const ok = check(res, { 'token refresh: 200': (r) => r.status === 200 });
  if (!ok) return; // keep the old (soon-expired) token rather than crash the iteration
  const body = JSON.parse(res.body);
  account.access_token = body.access_token;
  account.refresh_token = body.refresh_token; // rotates -- old one is dead now, never reuse it
  account._mintedAt = Date.now() / 1000;
}
