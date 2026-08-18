"""perp position history (PERP-2548).

GET /perpetual/position-history (closed-position lifecycle list) and
GET /perpetual/position-history/:id (linked open->close fills). both require auth.
"""
import time

import pytest

from configs.competition import position_history
from lib.perp import (
    PerpParty,
    close_all_perp_positions,
    create_perp_order,
    flatten_perp_pair,
    get_perp_mark_price,
    get_perp_position_history,
    get_perp_position_history_detail,
    get_perp_positions,
    get_perp_top_of_book,
    maker_price_inside_spread,
    resolve_perp_market,
    size_amount,
    wait_for_perp_fill,
)
from lib.poll import poll_until
from lib.report import record, record_check, step
from lib.schemas import FlatError
from lib.validate import parsed_json

LEVERAGE = position_history.leverage


def long_size(positions) -> float:
    # total long exposure. delta-based. worker account shared (AGENTS.md).
    return sum(float(p.amount) for p in positions if p.direction == "long")


def _parse_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        from datetime import datetime

        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


@pytest.mark.stateless
class TestPerpPositionHistoryContract:
    def test_requires_app_session_id_on_the_list_400_missing_parameter(self, fresh_wallet, env):
        # arrange — authenticated, but no app_session_id query. checked before any account lookup.
        wallet = fresh_wallet()
        client = env.client_for(wallet.jwt, env.trading_base)

        # act
        res = client.get("/perpetual/position-history")

        # assert
        assert res.status == 400, "missing app_session_id -> 400"
        body = parsed_json(res, FlatError)
        record_check(name="list error code == missing_parameter", passed=body.error == "missing_parameter",
                     detail=body.model_dump())
        assert body.error == "missing_parameter", "list missing-param error code"

    def test_rejects_out_of_range_list_page_size_400_validation_failed(self, fresh_wallet, env):
        # arrange — app_session_id present so page_size validator fires, not missing-param guard.
        wallet = fresh_wallet()
        client = env.client_for(wallet.jwt, env.trading_base)

        # act — one past documented max (1..100).
        res = client.get(f"/perpetual/position-history?app_session_id={wallet.address}"
                         f"&page_size={position_history.list_max_page_size + 1}")

        # assert
        assert res.status == 400, "out-of-range page_size -> 400"
        body = parsed_json(res, FlatError)
        record_check(name="list error code == validation_failed", passed=body.error == "validation_failed",
                     detail=body.model_dump())
        assert body.error == "validation_failed", "list page_size error code"

    def test_requires_app_session_id_on_the_detail_400_missing_parameter(self, fresh_wallet, env):
        # arrange
        wallet = fresh_wallet()
        client = env.client_for(wallet.jwt, env.trading_base)

        # act — id present in path, but no app_session_id. checked first.
        res = client.get(f"/perpetual/position-history/{position_history.unknown_position_id}")

        # assert
        assert res.status == 400, "detail missing app_session_id -> 400"
        body = parsed_json(res, FlatError)
        record_check(name="detail error code == missing_parameter", passed=body.error == "missing_parameter",
                     detail=body.model_dump())
        assert body.error == "missing_parameter", "detail missing-param error code"

    def test_rejects_out_of_range_detail_page_size_400_validation_failed(self, fresh_wallet, env):
        # arrange — app_session_id and id present so page_size validator fires (1..500).
        wallet = fresh_wallet()
        client = env.client_for(wallet.jwt, env.trading_base)

        # act
        res = client.get(f"/perpetual/position-history/{position_history.unknown_position_id}"
                         f"?app_session_id={wallet.address}&page_size={position_history.detail_max_page_size + 1}")

        # assert
        assert res.status == 400, "detail out-of-range page_size -> 400"
        body = parsed_json(res, FlatError)
        record_check(name="detail error code == validation_failed", passed=body.error == "validation_failed",
                     detail=body.model_dump())
        assert body.error == "validation_failed", "detail page_size error code"

    def test_returns_404_for_unknown_position_id_on_the_detail(self, fresh_wallet, env):
        # arrange — valid params, but well-formed id that not exist.
        wallet = fresh_wallet()
        client = env.client_for(wallet.jwt, env.trading_base)

        # act
        res = client.get(f"/perpetual/position-history/{position_history.unknown_position_id}"
                         f"?app_session_id={wallet.address}")

        # assert — not-found business contract.
        assert res.status == 404, "unknown position id -> 404"
        body = parsed_json(res, FlatError)
        record_check(name="detail error code == not_found", passed=body.error == "not_found", detail=body.model_dump())
        assert body.error == "not_found", "unknown-id error code"


# class-scoped (NOT module-autouse): the stateless contract tests above must not provision
# funded accounts. only the lifecycle class below pulls this in via usefixtures.
@pytest.fixture(scope="class")
def _flatten_after(account, perp_maker):
    yield
    m = resolve_perp_market(account.trading_client, position_history.market)
    flatten_perp_pair(
        PerpParty(order_client=account.order_client, trading_client=account.trading_client, app_session_id=account.app_session_id),
        PerpParty(order_client=perp_maker.order_client, trading_client=perp_maker.trading_client, app_session_id=perp_maker.app_session_id),
        m, LEVERAGE,
    )


@pytest.mark.serial
@pytest.mark.usefixtures("_flatten_after")
class TestPerpPositionHistoryLifecycle:
    @pytest.mark.timeout(300)  # first use of account do faucet + transfer + enroll
    def test_1_list_envelope_is_well_formed_and_honours_market_filter(self, account):
        # arrange — real acct that exists. fresh wallets have no perp account.
        m = resolve_perp_market(account.trading_client, position_history.market)

        # act — read the (possibly empty) history for this market.
        body = step("read position history",
                    lambda: get_perp_position_history(account.trading_client, account.app_session_id,
                                                      market=m.market, page_size=50))
        record("history envelope", {"count": len(body.positions), "total": body.total, "page": body.page,
                                    "page_size": body.page_size, "has_more": body.has_more})

        # assert — envelope echoes the requested page_size (real behavioral check; isinstance
        # list would be vacuous — lib normalizes null->[] always, §13) and every returned row
        # is for the requested market.
        all_for_market = all(p.market.upper() == m.market.upper() for p in body.positions)
        record_check(name="envelope echoes requested page_size (50)", passed=body.page_size == 50,
                     detail={"page_size": body.page_size})
        record_check(name="every row matches the market filter", passed=all_for_market,
                     detail=[p.market for p in body.positions])
        assert body.page_size == 50, "envelope echoes the requested page_size"
        assert all_for_market, "market filter honoured"

    @pytest.mark.timeout(360)
    def test_2_fully_closed_position_appears_in_history_with_lifecycle_fields_and_linked_fills(self, account, perp_maker):
        # arrange — start FLAT so round-trip is clean 0 -> X -> 0 lifecycle (one closed-position
        # record). then snapshot existing history ids so we can pick out the one we create.
        mkt = resolve_perp_market(account.trading_client, position_history.market)
        close_all_perp_positions(account.order_client, account.app_session_id, mkt.market)
        poll_until(
            lambda: long_size(get_perp_positions(account.trading_client, account.app_session_id, mkt.market)),
            lambda size: size < 1e-9, timeout_s=30, intervals=(1, 2), message="account flattened before round-trip",
        )
        before_ids = {p.id for p in get_perp_position_history(
            account.trading_client, account.app_session_id, market=mkt.market, page_size=100).positions}

        mark = get_perp_mark_price(account.trading_client, mkt.market)
        assert mark > 0, "mark price available"
        amount = size_amount(position_history.order_notional_usd, mark, mkt)
        amt = float(amount)

        # act 1 — open long vs seeded perp_maker. subject is taker.
        top = get_perp_top_of_book(account.trading_client, mkt.market)
        create_perp_order(perp_maker.order_client, perp_maker.app_session_id, market=mkt.market,
                          side="sell", direction="short", type="limit", amount=amount,
                          price=maker_price_inside_spread("sell", top, mark, mkt), tif="gtc", leverage=LEVERAGE)
        open_uuid = step("open long (market buy)",
                         lambda: create_perp_order(account.order_client, account.app_session_id,
                                                   market=mkt.market, side="buy", direction="long",
                                                   type="market", amount=amount, leverage=LEVERAGE))
        wait_for_perp_fill(account.trading_client, account.app_session_id, mkt.market, open_uuid, 15)
        poll_until(
            lambda: long_size(get_perp_positions(account.trading_client, account.app_session_id, mkt.market)),
            lambda size: size > amt * 0.5, timeout_s=15, message="long position opened",
        )

        # act 2 — fully close reduce-only. position size back to 0.
        mark2 = get_perp_mark_price(account.trading_client, mkt.market)
        top2 = get_perp_top_of_book(account.trading_client, mkt.market)
        create_perp_order(perp_maker.order_client, perp_maker.app_session_id, market=mkt.market,
                          side="buy", direction="short", type="limit", amount=amount,
                          price=maker_price_inside_spread("buy", top2, mark2, mkt), tif="gtc",
                          reduce_only=True, leverage=LEVERAGE)
        close_uuid = step("close long (reduce-only market sell)",
                          lambda: create_perp_order(account.order_client, account.app_session_id,
                                                    market=mkt.market, side="sell", direction="long",
                                                    type="market", amount=amount, reduce_only=True,
                                                    leverage=LEVERAGE))
        wait_for_perp_fill(account.trading_client, account.app_session_id, mkt.market, close_uuid, 20)
        poll_until(
            lambda: long_size(get_perp_positions(account.trading_client, account.app_session_id, mkt.market)),
            lambda size: size < amt * 0.5, timeout_s=20, message="long position closed",
        )

        # assert 1 — closed lifecycle show up in position history. async ingestion -> poll for NEW id.
        step("await position-history ingestion", lambda: poll_until(
            lambda: any(p.id not in before_ids for p in get_perp_position_history(
                account.trading_client, account.app_session_id, market=mkt.market, page_size=100).positions),
            lambda seen: seen, timeout_s=position_history.ingest_timeout_s, intervals=(3,),
            message="new closed-position record ingested",
        ))
        listing = get_perp_position_history(account.trading_client, account.app_session_id,
                                            market=mkt.market, page_size=100)
        pos = next(p for p in listing.positions if p.id not in before_ids)
        record("closed position record", pos.model_dump())

        closed_qty = float(pos.closed_quantity or "0")
        max_held = float(pos.max_held or "0")
        realized_is_number = pos.realized_pnl is not None and _is_float(pos.realized_pnl)
        opened_at = _parse_ts(pos.opened_at)
        closed_at = _parse_ts(pos.closed_at)
        record_check(name="close_reason == normal (actively closed)", passed=pos.close_reason == "normal",
                     detail=pos.close_reason)
        record_check(name="direction == long", passed=pos.direction == "long", detail=pos.direction)
        record_check(name="closed_quantity ~= ordered amount", passed=abs(closed_qty - amt) < amt * 0.02,
                     detail={"closedQty": closed_qty, "amt": amt})
        record_check(name="max_held ~= ordered amount", passed=abs(max_held - amt) < amt * 0.02,
                     detail={"maxHeld": max_held, "amt": amt})
        record_check(name="realized_pnl is a number (incl. fees/funding)", passed=realized_is_number,
                     detail=pos.realized_pnl)
        record_check(name="opened_at <= closed_at, both present",
                     passed=opened_at is not None and closed_at is not None and closed_at >= opened_at,
                     detail={"opened_at": pos.opened_at, "closed_at": pos.closed_at})

        assert pos.close_reason == "normal", "actively-closed position -> normal"
        assert pos.direction == "long", "position direction"
        assert closed_qty == pytest.approx(amt, abs=1e-6), "closed_quantity matches the round-trip size"
        assert max_held == pytest.approx(amt, abs=1e-6), "max_held matches the peak size"
        assert realized_is_number, "realized_pnl is a finite number"
        assert opened_at is not None and closed_at is not None and closed_at >= opened_at, "opened_at <= closed_at"
        assert float(pos.entry_price or "0") > 0, "entry price recorded"
        assert float(pos.exit_price or "0") > 0, "exit price recorded"

        # assert 2 — linked-orders detail list open and close fills, ascending by time.
        detail = step("read linked-orders detail",
                      lambda: get_perp_position_history_detail(account.trading_client, account.app_session_id,
                                                               pos.id, page_size=500))
        record("linked fills", [f.model_dump() for f in detail.fills])
        open_fill = next((f for f in detail.fills if f.kind == "open"), None)
        close_fill = next((f for f in detail.fills if f.kind == "close"), None)
        well_formed = all(
            len(f.id) > 0 and float(f.amount or "0") > 0 and float(f.price or "0") > 0 and (f.executed_at or "")
            for f in detail.fills
        )
        times = [_parse_ts(f.executed_at) or 0 for f in detail.fills]
        ascending = all(t >= times[i - 1] for i, t in enumerate(times) if i > 0)

        record_check(name="detail lists >= 2 fills", passed=len(detail.fills) >= 2, detail=len(detail.fills))
        record_check(name="has an open fill and a close fill", passed=bool(open_fill) and bool(close_fill),
                     detail={"open": open_fill.kind if open_fill else None, "close": close_fill.kind if close_fill else None})
        record_check(name="open fill has no realized PnL (dash rule)",
                     passed=float(open_fill.pnl or "0") == 0 if open_fill else False,
                     detail=open_fill.pnl if open_fill else None)
        record_check(name="close fill carries realized PnL", passed=close_fill is not None and close_fill.pnl is not None,
                     detail=close_fill.pnl if close_fill else None)
        record_check(name="every fill well-formed (id/amount/price/time)", passed=well_formed,
                     detail=[{"id": f.id, "amount": f.amount, "price": f.price, "executed_at": f.executed_at}
                             for f in detail.fills])
        record_check(name="fills ascending by time (open->close)", passed=ascending, detail=times)

        assert len(detail.fills) >= 2, "linked fills present (open + close)"
        assert open_fill is not None, "an open fill exists"
        assert close_fill is not None, "a close fill exists"
        assert float(open_fill.pnl or "0") == 0, "open fill realizes no PnL"
        assert close_fill.pnl is not None, "close fill carries a realized PnL value"
        assert well_formed, "every fill has id/amount/price/executed_at"
        assert ascending, "fills sorted ascending by execution time"


def _is_float(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False
