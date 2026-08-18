"""NIM-balance tier path.

comp tiers use EITHER-threshold rule: tier qualifies by campaign volume OR by 24h-average
NIM balance. so holding enough NIM deepens overlay tier with NO trading volume.

@trades: funds via uat faucet (deposits NIM), places NO orders. no retries so a retry
can't double-deposit. fresh enrolled wallet starts at Base (0 volume). faucet ~24x next tier
NIM requirement (campaign_nim_balance is 24h hour-weighted average, so one deposit
~= deposit/24 crosses it in a tick), then assert either-threshold tier deepened.

DOWN path (sell NIM -> tier reverts) omitted on purpose: 24h average cannot drain within
a run (needs >24h soak), can't assert deterministically.
"""
import math

import pytest

from configs.competition import competition_slug, nim_flow
from lib.fees import bps_to_rate, get_fee_tier_effective
from lib.funding import faucet_deposit
from lib.poll import poll_until
from lib.schemas import EnrollResponse
from lib.tiers import competition_to_tiers, expected_comp_tier, expected_tier_either, get_competition
from lib.validate import parsed_json


@pytest.mark.trades
@pytest.mark.timeout(360)  # enroll + two backend ingestions (overlay activation, then NIM 24h average)
class TestCompetitionNimTier:
    def test_holding_nim_deepens_overlay_tier_via_either_threshold_rule(self, fresh_wallet, env):
        # arrange: enroll fresh wallet (0 volume) into overlay comp, wait overlay activate
        # (enroll alone activates it, ~20s; up to ~60s parallel load).
        wallet = fresh_wallet()
        hub = env.client_for(wallet.jwt)
        enroll = hub.post(f"/api/v1/competitions/{competition_slug}/enroll",
                          data={"address": wallet.address, "terms_accepted": True})
        assert enroll.status in (200, 201)
        parsed_json(enroll, EnrollResponse)
        trading = env.client_for(wallet.jwt, env.trading_base)
        poll_until(lambda: get_fee_tier_effective(trading).overlay.active, lambda a: a is True,
                   timeout_s=150, intervals=(2, 3, 5), message="competition overlay became active")

        # resolve live tiers and target: first tier ABOVE volume-qualified tier that carries
        # NIM requirement (for 0-volume account that's VIP1). no hardcoded thresholds.
        before = get_fee_tier_effective(trading)
        tiers = competition_to_tiers(get_competition(hub, competition_slug)) or []
        assert len(tiers) > 1, "competition.fee_tiers populated"
        vol_now = float(before.overlay.campaign_volume_usd or "0")
        vol_tier = expected_comp_tier(tiers, vol_now)
        target = next((t for t in tiers if t.level > vol_tier.level and t.nim_min > 0), None)
        assert target is not None, "a higher tier with a NIM requirement exists to target"
        pre_eff_perp_taker = bps_to_rate(before.effective.perp_taker_bps)

        # act: faucet ~24x target NIM requirement so 24h average crosses it, then wait
        # campaign_nim_balance to ingest up to requirement.
        deposit = math.ceil(target.nim_min * nim_flow.deposit_multiplier)
        # faucet client retries 5xx (client_for default) ON PURPOSE: UAT faucet 503s transient,
        # NIM credit additive. duplicate deposit harmless (2x deposit still keeps 24h average
        # below next tier requirement). reliability on transient 503 > idempotency here.
        faucet = env.client_for(None, env.faucet_url)
        faucet_deposit(faucet, wallet.app_session_id, str(deposit), nim_flow.asset)
        poll_until(
            lambda: float(get_fee_tier_effective(trading).overlay.campaign_nim_balance or "0"),
            lambda y: y >= target.nim_min, timeout_s=nim_flow.ingest_timeout_s, intervals=(5,),
            message="campaign_nim_balance ingested up to the target requirement",
        )

        # assert: either-threshold tier now above volume-only tier, overlay resolved to it,
        # best-of(standard, overlay) stepped effective perp taker DOWN, via NIM alone.
        after = get_fee_tier_effective(trading)
        campaign_nim = float(after.overlay.campaign_nim_balance or "0")
        either_tier = expected_tier_either(tiers, vol_now, campaign_nim)
        assert either_tier.level > vol_tier.level, "NIM qualifies a tier above the volume-only tier"
        assert bps_to_rate(after.overlay.perp_taker_bps or "0") == pytest.approx(either_tier.perp_taker, abs=1e-6), \
            "overlay perp taker == either-threshold tier rate"
        standard = bps_to_rate(after.standard.perp_taker_bps)
        overlay = bps_to_rate(after.overlay.perp_taker_bps or after.standard.perp_taker_bps)
        assert bps_to_rate(after.effective.perp_taker_bps) == pytest.approx(min(standard, overlay), abs=1e-6), \
            "effective == best-of(standard, NIM-deepened overlay)"
        assert bps_to_rate(after.effective.perp_taker_bps) < pre_eff_perp_taker, \
            "effective perp taker stepped down via NIM"
