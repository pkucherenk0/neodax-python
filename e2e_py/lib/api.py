"""Pure API helpers -- no browser/page involved. Ported from e2e/lib/actions.ts's
fetchLiveMarkPrice/matchRestingOrderWithApiCounterparty. Uses Playwright's own request context
(same as every other suite in this repo) rather than adding a new `requests` dependency.
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
    """Shared live book, thin market -- sweep the entire bid book rather than target our own
    order (can't reliably target just ours: interrupted past runs can leave stale resting bids
    we have no credentials to cancel, and the server tick-rounds submitted prices so filtering
    by our computed price is unreliable)."""
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
