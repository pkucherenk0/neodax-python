"""spot fee overlay (perp-spot-0, volume_source=spot_perp -> spot IS in comp).

@stateless: enroll + read fee endpoints. no funding, no trades.
at Base tier (0 volume) overlay rates == standard, no discount yet. assertable now: overlay
active for enrolled account, best-of never makes spot worse than standard.
"""
import pytest

from configs.competition import competition_slug, spot_market
from lib.fees import bps_to_rate, get_fee_tier_effective, get_spot_market_fee_rate
from lib.poll import poll_until
from lib.schemas import EnrollResponse
from lib.validate import parsed_json


@pytest.mark.stateless
@pytest.mark.timeout(240)  # overlay activation is backend ingestion (~20s isolated, ~60s under load)
class TestCompetitionSpotFees:
    def test_enrolled_account_gets_overlay_on_spot_effective_is_best_of_never_worse(self, fresh_wallet, env):
        # arrange: enroll fresh wallet into overlay comp.
        wallet = fresh_wallet()
        hub = env.client_for(wallet.jwt)
        enroll = hub.post(f"/api/v1/competitions/{competition_slug}/enroll",
                          data={"address": wallet.address, "terms_accepted": True})
        assert enroll.status in (200, 201)
        parsed_json(enroll, EnrollResponse)
        trading = env.client_for(wallet.jwt, env.trading_base)

        # act: overlay ingests few sec after enroll. poll until active.
        poll_until(lambda: get_fee_tier_effective(trading).overlay.active, lambda a: a is True,
                   timeout_s=150, intervals=(2, 3, 5), message="competition overlay became active")
        fee = get_fee_tier_effective(trading)

        # assert: overlay is THIS comp's.
        assert fee.overlay.slug == competition_slug

        # effective spot = best-of(standard, overlay) = cheaper of two, never above standard.
        std_taker = float(fee.standard.spot_taker_bps)
        std_maker = float(fee.standard.spot_maker_bps)
        ov_taker = float(fee.overlay.spot_taker_bps or fee.standard.spot_taker_bps)
        ov_maker = float(fee.overlay.spot_maker_bps or fee.standard.spot_maker_bps)
        eff_taker = float(fee.effective.spot_taker_bps)
        eff_maker = float(fee.effective.spot_maker_bps)

        assert eff_taker == pytest.approx(min(std_taker, ov_taker), abs=1e-5), "effective spot taker = best-of"
        assert eff_maker == pytest.approx(min(std_maker, ov_maker), abs=1e-5), "effective spot maker = best-of"
        assert eff_taker <= std_taker, "spot taker never above standard"
        assert eff_maker <= std_maker, "spot maker never above standard"

        # cross-check: account live spot fee-rate matches effective bps, from fee engine.
        rate = get_spot_market_fee_rate(trading, wallet.app_session_id, spot_market)
        assert rate.taker_rate == pytest.approx(bps_to_rate(fee.effective.spot_taker_bps), abs=5e-7), \
            "market-fee-rate taker == effective spot taker"
        assert rate.source in ("fee_engine", "fee_tier")
