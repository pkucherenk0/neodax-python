"""fee readback. port of lib/fees.ts. fee-tier-effective endpoint = authoritative best-of
(standard VIP tier vs competition overlay). rate fields = bps strings, callers float().
clients must be scoped to trading host.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from lib.http import ResilientClient
from lib.schemas import FeeTierEffective, spot_market_fee_rate_schema
from lib.validate import parsed_json


def get_fee_tier_effective(trading_client: ResilientClient) -> FeeTierEffective:
    return parsed_json(trading_client.get("/account/fee-tier-effective"), FeeTierEffective)


@dataclass(frozen=True)
class SpotFeeRate:
    maker_rate: float
    taker_rate: float
    source: str


def get_spot_market_fee_rate(trading_client: ResilientClient, app_session_id: str, market: str) -> SpotFeeRate:
    """account live effective SPOT fee rate for market. decimals, e.g. 0.001 = 10bps."""
    rows = parsed_json(
        trading_client.get(f"/spot/account/market-fee-rate?app_session_id={quote(app_session_id)}&market={quote(market)}"),
        spot_market_fee_rate_schema,
    )
    if not rows:
        raise AssertionError(f"no spot market-fee-rate row for market {market}")
    row = rows[0]
    return SpotFeeRate(maker_rate=float(row.maker_fee_rate), taker_rate=float(row.taker_fee_rate), source=row.source)


def bps_to_rate(bps: str) -> float:
    """bps string ("10", "3.5") -> decimal rate (0.001, 0.00035)."""
    return float(bps) / 10_000


# charged-fee rate matching. API return fee at HIGH precision, so fill.charged_rate = fee/notional
# equal tier rate EXACTLY (verified live). only tolerance needed = IEEE-754 float-noise.
# match to 6 decimals = +-0.005 bps window, ~2000x tighter than smallest tier gap (0.1 bps),
# so wrong-tier charge always caught. NOT wide relative band — that dilute point of fee check.
RATE_MATCH_DECIMALS = 6
RATE_TOLERANCE = 0.5 * 10**-RATE_MATCH_DECIMALS


def rates_match(a: float, b: float) -> bool:
    return abs(a - b) < RATE_TOLERANCE
