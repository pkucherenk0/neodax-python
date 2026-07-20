"""spot orders — non-filling lifecycle. port of suites/neodax/spot/orders.spec.ts.

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
        # arrange — price 10% below market (rests as bid, never fills). spot book can be
        # completely empty on a quiet UAT market (no resting orders from anyone yet) -> fall
        # back to the corresponding perp market's oracle-fed mark price.
        mark = get_perp_mark_price(account.trading_client, f"{spot_market}-PERP")
        ref = spot_reference_price_or_mark(get_spot_top_of_book(account.trading_client, spot_market), mark)
        assert ref > 0, "spot reference price available"
        amount = "1.0000"
        price = f"{ref * 0.9:.2f}"
        # ground truth for the locked amount: a LIMIT buy reserves exactly amount x price of
        # quote, no buffer/fee (the slippage buffer only applies to MARKET orders). confirmed
        # against BE source: portfolio_manager/spot/spot_service.go LockOrderFunds/
        # PrepareLockOrderFunds (~L1800-1829), effectivePrice=price for limit orders; see also
        # TestSpotService_LockOrderFunds_PostOnly_BuyLocksQuoteAtPrice in spot_service_test.go.
        reserved_notional = float(amount) * float(price)
        # the shared `account` fixture faucets spot with `funding.spot_usdt`, then transfers
        # `funding.perp_usdt` of it into perps -- it confirms the PERP side settled (>=90%
        # threshold) but never re-confirms spot afterward. reading spot "before" immediately
        # can catch that debit still trickling in (this is the actual cause of a recurring
        # flake here). settle it against the fixture's own known funded baseline first --
        # that's the real independent oracle (the config amounts used to fund the account),
        # not a guessed value.
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
        # release target is `before` -- justified now that the lock itself was proven exact
        # (reserved_notional == before - during above), so full release must land back on
        # exactly `before`. generous timeout: this is the step that occasionally lags for
        # real (not a design flaw, just genuine eventual-consistency). Capture the value
        # poll_until itself confirmed -- a SEPARATE fresh read right after can hit a
        # different backend replica/cache and observe a different number even though the
        # poll already succeeded against a consistent one.
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
