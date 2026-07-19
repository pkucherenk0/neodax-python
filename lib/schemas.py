"""response contracts. port of lib/schemas.ts (zod -> pydantic v2).

validate SHAPE of response, not just status = core of API integration testing.
catch silent contract drift (renamed field, changed type, dropped key) that status-only miss.
tests parse responses through these via `parsed_json`.

pydantic notes:
  - `extra="allow"` == zod .passthrough()
  - bare-array wire shapes use TypeAdapter (pydantic has no root array model)
  - financial amounts are STRINGS on this API. keep str, parse at call sites.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, TypeAdapter


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Loose(BaseModel):
    model_config = ConfigDict(extra="allow")


# CONFIRMED against competition lib/accounts.js (proven in production).
class AuthChallenge(_Loose):
    challenge: str


class AuthVerify(_Loose):
    access_token: str
    refresh_token: str | None = None
    expires_in: float | None = None


# CONFIRMED against live UAT (enroll into spot-0 -> 201).
class EnrollResponse(_Loose):
    competition_id: str
    owner_address: str
    joined_at: str
    effective_join_hour: str | None = None
    volume_at_join_usd: str | None = None  # financial amounts are strings on this API


# CONFIRMED against live UAT: errors nested under `error`.
#   { "error": { "code": "TERMS_NOT_ACCEPTED", "message": "terms_accepted must be true" } }
class _ErrorInner(_Loose):
    code: str
    message: str | None = None


class ErrorResponse(_Loose):
    error: _ErrorInner


# trading-api (perp) error shape FLAT — `error` = code string, not nested object:
#   { "error": "market_not_found", "message": "perpetuals market not found or inactive" }
class FlatError(_Loose):
    error: str
    message: str | None = None


# confirmed against live UAT: bps + volume requirements come back as strings.
class FeeTier(_Loose):
    tier_level: int
    tier_name: str
    campaign_volume_req_usd: str
    campaign_yellow_req: str | None = None
    spot_maker_bps: str
    spot_taker_bps: str
    perp_maker_bps: str
    perp_taker_bps: str


class CompetitionSchedule(_Loose):
    slug: str
    status: str  # e.g. 'settled' | 'review' | (open states). see config is_enrollable()
    starts_at: str | None = None
    ends_at: str | None = None
    fee_tiers: list[FeeTier] | None = None
    volume_source: str | None = None
    total_volume_usd: str | None = None


# --- funding (faucet / transfer / balances). confirmed against live UAT. ---
class FaucetResponse(_Loose):
    success: bool
    message: str | None = None


class TransferResponse(_Loose):
    transfer_id: str
    message: str | None = None


class BalanceEntry(_Loose):
    asset_symbol: str
    available_balance: str  # financial amounts are strings on this API


class SpotAccount(_Loose):
    balances: list[BalanceEntry]


# GET /perpetual/balance returns bare array of balance entries.
perp_balance_schema = TypeAdapter(list[BalanceEntry])


# --- fee resolution. confirmed against live UAT. bps values come back as strings ("10", "3.5"). ---
class _BpsGroup(_Loose):
    spot_maker_bps: str
    spot_taker_bps: str
    perp_maker_bps: str
    perp_taker_bps: str


class _StandardFees(_BpsGroup):
    fee_tier: int | None = None


class _OverlayFees(_Loose):
    active: bool
    slug: str | None = None
    campaign_volume_usd: str | None = None
    campaign_yellow_balance: str | None = None  # 24h hour-weighted average YELLOW holding
    spot_maker_bps: str | None = None
    spot_taker_bps: str | None = None
    perp_maker_bps: str | None = None
    perp_taker_bps: str | None = None


# GET /account/fee-tier-effective — authoritative best-of: standard tier, competition overlay
# (active only after enrollment ingests, ~20s), effective (charged) rates.
class FeeTierEffective(_Loose):
    standard: _StandardFees
    overlay: _OverlayFees
    effective: _BpsGroup


class SpotMarketFeeRateRow(_Loose):
    market: str
    maker_fee_rate: str
    taker_fee_rate: str
    source: str


# GET /spot/account/market-fee-rate — account effective SPOT rate as decimals ("0.001" = 10bps).
spot_market_fee_rate_schema = TypeAdapter(list[SpotMarketFeeRateRow])


# --- spot orders & trades. confirmed against live UAT. ---
class SpotOrderResponse(_Loose):
    order_uuid: str


class SpotTrade(_Loose):
    id: str
    order_id: str
    market: str
    amount: str
    price: str
    total: str
    is_buyer: bool
    is_maker: bool
    fee: str


class SpotTradesResponse(_Loose):
    trades: list[SpotTrade]


class RichBalanceEntry(_Loose):
    asset_symbol: str
    total_balance: str | None = None
    available_balance: str
    locked_balance: str | None = None


# GET /spot/account — balances with total/locked (richer than funding read). confirmed from BE.
class SpotAccountRich(_Loose):
    balances: list[RichBalanceEntry]


# GET /spot/open_orders and /spot/orders -> { orders: [...] }. item id = `order_id`, resting state "wait".
class SpotOrderItem(_Loose):
    order_id: str
    market: str
    price: str
    amount: str
    origin_amount: str | None = None
    fill_amount: str | None = None
    side: str
    type: str
    time_in_force: str | None = None
    state: str


class SpotOrdersResponse(_Loose):
    orders: list[SpotOrderItem]


# DELETE /spot/order response (async).
class SpotCancelResponse(_Loose):
    message: str | None = None


# --- perp market metadata, pricing, orders & trades. ---


class ExchangeFilter(_Loose):
    filter_type: str
    config: dict | None = None


class ExchangeSymbol(_Loose):
    symbol: str
    status: str | None = None
    amount_precision: int | None = None
    price_precision: int | None = None
    max_allowed_leverage: str | None = None  # per-market leverage cap
    filters: list[ExchangeFilter] | None = None


# GET /perpetual/exchangeInfo — symbol list with lot/tick/min-notional filters. loose on filter
# config (varies by symbol).
class PerpExchangeInfo(_Loose):
    symbols: list[ExchangeSymbol]


# GET /orderbook?symbol= — same endpoint serves spot + perp. levels = [price, qty] string pairs.
class Orderbook(_Loose):
    bids: list[list[str]] | None = None
    asks: list[list[str]] | None = None


class FundingRateRow(_Loose):
    market: str
    mark_price: str | None = None


# GET /perpetual/funding-rates/current?symbols= — carries mark price per market.
class PerpFundingRates(_Loose):
    funding_rates: list[FundingRateRow] | None = None


# POST /perpetual/order.
class PerpOrderResponse(_Loose):
    order_uuid: str


# GET /perpetual/trades — perp fills key on `order_uuid` (spot uses `order_id`). fee =
# quote-denominated (USDT). exec_type in trade | liquidation | liquidation_takeover | adl.
# total = amount x price (signed). YEN-3325: a liquidation_takeover MUST persist price > 0.
class PerpTradeRow(_Loose):
    order_uuid: str
    market: str
    amount: str
    price: str
    is_maker: bool
    fee: str
    total: str | None = None
    exec_type: str | None = None
    is_buyer: bool | None = None
    executed_at: str | None = None


class PerpTradesResponse(_Loose):
    trades: list[PerpTradeRow]


class PerpRichBalanceEntry(_Loose):
    asset_symbol: str
    total_balance: str | None = None
    available_balance: str
    allocated_balance: str | None = None
    locked_balance: str | None = None


# GET /perpetual/balance — array of per-asset balances (subset of fields we assert on).
perp_balance_rich_schema = TypeAdapter(list[PerpRichBalanceEntry])


# GET /perpetual/account — big object. read available_balance + initial_leverages[market].
class PerpAccount(_Loose):
    available_balance: str | None = None
    total_account_equity: str | None = None
    initial_leverages: dict[str, str] | None = None
    margin_modes: dict[str, str] | None = None


# GET /perpetual/open_orders and /perpetual/orders — { orders: [...] }. NOTE: order id field =
# `order_id` here (equals order_uuid returned by POST /perpetual/order).
class PerpOrderItem(_Loose):
    order_id: str
    market: str
    price: str
    amount: str
    origin_amount: str | None = None
    fill_amount: str | None = None
    side: str
    direction: str
    type: str
    time_in_force: str | None = None
    state: str
    reduce_only: bool | None = None
    leverage: str | None = None


class PerpOrdersResponse(_Loose):
    orders: list[PerpOrderItem]


class PerpPosition(_Loose):
    uuid: str | None = None
    market: str
    direction: str
    amount: str
    entry_price: str | None = None
    mark_price: str | None = None
    leverage: str | None = None
    unrealized_pnl: str | None = None
    allocated_margin: str | None = None
    liquidation_price: str | None = None
    margin_mode: str | None = None
    margin_asset: str | None = None


# GET /perpetual/positions — bare array.
perp_positions_schema = TypeAdapter(list[PerpPosition])


# POST /perpetual/leverage response.
class PerpLeverageResponse(_Loose):
    success: bool
    message: str | None = None
    market: str | None = None
    leverage: str | None = None


# DELETE /perpetual/order response (async. "cancellation request sent").
class PerpCancelResponse(_Loose):
    message: str | None = None


# GET /perpetual/market-risk-tiers — per-market leverage-based tiered-margin ladder (YEN-2544).
# public (no auth). rate/leverage/qty fields STRINGS. `max_position_qty` = tier UPPER
# quote-notional cap (name historical — it is quote, not contracts).
class PerpRiskTierRow(_Loose):
    symbol: str
    tier_index: int
    max_position_qty: str
    max_leverage: str
    initial_margin_rate: str
    maintenance_margin_rate: str
    config_version: int
    from_config_table: bool


class PerpRiskTiersResponse(_Loose):
    tiers: list[PerpRiskTierRow]


# GET /perpetual/position-history — closed-position lifecycle records (YEN-2548). financial
# fields = strings. empty ones dropped by proto omitempty -> optional.
# close_reason in normal | liquidated | adl.
class PerpPositionHistoryItem(_Loose):
    id: str
    market: str
    direction: str  # long | short
    amount: str | None = None
    entry_price: str | None = None
    exit_price: str | None = None
    leverage: str | None = None
    allocated_margin: str | None = None
    initial_margin: str | None = None
    realized_pnl: str | None = None
    pnl_ratio: str | None = None  # ROI = realized_pnl / max initial margin
    closed_quantity: str | None = None
    max_held: str | None = None
    total_trading_fee: str | None = None
    net_funding_fee: str | None = None
    liquidation_price: str | None = None
    margin_mode: str | None = None
    close_reason: str | None = None  # normal | liquidated | adl
    opened_at: str | None = None
    closed_at: str | None = None
    updated_at: str | None = None


class PerpPositionHistoryResponse(_Loose):
    # empty history comes back as `null` (not []). confirmed live on UAT. normalized to [] in lib.
    positions: list[PerpPositionHistoryItem] | None = None
    total: int | None = None
    page: int | None = None
    page_size: int | None = None
    next_cursor: str | None = None
    has_more: bool | None = None


# GET /perpetual/position-history/:id — linked-orders (fills) of one closed position, ascending
# by time. exec_type in trade | liquidation | liquidation_takeover | adl. kind in open | close.
class PerpPositionFill(_Loose):
    id: str
    direction: str | None = None  # long | short (position-direction view)
    amount: str | None = None
    price: str | None = None
    pnl: str | None = None
    fee: str | None = None
    fee_currency: str | None = None
    exec_type: str | None = None
    kind: str | None = None  # open | close
    executed_at: str | None = None


class PerpPositionHistoryDetail(_Loose):
    fills: list[PerpPositionFill] | None = None  # may be null when empty. normalized in lib
    next_cursor: str | None = None
    has_more: bool | None = None
    page_size: int | None = None


# GET /perpetual/transaction/history — ledger rows. type filter incl. liquidation_partial (the
# Stage0 tiered-reduction marker, YEN-2545). amount signed from user view.
class PerpTransactionItem(_Loose):
    transaction_id: int
    transaction_time: str
    transaction_type: str  # e.g. LIQUIDATION_PARTIAL | LIQUIDATION | REALIZED_PNL | FEE | ...
    market: str
    asset: str
    amount: str


class PerpTransactionHistory(_Loose):
    items: list[PerpTransactionItem] | None = None  # may be null when empty. normalized in lib
    total: int | None = None
    has_next: bool | None = None
    has_more: bool | None = None
    next_cursor: str | None = None
    page: int | None = None
    page_size: int | None = None


# POST {faucet}/api/simulate-mark-price — inject mark-price event (kafka) to drive liquidation on
# UAT (YEN-2545 test). echoes published values + event_id. no auth.
class SimulateMarkPrice(_Loose):
    success: bool
    message: str | None = None
    market: str | None = None
    mark_price: str | None = None
    index_price: str | None = None
    funding_rate: str | None = None
    next_funding_time: str | None = None
    event_id: str | None = None
    published_at: str | None = None
