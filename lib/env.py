"""endpoint set per env.

base_url      -> hub: competitions / enroll  (/api/v1/competitions/...)
auth_base     -> auth: wallet challenge/verify  (/auth/challenge, /auth/verify)
trading_base  -> api: perp/spot trading + balances

real hosts are NOT committed -- always come from env vars (NIMBUS_<ENV>_*, see
.env.example). local dev: copy .env.example -> .env (git-ignored), fill in, conftest
loads it automatically. CI: set the same names as repo/environment secrets.
"""
from __future__ import annotations

import os

from lib.types import EnvConfig, EnvName


def _require(var: str) -> str:
    val = os.environ.get(var)
    if not val:
        raise RuntimeError(f"{var} is not set. Copy .env.example to .env and fill it in (or set it in CI).")
    return val


def resolve_env(name: EnvName) -> EnvConfig:
    prefix = f"NIMBUS_{name.upper()}"
    faucet_url = os.environ.get(f"{prefix}_FAUCET_URL")
    return EnvConfig(
        name=name,
        base_url=_require(f"{prefix}_BASE"),
        auth_base=_require(f"{prefix}_AUTH_BASE"),
        trading_base=_require(f"{prefix}_TRADING_BASE"),
        faucet_url=faucet_url,
        has_faucet=faucet_url is not None,
    )
