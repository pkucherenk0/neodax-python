"""competition fee schedule + pure tier math."""
from __future__ import annotations

from urllib.parse import quote

from lib.http import ResilientClient
from lib.schemas import CompetitionSchedule
from lib.types import Tier
from lib.validate import parsed_json


def get_competition(hub_client: ResilientClient, slug: str) -> CompetitionSchedule:
    """GET {hub}/api/v1/competitions/{slug} — authoritative source for campaign fee schedule."""
    return parsed_json(hub_client.get(f"/api/v1/competitions/{quote(slug)}"), CompetitionSchedule)


def _bps(value: str | None) -> float:
    return float("nan") if value is None else float(value) / 10_000


def competition_to_tiers(comp: CompetitionSchedule) -> list[Tier] | None:
    """map competition fee_tiers[] -> unified Tier[] (ascending by campaign volume requirement).

    return None when fee_tiers absent/empty (field null until business populates it).
    """
    ft = comp.fee_tiers
    if not ft:
        return None
    tiers = [
        Tier(
            level=t.tier_level,
            name=t.tier_name,
            perp_taker=_bps(t.perp_taker_bps),
            perp_maker=_bps(t.perp_maker_bps),
            spot_taker=_bps(t.spot_taker_bps),
            spot_maker=_bps(t.spot_maker_bps),
            vol_min=float(t.campaign_volume_req_usd),
            nim_min=float(t.campaign_nim_req or "0") or 0.0,
        )
        for t in ft
    ]
    return sorted(tiers, key=lambda t: t.vol_min)


def expected_comp_tier(tiers: list[Tier], cum_vol: float) -> Tier:
    """highest tier whose campaign volume requirement met by cum_vol."""
    if not tiers:
        raise ValueError("expected_comp_tier: empty tier schedule")
    current = tiers[0]
    for t in tiers:
        if cum_vol >= t.vol_min:
            current = t
    return current


def expected_tier_either(tiers: list[Tier], vol: float, nim: float) -> Tier:
    """either-threshold qualification: highest tier met by EITHER campaign volume OR
    24h-average NIM balance. why holding enough NIM can qualify tier with no volume."""
    if not tiers:
        raise ValueError("expected_tier_either: empty tier schedule")
    current = tiers[0]
    for t in tiers:
        if vol >= t.vol_min or nim >= t.nim_min:
            current = t
    return current
