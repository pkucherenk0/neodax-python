"""pure API helpers, no browser/page. ported from e2e/lib/actions.ts fetchLiveMarkPrice/
matchRestingOrderWithApiCounterparty. playwright request context, not requests -- same as
every other suite here.
"""
from __future__ import annotations

from eth_account import Account
from eth_account.messages import encode_defunct
from eth_account.signers.local import LocalAccount
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


def _authenticate(playwright: Playwright, auth_base: str, account: LocalAccount) -> str:
    """fresh challenge/sign/verify round trip -- returns a live access_token. TTL is 60s and no
    refresh_token is saved anywhere in this project's arrangement, so re-deriving from the raw
    secret is the only reliable way to get a live token this late in a run -- the original
    tokens minted before the UI flow even started are very likely dead by cleanup time
    (position confirmation alone can poll for up to 45s)."""
    ctx = playwright.request.new_context(base_url=auth_base)
    try:
        address = account.address
        ch = ctx.post("/auth/challenge", data={"wallet_address": address})
        challenge = ch.json()["challenge"]
        signed = Account.sign_message(encode_defunct(text=challenge), private_key=account.key)
        sig = signed.signature.hex()
        sig = sig if sig.startswith("0x") else "0x" + sig
        v = ctx.post("/auth/verify", data={"wallet_address": address, "challenge": challenge, "signature": sig})
        return v.json()["access_token"]
    finally:
        ctx.dispose()


def close_all_perp_positions(playwright: Playwright, arrangement: Arrangement, account: LocalAccount, address: str) -> None:
    """FALLBACK ONLY -- see flatten_subject_and_maker for the primary mechanism. POST
    /perpetual/positions/close dispatches a reduce-only IOC MARKET order -- confirmed live: on
    this thin market it found no counterparty and expired unfilled (0 fill). Kept as a best-
    effort last resort for any residue flatten_subject_and_maker's own cross couldn't clear
    (mirrors root lib/perp.py's flatten_perp_pair, which has the exact same fallback for the
    exact same reason). Never raises."""
    access_token = _authenticate(playwright, arrangement.env.auth_base, account)
    ctx = playwright.request.new_context(
        base_url=arrangement.env.trading_base,
        extra_http_headers={"Authorization": f"Bearer {access_token}"},
    )
    try:
        for _ in range(20):
            res = ctx.post("/perpetual/positions/close",
                           data={"app_session_id": address, "market": arrangement.market})
            if not res.ok:
                return
            try:
                body = res.json()
            except Exception:
                return
            if not body.get("partial"):
                return  # no more legs left
    except Exception:
        pass  # best-effort, never fail the test over teardown
    finally:
        ctx.dispose()


def _get_open_leg(playwright: Playwright, arrangement: Arrangement, access_token: str, address: str) -> tuple[str, float] | None:
    """(direction, amount) for the first open leg on arrangement.market, or None if flat."""
    ctx = playwright.request.new_context(
        base_url=arrangement.env.trading_base, extra_http_headers={"Authorization": f"Bearer {access_token}"},
    )
    try:
        res = ctx.get(f"/perpetual/positions?app_session_id={address}&market={arrangement.market}")
        if not res.ok:
            return None
        for p in res.json():
            amt = float(p.get("amount") or 0)
            if amt > 0:
                return p.get("direction"), amt
        return None
    finally:
        ctx.dispose()


def flatten_subject_and_maker(playwright: Playwright, arrangement: Arrangement) -> None:
    """flatten subject (long) + maker (short) by crossing their positions reduce-only against
    EACH OTHER -- never rely on external book liquidity. confirmed live: a plain reduce-only
    IOC market close alone can't find a counterparty on this thin market (see
    close_all_perp_positions) -- the exact reason root lib/perp.py's flatten_perp_pair exists;
    ported here. maker rests a reduce-only GTC bid near mark; subject market-sells (reduce-
    only) into it. falls back to close_all_perp_positions for any residue. best-effort:
    never raises."""
    try:
        subject_token = _authenticate(playwright, arrangement.env.auth_base, arrangement.subject.to_account())
        maker_token = _authenticate(playwright, arrangement.env.auth_base, arrangement.maker.to_account())

        subject_leg = _get_open_leg(playwright, arrangement, subject_token, arrangement.subject.address)
        maker_leg = _get_open_leg(playwright, arrangement, maker_token, arrangement.maker.address)

        if subject_leg and maker_leg and subject_leg[0] == "long" and maker_leg[0] == "short":
            size = f"{min(subject_leg[1], maker_leg[1]):.2f}"
            mark = fetch_live_mark_price(playwright, arrangement)
            ctx = playwright.request.new_context(base_url=arrangement.env.trading_base)
            try:
                ctx.post("/perpetual/order", headers={"Authorization": f"Bearer {maker_token}"}, data={
                    "app_session_id": arrangement.maker.address, "market": arrangement.market, "side": "buy",
                    "direction": "short", "type": "limit", "amount": size, "price": f"{mark:.2f}",
                    "time_in_force": "gtc", "reduce_only": True, "leverage": "10",
                })
                ctx.post("/perpetual/order", headers={"Authorization": f"Bearer {subject_token}"}, data={
                    "app_session_id": arrangement.subject.address, "market": arrangement.market, "side": "sell",
                    "direction": "long", "type": "market", "amount": size, "reduce_only": True, "leverage": "10",
                })
            finally:
                ctx.dispose()
    except Exception:
        pass
    finally:
        close_all_perp_positions(playwright, arrangement, arrangement.subject.to_account(), arrangement.subject.address)
        close_all_perp_positions(playwright, arrangement, arrangement.maker.to_account(), arrangement.maker.address)
