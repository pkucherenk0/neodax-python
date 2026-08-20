"""One-off arrangement for the k6 performance suite in perf/.

k6's embedded JS runtime has no secp256k1/keccak library, so it can't mint a session itself
(the wallet-signature challenge/verify step needs eth_account, same as tools/arrange_metamask_e2e.py).
This script does that minting in Python (this repo's existing lib/, same pattern), then writes
already-authenticated {address, access_token, refresh_token} pairs to perf/data/accounts.json for
k6 to load. k6 only ever calls POST /auth/refresh from there on (lib/auth.py's refresh_access_token
equivalent) -- pure JSON, no signing needed.

access_token TTL is 60s and refresh_token is single-use/rotating (see lib/auth.py) -- this file
goes stale in about a minute. Run it immediately before every k6 run that needs auth
(scripts/account-reads/, scripts/order-placement/); never reuse a stale copy across sessions,
same as e2e/.arrangement.json.

--count N (default 5) subject accounts (spot+perp funded), one dedicated per k6 VU -- a k6 VU
count above N means two VUs would share an account and race on refresh_token rotation, breaking
one of them. Plus one `maker` account (spot+perp funded) for order-placement's resting
counterparty. `market` in the output defaults to an idle-and-confirmed-tradeable market from
CONVENTIONS.md's UAT allocation (LINKUSDT-PERP) -- never the shared default (BTC/ETH) or the
liquidation tests' SUI/DOGE.

run (repo root, venv active): python3 tools/arrange_perf_accounts.py [--count N] [--market SYMBOL]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import to_checksum_address
from playwright.sync_api import sync_playwright

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from configs.competition import funding
from lib.env import resolve_env
from lib.funding import faucet_deposit, get_perp_available, get_spot_available, transfer_spot_to_perp, wait_for_balance

OUT = Path(__file__).resolve().parent.parent / "perf" / "data" / "accounts.json"
DEFAULT_MARKET = "LINKUSDT-PERP"  # idle, confirmed live-tradeable -- see CONVENTIONS.md


def _sig_hex(sig: bytes) -> str:
    h = sig.hex()
    return h if h.startswith("0x") else "0x" + h


def _mint_fund_and_auth(auth_ctx, faucet_ctx, trading_ctx_factory, pw, role: str) -> dict:
    """mint a fresh wallet, auth it, fund spot+perp, return {address, access_token, refresh_token}."""
    wallet = Account.create()
    address = to_checksum_address(wallet.address)
    ch = auth_ctx.post("/auth/challenge", data={"wallet_address": address})
    assert ch.ok, f"auth challenge failed ({role}): HTTP {ch.status} {ch.text()}"
    challenge = ch.json()["challenge"]
    signed = Account.sign_message(encode_defunct(text=challenge), private_key=wallet.key)
    v = auth_ctx.post("/auth/verify", data={
        "wallet_address": address, "challenge": challenge, "signature": _sig_hex(signed.signature),
    })
    assert v.ok, f"auth verify failed ({role}): HTTP {v.status} {v.text()}"
    body = v.json()
    access_token, refresh_token = body["access_token"], body["refresh_token"]

    trading = trading_ctx_factory(access_token)
    faucet_deposit(faucet_ctx, address, funding.spot_usdt)
    wait_for_balance(lambda: get_spot_available(trading, address), float(funding.spot_usdt) * 0.9, funding.settle_timeout_s)
    transfer_spot_to_perp(trading, address, funding.perp_usdt)
    wait_for_balance(lambda: get_perp_available(trading, address), float(funding.perp_usdt) * 0.9, funding.settle_timeout_s)
    trading.dispose()
    print(f"{role} funded (spot+perp): {address}")
    return {"address": address, "access_token": access_token, "refresh_token": refresh_token}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=5, help="number of subject accounts (default 5)")
    parser.add_argument("--market", default=DEFAULT_MARKET, help=f"perp market for order-placement (default {DEFAULT_MARKET})")
    args = parser.parse_args()

    cfg = resolve_env("uat")
    with sync_playwright() as pw:
        auth_ctx = pw.request.new_context(base_url=cfg.auth_base)
        faucet_ctx = pw.request.new_context(base_url=cfg.faucet_url)

        def trading_ctx_factory(token: str):
            return pw.request.new_context(base_url=cfg.trading_base, extra_http_headers={"Authorization": f"Bearer {token}"})

        accounts = [_mint_fund_and_auth(auth_ctx, faucet_ctx, trading_ctx_factory, pw, f"subject-{i+1}")
                    for i in range(args.count)]
        maker = _mint_fund_and_auth(auth_ctx, faucet_ctx, trading_ctx_factory, pw, "maker")

        auth_ctx.dispose()
        faucet_ctx.dispose()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "env": {"trading_base": cfg.trading_base, "auth_base": cfg.auth_base},
        "market": args.market,
        "accounts": accounts,
        "maker": maker,
    }, indent=2))
    print(f"wrote {OUT} ({len(accounts)} subject accounts + 1 maker)")


if __name__ == "__main__":
    main()
