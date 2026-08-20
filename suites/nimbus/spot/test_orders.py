"""spot orders — non-filling lifecycle.

rest GTC limit BUY well below market, see in open_orders + orders history, cancel, confirm
reserved quote (USDT) released. @trades (no counterparty, never fills). teardown cancels
leftovers from failed run.
"""
import pytest

from configs.competition import funding, spot_market
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
        # arrange — price 10% below market (rests as bid, never fills). spot book can be empty
        # on a quiet UAT market -> fall back to the perp market's oracle-fed mark price.
        mark = get_perp_mark_price(account.trading_client, f"{spot_market}-PERP")
        ref = spot_reference_price_or_mark(get_spot_top_of_book(account.trading_client, spot_market), mark)
        assert ref > 0, "spot reference price available"
        amount = "1.0000"
        price = f"{ref * 0.9:.2f}"
        # ground truth: limit buy locks amount x price of quote exactly, no buffer/fee
        # (slippage buffer is market-only). src: spot_service.go LockOrderFunds/
        # PrepareLockOrderFunds ~L1800-1829, TestSpotService_LockOrderFunds_PostOnly_BuyLocksQuoteAtPrice.
        reserved_notional = float(amount) * float(price)
        # `account` fixture confirms PERP settled (>=90%) but never re-confirms spot after the
        # transfer -- reading spot "before" immediately can catch that debit still trickling in
        # (real recurring flake). settle against the fixture's known funded baseline first.
        expected_baseline = float(funding.spot_usdt) - float(funding.perp_usdt)
        before = poll_until(
            lambda: get_spot_balance_snapshot(account.trading_client, account.app_session_id, "USDT"),
            lambda snap: abs(snap.available - expected_baseline) < 1e-6, timeout_s=20,
            message="USDT settled to its funded baseline (spot deposit minus perp transfer) before this test's own order",
        )

        # act 1 — place the resting order, confirm it rests.
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
        # the lock can trail the order becoming visible in open_orders by a beat -> poll for
        # the exact expected lock (proven BE formula above), not a single read.
        poll_until(
            lambda: get_spot_balance_snapshot(account.trading_client, account.app_session_id, "USDT").available,
            lambda avail: abs(avail - (before.available - reserved_notional)) < 1e-6, timeout_s=15,
            message="USDT locked (matches the order's own price x amount)",
        )
        during = get_spot_balance_snapshot(account.trading_client, account.app_session_id, "USDT")

        record_check(
            name="resting spot limit present with expected shape",
            passed=mine.type == "limit" and mine.side == "buy" and mine.state in RESTING_STATES
                   and float(mine.fill_amount or "0") == 0,
            detail={"type": mine.type, "side": mine.side, "state": mine.state, "price": mine.price,
                    "fill_amount": mine.fill_amount},
        )
        record_check(
            name="locked exactly the order's own notional (price x amount)",
            passed=abs((before.available - during.available) - reserved_notional) < 1e-6,
            detail={"before": before.available, "during": during.available, "reservedNotional": reserved_notional},
        )
        # hit /spot/orders (history): response shape schema-validated (contract check).
        # just-placed order inclusion eventually-consistent on spot (lags open_orders, unlike
        # perp /orders). so RECORD presence as observation, not assert on timing.
        history = get_spot_orders(account.trading_client, account.app_session_id, spot_market)
        record_check(name="/spot/orders returns a valid order list", passed=isinstance(history, list),
                     detail={"count": len(history), "containsOurs": any(o.order_id == order_uuid for o in history)})

        # act 2 — cancel it. async, poll until it leaves open_orders, then until the
        # locked USDT releases (trails the open_orders change by a beat).
        cancel = step("cancel the resting spot order",
                      lambda: cancel_spot_order(account.order_client, account.app_session_id, spot_market, order_uuid))
        record("cancel response", cancel.model_dump())
        poll_until(
            lambda: any(o.order_id == order_uuid for o in get_spot_open_orders(account.trading_client, account.app_session_id, spot_market)),
            lambda seen: not seen, timeout_s=15, message="cancelled order left open_orders",
        )
        # release target `before` valid since lock was proven exact above; real eventual-consistency
        # lag (generous timeout). capture poll_until's own value, not a fresh re-read (different replica risk).
        after_available = poll_until(
            lambda: get_spot_balance_snapshot(account.trading_client, account.app_session_id, "USDT").available,
            lambda avail: abs(avail - before.available) < 1e-6, timeout_s=30,
            message="USDT released back after cancel (matches the locked notional)",
        )
        record("USDT around resting order", {"before": before.available, "duringAvailable": during.available,
                                             "duringLocked": during.locked, "afterCancel": after_available,
                                             "reservedNotional": reserved_notional})
        record_check(name="released exactly the locked notional back",
                     passed=abs(after_available - before.available) < 1e-6,
                     detail={"before": before.available, "after": after_available})

        # assert — resting shape was correct, lock/release both match the order's own known
        # notional (independent oracle: the BE's own reservation formula, not a guessed value).
        assert mine.type == "limit", "order is a limit"
        assert mine.state in RESTING_STATES, "order is in a resting state"
        assert float(mine.fill_amount or "0") == 0, "resting order is unfilled"
        locked = before.available - during.available
        assert locked == pytest.approx(reserved_notional, abs=1e-6), \
            f"resting order locked exactly its own notional (price x amount): expected {reserved_notional}, locked {locked}"
        assert after_available == pytest.approx(before.available, abs=1e-6), \
            f"USDT restored after cancel: expected {before.available}, got {after_available}"
