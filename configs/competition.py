"""typed run params. port of config/competition.ts. override per-run via env vars.
defaults keep plain `pytest` runnable.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _f(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _i(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


# single competition under test. only ONE competition can have fee overlay on at a time.
# that is `perp-spot-0` (volume_source = spot_perp), so both spot AND perp trades count.
competition_slug = os.environ.get("NEODAX_COMPETITION", "perp-spot-0")

# markets for trade/fee tests. resolved against exchange info on this env.
spot_market = os.environ.get("NEODAX_SPOT_MARKET", "ETHUSDT")
perp_market = os.environ.get("NEODAX_PERP_MARKET", "BTCUSDT-PERP")

# known-settled competition, for "not enrollable" (409) contract test. separate from
# configured slug so test deterministic no matter what NEODAX_COMPETITION points at.
settled_competition_slug = os.environ.get("NEODAX_SETTLED_COMPETITION", "pablo-15")

# statuses where competition SHOULD accept enrollments. not-yet-started competition NOT
# enrollable. 'scheduled' left out on purpose (enroll-before-start is a tracked API bug).
ENROLLABLE_STATUSES = ["active", "open", "live", "enrolling"]


def is_enrollable(status: str) -> bool:
    return status.lower() in ENROLLABLE_STATUSES


@dataclass(frozen=True)
class Funding:
    """funding amounts for @trades accounts (uat faucet)."""

    spot_usdt: str = os.environ.get("NEODAX_FAUCET_USDT", "100000")
    perp_usdt: str = os.environ.get("NEODAX_PERP_USDT", "50000")
    maker_eth: str = os.environ.get("NEODAX_MAKER_ETH", "200")  # spot maker inventory (base asset)
    perp_maker_usdt: str = os.environ.get("NEODAX_PERP_MAKER_USDT", "50000")  # perp maker collateral
    settle_timeout_s: float = _f("NEODAX_SETTLE_TIMEOUT_MS", 30_000) / 1000  # async faucet/transfer budget


funding = Funding()


@dataclass(frozen=True)
class PerpTradeCfg:
    """perp trade test sizing. small per-fill notional. leverage only so order accepted."""

    order_notional_usd: float = _f("NEODAX_PERP_ORDER_NOTIONAL", 5000)
    leverage: int = _i("NEODAX_PERP_LEVERAGE", 5)


perp_trade = PerpTradeCfg()


@dataclass(frozen=True)
class FeeTierFlowCfg:
    """@serial fee-tier flow: drive volume until overlay steps to target tier. exact threshold
    read LIVE from competition.fee_tiers (never hardcoded). these only sizing/safety knobs."""

    target_tier_level: int = _i("NEODAX_TARGET_TIER_LEVEL", 1)
    drive_order_notional_usd: float = _f("NEODAX_DRIVE_ORDER_NOTIONAL", 50_000)
    max_cycles: int = _i("NEODAX_DRIVE_MAX_CYCLES", 12)
    volume_overshoot: float = _f("NEODAX_DRIVE_OVERSHOOT", 1.15)
    ingest_timeout_s: float = _f("NEODAX_INGEST_TIMEOUT_MS", 180_000) / 1000


fee_tier_flow = FeeTierFlowCfg()


@dataclass(frozen=True)
class RiskTierCfg:
    """perp leverage-based tiered margin (YEN-2544). per-tier caps/rates read LIVE."""

    market: str = perp_market
    global_max_leverage: int = _i("NEODAX_GLOBAL_MAX_LEVERAGE", 125)  # BE limits.MaxLeverage
    unknown_symbol: str = os.environ.get("NEODAX_UNKNOWN_PERP_SYMBOL", "NOTAMARKET-PERP")
    over_tier_multiplier: float = _f("NEODAX_OVER_TIER_MULT", 1.2)


risk_tier = RiskTierCfg()


@dataclass(frozen=True)
class PositionHistoryCfg:
    """perp position history (YEN-2548). negative-path knobs (bounds mirror BE validators)."""

    market: str = perp_market
    list_max_page_size: int = _i("NEODAX_POSHIST_LIST_MAX", 100)
    detail_max_page_size: int = _i("NEODAX_POSHIST_DETAIL_MAX", 500)
    unknown_position_id: str = os.environ.get("NEODAX_UNKNOWN_POSITION_ID", "00000000-0000-0000-0000-000000000000")
    order_notional_usd: float = _f("NEODAX_PERP_ORDER_NOTIONAL", 5000)
    leverage: int = _i("NEODAX_PERP_LEVERAGE", 5)
    ingest_timeout_s: float = _f("NEODAX_POSHIST_INGEST_MS", 90_000) / 1000


position_history = PositionHistoryCfg()


@dataclass(frozen=True)
class TieredReductionCfg:
    """perp tiered position reduction / Stage0 liquidation (YEN-2545). thin market (SUI) so a
    boundary-spanning position is fundable. maker seeds both the open fill and the reduce bid.
    AS-BUILT: engine does ONE Stage0 partial peel then Stage1 full close -> min_pieces=1.
    WARNING: mark injection is market-wide (can liquidate others) -> thin market + restore."""

    market: str = os.environ.get("NEODAX_LIQ_MARKET", "SUIUSDT-PERP")
    open_notional_usd: float = _f("NEODAX_LIQ_OPEN_NOTIONAL", 400_000)  # tier3; IM ~8k @50x
    leverage: int = _i("NEODAX_LIQ_LEVERAGE", 50)  # <= tier3 max 50
    subject_deposit_usdt: str = os.environ.get("NEODAX_LIQ_SUBJECT_USDT", "25000")
    maker_deposit_usdt: str = os.environ.get("NEODAX_LIQ_MAKER_USDT", "45000")
    maker_bid_below_entry_pct: float = _f("NEODAX_LIQ_MAKER_BELOW", 0.005)  # near-entry PnL-neutral fills
    step_drop_pct: float = _f("NEODAX_LIQ_STEP_DROP", 0.0035)  # gentle creep: one-tier peel per trigger
    max_steps: int = _i("NEODAX_LIQ_MAX_STEPS", 40)
    min_pieces: int = _i("NEODAX_LIQ_MIN_PIECES", 1)  # >= this many partial reductions (as-built: 1)
    step_hold_s: float = _f("NEODAX_LIQ_STEP_HOLD_MS", 7_000) / 1000  # scanner (2s) + ingest headroom
    # TC-LIQ-030 single-tier: tier2 open + FAT deposit so ONE reduction to tier1 heals the ladder.
    one_tier_open_notional_usd: float = _f("NEODAX_LIQ1_OPEN_NOTIONAL", 150_000)  # tier2 (>100k, <=250k)
    one_tier_subject_deposit_usdt: str = os.environ.get("NEODAX_LIQ1_SUBJECT_USDT", "15000")


tiered_reduction = TieredReductionCfg()


@dataclass(frozen=True)
class TakeoverLiquidationCfg:
    """perp full-liquidation takeover price integrity (YEN-3325). repro needs TWO markets:
    dominant LONG (crashed toward 0) + small SHORT held ~flat, long_notional > deposit +
    short_notional. on fixed code the SHORT leg's takeover records price=0 (clamp fired).
    WARNING: mark injection is MARKET-WIDE. restore ASAP."""

    long_market: str = os.environ.get("NEODAX_TKO_LONG_MARKET", "SUIUSDT-PERP")  # A: crashed toward 0
    short_market: str = os.environ.get("NEODAX_TKO_SHORT_MARKET", "DOGEUSDT-PERP")  # B: receives clamp
    long_notional_usd: float = _f("NEODAX_TKO_LONG_NOTIONAL", 40_000)
    short_notional_usd: float = _f("NEODAX_TKO_SHORT_NOTIONAL", 4_000)
    subject_deposit_usdt: str = os.environ.get("NEODAX_TKO_SUBJECT_USDT", "3000")  # 40k >> 3k+4k
    maker_deposit_usdt: str = os.environ.get("NEODAX_TKO_MAKER_USDT", "12000")  # takes BOTH open sides
    leverage: int = _i("NEODAX_TKO_LEVERAGE", 20)  # IM = 44k/20 = 2200 <= 3000 deposit
    crash_to_pct: float = _f("NEODAX_TKO_CRASH_PCT", 0.01)  # long mark -> entry x this
    short_flat_above_pct: float = _f("NEODAX_TKO_SHORT_ABOVE", 0.01)  # small LOSS, not IOC'd
    liquidate_timeout_s: float = _f("NEODAX_TKO_LIQ_TIMEOUT_MS", 120_000) / 1000
    trade_ingest_timeout_s: float = _f("NEODAX_TKO_TRADE_MS", 60_000) / 1000


takeover_liquidation = TakeoverLiquidationCfg()


@dataclass(frozen=True)
class YellowFlowCfg:
    """YELLOW-balance tier path (either-threshold rule). campaign_yellow_balance = 24h
    HOUR-WEIGHTED average, so single deposit of ~24x target requirement crosses it in one tick.
    DOWN path not tested: 24h average can not drain in a run."""

    asset: str = os.environ.get("NEODAX_YELLOW_ASSET", "YELLOW")
    deposit_multiplier: float = _f("NEODAX_YELLOW_MULT", 24) * 1.05
    ingest_timeout_s: float = _f("NEODAX_YELLOW_INGEST_TIMEOUT_MS", 150_000) / 1000


yellow_flow = YellowFlowCfg()
