"""perp trade vs comp (perp-spot-0). port of suites/competition/perp-trade.spec.ts.

@trades: real orders + faucet spend on uat. mirrors spot two-account maker/taker: perp_maker
rests limit inside spread (guaranteed counterparty), enrolled_account (subject) hits it with
market order. positions HEDGE-mode: taker works LONG leg, resting maker SHORT leg. asserts
per-fill charged fee (from /perpetual/trades) matches fee engine, fill counts as comp volume.
"""
import pytest

from configs.competition import perp_market, perp_trade
from lib.fees import RATE_TOLERANCE, bps_to_rate, get_fee_tier_effective, rates_match
from lib.perp import (
    PerpParty,
    create_perp_order,
    flatten_perp_pair,
    get_perp_fills_for_order,
    get_perp_mark_price,
    get_perp_top_of_book,
    maker_price_inside_spread,
    resolve_perp_market,
    size_amount,
)
from lib.poll import poll_until
from lib.report import record, record_check, step


@pytest.fixture(scope="module", autouse=True)
def _flatten_after(enrolled_account, perp_maker):
    """teardown: flatten any position this module opened so it can't accumulate margin or be
    liquidated and corrupt later tests sharing this worker's accounts. best-effort."""
    yield
    mkt = resolve_perp_market(enrolled_account.trading_client, perp_market)
    flatten_perp_pair(
        PerpParty(order_client=enrolled_account.order_client, trading_client=enrolled_account.trading_client, app_session_id=enrolled_account.app_session_id),
        PerpParty(order_client=perp_maker.order_client, trading_client=perp_maker.trading_client, app_session_id=perp_maker.app_session_id),
        mkt, perp_trade.leverage,
    )


@pytest.mark.trades
@pytest.mark.timeout(600)
class TestCompetitionPerpTrade:
    def test_enrolled_taker_perp_fill_charged_resolved_fee_and_counts_toward_volume(self, enrolled_account, perp_maker):
        # arrange: resolve market filters, price off mark + top-of-book, rest maker SELL
        # one tick inside spread so it becomes best ask (guaranteed counterparty for buy).
        mkt = step("resolve perp market", lambda: resolve_perp_market(enrolled_account.trading_client, perp_market))
        mark = step("read mark price", lambda: get_perp_mark_price(enrolled_account.trading_client, mkt.market))
        assert mark > 0, "perp mark price available"
        amount = size_amount(perp_trade.order_notional_usd, mark, mkt)

        def rest_maker():
            top = get_perp_top_of_book(enrolled_account.trading_client, mkt.market)
            price = maker_price_inside_spread("sell", top, mark, mkt)
            create_perp_order(perp_maker.order_client, perp_maker.app_session_id, market=mkt.market,
                              side="sell", direction="short", type="limit", amount=amount, price=price,
                              tif="gtc", leverage=perp_trade.leverage)
            return price

        maker_price = step("rest maker SELL one tick inside the spread", rest_maker)
        volume_before = float(get_fee_tier_effective(enrolled_account.trading_client).overlay.campaign_volume_usd or "0")
        record("setup", {"market": mkt.market, "mark": mark, "amount": amount, "makerPrice": maker_price,
                         "orderNotionalUsd": perp_trade.order_notional_usd, "volumeBefore": volume_before})

        # act: subject market-buys LONG leg, lifts maker ask (subject is taker).
        def take_and_await():
            order_uuid = create_perp_order(enrolled_account.order_client, enrolled_account.app_session_id, market=mkt.market,
                                           side="buy", direction="long", type="market", amount=amount,
                                           leverage=perp_trade.leverage)
            poll_until(
                lambda: get_perp_fills_for_order(enrolled_account.trading_client, enrolled_account.app_session_id, mkt.market, order_uuid).fills,
                lambda fills: fills > 0, timeout_s=15, message="perp taker fill appeared",
            )
            return get_perp_fills_for_order(enrolled_account.trading_client, enrolled_account.app_session_id, mkt.market, order_uuid)

        fill = step("subject market-buys (taker) and awaits the fill", take_and_await)
        record("taker fill", fill.__dict__)

        # assert 1: real TAKER fill executed.
        record_check(name="real taker fill executed", passed=not fill.is_maker and fill.amount > 0,
                     detail={"isMaker": fill.is_maker, "amount": fill.amount, "notional": fill.notional})
        assert not fill.is_maker, "subject took liquidity (not maker)"
        assert fill.amount > 0, "base filled"

        # assert 2: charged fee matches resolved effective perp taker rate (ground truth).
        eff_taker = bps_to_rate(get_fee_tier_effective(enrolled_account.trading_client).effective.perp_taker_bps)
        record_check(name="charged perp fee == effective perp taker rate",
                     passed=rates_match(fill.charged_rate, eff_taker),
                     detail={"charged": fill.charged_rate, "effective": eff_taker})
        assert fill.charged_rate == pytest.approx(eff_taker, abs=RATE_TOLERANCE), \
            "charged perp fee == effective perp taker rate"

        # assert 3: fill notional accrues to subject comp volume (after ingestion).
        with_step = lambda: poll_until(
            lambda: float(get_fee_tier_effective(enrolled_account.trading_client).overlay.campaign_volume_usd or "0"),
            lambda vol: vol > volume_before, timeout_s=120, intervals=(5,),
            message="fill notional accrued to competition volume",
        )
        step("await competition-volume ingestion", with_step)
        volume_after = float(get_fee_tier_effective(enrolled_account.trading_client).overlay.campaign_volume_usd or "0")
        record_check(name="fill notional accrued to competition volume", passed=volume_after > volume_before,
                     detail={"volumeBefore": volume_before, "volumeAfter": volume_after, "delta": volume_after - volume_before})
