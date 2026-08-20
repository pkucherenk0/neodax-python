// Place-and-cancel a never-filling resting order (10% below mark) -- shared by every
// order-placement/*.js script. Cancel retries a bounded few times: unlike order PLACEMENT
// (CONVENTIONS.md #8 -- a retry there re-places a live order, doubling volume), cancellation is
// idempotent -- a second cancel on an already-cancelled order is a safe no-op -- so retrying a
// transient cancel failure is the right way to avoid leaving a real resting order on the shared
// live book, not a violation of the no-retry rule.
import http from 'k6/http';
import { check, group, sleep } from 'k6';

export function placeAndCancelRestingOrder(envCfg, market, account, headers) {
  let orderUuid;

  group('place resting limit buy (10% below mark, never fills)', function () {
    const markRes = http.get(
      `${envCfg.trading_base}/perpetual/funding-rates/current?symbols=${market}`,
      { headers: { 'User-Agent': 'Mozilla/5.0' } },
    );
    const okMark = check(markRes, { 'mark price: 200': (r) => r.status === 200 });
    if (!okMark) return;
    const mark = parseFloat(JSON.parse(markRes.body).funding_rates[0].mark_price);
    const price = (mark * 0.9).toFixed(2);

    const res = http.post(`${envCfg.trading_base}/perpetual/order`, JSON.stringify({
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
    const placed = check(res, { 'order placed: 200': (r) => r.status === 200 });
    if (placed) orderUuid = JSON.parse(res.body).order_uuid;
  });

  if (!orderUuid) return; // placement failed -- nothing to cancel

  group('cancel it', function () {
    let res;
    for (let attempt = 0; attempt < 3; attempt++) {
      res = http.del(`${envCfg.trading_base}/perpetual/order`, JSON.stringify({
        app_session_id: account.address,
        market,
        order_uuid: orderUuid,
      }), headers);
      if (res.status === 200) break;
      sleep(0.3);
    }
    check(res, { 'cancel: 200': (r) => r.status === 200 });
  });
}
