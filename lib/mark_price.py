"""mark-price simulator (YEN-2545 test). port of lib/mark-price.ts.

POST {faucet}/api/simulate-mark-price publish a mark-price event to kafka -> feeds the
liquidation scanner, so we can drive a position into liquidation on UAT. client scoped to
the faucet host. no auth.

WARNING: injection is MARKET-WIDE. it can liquidate other accounts on that market. use small
moves, restore the real mark after (see restore_mark_price).

python note: the TS holdMarkPrice used a background async loop. playwright sync-api objects
are not thread-safe, so the port pumps injections INLINE: callers re-inject inside their own
poll loop (see MarkHolder.pump / lib/liquidation.py). same effect — the injected extreme is
re-published every ~1.5s so every 2s scanner window sees it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from lib.http import ResilientClient
from lib.schemas import SimulateMarkPrice
from lib.validate import parsed_json


@dataclass(frozen=True)
class MarkPriceInject:
    market: str
    mark_price: str  # required
    index_price: str | None = None  # default = mark on the server if omitted
    funding_rate: str | None = None
    next_funding_time: str | None = None  # RFC3339


def simulate_mark_price(faucet_client: ResilientClient, inj: MarkPriceInject) -> SimulateMarkPrice:
    """inject one mark-price event. returns the parsed echo ({success, event_id, ...})."""
    data: dict = {"market": inj.market, "mark_price": inj.mark_price}
    if inj.index_price:
        data["index_price"] = inj.index_price
    if inj.funding_rate:
        data["funding_rate"] = inj.funding_rate
    if inj.next_funding_time:
        data["next_funding_time"] = inj.next_funding_time
    res = faucet_client.post("/api/simulate-mark-price", data=data)
    if not res.ok:
        raise AssertionError(f"simulate-mark-price failed: HTTP {res.status} {res.text()}")
    return parsed_json(res, SimulateMarkPrice)


class MarkHolder:
    """the injected mark only holds ~2s then reverts to the real feed. to KEEP a price you must
    re-submit every ~2s. pump() re-injects every injection when interval_s elapsed — call it
    from inside your poll loop (single-threaded equivalent of TS holdMarkPrice)."""

    def __init__(self, faucet_client: ResilientClient, injections: list[MarkPriceInject], interval_s: float = 1.5) -> None:
        self._client = faucet_client
        self._injections = injections
        self._interval = interval_s
        self._last = 0.0

    def pump(self) -> None:
        if time.monotonic() - self._last < self._interval:
            return
        self._last = time.monotonic()
        for inj in self._injections:
            try:
                simulate_mark_price(self._client, inj)
            except Exception:
                pass  # best-effort. next pump retries


def restore_mark_price(faucet_client: ResilientClient, market: str, mark_price: str) -> None:
    """re-publish a mark so the market returns to `mark_price` (best-effort: never raises).
    call after a test so a leftover injected extreme does not keep hitting other accounts."""
    try:
        simulate_mark_price(faucet_client, MarkPriceInject(market=market, mark_price=mark_price))
    except Exception:
        return
