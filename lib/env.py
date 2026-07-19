"""endpoint set per env. port of lib/env.ts.

base_url      -> hub: competitions / enroll  (/api/v1/competitions/...)
auth_base     -> auth: wallet challenge/verify  (/auth/challenge, /auth/verify)
trading_base  -> api: perp/spot trading + balances
env vars override single endpoint for local target.
"""
from __future__ import annotations

import os

from lib.types import EnvConfig, EnvName

ENVS: dict[str, EnvConfig] = {
    "uat": EnvConfig(
        name="uat",
        base_url="https://hub.uat.yellow.pro.neodax.app",
        auth_base="https://auth.uat.yellow.pro.neodax.app",
        trading_base="https://api.uat.yellow.pro.neodax.app",
        faucet_url="https://faucet.uat.yellow.pro.neodax.app",  # origin. funding post /api/deposit
        has_faucet=True,
    ),
    "stage": EnvConfig(
        name="stage",
        base_url="https://hub.staging.yellow.pro.neodax.app",
        auth_base="https://auth.staging.yellow.pro.neodax.app",
        trading_base="https://api.staging.yellow.pro.neodax.app",
        faucet_url=None,
        has_faucet=False,
    ),
}


def resolve_env(name: EnvName) -> EnvConfig:
    base = ENVS[name]
    return EnvConfig(
        name=base.name,
        base_url=os.environ.get("NEODAX_BASE", base.base_url),
        auth_base=os.environ.get("NEODAX_AUTH_BASE", base.auth_base),
        trading_base=os.environ.get("NEODAX_TRADING_BASE", base.trading_base),
        faucet_url=base.faucet_url,
        has_faucet=base.has_faucet,
    )
