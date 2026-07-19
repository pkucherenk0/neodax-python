"""spot trade vs comp (perp-spot-0). port of suites/competition/spot-trade.spec.ts.

@trades: real orders + faucet spend on uat. two accounts: spot_maker rests sell inside
spread, enrolled_account (subject) lifts it as taker. asserts charged fee matches fee engine,
fill counts as comp volume for subject. ETHUSDT book thin, so make own liquidity.
"""
import pytest

from config.competition import spot_market
from lib.fees import RATE_TOLERANCE, bps_to_rate, get_fee_tier_effective
from lib.poll import poll_until
from lib.spot import create_spot_order, get_spot_fills_for_order, get_spot_top_of_book, spot_resting_sell_price


@pytest.mark.trades
@pytest.mark.timeout(600)  # first `enrolled_account` use -> faucet + transfer + enroll; ~1min volume ingestion
class TestCompetitionSpotTrade:
    def test_enrolled_taker_spot_fill_charged_resolved_fee_and_counts_toward_volume(self, enrolled_account, spot_maker):
        # arrange: maker rests sell one tick inside spread (best ask, from live book)
        # so taker market buy lifts it. derived price avoids hardcoded level crossing/missing.
        amount = "10.0000"
        price = spot_resting_sell_price(get_spot_top_of_book(spot_maker.trading_client, spot_market))
        create_spot_order(spot_maker.order_client, spot_maker.app_session_id, market=spot_market,
                          side="sell", type="limit", amount=amount, price=price, tif="gtc")
        volume_before = float(get_fee_tier_effective(enrolled_account.trading_client).overlay.campaign_volume_usd or "0")

        # act: subject market-buys, lifts maker ask (subject is taker).
        order_uuid = create_spot_order(enrolled_account.order_client, enrolled_account.app_session_id,
                                       market=spot_market, side="buy", type="market", amount=amount)
        poll_until(
            lambda: get_spot_fills_for_order(enrolled_account.trading_client, enrolled_account.app_session_id, spot_market, order_uuid).fills,
            lambda fills: fills > 0, timeout_s=15, message="spot taker fill appeared",
        )
        fill = get_spot_fills_for_order(enrolled_account.trading_client, enrolled_account.app_session_id, spot_market, order_uuid)

        # assert 1: real TAKER fill executed.
        assert not fill.is_maker, "subject took liquidity (not maker)"
        assert fill.amount > 0, "base filled"

        # assert 2: charged fee matches resolved effective spot taker rate (ground truth).
        eff_taker = bps_to_rate(get_fee_tier_effective(enrolled_account.trading_client).effective.spot_taker_bps)
        assert fill.charged_rate == pytest.approx(eff_taker, abs=RATE_TOLERANCE), \
            "charged spot fee == effective spot taker rate"

        # assert 3: fill notional accrues to subject comp volume (after ingestion).
        poll_until(
            lambda: float(get_fee_tier_effective(enrolled_account.trading_client).overlay.campaign_volume_usd or "0"),
            lambda vol: vol > volume_before, timeout_s=120, intervals=(5,),
            message="fill notional accrued to competition volume",
        )
