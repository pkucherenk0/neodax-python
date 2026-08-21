"""perp orders. non-filling order lifecycle.

place resting gtc limit, see in open_orders + orders history, cancel, confirm reserved
margin released. @trades no counterparty needed — order priced so never fills. teardown
cancels anything failed run left resting.
"""
import pytest

from configs.competition import funding, perp_market, perp_trade
from lib.perp import (
    cancel_perp_order,
    create_perp_order,
    get_perp_balance_snapshot,
    get_perp_mark_price,
    get_perp_open_orders,
    get_perp_orders,
    resolve_perp_market,
    round_tick,
    size_amount,
)
from lib.poll import poll_until
from lib.report import record, record_check, step

LEVERAGE = 10
RESTING_STATES = ["wait", "open"]


@pytest.fixture(scope="module", autouse=True)
def _cancel_leftovers(account):
    yield
    try:
        open_orders = get_perp_open_orders(account.trading_client, account.app_session_id, perp_market)
    except Exception:
        open_orders = []
    for o in open_orders:
        try:
            cancel_perp_order(account.order_client, account.app_session_id, perp_market, o.order_id)
        except Exception:
            pass


@pytest.mark.trades
@pytest.mark.timeout(300)  # first use of account do faucet + transfer + enroll
class TestPerpOrders:
    def test_resting_limit_order_appears_in_open_orders_and_can_be_cancelled(self, account):
        # arrange — resolve market, price a buy 5% below mark (rests, never fills, inside price band).
        mkt = resolve_perp_market(account.trading_client, perp_market)
        mark = get_perp_mark_price(account.trading_client, mkt.market)
        assert mark > 0, "perp mark price available"
        amount = size_amount(perp_trade.order_notional_usd, mark, mkt)
        rest_price = round_tick(mark * 0.95, mkt.tick_size, mkt.price_precision)
        # ground truth: locks qty x price / leverage exactly, no buffer/fee -- see docs/test-cases/perps.md.
        reserved_margin = (float(amount) * float(rest_price)) / LEVERAGE
        # settle "before" to fixture's known baseline first -- see docs/test-cases/perps.md.
        expected_baseline = float(funding.perp_usdt)
        before = poll_until(
            lambda: get_perp_balance_snapshot(account.trading_client, account.app_session_id),
            lambda snap: abs(snap.available - expected_baseline) < 1e-6, timeout_s=20,
            message="perp collateral settled to its funded baseline before this test's own order",
        )

        # act 1 — place the resting order, confirm it rests.
        order_uuid = step("place resting GTC limit buy 5% below mark",
                          lambda: create_perp_order(account.order_client, account.app_session_id,
                                                    market=mkt.market, side="buy", direction="long",
                                                    type="limit", amount=amount, price=rest_price,
                                                    tif="gtc", leverage=LEVERAGE))

        poll_until(
            lambda: any(o.order_id == order_uuid for o in get_perp_open_orders(account.trading_client, account.app_session_id, mkt.market)),
            lambda seen: seen, timeout_s=10, message="resting order appeared in open_orders",
        )
        mine = next(o for o in get_perp_open_orders(account.trading_client, account.app_session_id, mkt.market)
                    if o.order_id == order_uuid)
        record("resting order", mine.model_dump())
        # lock can trail open_orders visibility by a beat -- poll for the exact expected lock.
        poll_until(
            lambda: get_perp_balance_snapshot(account.trading_client, account.app_session_id).available,
            lambda avail: abs(avail - (before.available - reserved_margin)) < 1e-6, timeout_s=15,
            message="margin locked (matches the order's own notional / leverage)",
        )
        during = get_perp_balance_snapshot(account.trading_client, account.app_session_id)
        in_history = any(o.order_id == order_uuid
                         for o in get_perp_orders(account.trading_client, account.app_session_id, mkt.market))

        # resting unfilled gtc limit sits in "wait" state, accept "open" too -- real proof of resting is fill_amount == 0.
        record_check(
            name="resting limit present in open_orders with expected shape",
            passed=mine.type == "limit" and mine.side == "buy" and mine.state in RESTING_STATES
                   and float(mine.fill_amount or "0") == 0,
            detail={"type": mine.type, "side": mine.side, "state": mine.state, "price": mine.price,
                    "fill_amount": mine.fill_amount},
        )
        record_check(name="order also visible in /orders history", passed=in_history, detail={"inHistory": in_history})
        record_check(
            name="locked exactly the order's own margin (notional / leverage)",
            passed=abs((before.available - during.available) - reserved_margin) < 1e-6,
            detail={"before": before.available, "during": during.available, "reservedMargin": reserved_margin},
        )

        # act 2 — cancel it. async, poll until it leaves open_orders.
        cancel = step("cancel the resting order",
                      lambda: cancel_perp_order(account.order_client, account.app_session_id, mkt.market, order_uuid))
        record("cancel response", cancel.model_dump())
        poll_until(
            lambda: any(o.order_id == order_uuid for o in get_perp_open_orders(account.trading_client, account.app_session_id, mkt.market)),
            lambda seen: not seen, timeout_s=15, message="cancelled order left open_orders",
        )
        # capture poll_until's own value, not a fresh re-read (different replica risk).
        after_available = poll_until(
            lambda: get_perp_balance_snapshot(account.trading_client, account.app_session_id).available,
            lambda avail: abs(avail - before.available) < 1e-6, timeout_s=30,
            message="margin released back after cancel (matches the locked margin)",
        )
        record("balance around resting order", {"before": before.available, "duringAvailable": during.available,
                                                "duringLocked": during.locked, "afterCancel": after_available,
                                                "reservedMargin": reserved_margin})
        record_check(name="released exactly the locked margin back",
                     passed=abs(after_available - before.available) < 1e-6,
                     detail={"before": before.available, "after": after_available})

        # assert — resting shape correct, lock/release match the order's own margin (independent oracle, see docs/test-cases/perps.md).
        assert mine.type == "limit", "order is a limit"
        assert mine.state in RESTING_STATES, "order is in a resting state"
        assert float(mine.fill_amount or "0") == 0, "resting order is unfilled"
        assert in_history, "order appears in /orders history"
        locked = before.available - during.available
        assert locked == pytest.approx(reserved_margin, abs=1e-6), \
            f"resting order locked exactly its own margin (notional / leverage): expected {reserved_margin}, locked {locked}"
        assert after_available == pytest.approx(before.available, abs=1e-6), \
            f"collateral restored after cancel: expected {before.available}, got {after_available}"
