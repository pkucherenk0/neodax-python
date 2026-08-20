// Loads perf/data/accounts.json (tools/arrange_perf_accounts.py) and assigns one dedicated
// account per VU -- never shared, see ../README.md (refresh_token is single-use/rotating).
import { SharedArray } from 'k6/data';

const data = JSON.parse(open('../data/accounts.json'));

export const env = data.env;
export const market = data.market;
export const maker = data.maker;

const accounts = new SharedArray('perf-accounts', function () {
  return data.accounts;
});

// mutable, VU-local copy -- auth.js's refresh() updates access_token/refresh_token on this
// object in place across iterations within the same VU's lifetime.
export function perVUAccount() {
  const idx = (__VU - 1) % accounts.length;
  return Object.assign({}, accounts[idx]);
}
