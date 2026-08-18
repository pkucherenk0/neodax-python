"""spot trade — real maker/taker fill vs SEEDED liquidity.

spot_maker rests sell one tick inside spread, enrolled account market-buys and lifts it.
assert taker fill executed and both balances moved (USDT spent, base received). @trades.
no competition overlay here — that in suites/competition/. this pure spot-trading mechanic.
"""
import re

import pytest

from configs.competition import spot_market
from lib.perp import get_perp_mark_price
from lib.poll import poll_until
from lib.report import record, record_check, stepping
from lib.spot import (
    create_spot_order,
    get_spot_balance_snapshot,
    get_spot_fills_for_order,
    get_spot_top_of_book,
    spot_resting_sell_price_or_mark,
)


@pytest.mark.trades
@pytest.mark.timeout(300)  # first `account` use -> faucet + transfer + enroll
class TestSpotTrade:
    def test_market_buy_fills_against_seeded_maker_and_moves_both_balances(self, account, spot_maker):
        # arrange — snapshot balances, seed maker liquidity: rests SELL one tick inside
        # spread -> guaranteed counterparty (best ask). book can be completely empty on a
        # quiet UAT market -> fall back to the corresponding perp market's oracle-fed mark
        # price, so this resting order becomes the first price point.
        base = re.sub(r"USDT$", "", spot_market)  # ETHUSDT -> ETH
        amount = "10.0000"
        usdt_before = get_spot_balance_snapshot(account.trading_client, account.app_session_id, "USDT")
        base_before = get_spot_balance_snapshot(account.trading_client, account.app_session_id, base)

        mark = get_perp_mark_price(spot_maker.trading_client, f"{spot_market}-PERP")
        price = spot_resting_sell_price_or_mark(get_spot_top_of_book(spot_maker.trading_client, spot_market), mark)
        create_spot_order(spot_maker.order_client, spot_maker.app_session_id, market=spot_market,
                          side="sell", type="limit", amount=amount, price=price, tif="gtc")

        # act — account market-buys (taker) and awaits the fill.
        with stepping("account market-buys (taker) and awaits the fill"):
            order_uuid = create_spot_order(account.order_client, account.app_session_id,
                                           market=spot_market, side="buy", type="market", amount=amount)
            poll_until(
                lambda: get_spot_fills_for_order(account.trading_client, account.app_session_id, spot_market, order_uuid).fills,
                lambda fills: fills > 0, timeout_s=15, message="spot taker fill appeared",
            )
        fill = get_spot_fills_for_order(account.trading_client, account.app_session_id, spot_market, order_uuid)
        record("spot taker fill", fill.__dict__)

        # assert — real taker fill, USDT spent, base received. balance ingestion trails
        # trade recording by a beat -> poll rather than read once.
        poll_until(
            lambda: get_spot_balance_snapshot(account.trading_client, account.app_session_id, base).available,
            lambda avail: avail > base_before.available, timeout_s=15, message=f"{base} balance credited from the buy",
        )
        usdt_after = get_spot_balance_snapshot(account.trading_client, account.app_session_id, "USDT")
        base_after = get_spot_balance_snapshot(account.trading_client, account.app_session_id, base)
        record("balances around trade", {"usdtBefore": usdt_before.available, "usdtAfter": usdt_after.available,
                                         "baseBefore": base_before.available, "baseAfter": base_after.available})

        record_check(name="a real taker fill executed", passed=not fill.is_maker and fill.amount > 0,
                     detail={"isMaker": fill.is_maker, "amount": fill.amount, "total": fill.total, "fee": fill.fee})
        record_check(name="USDT spent and base asset received",
                     passed=usdt_after.available < usdt_before.available and base_after.available > base_before.available,
                     detail={"usdtDelta": usdt_after.available - usdt_before.available,
                             "baseDelta": base_after.available - base_before.available})

        assert not fill.is_maker, "account took liquidity (not maker)"
        assert fill.amount > 0, "base filled"
        assert usdt_after.available < usdt_before.available, "USDT spent on the buy"
        assert base_after.available > base_before.available, f"{base} received from the buy"
