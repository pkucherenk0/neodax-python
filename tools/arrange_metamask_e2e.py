"""One-off arrangement for the MetaMask (dappwright) visual e2e test in e2e/.

dappwright (real MetaMask automation) only exists in Node/TS, so that test lives outside
this Python suite. This script does the API side in Python (this repo's existing lib/,
same as every other suite) — mints + funds two throwaway UAT accounts, then writes their
credentials to e2e/.arrangement.json for the Node/Playwright test to read.

subject: spot-funded only, and generated from a fresh BIP-39 mnemonic (not just a raw key) —
         the Node test onboards MetaMask directly with that mnemonic as its ONE and only
         account, so there's no "import a second account + switch to it" step/ambiguity.
         The Node test transfers spot -> perp itself via the real UI — that's the thing
         under test, so it should start from a spot-only balance.
maker:   spot + perp funded, ready to rest the crossing order. The Node test places that
         order itself via a raw API call once the subject's order is resting (needs a
         live price at that moment, so the order isn't pre-placed here).

run (repo root, venv active): python3 tools/arrange_metamask_e2e.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eth_account import Account
from eth_account.messages import encode_defunct
from playwright.sync_api import sync_playwright

Account.enable_unaudited_hdwallet_features()

from configs.competition import funding, perp_market
from lib.env import resolve_env
from lib.funding import faucet_deposit, get_perp_available, get_spot_available, transfer_spot_to_perp, wait_for_balance

OUT = Path(__file__).resolve().parent.parent / "e2e" / ".arrangement.json"


def _sig_hex(sig: bytes) -> str:
    h = sig.hex()
    return h if h.startswith("0x") else "0x" + h


def _mint_and_auth(auth_ctx, wallet):
    ch = auth_ctx.post("/auth/challenge", data={"wallet_address": wallet.address})
    assert ch.ok, f"auth challenge failed: HTTP {ch.status} {ch.text()}"
    challenge = ch.json()["challenge"]
    signed = Account.sign_message(encode_defunct(text=challenge), private_key=wallet.key)
    v = auth_ctx.post("/auth/verify", data={
        "wallet_address": wallet.address, "challenge": challenge, "signature": _sig_hex(signed.signature),
    })
    assert v.ok, f"auth verify failed: HTTP {v.status} {v.text()}"
    return v.json()["access_token"]


def main() -> None:
    cfg = resolve_env("uat")
    with sync_playwright() as pw:
        auth_ctx = pw.request.new_context(base_url=cfg.auth_base)
        faucet_ctx = pw.request.new_context(base_url=cfg.faucet_url)

        subject_wallet, subject_mnemonic = Account.create_with_mnemonic()
        subject_token = _mint_and_auth(auth_ctx, subject_wallet)
        subject_trading = pw.request.new_context(base_url=cfg.trading_base,
                                                  extra_http_headers={"Authorization": f"Bearer {subject_token}"})
        faucet_deposit(faucet_ctx, subject_wallet.address, funding.spot_usdt)
        wait_for_balance(lambda: get_spot_available(subject_trading, subject_wallet.address),
                         float(funding.spot_usdt) * 0.9, funding.settle_timeout_s)
        print(f"subject funded (spot only): {subject_wallet.address}")

        maker_wallet = Account.create()
        maker_token = _mint_and_auth(auth_ctx, maker_wallet)
        maker_trading = pw.request.new_context(base_url=cfg.trading_base,
                                                extra_http_headers={"Authorization": f"Bearer {maker_token}"})
        faucet_deposit(faucet_ctx, maker_wallet.address, funding.spot_usdt)
        wait_for_balance(lambda: get_spot_available(maker_trading, maker_wallet.address),
                         float(funding.spot_usdt) * 0.9, funding.settle_timeout_s)
        transfer_spot_to_perp(maker_trading, maker_wallet.address, funding.perp_usdt)
        wait_for_balance(lambda: get_perp_available(maker_trading, maker_wallet.address),
                         float(funding.perp_usdt) * 0.9, funding.settle_timeout_s)
        print(f"maker funded (spot+perp): {maker_wallet.address}")

        auth_ctx.dispose()
        faucet_ctx.dispose()
        subject_trading.dispose()
        maker_trading.dispose()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "env": {"trading_base": cfg.trading_base, "auth_base": cfg.auth_base},
        "market": perp_market,
        "subject": {"address": subject_wallet.address, "mnemonic": subject_mnemonic},
        "maker": {"address": maker_wallet.address, "access_token": maker_token},
    }, indent=2))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
