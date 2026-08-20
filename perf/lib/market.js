// Perp market filters (tick_size, step_size, precision) -- mirrors lib/perp.py's
// resolve_perp_market()/round_tick(). Fetched once per VU and cached (module scope is
// per-VU in k6, not shared across VUs), since these don't change during a run.
import http from 'k6/http';

const cache = {};

export function marketFilters(envCfg, symbol) {
  if (cache[symbol]) return cache[symbol];
  const res = http.get(`${envCfg.trading_base}/perpetual/exchangeInfo`, { headers: { 'User-Agent': 'Mozilla/5.0' } });
  if (res.status !== 200) return null; // caller checks for null and skips the iteration cleanly
  let body;
  try {
    body = JSON.parse(res.body);
  } catch (e) {
    return null; // non-JSON body (e.g. a transient error page) -- same as a non-200, not a crash
  }
  const info = (body.symbols || []).find((s) => s.symbol === symbol);
  if (!info) return null;
  const priceFilter = info.filters.find((f) => f.filter_type === 'PRICE_FILTER').config;
  const lotSize = info.filters.find((f) => f.filter_type === 'LOT_SIZE').config;
  cache[symbol] = {
    tickSize: parseFloat(priceFilter.tick_size),
    stepSize: parseFloat(lotSize.step_size),
    pricePrecision: info.price_precision,
    amountPrecision: info.amount_precision,
  };
  return cache[symbol];
}

// floors `value` to the nearest `step` and formats to `precision` decimals -- same rounding
// direction as lib/perp.py's round_tick/size_amount (floor, never round up past a real limit).
export function roundToStep(value, step, precision) {
  const rounded = Math.floor(value / step) * step;
  return rounded.toFixed(precision);
}
