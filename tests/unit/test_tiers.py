"""offline unit tests for pure tier math. no network."""
import pytest

from lib.schemas import CompetitionSchedule
from lib.tiers import competition_to_tiers, expected_comp_tier, expected_tier_either

# deliberately given OUT of order (VIP2 before VIP1) to prove the sort.
COMP = CompetitionSchedule.model_validate({
    "slug": "c",
    "status": "active",
    "fee_tiers": [
        {"tier_level": 2, "tier_name": "VIP2", "campaign_volume_req_usd": "1000", "campaign_nim_req": "50",
         "spot_maker_bps": "6", "spot_taker_bps": "8", "perp_maker_bps": "0.8", "perp_taker_bps": "3.5"},
        {"tier_level": 1, "tier_name": "VIP1", "campaign_volume_req_usd": "250", "campaign_nim_req": "10",
         "spot_maker_bps": "8", "spot_taker_bps": "10", "perp_maker_bps": "1", "perp_taker_bps": "4"},
    ],
})


class TestCompetitionToTiers:
    def test_maps_and_sorts_ascending_by_volume_requirement_bps_to_decimal_rate(self):
        tiers = competition_to_tiers(COMP)
        assert tiers is not None
        assert [t.vol_min for t in tiers] == [250, 1000]
        assert [t.name for t in tiers] == ["VIP1", "VIP2"]
        assert tiers[0].perp_taker == pytest.approx(4 / 10_000, abs=1e-12)
        assert tiers[0].spot_maker == pytest.approx(8 / 10_000, abs=1e-12)
        assert tiers[0].nim_min == 10
        assert tiers[1].perp_maker == pytest.approx(0.8 / 10_000, abs=1e-12)

    def test_returns_none_when_fee_tiers_is_absent_or_empty(self):
        assert competition_to_tiers(CompetitionSchedule.model_validate({"slug": "c", "status": "active"})) is None
        assert competition_to_tiers(
            CompetitionSchedule.model_validate({"slug": "c", "status": "active", "fee_tiers": []})) is None


class TestExpectedCompTier:
    """highest tier met by volume; floor = lowest."""

    tiers = competition_to_tiers(COMP)

    def test_below_the_first_threshold_falls_to_the_lowest_tier(self):
        assert expected_comp_tier(self.tiers, 100).name == "VIP1"

    def test_at_or_above_a_threshold_picks_that_tier(self):
        assert expected_comp_tier(self.tiers, 250).name == "VIP1"
        assert expected_comp_tier(self.tiers, 999).name == "VIP1"
        assert expected_comp_tier(self.tiers, 1000).name == "VIP2"
        assert expected_comp_tier(self.tiers, 5000).name == "VIP2"


class TestExpectedTierEither:
    """volume OR nim qualifies."""

    tiers = competition_to_tiers(COMP)

    def test_nim_alone_can_qualify_a_higher_tier_with_no_volume(self):
        assert expected_tier_either(self.tiers, 0, 50).name == "VIP2"  # nim 50 >= VIP2 nim_min
        assert expected_tier_either(self.tiers, 0, 10).name == "VIP1"

    def test_volume_alone_still_qualifies(self):
        assert expected_tier_either(self.tiers, 1000, 0).name == "VIP2"

    def test_takes_the_higher_of_the_two_paths(self):
        assert expected_tier_either(self.tiers, 300, 50).name == "VIP2"  # vol->VIP1, nim->VIP2 => VIP2


class TestEmptyScheduleGuards:
    """throw (not silently return None)."""

    def test_expected_comp_tier_raises_on_empty(self):
        with pytest.raises(ValueError):
            expected_comp_tier([], 100)

    def test_expected_tier_either_raises_on_empty(self):
        with pytest.raises(ValueError):
            expected_tier_either([], 100, 100)
