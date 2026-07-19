"""offline unit tests for pure risk-tier math. port of test/unit/risk-tiers.test.ts. no network."""
import pytest

from lib.risk_tiers import RiskTier, maintenance_margin_for_notional, select_risk_tier_for_notional


def _tier(idx: int, cap: float, lev: float, imr: float, mmr: float) -> RiskTier:
    return RiskTier(symbol="X", tier_index=idx, max_notional_quote=cap, max_leverage=lev,
                    initial_margin_rate=imr, maintenance_margin_rate=mmr, config_version=1,
                    from_config_table=True)


# pure tier math (mutation-tested in the TS original). tiers: caps 100/250/1000, mmr 0.005/0.01/0.025.
TIERS = [
    _tier(1, 100, 100, 0.01, 0.005),
    _tier(2, 250, 50, 0.02, 0.01),
    _tier(3, 1000, 20, 0.05, 0.025),
]


class TestSelectRiskTierForNotional:
    def test_picks_the_tier_by_inclusive_upper_cap(self):
        # band = (prevCap, cap]
        assert select_risk_tier_for_notional(TIERS, 50).tier_index == 1
        assert select_risk_tier_for_notional(TIERS, 100).tier_index == 1  # exactly on cap -> lower tier
        assert select_risk_tier_for_notional(TIERS, 100.01).tier_index == 2  # just over -> next
        assert select_risk_tier_for_notional(TIERS, 250).tier_index == 2
        assert select_risk_tier_for_notional(TIERS, 250.01).tier_index == 3
        assert select_risk_tier_for_notional(TIERS, 1000).tier_index == 3

    def test_uses_the_absolute_notional(self):
        assert select_risk_tier_for_notional(TIERS, -90).tier_index == 1
        assert select_risk_tier_for_notional(TIERS, -300).tier_index == 3

    def test_returns_none_above_every_cap(self):
        assert select_risk_tier_for_notional(TIERS, 1000.01) is None
        assert select_risk_tier_for_notional(TIERS, 5000) is None

    def test_sorts_unsorted_input_by_cap_first(self):
        reversed_tiers = list(reversed(TIERS))
        assert select_risk_tier_for_notional(reversed_tiers, 100.01).tier_index == 2
        assert select_risk_tier_for_notional(reversed_tiers, 50).tier_index == 1


class TestMaintenanceMarginForNotional:
    def test_equals_notional_times_mmr_of_the_selected_tier(self):
        assert maintenance_margin_for_notional(TIERS, 100) == pytest.approx(100 * 0.005, abs=1e-10)  # tier1
        assert maintenance_margin_for_notional(TIERS, 200) == pytest.approx(200 * 0.01, abs=1e-10)  # tier2
        assert maintenance_margin_for_notional(TIERS, 500) == pytest.approx(500 * 0.025, abs=1e-10)  # tier3

    def test_above_all_caps_uses_the_highest_bracket_mmr(self):
        assert maintenance_margin_for_notional(TIERS, 5000) == pytest.approx(5000 * 0.025, abs=1e-10)

    def test_scales_linearly_with_notional_within_a_tier(self):
        assert maintenance_margin_for_notional(TIERS, 60) == pytest.approx(60 * 0.005, abs=1e-10)
        assert maintenance_margin_for_notional(TIERS, 90) == pytest.approx(90 * 0.005, abs=1e-10)
