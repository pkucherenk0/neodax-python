// TIER 4 -- REAL MONEY, REAL FILLS. Maker+taker cross and fill, then flatten both sides. SMOKE-ONLY (see ../../README.md before scaling up).
// Provision a fresh maker+taker pair first (60s token TTL -- do this immediately before running):
//   python3 tools/arrange_perf_accounts.py --count 0 --pairs 1
// Run: k6 run perf/scripts/order-matching/smoke.js
import http from 'k6/http';
import { check, group, sleep } from 'k6';
import { env, matchingMarket, perVUPair } from '../../lib/accounts.js';
import { authHeaders } from '../../lib/auth.js';
import { closePositionWithRetry } from '../../lib/orders.js';
import { marketFilters, roundToStep } from '../../lib/market.js';

export const options = {
  vus: 1,
  iterations: 3,
  thresholds: {
    http_req_duration: ['p(95)<1500'],
    http_req_failed: ['rate<0.02'],
    checks: ['rate>0.95'],
  },
};

const NOTIONAL_USD = 500; // small on purpose -- minimize price impact on a thin market
const pair = perVUPair();

export default function () {
  const makerHeaders = authHeaders(pair.maker);
  const takerHeaders = authHeaders(pair.taker);
  const filters = marketFilters(env, matchingMarket);
  const okFilters = check(filters, { 'exchangeInfo filters: available': (f) => f !== null });
  if (!okFilters) return;

  const markRes = http.get(
    `${env.trading_base}/perpetual/funding-rates/current?symbols=${matchingMarket}`,
    { headers: { 'User-Agent': 'Mozilla/5.0' } },
  );
  const okMark = check(markRes, { 'mark price: 200': (r) => r.status === 200 });
  if (!okMark) return;
  const mark = parseFloat(JSON.parse(markRes.body).funding_rates[0].mark_price);
  const amount = roundToStep(NOTIONAL_USD / mark, filters.stepSize, filters.amountPrecision);
  const restPrice = roundToStep(mark * 0.999, filters.tickSize, filters.pricePrecision); // inside spread, guaranteed cross

  let filled = false;

  group('maker rests a small sell inside spread', function () {
    const res = http.post(`${env.trading_base}/perpetual/order`, JSON.stringify({
      app_session_id: pair.maker.address,
      market: matchingMarket,
      side: 'sell',
      direction: 'short',
      type: 'limit',
      amount,
      price: restPrice,
      time_in_force: 'gtc',
      reduce_only: false,
      leverage: '5',
    }), makerHeaders);
    check(res, { 'maker order: 200': (r) => r.status === 200 });
  });

  group('taker crosses it with a market buy', function () {
    const res = http.post(`${env.trading_base}/perpetual/order`, JSON.stringify({
      app_session_id: pair.taker.address,
      market: matchingMarket,
      side: 'buy',
      direction: 'long',
      type: 'market',
      amount,
      reduce_only: false,
      leverage: '5',
    }), takerHeaders);
    check(res, { 'taker order: 200': (r) => r.status === 200 });
  });

  group('confirm the fill', function () {
    // bounded poll (mirrors lib/perp.py's wait_for_perp_fill) -- fills are async, not instant.
    for (let attempt = 0; attempt < 5; attempt++) {
      const res = http.get(
        `${env.trading_base}/perpetual/positions?app_session_id=${pair.taker.address}&market=${matchingMarket}`,
        takerHeaders,
      );
      if (res.status === 200) {
        const positions = JSON.parse(res.body).positions || [];
        if (positions.some((p) => p.direction === 'long' && parseFloat(p.amount) > 0)) {
          filled = true;
          break;
        }
      }
      sleep(1);
    }
    check(null, { 'taker position opened (fill confirmed)': () => filled });
  });

  if (filled) {
    group('flatten both sides', function () {
      const takerClose = closePositionWithRetry(env, matchingMarket, pair.taker, 'sell', 'long', amount, takerHeaders);
      check(takerClose, { 'taker flattened: 200': (r) => r.status === 200 });
      const makerClose = closePositionWithRetry(env, matchingMarket, pair.maker, 'buy', 'short', amount, makerHeaders);
      check(makerClose, { 'maker flattened: 200': (r) => r.status === 200 });
    });
  }

  sleep(2);
}
