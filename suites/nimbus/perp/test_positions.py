"""perp position lifecycle @serial.

open long via market taker fill vs seeded perp_maker, verify in /positions, then close
reduce-only. check balance/margin each step. NO retries.

run: pytest -m serial suites/nimbus/perp/test_positions.py   (one process, never -n)
"""
import math

import pytest

from configs.competition import perp_market, perp_trade
from lib.perp import (
    PerpParty,
    create_perp_order,
    flatten_perp_pair,
    get_perp_balance_snapshot,
    get_perp_mark_price,
    get_perp_positions,
    get_perp_top_of_book,
    maker_price_inside_spread,
    resolve_perp_market,
    size_amount,
    wait_for_perp_fill,
)
from lib.poll import poll_until
from lib.report import record, record_check, step

LEVERAGE = 10


def long_size(positions) -> float:
    """total long exposure for market. assert use delta before->after, never absolute size —
    worker account shared across spec files, may already hold position. see AGENTS.md."""
    return sum(float(p.amount) for p in positions if p.direction == "long")


# ordered phases share the resolved market via module state.
state: dict = {"mkt": None}


@pytest.fixture(scope="module", autouse=True)
def _flatten_after(account, perp_maker):
    """seeded teardown. cross two accts mirrored positions reduce-only. no external liquidity."""
    yield
    m = resolve_perp_market(account.trading_client, perp_market)
    flatten_perp_pair(
        PerpParty(order_client=account.order_client, trading_client=account.trading_client, app_session_id=account.app_session_id),
        PerpParty(order_client=perp_maker.order_client, trading_client=perp_maker.trading_client, app_session_id=perp_maker.app_session_id),
        m, LEVERAGE,
    )


@pytest.mark.serial
@pytest.mark.xdist_group(name="positions")  # test_2 closes the position test_1 opened
class TestPerpPositionLifecycle:
    @pytest.mark.timeout(300)  # first use of account do faucet + transfer + enroll
    def test_1_market_order_fills_against_seeded_liquidity_and_opens_position(self, account, perp_maker):
        # arrange — resolve market, snapshot balance/position before, size the order,
        # perp_maker rests sell one tick inside spread (guaranteed counterparty for buy).
        mkt = state["mkt"] = resolve_perp_market(account.trading_client, perp_market)
        before = get_perp_balance_snapshot(account.trading_client, account.app_session_id)
        long_before = long_size(get_perp_positions(account.trading_client, account.app_session_id, mkt.market))
        mark = get_perp_mark_price(account.trading_client, mkt.market)
        amount = size_amount(perp_trade.order_notional_usd, mark, mkt)
        amt = float(amount)

        top = get_perp_top_of_book(account.trading_client, mkt.market)
        maker_price = maker_price_inside_spread("sell", top, mark, mkt)
        create_perp_order(perp_maker.order_client, perp_maker.app_session_id, market=mkt.market,
                          side="sell", direction="short", type="limit", amount=amount, price=maker_price,
                          tif="gtc", leverage=LEVERAGE)

        # act — subject market-buys, opening a long.
        order_uuid = step("subject market-buys (opens a long)",
                          lambda: create_perp_order(account.order_client, account.app_session_id,
                                                    market=mkt.market, side="buy", direction="long",
                                                    type="market", amount=amount, leverage=LEVERAGE))
        fill = wait_for_perp_fill(account.trading_client, account.app_session_id, mkt.market, order_uuid, 15)
        record("open fill", fill.__dict__)

        # assert — position/balance reflect the fill. long exposure grow by ~order amount,
        # delta not absolute (worker account shared across spec files, see AGENTS.md).
        poll_until(
            lambda: long_size(get_perp_positions(account.trading_client, account.app_session_id, mkt.market)),
            lambda size: size > long_before + amt * 0.5, timeout_s=15, message="long exposure grew",
        )
        positions = get_perp_positions(account.trading_client, account.app_session_id, mkt.market)
        long_after = long_size(positions)
        long_row = next((p for p in positions if p.direction == "long"), None)
        record("open position", long_row.model_dump() if long_row else {"note": "no discrete long row", "longAfter": long_after})
        after = get_perp_balance_snapshot(account.trading_client, account.app_session_id)
        record("balance around open", {"before": before.available, "after": after.available,
                                       "allocated": after.allocated, "longBefore": long_before, "longAfter": long_after})

        record_check(name="long exposure increased by the ordered size",
                     passed=abs(long_after - long_before - amt) < amt * 0.001,
                     detail={"longBefore": long_before, "longAfter": long_after, "orderAmount": amt})
        record_check(name="available collateral dropped by locked margin",
                     passed=after.available < before.available,
                     detail={"before": before.available, "after": after.available, "allocated": after.allocated})

        assert fill.fills > 0, "taker fill executed"
        assert fill.amount == pytest.approx(amt, abs=1e-6), "our order filled at the ordered size"
        assert long_after - long_before == pytest.approx(amt, abs=1e-6), "long exposure grew by the order amount"
        assert after.available < before.available, "available dropped by locked margin"

    @pytest.mark.timeout(180)
    def test_2_closing_the_position_flattens_it_and_releases_margin(self, account, perp_maker):
        # arrange — close ACTUAL open size, not a fresh mark recompute (mark moves live,
        # drift -> insufficient_position). floor to step size, matches flatten_perp_pair.
        # maker rests reduce-only buy as guaranteed counterparty.
        mkt = state["mkt"]
        assert mkt is not None, "phase 1 resolved the market"
        before = get_perp_balance_snapshot(account.trading_client, account.app_session_id)
        long_before = long_size(get_perp_positions(account.trading_client, account.app_session_id, mkt.market))
        assert long_before > 0, "a long position exists to close"

        mark = get_perp_mark_price(account.trading_client, mkt.market)
        step_size = mkt.step_size if mkt.step_size > 0 else 10 ** -mkt.amount_precision
        dp = max(mkt.amount_precision, 0)
        amt = math.floor(long_before / step_size) * step_size
        amount = f"{amt:.{dp}f}"
        top = get_perp_top_of_book(account.trading_client, mkt.market)
        maker_bid = maker_price_inside_spread("buy", top, mark, mkt)
        create_perp_order(perp_maker.order_client, perp_maker.app_session_id, market=mkt.market,
                          side="buy", direction="short", type="limit", amount=amount, price=maker_bid,
                          tif="gtc", reduce_only=True, leverage=LEVERAGE)

        # act — subject reduce-only sells to close.
        close_uuid = step("subject reduce-only sells to close",
                          lambda: create_perp_order(account.order_client, account.app_session_id,
                                                    market=mkt.market, side="sell", direction="long",
                                                    type="market", amount=amount, reduce_only=True,
                                                    leverage=LEVERAGE))
        close_fill = wait_for_perp_fill(account.trading_client, account.app_session_id, mkt.market, close_uuid, 20)
        record("close fill", close_fill.__dict__)

        # assert — long exposure drops by our amount (delta, not to zero if prior suite left
        # one) and margin releases back.
        poll_until(
            lambda: long_size(get_perp_positions(account.trading_client, account.app_session_id, mkt.market)),
            lambda size: size < long_before - amt * 0.5, timeout_s=20, message="long exposure dropped",
        )
        long_after = long_size(get_perp_positions(account.trading_client, account.app_session_id, mkt.market))
        after = get_perp_balance_snapshot(account.trading_client, account.app_session_id)
        record("balance around close", {"before": before.available, "after": after.available,
                                        "longBefore": long_before, "longAfter": long_after})

        record_check(name="our long exposure closed (reduced by the order amount)",
                     passed=abs(long_before - long_after - amt) < amt * 0.001,
                     detail={"longBefore": long_before, "longAfter": long_after, "orderAmount": amt})
        record_check(name="margin released (available rose back)", passed=after.available > before.available,
                     detail={"before": before.available, "after": after.available})

        assert long_before - long_after == pytest.approx(amt, abs=1e-6), "long exposure reduced by the order amount"
        assert after.available > before.available, "available rose after releasing margin"
