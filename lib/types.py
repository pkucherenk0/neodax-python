"""canonical shapes for harness. read this first to understand data flow.

python port of lib/types.ts — formalize structures that live as prose in the TS original.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

EnvName = Literal["uat", "stage"]


# endpoint set + funding model for one env. uat auto-fund fresh wallets via faucet.
# stage use fixed pre-funded pool, no faucet.
@dataclass(frozen=True)
class EnvConfig:
    name: EnvName
    base_url: str
    auth_base: str
    trading_base: str
    faucet_url: str | None  # None on stage
    has_faucet: bool


# wallet that authenticated and (where applicable) been funded.
# app_session_id = wallet address used as app_session_id in trading/faucet calls.
# jwt = auth service access_token.
@dataclass(frozen=True)
class FundedAccount:
    address: str
    jwt: str
    app_session_id: str


# unit of assertion. info=True checks observational, NEVER fail run.
@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: object
    info: bool = False


Role = Literal["taker", "maker"]


# fee-schedule tier resolved from live competition schedule. rates = decimals (0.0004 = 4bps).
# vol_min / yellow_min = campaign thresholds that qualify tier.
@dataclass(frozen=True)
class Tier:
    level: int
    name: str
    perp_taker: float
    perp_maker: float
    spot_taker: float
    spot_maker: float
    vol_min: float
    yellow_min: float
