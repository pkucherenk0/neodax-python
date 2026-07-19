"""spot order placement + fill readback. port of lib/spot.ts."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from lib.artifacts import record_order, record_trade
from lib.http import ResilientClient
from lib.schemas import (
    Orderbook,
    SpotAccountRich,
    SpotCancelResponse,
    SpotOrderItem,
    SpotOrderResponse,
    SpotOrdersResponse,
    SpotTradesResponse,
)
from lib.validate import parsed_json


@dataclass(frozen=True)
class SpotTopOfBook:
    best_bid: float
    best_ask: float


def _level_price(levels: list[list[str]] | None) -> float:
    try:
        return float(levels[0][0]) if levels else 0.0
    except (ValueError, IndexError):
        return 0.0


def get_spot_top_of_book(client: ResilientClient, market: str) -> SpotTopOfBook:
    """GET /orderbook?symbol= — spot top of book (0 on empty/unavailable side)."""
    body = parsed_json(client.get(f"/orderbook?symbol={quote(market)}"), Orderbook)
    return SpotTopOfBook(best_bid=_level_price(body.bids), best_ask=_level_price(body.asks))


def spot_reference_price(top: SpotTopOfBook) -> float:
    """reference price from live book (best bid, else best ask, else 0)."""
    return top.best_bid if top.best_bid > 0 else top.best_ask


def spot_resting_sell_price(top: SpotTopOfBook, tick: float = 0.01, dp: int = 2) -> str:
    """resting SELL price one tick inside spread so maker becomes best ask = guaranteed lift
    target for market buy. derive from live book, not hardcoded level (would cross = become
    taker, or miss our order if market moved). fall back for thin/one-sided book."""
    if top.best_bid > 0 and top.best_ask > top.best_bid + tick:
        return f"{max(top.best_ask - tick, top.best_bid + tick):.{dp}f}"
    if top.best_ask > 0:
        return f"{top.best_ask:.{dp}f}"
    if top.best_bid > 0:
        return f"{top.best_bid + tick:.{dp}f}"
    return f"{0:.{dp}f}"  # empty book. market buy can't fill anyway. fill poll surface it


def create_spot_order(
    client: ResilientClient,
    app_session_id: str,
    *,
    market: str,
    side: str,
    type: str,
    amount: str,
    price: str | None = None,
    tif: str | None = None,
) -> str:
    """POST /spot/order. return order_uuid. NOTE: pass order-placing client with
    retry_on_5xx=False to avoid double execution on transient 5xx."""
    data: dict = {"app_session_id": app_session_id, "market": market, "side": side, "type": type, "amount": amount}
    if price:
        data["price"] = price
    if tif:
        data["time_in_force"] = tif
    res = client.post("/spot/order", data=data)
    if not res.ok:
        raise AssertionError(f"spot order failed: HTTP {res.status} {res.text()}")
    body = parsed_json(res, SpotOrderResponse)
    record_order(venue="spot", market=market, order_uuid=body.order_uuid, side=side, order_type=type,
                 amount=amount, price=price, app_session_id=app_session_id)
    return body.order_uuid


@dataclass(frozen=True)
class SpotFillSummary:
    fills: int
    amount: float  # base filled
    total: float  # quote filled
    fee: float
    is_maker: bool
    # charged fee as rate. buys pay fee in base (fee/amount). sells in quote (fee/total).
    charged_rate: float


def get_spot_fills_for_order(client: ResilientClient, app_session_id: str, market: str, order_uuid: str) -> SpotFillSummary:
    """aggregate fills for one order_uuid from /spot/trades."""
    body = parsed_json(
        client.get(f"/spot/trades?app_session_id={quote(app_session_id)}&market={quote(market)}&page_size=100"),
        SpotTradesResponse,
    )
    mine = [t for t in body.trades if t.order_id == order_uuid]
    amount = sum(float(t.amount) for t in mine)
    total = sum(float(t.total) for t in mine)
    fee = sum(float(t.fee) for t in mine)
    is_maker = any(t.is_maker for t in mine)
    is_buyer = any(t.is_buyer for t in mine)
    record_trade(venue="spot", market=market, order_uuid=order_uuid, trade_ids=[t.id for t in mine],
                 fills=len(mine), app_session_id=app_session_id)
    denom = amount if is_buyer else total
    return SpotFillSummary(fills=len(mine), amount=amount, total=total, fee=fee, is_maker=is_maker,
                           charged_rate=fee / denom if denom > 0 else 0.0)


# --- spot account / orders (basic spot API surface) ---


@dataclass(frozen=True)
class SpotBalanceSnapshot:
    available: float
    total: float
    locked: float  # reserved by open (resting) orders


def _num(s: str | None) -> float:
    return float(s) if s else 0.0


def get_spot_balance_snapshot(client: ResilientClient, app_session_id: str, asset: str) -> SpotBalanceSnapshot:
    """GET /spot/account -> snapshot of one asset balances."""
    body = parsed_json(
        client.get(f"/spot/account?app_session_id={quote(app_session_id)}&asset={quote(asset)}"), SpotAccountRich
    )
    e = next((b for b in body.balances if b.asset_symbol == asset), None)
    return SpotBalanceSnapshot(
        available=_num(e.available_balance if e else None),
        total=_num(e.total_balance if e else None),
        locked=_num(e.locked_balance if e else None),
    )


def get_spot_open_orders(client: ResilientClient, app_session_id: str, market: str) -> list[SpotOrderItem]:
    """GET /spot/open_orders -> currently-open orders (filtered to market)."""
    body = parsed_json(
        client.get(f"/spot/open_orders?app_session_id={quote(app_session_id)}&market={quote(market)}&page_size=100"),
        SpotOrdersResponse,
    )
    return body.orders


def get_spot_orders(client: ResilientClient, app_session_id: str, market: str) -> list[SpotOrderItem]:
    """GET /spot/orders -> all orders incl. history (filtered to market)."""
    body = parsed_json(
        client.get(f"/spot/orders?app_session_id={quote(app_session_id)}&market={quote(market)}&page_size=100"),
        SpotOrdersResponse,
    )
    return body.orders


def cancel_spot_order(order_client: ResilientClient, app_session_id: str, market: str, order_uuid: str) -> SpotCancelResponse:
    """DELETE /spot/order -> cancel one order (JSON body. async)."""
    res = order_client.delete("/spot/order", data={"app_session_id": app_session_id, "market": market, "order_uuid": order_uuid})
    if not res.ok:
        raise AssertionError(f"spot cancel failed: HTTP {res.status} {res.text()}")
    return parsed_json(res, SpotCancelResponse)
