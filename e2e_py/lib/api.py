"""pure API helpers, no browser/page. ported from e2e/lib/actions.ts fetchLiveMarkPrice/
matchRestingOrderWithApiCounterparty. playwright request context, not requests -- same as
every other suite here.
"""
from __future__ import annotations

from playwright.sync_api import Playwright

from lib.arrangement import Arrangement


def fetch_live_mark_price(playwright: Playwright, arrangement: Arrangement) -> float:
    ctx = playwright.request.new_context(
        base_url=arrangement.env.trading_base,
        extra_http_headers={"Authorization": f"Bearer {arrangement.maker.access_token}"},
    )
    try:
        res = ctx.get(f"/perpetual/funding-rates/current?symbols={arrangement.market}")
        body = res.json()
        rates = body.get("funding_rates") or [{}]
        mark = float(rates[0].get("mark_price") or "0")
        if not mark > 0:
            raise RuntimeError(f"could not read a live mark price: {body}")
        return mark
    finally:
        ctx.dispose()


def match_resting_order_with_api_counterparty(playwright: Playwright, arrangement: Arrangement) -> None:
    """shared live book, thin market -- sweep whole bid book, not just our order (stale bids
    from interrupted runs, no creds to cancel; server tick-rounds prices, can't filter by ours)."""
    ctx = playwright.request.new_context(base_url=arrangement.env.trading_base)
    try:
        book_res = ctx.get(f"/orderbook?symbol={arrangement.market}")
        book = book_res.json()
        bids = book.get("bids") or []
        sweep_amount = sum(float(size) for _, size in bids)
        if sweep_amount < 0.01:
            raise RuntimeError(f"no bids in the live book to sweep (bids: {bids})")

        order_res = ctx.post(
            "/perpetual/order",
            headers={"Authorization": f"Bearer {arrangement.maker.access_token}"},
            data={
                "app_session_id": arrangement.maker.address,
                "market": arrangement.market,
                "side": "sell",
                "direction": "short",
                "type": "market",
                "amount": f"{sweep_amount:.3f}",
                "leverage": "10",
            },
        )
        if not order_res.ok:
            raise RuntimeError(f"maker counter-order failed: HTTP {order_res.status} {order_res.text()}")
    finally:
        ctx.dispose()
