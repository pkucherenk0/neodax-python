"""ordered fee-tier flow (@serial). port of suites/competition/perp-fee-tier.spec.ts.

ONE process, phases share accumulated comp volume (module-level `flow` state). enrolled fresh
account starts at Base tier. driving perp volume past VIP1 campaign threshold makes overlay
resolve to VIP1, best-of(standard, overlay) steps effective fee DOWN. proven on both perp and
spot. thresholds/rates read LIVE from comp schedule (CONVENTIONS §9, no hardcoded tiers).
NO retries: retry re-places live orders.

run: pytest -m serial suites/competition/test_perp_fee_tier.py   (never with -n)
"""
import pytest

from config.competition import competition_slug, fee_tier_flow, perp_market, perp_trade, spot_market
from lib.fee_flow import MakerRef, VolumeParticipant, capture_subject_maker_fill, drive_competition_volume
from lib.fees import RATE_TOLERANCE, bps_to_rate, get_fee_tier_effective, rates_match
from lib.perp import PerpParty, flatten_perp_pair, resolve_perp_market
from lib.poll import poll_until
from lib.report import annotate, record, record_check
from lib.spot import create_spot_order, get_spot_fills_for_order, get_spot_top_of_book, spot_resting_sell_price
from lib.tiers import competition_to_tiers, expected_comp_tier, get_competition

# shared across ordered phases (module state carries resolved schedule + qualified tier +
# ingested campaign volume between tests).
flow: dict = {"tiers": [], "base": None, "target": None, "campaign_vol": 0.0}


@pytest.fixture(scope="module", autouse=True)
def _flatten_after(account, perp_maker):
    """teardown: SEEDED close so maker-side test position can't leak onto shared account."""
    yield
    mkt = resolve_perp_market(account.trading_client, perp_market)
    flatten_perp_pair(
        PerpParty(order_client=account.order_client, trading_client=account.trading_client, app_session_id=account.app_session_id),
        PerpParty(order_client=perp_maker.order_client, trading_client=perp_maker.trading_client, app_session_id=perp_maker.app_session_id),
        mkt, perp_trade.leverage,
    )


@pytest.mark.serial
class TestCompetitionFeeTierStepDown:
    def test_1_schedule_offers_cheaper_overlay_tier_above_base_volume_threshold(self, env):
        # arrange: fetch live comp schedule.
        comp = get_competition(env.client_for(), competition_slug)

        # act: map fee_tiers[] to unified tier shape (ascending by campaign volume requirement).
        tiers = competition_to_tiers(comp) or []

        # assert: schedule populated, target tier overlay strictly cheaper than base,
        # so step-down realizable (else best-of keeps base rate, nothing drops).
        assert len(tiers) > 1, "competition.fee_tiers populated"
        base = tiers[0]
        target = next((t for t in tiers if t.level == fee_tier_flow.target_tier_level), None)
        assert target is not None, f"tier level {fee_tier_flow.target_tier_level} present in schedule"
        assert target.vol_min > 0, "target tier has a positive volume threshold"
        assert target.perp_taker < base.perp_taker, "overlay perp taker cheaper than base"
        assert target.spot_taker < base.spot_taker, "overlay spot taker cheaper than base"
        record("competition schedule", {
            "base": {"name": base.name, "perpTaker": base.perp_taker, "spotTaker": base.spot_taker},
            "target": {"level": target.level, "name": target.name, "volMin": target.vol_min,
                       "perpTaker": target.perp_taker, "spotTaker": target.spot_taker},
        })
        record_check(name="overlay target tier is cheaper than base (step-down realizable)",
                     passed=target.perp_taker < base.perp_taker and target.spot_taker < base.spot_taker,
                     detail={"basePerpTaker": base.perp_taker, "targetPerpTaker": target.perp_taker})
        flow["tiers"] = tiers
        flow["base"] = base
        flow["target"] = target

    @pytest.mark.timeout(600)  # driving volume + waiting ingestion genuinely long-running
    def test_2_driving_volume_past_threshold_activates_overlay_at_qualified_tier(self, account, perp_maker):
        target = flow["target"]
        assert target is not None, "phase 1 resolved the schedule"

        # arrange: resolve perp market filters, measure subject starting campaign volume.
        mkt = resolve_perp_market(account.trading_client, perp_market)
        start_vol = float(get_fee_tier_effective(account.trading_client).overlay.campaign_volume_usd or "0")
        target_volume_usd = max(0.0, target.vol_min * fee_tier_flow.volume_overshoot - start_vol)

        # act: round-trip perp (subject takes, perp_maker rests inside spread) until subject
        # traded notional covers gap to threshold.
        driven = drive_competition_volume(
            subject=VolumeParticipant(order_client=account.order_client, trading_client=account.trading_client,
                                      app_session_id=account.app_session_id),
            maker=MakerRef(order_client=perp_maker.order_client, app_session_id=perp_maker.app_session_id),
            market=mkt, leverage=perp_trade.leverage,
            order_notional_usd=fee_tier_flow.drive_order_notional_usd,
            target_volume_usd=target_volume_usd, max_cycles=fee_tier_flow.max_cycles,
        )
        # surface driver outcome (never silently drop skipped legs). meaningful outcome
        # (campaign volume crossed threshold) asserted by poll below — do NOT assert
        # traded>0, which fails spurious if account already crossed (target -> 0 cycles).
        annotate(f"volume-driver: cycles={driven.cycles} skipped={driven.skipped} "
                 f"traded=${round(driven.traded_volume_usd)} target=${round(target_volume_usd)}")
        assert driven.skipped <= driven.cycles, "thin-book skips stayed at/below cycle count (fills dominated)"

        # assert: campaign volume ingests past threshold, overlay resolves to qualified tier.
        poll_until(
            lambda: float(get_fee_tier_effective(account.trading_client).overlay.campaign_volume_usd or "0"),
            lambda vol: vol >= target.vol_min, timeout_s=fee_tier_flow.ingest_timeout_s, intervals=(5,),
            message="campaign volume ingested past the threshold",
        )
        eff = get_fee_tier_effective(account.trading_client)
        assert eff.overlay.active is True, "overlay active after ingestion"
        flow["campaign_vol"] = float(eff.overlay.campaign_volume_usd or "0")
        qualified = expected_comp_tier(flow["tiers"], flow["campaign_vol"])
        record("volume driven", {"cycles": driven.cycles, "skipped": driven.skipped,
                                 "traded": driven.traded_volume_usd, "startVol": start_vol,
                                 "campaignVolAfter": flow["campaign_vol"]})
        record_check(name="overlay activated at the qualified tier after ingestion", passed=eff.overlay.active,
                     detail={"campaignVol": flow["campaign_vol"], "qualifiedTier": qualified.name})
        assert bps_to_rate(eff.overlay.perp_taker_bps or "0") == pytest.approx(qualified.perp_taker, abs=1e-6), \
            "overlay perp taker == qualified tier rate"

    @pytest.mark.timeout(300)
    def test_3_effective_perp_taker_steps_down_to_best_of_standard_overlay(self, account):
        target = flow["target"]
        base = flow["base"]
        assert target is not None and base is not None, "phase 1 resolved the schedule"

        # act: let per-account engine settle effective rate to deepened tier (can trail
        # ingestion by ~1 fill). wait until AT target rate or deeper (best-of <= target).
        poll_until(
            lambda: bps_to_rate(get_fee_tier_effective(account.trading_client).effective.perp_taker_bps),
            lambda rate: rate <= target.perp_taker, timeout_s=fee_tier_flow.ingest_timeout_s, intervals=(5,),
            message="effective perp taker settled at/below the target tier rate",
        )
        eff = get_fee_tier_effective(account.trading_client)

        # assert: effective == best-of(standard, overlay), stepped down below base rate.
        standard = bps_to_rate(eff.standard.perp_taker_bps)
        overlay = bps_to_rate(eff.overlay.perp_taker_bps or eff.standard.perp_taker_bps)
        effective = bps_to_rate(eff.effective.perp_taker_bps)
        record_check(name="effective perp taker == best-of(standard, overlay), stepped down below base",
                     passed=rates_match(effective, min(standard, overlay)) and effective < base.perp_taker,
                     detail={"standard": standard, "overlay": overlay, "effective": effective})
        assert effective == pytest.approx(min(standard, overlay), abs=1e-6), \
            "effective perp taker == best-of(standard, overlay)"
        assert effective < base.perp_taker, "effective perp taker stepped down below base"

    @pytest.mark.timeout(300)
    def test_4_spot_taker_fill_charged_discounted_overlay_rate(self, account, spot_maker):
        base = flow["base"]
        assert base is not None, "phase 1 resolved the schedule"

        # arrange: maker rests spot sell one tick inside spread (best ask, from live book)
        # so taker market buy lifts it.
        amount = "10.0000"
        price = spot_resting_sell_price(get_spot_top_of_book(spot_maker.trading_client, spot_market))
        create_spot_order(spot_maker.order_client, spot_maker.app_session_id, market=spot_market,
                          side="sell", type="limit", amount=amount, price=price, tif="gtc")

        # act: subject market-buys, lifts maker ask (subject is taker).
        order_uuid = create_spot_order(account.order_client, account.app_session_id,
                                       market=spot_market, side="buy", type="market", amount=amount)
        poll_until(
            lambda: get_spot_fills_for_order(account.trading_client, account.app_session_id, spot_market, order_uuid).fills,
            lambda fills: fills > 0, timeout_s=15, message="spot taker fill appeared",
        )
        fill = get_spot_fills_for_order(account.trading_client, account.app_session_id, spot_market, order_uuid)
        record("spot taker fill", {"restingPrice": price, **fill.__dict__})

        # assert: real taker fill charged discounted overlay spot rate, below base spot rate (10->8 bps).
        eff = get_fee_tier_effective(account.trading_client)
        overlay_spot_taker = bps_to_rate(eff.overlay.spot_taker_bps or eff.standard.spot_taker_bps)
        record_check(name="spot taker fill charged the discounted overlay rate, below base",
                     passed=rates_match(fill.charged_rate, overlay_spot_taker) and fill.charged_rate < base.spot_taker,
                     detail={"charged": fill.charged_rate, "overlaySpotTaker": overlay_spot_taker,
                             "baseSpotTaker": base.spot_taker})
        assert not fill.is_maker, "subject took liquidity (not maker)"
        assert fill.charged_rate == pytest.approx(overlay_spot_taker, abs=RATE_TOLERANCE), \
            "charged spot taker == overlay discounted rate"
        assert fill.charged_rate < base.spot_taker, "charged spot taker below base spot rate"

    @pytest.mark.timeout(120)
    def test_5_subject_maker_fill_charged_discounted_overlay_maker_rate(self, account, perp_maker):
        # arrange: roles flip. enrolled subject RESTS sell as best ask (maker/short),
        # non-enrolled perp_maker lifts it. retry on moving book so subject reliably gets
        # a MAKER fill. proves overlay discounts subject MAKER side too.
        mkt = resolve_perp_market(account.trading_client, perp_market)

        # act: capture one clean maker fill for enrolled subject.
        fill = capture_subject_maker_fill(
            subject=VolumeParticipant(order_client=account.order_client, trading_client=account.trading_client,
                                      app_session_id=account.app_session_id),
            maker=MakerRef(order_client=perp_maker.order_client, app_session_id=perp_maker.app_session_id),
            market=mkt, leverage=perp_trade.leverage, order_notional_usd=perp_trade.order_notional_usd,
        )
        assert fill is not None, "captured a clean subject maker fill within the retry budget"

        # assert: maker fill charged best-of(standard, overlay) maker rate, strictly below
        # standard maker rate. overlay discounts maker side, not just taker.
        eff = get_fee_tier_effective(account.trading_client)
        standard_maker = bps_to_rate(eff.standard.perp_maker_bps)
        overlay_maker = bps_to_rate(eff.overlay.perp_maker_bps or eff.standard.perp_maker_bps)
        record("subject maker fill", fill.__dict__)
        record_check(name="subject MAKER fill charged discounted overlay maker rate, below standard",
                     passed=fill.is_maker and rates_match(fill.charged_rate, min(standard_maker, overlay_maker))
                            and fill.charged_rate < standard_maker,
                     detail={"charged": fill.charged_rate, "standardMaker": standard_maker, "overlayMaker": overlay_maker})
        assert fill.is_maker, "subject rested liquidity (maker)"
        assert fill.charged_rate == pytest.approx(min(standard_maker, overlay_maker), abs=RATE_TOLERANCE), \
            "subject maker fill == best-of(standard, overlay) maker rate"
        assert fill.charged_rate < standard_maker, "subject maker fee discounted below standard by the overlay"
