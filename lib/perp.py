"""perp market metadata (lot/tick/min-notional), pricing (mark + top of book), order sizing,
order placement + fill readback. port of lib/perp.ts.

clients scoped to trading host. financial values arrive as strings, float()'d.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from urllib.parse import quote

from lib.artifacts import record_order, record_trade
from lib.http import ResilientClient
from lib.schemas import (
    Orderbook,
    PerpAccount,
    PerpCancelResponse,
    PerpExchangeInfo,
    PerpFundingRates,
    PerpLeverageResponse,
    PerpOrderItem,
    PerpOrderResponse,
    PerpOrdersResponse,
    PerpPosition,
    PerpPositionHistoryDetail,
    PerpPositionHistoryResponse,
    PerpTradesResponse,
    PerpTransactionHistory,
    perp_balance_rich_schema,
    perp_positions_schema,
)
from lib.validate import parsed_json


@dataclass(frozen=True)
class PerpMarket:
    market: str
    amount_precision: int
    price_precision: int
    step_size: float
    tick_size: float
    min_qty: float
    min_notional: float
    max_allowed_leverage: float  # per-market leverage cap. 0 if exchange didn't report one
    resolved: bool  # False when wanted symbol not found -> fell back


def _filter_config(filters, filter_type: str) -> dict:
    for f in filters or []:
        if f.filter_type == filter_type:
            return f.config or {}
    return {}


def _num_field(cfg: dict, key: str) -> float:
    # parse filter-config field expected numeric string. NaN if absent/non-string.
    v = cfg.get(key)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return float("nan")
    return float("nan")


def _or(v: float, fallback: float) -> float:
    return v if (not math.isnan(v) and v != 0) else fallback


def resolve_perp_market(trading_client: ResilientClient, wanted: str) -> PerpMarket:
    """GET /perpetual/exchangeInfo -> wanted symbol lot/tick/notional filters (honour precision)."""
    body = parsed_json(trading_client.get("/perpetual/exchangeInfo"), PerpExchangeInfo)
    symbols = body.symbols
    trading = [s for s in symbols if (s.status or "").upper() == "TRADING"]
    chosen = (
        next((s for s in trading if s.symbol == wanted), None)
        or next((s for s in symbols if s.symbol == wanted), None)
        or (trading[0] if trading else None)
        or (symbols[0] if symbols else None)
    )
    if chosen is None:
        return PerpMarket(market=wanted, amount_precision=3, price_precision=2, step_size=0.001,
                          tick_size=0.01, min_qty=0, min_notional=0, max_allowed_leverage=0, resolved=False)
    lot = _filter_config(chosen.filters, "LOT_SIZE")
    notional = _filter_config(chosen.filters, "MIN_NOTIONAL")
    price_f = _filter_config(chosen.filters, "PRICE_FILTER")
    amount_precision = chosen.amount_precision if chosen.amount_precision is not None else 3
    price_precision = chosen.price_precision if chosen.price_precision is not None else 2
    try:
        max_lev = float(chosen.max_allowed_leverage or "")
    except ValueError:
        max_lev = 0.0
    return PerpMarket(
        market=chosen.symbol,
        amount_precision=amount_precision,
        price_precision=price_precision,
        step_size=_or(_num_field(lot, "step_size"), 10**-amount_precision),
        tick_size=_or(_num_field(price_f, "tick_size"), 0.01),
        min_qty=0 if math.isnan(_num_field(lot, "min_qty")) else _num_field(lot, "min_qty"),
        min_notional=0 if math.isnan(_num_field(notional, "min_notional")) else _num_field(notional, "min_notional"),
        max_allowed_leverage=max_lev if max_lev > 0 else 0,
        resolved=chosen.symbol == wanted,
    )


def get_perp_mark_price(trading_client: ResilientClient, market: str) -> float:
    """GET /perpetual/funding-rates/current?symbols= -> market mark price (0 if unavailable)."""
    body = parsed_json(
        trading_client.get(f"/perpetual/funding-rates/current?symbols={quote(market)}"), PerpFundingRates
    )
    entry = next((r for r in (body.funding_rates or []) if r.market == market), None)
    try:
        mark = float(entry.mark_price) if entry and entry.mark_price else 0.0
    except ValueError:
        mark = 0.0
    return mark if mark > 0 else 0.0


@dataclass(frozen=True)
class TopOfBook:
    best_bid: float
    best_ask: float


def _level_price(levels: list[list[str]] | None) -> float:
    try:
        return float(levels[0][0]) if levels else 0.0
    except (ValueError, IndexError):
        return 0.0


def get_perp_top_of_book(trading_client: ResilientClient, market: str) -> TopOfBook:
    """GET /orderbook?symbol= -> perp top of book (0 on empty/unavailable side)."""
    body = parsed_json(trading_client.get(f"/orderbook?symbol={quote(market)}"), Orderbook)
    return TopOfBook(best_bid=_level_price(body.bids), best_ask=_level_price(body.asks))


def _decimals_of(step: float) -> int:
    s = f"{step:.10f}".rstrip("0")
    return len(s.split(".")[1]) if "." in s else 0


def size_amount(notional_usd: float, price: float, mkt: PerpMarket) -> str:
    """size target USD notional -> lot-aligned base amount, honour step/minQty/minNotional."""
    step = mkt.step_size if mkt.step_size > 0 else 10**-mkt.amount_precision
    decimals = max(_decimals_of(step), mkt.amount_precision)
    min_notional = mkt.min_notional * 1.05 if mkt.min_notional > 0 else 0
    qty = max(notional_usd, min_notional) / price
    qty = math.ceil(qty / step) * step
    if mkt.min_qty > 0 and qty < mkt.min_qty:
        qty = mkt.min_qty
    while mkt.min_notional > 0 and qty * price < mkt.min_notional:
        qty += step
    return f"{qty:.{decimals}f}"


def round_tick(price: float, tick: float, dp: int) -> str:
    """round price to market tick, format at price precision."""
    return f"{round(price / tick) * tick:.{dp}f}"


def maker_price_inside_spread(rest_side: str, top: TopOfBook, mark: float, mkt: PerpMarket) -> str:
    """tick-aligned maker price that becomes best price on its side (market order on other side
    guaranteed to match). maker SELL -> one tick below best ask. maker BUY -> one tick above best
    bid. no real spread -> fall back to mark-relative quote. `rest_side` = maker own side."""
    tick = mkt.tick_size if mkt.tick_size > 0 else 0.01
    has_spread = top.best_bid > 0 and top.best_ask > top.best_bid + tick / 2
    if has_spread:
        if rest_side == "sell":
            px = max(top.best_ask - tick, top.best_bid + tick)  # best ask, still strictly above best bid
        else:
            px = min(top.best_bid + tick, top.best_ask - tick)  # best bid, still strictly below best ask
    else:
        px = mark * 1.001 if rest_side == "sell" else mark * 0.999  # empty/1-tick book -> mark-relative
    return round_tick(px, tick, mkt.price_precision)


def create_perp_order(
    client: ResilientClient,
    app_session_id: str,
    *,
    market: str,
    side: str,
    direction: str,
    type: str,
    amount: str,
    leverage: float | str,
    price: str | None = None,
    tif: str | None = None,
    reduce_only: bool = False,
) -> str:
    """POST /perpetual/order. return order_uuid. pass order-placing client with
    retry_on_5xx=False to avoid double execution on transient 5xx."""
    data: dict = {
        "app_session_id": app_session_id,
        "market": market,
        "side": side,
        "direction": direction,
        "type": type,
        "amount": amount,
        "reduce_only": reduce_only,
        "leverage": str(leverage),
    }
    if price:
        data["price"] = price
    if tif:
        data["time_in_force"] = tif
    res = client.post("/perpetual/order", data=data)
    if not res.ok:
        raise AssertionError(f"perp order failed: HTTP {res.status} {res.text()}")
    body = parsed_json(res, PerpOrderResponse)
    # save order id + context (CONVENTIONS §12)
    record_order(venue="perp", market=market, order_uuid=body.order_uuid, side=side, direction=direction,
                 order_type=type, amount=amount, price=price, app_session_id=app_session_id)
    return body.order_uuid


def close_all_perp_positions(order_client: ResilientClient, app_session_id: str, market: str | None = None) -> None:
    """POST /perpetual/positions/close — flatten open position legs (reduce_only per leg). loop
    until nothing remains. best-effort TEARDOWN: never raise, safe from teardown fixtures."""
    try:
        for _ in range(20):
            data: dict = {"app_session_id": app_session_id}
            if market:
                data["market"] = market
            res = order_client.post("/perpetual/positions/close", data=data)
            if not res.ok:
                return
            try:
                body = res.json()
            except Exception:
                body = {}
            if not body.get("partial"):
                return  # no more legs left
    except Exception:
        return  # best-effort teardown. close failure must not fail test run


@dataclass(frozen=True)
class PerpFillSummary:
    fills: int
    amount: float  # base filled
    notional: float  # sum amount x price (USD)
    fee: float  # quote-denominated (USDT)
    is_maker: bool
    charged_rate: float  # fee / notional


def get_perp_fills_for_order(
    client: ResilientClient, app_session_id: str, market: str, order_uuid: str
) -> PerpFillSummary:
    """aggregate fills for one order_uuid from /perpetual/trades."""
    body = parsed_json(
        client.get(f"/perpetual/trades?app_session_id={quote(app_session_id)}&market={quote(market)}&page_size=100"),
        PerpTradesResponse,
    )
    mine = [t for t in body.trades if t.order_uuid == order_uuid]
    amount = sum(float(t.amount) for t in mine)
    notional = sum(float(t.amount) * float(t.price) for t in mine)
    fee = sum(float(t.fee) for t in mine)
    is_maker = any(t.is_maker for t in mine)
    record_trade(venue="perp", market=market, order_uuid=order_uuid, trade_ids=[], fills=len(mine),
                 app_session_id=app_session_id)
    return PerpFillSummary(fills=len(mine), amount=amount, notional=notional, fee=fee, is_maker=is_maker,
                           charged_rate=fee / notional if notional > 0 else 0.0)


@dataclass(frozen=True)
class PerpTrade:
    """one /perpetual/trades row, numbers parsed. exec_type in trade|liquidation|liquidation_takeover|adl.
    total = amount x price (signed). price/total are the fields YEN-3325 corrupted (negative on takeover)."""

    order_uuid: str
    market: str
    amount: float
    price: float
    total: float  # wire `total` if present, else amount x price
    fee: float
    is_maker: bool
    is_buyer: bool | None
    exec_type: str  # '' if BE omitted it
    executed_at: str | None


def get_perp_trades(client: ResilientClient, app_session_id: str, market: str, page_size: int = 100) -> list[PerpTrade]:
    """GET /perpetual/trades -> parsed trade rows (all exec_types) for one market, newest page.
    used by liquidation invariant checks (price/total sign)."""
    body = parsed_json(
        client.get(f"/perpetual/trades?app_session_id={quote(app_session_id)}&market={quote(market)}&page_size={page_size}"),
        PerpTradesResponse,
    )
    out: list[PerpTrade] = []
    for t in body.trades:
        amount = float(t.amount)
        price = float(t.price)
        out.append(PerpTrade(
            order_uuid=t.order_uuid, market=t.market, amount=amount, price=price,
            total=float(t.total) if t.total is not None else amount * price,
            fee=float(t.fee), is_maker=t.is_maker, is_buyer=t.is_buyer,
            exec_type=t.exec_type or "", executed_at=t.executed_at,
        ))
    return out


# --- account / balance / orders / positions / leverage (basic perp API surface) ---


@dataclass(frozen=True)
class PerpBalanceSnapshot:
    available: float
    total: float
    allocated: float  # margin allocated to open positions
    locked: float  # reserved by open (resting) orders


def _num(s: str | None) -> float:
    return float(s) if s else 0.0


def get_perp_balance_snapshot(client: ResilientClient, app_session_id: str, asset: str = "USDT") -> PerpBalanceSnapshot:
    """GET /perpetual/balance -> snapshot of one asset balances (default USDT collateral)."""
    rows = parsed_json(client.get(f"/perpetual/balance?app_session_id={quote(app_session_id)}"), perp_balance_rich_schema)
    e = next((r for r in rows if r.asset_symbol == asset), None)
    return PerpBalanceSnapshot(
        available=_num(e.available_balance if e else None),
        total=_num(e.total_balance if e else None),
        allocated=_num(e.allocated_balance if e else None),
        locked=_num(e.locked_balance if e else None),
    )


def get_perp_account(client: ResilientClient, app_session_id: str) -> PerpAccount:
    """GET /perpetual/account -> full account (equity, per-market initial leverage, margin modes)."""
    return parsed_json(client.get(f"/perpetual/account?app_session_id={quote(app_session_id)}"), PerpAccount)


def set_perp_leverage(order_client: ResilientClient, app_session_id: str, market: str, leverage: float | str) -> PerpLeverageResponse:
    """POST /perpetual/leverage -> set account initial leverage for market. order-placing client."""
    res = order_client.post("/perpetual/leverage", data={"app_session_id": app_session_id, "market": market, "leverage": str(leverage)})
    if not res.ok:
        raise AssertionError(f"set leverage failed: HTTP {res.status} {res.text()}")
    return parsed_json(res, PerpLeverageResponse)


def get_perp_open_orders(client: ResilientClient, app_session_id: str, market: str) -> list[PerpOrderItem]:
    """GET /perpetual/open_orders -> currently-open orders (filtered to market)."""
    body = parsed_json(
        client.get(f"/perpetual/open_orders?app_session_id={quote(app_session_id)}&market={quote(market)}&page_size=100"),
        PerpOrdersResponse,
    )
    return body.orders


def get_perp_orders(client: ResilientClient, app_session_id: str, market: str) -> list[PerpOrderItem]:
    """GET /perpetual/orders -> all orders incl. closed/cancelled history (filtered to market)."""
    body = parsed_json(
        client.get(f"/perpetual/orders?app_session_id={quote(app_session_id)}&market={quote(market)}&page_size=100"),
        PerpOrdersResponse,
    )
    return body.orders


def cancel_perp_order(order_client: ResilientClient, app_session_id: str, market: str, order_uuid: str) -> PerpCancelResponse:
    """DELETE /perpetual/order -> cancel one order (async. leaves open_orders shortly after)."""
    res = order_client.delete("/perpetual/order", data={"app_session_id": app_session_id, "market": market, "order_uuid": order_uuid})
    if not res.ok:
        raise AssertionError(f"cancel order failed: HTTP {res.status} {res.text()}")
    return parsed_json(res, PerpCancelResponse)


def get_perp_positions(client: ResilientClient, app_session_id: str, market: str | None = None) -> list[PerpPosition]:
    """GET /perpetual/positions -> open positions (optionally filtered to market)."""
    q = f"&market={quote(market)}" if market else ""
    return parsed_json(client.get(f"/perpetual/positions?app_session_id={quote(app_session_id)}{q}"), perp_positions_schema)


def get_perp_position_history(
    client: ResilientClient, app_session_id: str, *, market: str | None = None, page_size: int | None = None
) -> PerpPositionHistoryResponse:
    """GET /perpetual/position-history -> closed-position lifecycle records (paginated envelope).
    newest closed_at first. empty history returns positions:null on wire — normalized to []."""
    parts = [f"app_session_id={quote(app_session_id)}"]
    if market:
        parts.append(f"market={quote(market)}")
    if page_size:
        parts.append(f"page_size={page_size}")
    body = parsed_json(client.get(f"/perpetual/position-history?{'&'.join(parts)}"), PerpPositionHistoryResponse)
    if body.positions is None:
        body.positions = []
    return body


def get_perp_position_history_detail(
    client: ResilientClient, app_session_id: str, position_id: str, *, page_size: int | None = None
) -> PerpPositionHistoryDetail:
    """GET /perpetual/position-history/:id -> linked fills (open->close), ascending by time."""
    parts = [f"app_session_id={quote(app_session_id)}"]
    if page_size:
        parts.append(f"page_size={page_size}")
    body = parsed_json(client.get(f"/perpetual/position-history/{quote(position_id)}?{'&'.join(parts)}"), PerpPositionHistoryDetail)
    if body.fills is None:
        body.fills = []
    return body


def get_perp_transaction_history(
    client: ResilientClient, app_session_id: str, *, type: str | None = None, market: str | None = None,
    page_size: int | None = None,
) -> PerpTransactionHistory:
    """GET /perpetual/transaction/history -> ledger rows. type filter (e.g. 'liquidation_partial' =
    Stage0 tiered-reduction marker, YEN-2545). items may be null when empty -> normalized to []."""
    parts = [f"app_session_id={quote(app_session_id)}"]
    if type:
        parts.append(f"type={quote(type)}")
    if market:
        parts.append(f"market={quote(market)}")
    if page_size:
        parts.append(f"page_size={page_size}")
    body = parsed_json(client.get(f"/perpetual/transaction/history?{'&'.join(parts)}"), PerpTransactionHistory)
    if body.items is None:
        body.items = []
    return body


@dataclass(frozen=True)
class PerpParty:
    """trading party (subject or counterparty) for seeded closes."""

    order_client: ResilientClient
    trading_client: ResilientClient
    app_session_id: str


def _net_perp_position(positions: list[PerpPosition]) -> tuple[str, float]:
    """net position for market: signed sum of long/short legs -> (direction, amount)."""
    long = sum(float(p.amount) for p in positions if p.direction == "long")
    short = sum(float(p.amount) for p in positions if p.direction == "short")
    net = long - short
    return ("long", net) if net >= 0 else ("short", -net)


def wait_for_perp_fill(
    client: ResilientClient, app_session_id: str, market: str, order_uuid: str,
    timeout_s: float, poll_s: float = 0.5,
) -> PerpFillSummary:
    """poll /perpetual/trades until order shows fill or budget elapses. return fill summary
    (fills==0 if never appears). used by volume driver."""
    deadline = time.monotonic() + timeout_s
    fill = get_perp_fills_for_order(client, app_session_id, market, order_uuid)
    while fill.fills == 0 and time.monotonic() < deadline:
        time.sleep(poll_s)
        fill = get_perp_fills_for_order(client, app_session_id, market, order_uuid)
    return fill


def flatten_perp_pair(a: PerpParty, b: PerpParty, market: PerpMarket, leverage: float | str, max_rounds: int = 6) -> None:
    """SEEDED teardown: flatten two mutually-hedged accounts by crossing opposing positions
    REDUCE-ONLY against each other. never rely on external book liquidity.

    short-holder rests reduce-only bid one tick inside spread. long-holder reduce-only
    market-sells into it, both legs close together. loop to drain partials, then best-effort
    market close for non-mirrored residue. best-effort: never raise (safe in teardown).
    """
    step_size = market.step_size if market.step_size > 0 else 10**-market.amount_precision
    dp = max(market.amount_precision, 0)
    try:
        for _ in range(max_rounds):
            pos_a = get_perp_positions(a.trading_client, a.app_session_id, market.market)
            pos_b = get_perp_positions(b.trading_client, b.app_session_id, market.market)
            parties = [(a, _net_perp_position(pos_a)), (b, _net_perp_position(pos_b))]
            if all(net[1] < step_size for _, net in parties):
                return  # both flat
            long = next(((p, n) for p, n in parties if n[0] == "long" and n[1] >= step_size), None)
            short = next(((p, n) for p, n in parties if n[0] == "short" and n[1] >= step_size), None)
            if not long or not short:
                break  # not mutually hedged -> fall through to market-close fallback
            size = math.floor(min(long[1][1], short[1][1]) / step_size) * step_size
            if size < step_size:
                break
            amount = f"{size:.{dp}f}"
            mark = get_perp_mark_price(a.trading_client, market.market)
            top = get_perp_top_of_book(a.trading_client, market.market)
            bid = maker_price_inside_spread("buy", top, mark, market)
            # short-holder rest reduce-only bid. long-holder reduce-only market-sell into it.
            create_perp_order(short[0].order_client, short[0].app_session_id, market=market.market,
                              side="buy", direction="short", type="limit", amount=amount, price=bid,
                              tif="gtc", reduce_only=True, leverage=leverage)
            taker_uuid = create_perp_order(long[0].order_client, long[0].app_session_id, market=market.market,
                                           side="sell", direction="long", type="market", amount=amount,
                                           reduce_only=True, leverage=leverage)
            wait_for_perp_fill(long[0].trading_client, long[0].app_session_id, market.market, taker_uuid, 10.0)
        # residue (non-mirrored / partials cross couldn't clear). best-effort market close.
        close_all_perp_positions(a.order_client, a.app_session_id, market.market)
        close_all_perp_positions(b.order_client, b.app_session_id, market.market)
    except Exception:
        return  # best-effort teardown. never fail run
