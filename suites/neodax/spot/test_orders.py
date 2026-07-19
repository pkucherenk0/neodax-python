"""spot orders — non-filling lifecycle. port of suites/neodax/spot/orders.spec.ts.

rest GTC limit BUY well below market, see in open_orders + orders history, cancel, confirm
reserved quote (USDT) released. @trades (no counterparty, never fills). teardown cancels
leftovers from failed run.
"""
import pytest

from config.competition import spot_market
from lib.perp import get_perp_mark_price
from lib.poll import poll_until
from lib.report import record, record_check, step
from lib.spot import (
    cancel_spot_order,
    create_spot_order,
    get_spot_balance_snapshot,
    get_spot_open_orders,
    get_spot_orders,
    get_spot_top_of_book,
    spot_reference_price_or_mark,
)

RESTING_STATES = ["wait", "open"]


@pytest.fixture(scope="module", autouse=True)
def _cancel_leftovers(account):
    yield
    try:
        open_orders = get_spot_open_orders(account.trading_client, account.app_session_id, spot_market)
    except Exception:
        open_orders = []
    for o in open_orders:
        try:
            cancel_spot_order(account.order_client, account.app_session_id, spot_market, o.order_id)
        except Exception:
            pass


@pytest.mark.trades
@pytest.mark.timeout(300)  # first `account` use -> faucet + transfer + enroll
class TestSpotOrders:
    def test_resting_spot_limit_order_appears_in_open_orders_and_can_be_cancelled(self, account):
        # spot book can be completely empty on a quiet UAT market (no resting orders from
        # anyone yet) -> fall back to the corresponding perp market's oracle-fed mark price.
        mark = get_perp_mark_price(account.trading_client, f"{spot_market}-PERP")
        ref = spot_reference_price_or_mark(get_spot_top_of_book(account.trading_client, spot_market), mark)
        assert ref > 0, "spot reference price available"
        amount = "1.0000"
        price = f"{ref * 0.9:.2f}"  # 10% below market -> rests as bid, never fills
        before = get_spot_balance_snapshot(account.trading_client, account.app_session_id, "USDT")

        order_uuid = step("place resting GTC spot limit buy 10% below market",
                          lambda: create_spot_order(account.order_client, account.app_session_id,
                                                    market=spot_market, side="buy", type="limit",
                                                    amount=amount, price=price, tif="gtc"))

        poll_until(
            lambda: any(o.order_id == order_uuid for o in get_spot_open_orders(account.trading_client, account.app_session_id, spot_market)),
            lambda seen: seen, timeout_s=10, message="resting order appeared in open_orders",
        )
        mine = next(o for o in get_spot_open_orders(account.trading_client, account.app_session_id, spot_market)
                    if o.order_id == order_uuid)
        record("resting spot order", mine.model_dump())
        during = get_spot_balance_snapshot(account.trading_client, account.app_session_id, "USDT")

        record_check(
            name="resting spot limit present with expected shape",
            passed=mine.type == "limit" and mine.side == "buy" and mine.state in RESTING_STATES
                   and float(mine.fill_amount or "0") == 0,
            detail={"type": mine.type, "side": mine.side, "state": mine.state, "price": mine.price,
                    "fill_amount": mine.fill_amount},
        )
        # hit /spot/orders (history): response shape schema-validated (contract check).
        # just-placed order inclusion eventually-consistent on spot (lags open_orders, unlike
        # perp /orders). so RECORD presence as observation, not assert on timing.
        history = get_spot_orders(account.trading_client, account.app_session_id, spot_market)
        record_check(name="/spot/orders returns a valid order list", passed=isinstance(history, list),
                     detail={"count": len(history), "containsOurs": any(o.order_id == order_uuid for o in history)})

        cancel = step("cancel the resting spot order",
                      lambda: cancel_spot_order(account.order_client, account.app_session_id, spot_market, order_uuid))
        record("cancel response", cancel.model_dump())
        poll_until(
            lambda: any(o.order_id == order_uuid for o in get_spot_open_orders(account.trading_client, account.app_session_id, spot_market)),
            lambda seen: not seen, timeout_s=15, message="cancelled order left open_orders",
        )
        # the locked-USDT release trails the order leaving open_orders by a beat -> poll rather
        # than read once (same eventual-consistency lag as fill -> balance credit).
        poll_until(
            lambda: get_spot_balance_snapshot(account.trading_client, account.app_session_id, "USDT").available,
            lambda avail: abs(avail - before.available) < 1e-6, timeout_s=15,
            message="USDT released back after cancel",
        )
        after = get_spot_balance_snapshot(account.trading_client, account.app_session_id, "USDT")
        record("USDT around resting order", {"before": before.available, "duringAvailable": during.available,
                                             "duringLocked": during.locked, "afterCancel": after.available})
        record_check(name="available USDT restored after cancel",
                     passed=abs(after.available - before.available) < 1e-6,
                     detail={"before": before.available, "after": after.available})

        assert mine.type == "limit", "order is a limit"
        assert mine.state in RESTING_STATES, "order is in a resting state"
        assert float(mine.fill_amount or "0") == 0, "resting order is unfilled"
        assert after.available == pytest.approx(before.available, abs=1e-6), "USDT restored after cancel"
