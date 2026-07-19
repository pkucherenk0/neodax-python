"""perp leverage-based tiered margin (YEN-2544). port of suites/neodax/perp/risk-tiers.spec.ts.

GET /perpetual/market-risk-tiers contract + tier-param invariants liquidation module rely on.
@stateless: endpoint public, no auth, read-only. configured perp market MUST have real seeded
ladder. single-tier fallback (from_config_table=False) means risk tiers not configured on this
env — that fail worth flag, not skip.
"""
import pytest

from configs.competition import risk_tier
from lib.risk_tiers import get_market_risk_tiers, maintenance_margin_for_notional, select_risk_tier_for_notional
from lib.report import record, record_check
from lib.schemas import FlatError
from lib.validate import parsed_json

MARKET = risk_tier.market
RATE_ABS = 5e-7  # rate fields high-precision decimals. compare at 6dp, float-safe


@pytest.mark.stateless
class TestPerpMarketRiskTiers:
    def test_returns_seeded_risk_tier_ladder_with_documented_contract_shape(self, env):
        # arrange — trading-host client. endpoint no auth.
        client = env.client_for(None, env.trading_base)

        # act — fetch ladder for configured perp market.
        tiers = get_market_risk_tiers(client, MARKET)
        record("risk-tier ladder", {"market": MARKET, "count": len(tiers), "tiers": [t.__dict__ for t in tiers]})

        # assert — real multi-tier seeded ladder. every row belong to requested market and
        # carry positive well-formed params.
        wanted = MARKET.upper()
        all_for_market = all(t.symbol.upper() == wanted for t in tiers)
        well_formed = all(
            t.tier_index > 0 and t.max_notional_quote > 0 and t.max_leverage >= 1
            and t.initial_margin_rate > 0 and t.maintenance_margin_rate > 0
            for t in tiers
        )
        seeded = all(t.from_config_table for t in tiers)

        record_check(name="ladder is non-empty", passed=len(tiers) >= 1, detail=len(tiers))
        record_check(name="ladder is multi-tier (real ladder, not single-tier fallback)", passed=len(tiers) >= 2, detail=len(tiers))
        record_check(name="every row is for the requested market", passed=all_for_market, detail=[t.symbol for t in tiers])
        record_check(name="every tier from_config_table (configured, not synthetic)", passed=seeded,
                     detail=[t.from_config_table for t in tiers])

        assert len(tiers) >= 2, f"risk-tier ladder for {MARKET}"
        assert all_for_market, "every tier row belongs to the requested market"
        assert well_formed, "every tier has positive, well-formed parameters"
        assert seeded, f"every tier is from the config table (real ladder) for {MARKET}"

    def test_exposes_monotonic_ladder_leverage_falls_as_notional_cap_rises(self, env):
        # arrange
        client = env.client_for(None, env.trading_base)
        tiers = get_market_risk_tiers(client, MARKET)  # ascending by tier_index

        # act — compare each tier to predecessor. seeded market guaranteed >=2 tiers.
        assert len(tiers) >= 2, "need at least two tiers to assert monotonicity"
        for prev, cur in zip(tiers, tiers[1:]):
            # assert — bigger bracket => larger notional cap, lower max leverage, non-decreasing margin rates.
            record_check(
                name=f"tier {cur.tier_index} vs {prev.tier_index}: cap up, leverage down, IMR up, MMR up",
                passed=cur.max_notional_quote > prev.max_notional_quote and cur.max_leverage < prev.max_leverage
                       and cur.initial_margin_rate >= prev.initial_margin_rate
                       and cur.maintenance_margin_rate >= prev.maintenance_margin_rate,
                detail={"prev": prev.__dict__, "cur": cur.__dict__},
            )
            assert cur.max_notional_quote > prev.max_notional_quote, f"tier {cur.tier_index} notional cap strictly increases"
            assert cur.max_leverage < prev.max_leverage, f"tier {cur.tier_index} max leverage strictly decreases"
            assert cur.initial_margin_rate >= prev.initial_margin_rate, f"tier {cur.tier_index} IMR does not decrease"
            assert cur.maintenance_margin_rate >= prev.maintenance_margin_rate, f"tier {cur.tier_index} MMR does not decrease"

    def test_keeps_every_tier_mmr_strictly_below_its_imr(self, env):
        # arrange
        client = env.client_for(None, env.trading_base)

        # act
        tiers = get_market_risk_tiers(client, MARKET)

        # assert — core margin safety invariant. also DB CHECK constraint on BE. tier
        # maintenance req must sit below initial req, else liquidation could trigger before
        # position even openable.
        assert len(tiers) >= 1
        for t in tiers:
            record_check(name=f"tier {t.tier_index}: MMR < IMR", passed=t.maintenance_margin_rate < t.initial_margin_rate,
                         detail=t.__dict__)
            assert t.maintenance_margin_rate < t.initial_margin_rate, \
                f"tier {t.tier_index} MMR ({t.maintenance_margin_rate}) < IMR ({t.initial_margin_rate})"

    def test_sets_each_tier_imr_to_inverse_of_max_leverage(self, env):
        # arrange
        client = env.client_for(None, env.trading_base)

        # act
        tiers = get_market_risk_tiers(client, MARKET)

        # assert — terminology rule IMR = 1 / max_leverage. enforced by BE seed.
        assert len(tiers) >= 1
        for t in tiers:
            expected = 1 / t.max_leverage
            record_check(name=f"tier {t.tier_index}: IMR ~= 1/leverage",
                         passed=abs(t.initial_margin_rate - expected) < RATE_ABS,
                         detail={"imr": t.initial_margin_rate, "expected": expected, "maxLeverage": t.max_leverage})
            assert t.initial_margin_rate == pytest.approx(expected, abs=RATE_ABS), \
                f"tier {t.tier_index} IMR == 1/{t.max_leverage}"

    def test_assigns_notional_to_correct_tier_at_inclusive_band_boundaries(self, env):
        # arrange — live ladder is source of truth for band edges. no hardcoded caps.
        client = env.client_for(None, env.trading_base)
        tiers = get_market_risk_tiers(client, MARKET)
        assert len(tiers) >= 2, "need at least two tiers to probe a boundary"
        t1, t2 = tiers[0], tiers[1]

        # act — probe three notionals around tier-1 cap: just under, exactly on (inclusive), just over.
        under_cap = select_risk_tier_for_notional(tiers, t1.max_notional_quote * 0.5)
        on_cap = select_risk_tier_for_notional(tiers, t1.max_notional_quote)  # inclusive -> still tier 1
        over_cap = select_risk_tier_for_notional(tiers, t1.max_notional_quote * 1.0000001)  # -> tier 2
        record("boundary probe", {"cap": t1.max_notional_quote,
                                  "under": under_cap.tier_index if under_cap else None,
                                  "on": on_cap.tier_index if on_cap else None,
                                  "over": over_cap.tier_index if over_cap else None})

        # assert — band is (prevCap, cap]. on-cap stay in lower tier, just-over step up.
        record_check(name="notional under cap -> tier 1", passed=under_cap and under_cap.tier_index == t1.tier_index, detail=under_cap)
        record_check(name="notional exactly on cap -> tier 1 (inclusive)", passed=on_cap and on_cap.tier_index == t1.tier_index, detail=on_cap)
        record_check(name="notional just over cap -> tier 2", passed=over_cap and over_cap.tier_index == t2.tier_index, detail=over_cap)
        assert under_cap and under_cap.tier_index == t1.tier_index, "notional below the cap sits in tier 1"
        assert on_cap and on_cap.tier_index == t1.tier_index, "notional exactly on the cap sits in tier 1 (upper bound inclusive)"
        assert over_cap and over_cap.tier_index == t2.tier_index, "notional just above the cap steps to tier 2"

    def test_raises_required_mm_when_growing_notional_crosses_into_higher_tier(self, env):
        # arrange — live ladder. each cap is tier boundary.
        client = env.client_for(None, env.trading_base)
        tiers = get_market_risk_tiers(client, MARKET)
        assert len(tiers) >= 2, "need at least two tiers to cross a boundary"

        # act — compute MM at each tier boundary: at the cap, and just over it into the next tier.
        boundaries = []
        for i in range(1, len(tiers)):
            cap = tiers[i - 1].max_notional_quote
            mm_at_cap = maintenance_margin_for_notional(tiers, cap)  # on cap stay in lower tier (inclusive)
            mm_just_over = maintenance_margin_for_notional(tiers, cap * (1 + 1e-7))  # grow into higher tier
            mm_lower_rate = cap * tiers[i - 1].maintenance_margin_rate
            mm_higher_rate = cap * tiers[i].maintenance_margin_rate
            boundaries.append((tiers[i].tier_index, cap, mm_at_cap, mm_just_over, mm_lower_rate, mm_higher_rate))

        # assert — two distinct props at each tier cap:
        #   (a) realistic path: position grow across boundary. MM(just over) must never drop
        #       below MM(at cap). both terms carry notional, cant isolate tier effect alone.
        #   (b) isolated tier effect: MM for SAME notional under higher vs lower tier rate.
        #       hold notional fixed, so any increase is purely MMR stepping up.
        rate_jumps = 0
        for tier_index, cap, mm_at_cap, mm_just_over, mm_lower_rate, mm_higher_rate in boundaries:
            rate_stepped = mm_higher_rate > mm_lower_rate
            rate_jumps += int(rate_stepped)
            record_check(
                name=f"crossing into tier {tier_index}: MM non-decreasing; same-notional rate step raised MM = {rate_stepped}",
                passed=mm_just_over >= mm_at_cap - 1e-6 and mm_higher_rate >= mm_lower_rate,
                detail={"cap": cap, "mmAtCap": mm_at_cap, "mmJustOver": mm_just_over,
                        "mmLowerRate": mm_lower_rate, "mmHigherRate": mm_higher_rate},
            )
            assert mm_just_over >= mm_at_cap - 1e-6, f"MM must not drop as notional grows into tier {tier_index}"
            assert mm_higher_rate >= mm_lower_rate, f"same-notional MM must not fall in the higher tier {tier_index}"
        record("maintenance-margin tier-rate jumps", {"boundaries": len(tiers) - 1, "rateJumps": rate_jumps})

        # at least one boundary charge more maintenance margin for same notional once tier step
        # up. prove tier increase itself, not just bigger position, add liquidation pressure.
        assert rate_jumps > 0, "at least one tier boundary raises the maintenance margin for the same notional"

    def test_returns_all_markets_snapshot_including_configured_market(self, env):
        # arrange
        client = env.client_for(None, env.trading_base)

        # act — omit ?symbol returns every active market tiers in one snapshot.
        all_tiers = get_market_risk_tiers(client)
        symbols = sorted({t.symbol.upper() for t in all_tiers})
        record("all-markets snapshot", {"rowCount": len(all_tiers), "symbols": symbols})

        # assert — snapshot non-empty and contain market we queried by symbol above.
        has_market = MARKET.upper() in symbols
        record_check(name="snapshot is non-empty", passed=len(all_tiers) > 0, detail=len(all_tiers))
        record_check(name=f"snapshot includes {MARKET}", passed=has_market, detail=symbols)
        assert len(all_tiers) > 0, "all-markets snapshot is non-empty"
        assert has_market, f"all-markets snapshot includes {MARKET}"

    def test_returns_404_market_not_found_for_unknown_symbol(self, env):
        # arrange — symbol that not exist on exchange.
        client = env.client_for(None, env.trading_base)

        # act — raw call. helper raise on non-2xx. here assert negative contract directly.
        res = client.get(f"/perpetual/market-risk-tiers?symbol={risk_tier.unknown_symbol}")

        # assert — status + flat trading-api error contract.
        assert res.status == 404, "unknown symbol -> 404"
        body = parsed_json(res, FlatError)
        record_check(name="error code == market_not_found", passed=body.error == "market_not_found", detail=body.model_dump())
        assert body.error == "market_not_found", "unknown symbol error code"
