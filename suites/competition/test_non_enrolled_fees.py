"""negative control. port of suites/competition/non-enrolled-fees.spec.ts.

overlay must NEVER apply to an account that never enrolled. its fees follow standard schedule
only. perp_maker fixture is non-enrolled counterparty. roles flip vs enrolled-subject tests:
enrolled_account RESTS liquidity (one tick inside spread), non-enrolled perp_maker LIFTS as
taker. holds regardless of perp_maker volume: overlay gated on enroll, not volume.
"""
import pytest

from config.competition import perp_market, perp_trade
from lib.fees import RATE_TOLERANCE, bps_to_rate, get_fee_tier_effective
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


@pytest.fixture(scope="module", autouse=True)
def _flatten_after(enrolled_account, perp_maker):
    """teardown: SEEDED close. cross two accounts mirrored positions reduce-only (no reliance
    on external book liquidity) so nothing leaks onto shared worker accounts. best-effort."""
    yield
    mkt = resolve_perp_market(enrolled_account.trading_client, perp_market)
    flatten_perp_pair(
        PerpParty(order_client=enrolled_account.order_client, trading_client=enrolled_account.trading_client, app_session_id=enrolled_account.app_session_id),
        PerpParty(order_client=perp_maker.order_client, trading_client=perp_maker.trading_client, app_session_id=perp_maker.app_session_id),
        mkt, perp_trade.leverage,
    )


@pytest.mark.trades
@pytest.mark.timeout(600)
class TestCompetitionNonEnrolledFees:
    def test_non_enrolled_taker_perp_fill_charged_standard_fees_overlay_never_applies(self, enrolled_account, perp_maker):
        # arrange: enrolled_account rests SELL as best ask so non-enrolled maker can lift.
        mkt = resolve_perp_market(enrolled_account.trading_client, perp_market)
        mark = get_perp_mark_price(enrolled_account.trading_client, mkt.market)
        assert mark > 0, "perp mark price available"
        amount = size_amount(perp_trade.order_notional_usd, mark, mkt)
        top = get_perp_top_of_book(enrolled_account.trading_client, mkt.market)
        ask_price = maker_price_inside_spread("sell", top, mark, mkt)
        create_perp_order(enrolled_account.order_client, enrolled_account.app_session_id, market=mkt.market, side="sell",
                          direction="short", type="limit", amount=amount, price=ask_price, tif="gtc",
                          leverage=perp_trade.leverage)

        # act: NON-ENROLLED perp_maker market-buys, lifts enrolled_account's ask (maker is taker).
        order_uuid = create_perp_order(perp_maker.order_client, perp_maker.app_session_id, market=mkt.market,
                                       side="buy", direction="long", type="market", amount=amount,
                                       leverage=perp_trade.leverage)
        poll_until(
            lambda: get_perp_fills_for_order(perp_maker.trading_client, perp_maker.app_session_id, mkt.market, order_uuid).fills,
            lambda fills: fills > 0, timeout_s=15, message="non-enrolled taker fill appeared",
        )
        fill = get_perp_fills_for_order(perp_maker.trading_client, perp_maker.app_session_id, mkt.market, order_uuid)

        # assert: no overlay for a non-enrolled account. effective == standard.
        # real taker fill charged standard taker rate (ground truth).
        eff = get_fee_tier_effective(perp_maker.trading_client)
        standard = bps_to_rate(eff.standard.perp_taker_bps)
        assert eff.overlay.active is False, "competition overlay NOT active for a non-enrolled account"
        assert bps_to_rate(eff.effective.perp_taker_bps) == pytest.approx(standard, abs=1e-6), \
            "effective perp taker == standard (no overlay)"
        assert not fill.is_maker, "non-enrolled account took liquidity (not maker)"
        assert fill.charged_rate == pytest.approx(standard, abs=RATE_TOLERANCE), \
            "non-enrolled taker fill charged the standard rate"
