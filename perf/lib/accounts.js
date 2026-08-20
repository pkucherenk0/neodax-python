// Loads perf/data/accounts.json (tools/arrange_perf_accounts.py) and assigns one dedicated
// account (or maker+taker pair) per VU -- never shared, see ../README.md (refresh_token is
// single-use/rotating).
import { SharedArray } from 'k6/data';

const data = JSON.parse(open('../data/accounts.json'));

export const env = data.env;
export const market = data.market;
export const matchingMarket = data.matching_market;

const accounts = new SharedArray('perf-accounts', function () {
  return data.accounts;
});

const pairs = new SharedArray('perf-pairs', function () {
  return data.pairs;
});

// mutable, VU-local copy -- auth.js's refresh() updates access_token/refresh_token on this
// object in place across iterations within the same VU's lifetime.
export function perVUAccount() {
  const idx = (__VU - 1) % accounts.length;
  return Object.assign({}, accounts[idx]);
}

// same, but for a maker+taker pair (scripts/order-matching/) -- each side refreshed
// independently since they're different accounts with their own refresh_token.
export function perVUPair() {
  const idx = (__VU - 1) % pairs.length;
  const pair = pairs[idx];
  return { maker: Object.assign({}, pair.maker), taker: Object.assign({}, pair.taker) };
}
