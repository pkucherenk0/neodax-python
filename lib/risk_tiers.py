"""perp leverage-based tiered margin (YEN-2544). port of lib/risk-tiers.ts.

GET /perpetual/market-risk-tiers returns, per market, ladder of risk tiers. tier a position
sits in chosen by position QUOTE NOTIONAL (|base| x mark price). tier fixes max leverage /
initial-margin / maintenance-margin rates liquidation module then uses.
client-side mirror of BE selection rule so specs assert tier assignment without math in spec.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from lib.http import ResilientClient
from lib.schemas import PerpRiskTiersResponse
from lib.validate import parsed_json


@dataclass(frozen=True)
class RiskTier:
    symbol: str
    tier_index: int
    max_notional_quote: float  # max_position_qty — tier INCLUSIVE upper quote-notional cap
    max_leverage: float
    initial_margin_rate: float
    maintenance_margin_rate: float
    config_version: int
    from_config_table: bool  # True = real configured ladder. False = single synthetic fallback tier


def get_market_risk_tiers(trading_client: ResilientClient, symbol: str | None = None) -> list[RiskTier]:
    """GET /perpetual/market-risk-tiers?symbol= (public — no auth). omit symbol for all-markets.

    return rows parsed to numbers, ascending by tier_index. raise on non-2xx / contract drift —
    use raw client for 404 (unknown-symbol) negative path.
    """
    path = f"/perpetual/market-risk-tiers?symbol={quote(symbol)}" if symbol else "/perpetual/market-risk-tiers"
    body = parsed_json(trading_client.get(path), PerpRiskTiersResponse)
    tiers = [
        RiskTier(
            symbol=r.symbol,
            tier_index=r.tier_index,
            max_notional_quote=float(r.max_position_qty),
            max_leverage=float(r.max_leverage),
            initial_margin_rate=float(r.initial_margin_rate),
            maintenance_margin_rate=float(r.maintenance_margin_rate),
            config_version=r.config_version,
            from_config_table=r.from_config_table,
        )
        for r in body.tiers
    ]
    return sorted(tiers, key=lambda t: t.tier_index)


def select_risk_tier_for_notional(tiers: list[RiskTier], notional_quote_abs: float) -> RiskTier | None:
    """select tier for absolute quote notional: first tier (ascending cap) whose cap >= notional.

    upper bound INCLUSIVE — notional exactly on cap pick THAT tier, so each band = (prevCap, cap].
    return None when notional exceed every tier cap (BE rejects such order `risk_tier_exceeded`).
    mirror BE portfolio_manager_perp SelectTierForPositionNotional.
    """
    n = abs(notional_quote_abs)
    for t in sorted(tiers, key=lambda t: t.max_notional_quote):
        if n <= t.max_notional_quote:
            return t
    return None


def maintenance_margin_for_notional(tiers: list[RiskTier], notional_quote_abs: float) -> float:
    """maintenance-margin AMOUNT a position of this quote notional needs: notional x MMR(tier).

    tier chosen by notional. notional above every cap use highest bracket MMR (mirror BE
    MaintenanceMarginRateForHighestTier / ComputeDisplayMaintenanceMargin).
    """
    n = abs(notional_quote_abs)
    if not tiers:
        return 0.0
    highest = max(tiers, key=lambda t: t.max_notional_quote)
    tier = select_risk_tier_for_notional(tiers, n) or highest
    return n * tier.maintenance_margin_rate
