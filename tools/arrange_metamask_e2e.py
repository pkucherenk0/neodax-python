"""One-off arrangement for the MetaMask (dappwright) visual e2e test in e2e/.

dappwright (real MetaMask automation) only exists in Node, so that test lives outside
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
--out overrides the write path (default e2e/.arrangement.json) -- e2e_py/ (the Python POM port,
in progress) passes its own path so the two suites never race each other's arrangement file
during the coexistence period.
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

load_dotenv(Path(__file__).resolve().parent.parent / ".env")  # invoked from e2e/ (cwd), not repo root
Account.enable_unaudited_hdwallet_features()

from configs.competition import funding, perp_market
from lib.env import resolve_env
from lib.funding import faucet_deposit, get_perp_available, get_spot_available, transfer_spot_to_perp, wait_for_balance
from lib.http import DEFAULT_RPS, RateLimiter, ResilientClient, ResilientOptions

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "e2e" / ".arrangement.json"

# funding setup takes more transient 5xx than the hot path (same reasoning as
# fixtures/accounts.py's PATIENT_RETRIES) -- hit both a 404 account_not_found (spot balance
# read, right after a fresh deposit) and a 503 validation_unavailable (spot->perp transfer)
# from this exact script during the e2e_py migration's confidence runs. This script used bare,
# non-retrying contexts throughout (unlike fixtures/accounts.py's ClientFactory.make(...,
# max_retries=...)) -- fixed here since both the current Node e2e/ and e2e_py depend on it.
PATIENT_RETRIES = 6


def _hex(b: bytes) -> str:
    """bytes -> 0x-prefixed hex. used for both signatures and the maker's raw private key."""
    h = b.hex()
    return h if h.startswith("0x") else "0x" + h


def _mint_and_auth(auth_ctx, wallet) -> tuple[str, str]:
    """returns (checksummed_address, access_token). force EIP-55 casing -- faucet doesn't
    normalize it, mismatch silently drops a deposit. see e2e/README.md."""
    address = to_checksum_address(wallet.address)
    ch = auth_ctx.post("/auth/challenge", data={"wallet_address": address})
    assert ch.ok, f"auth challenge failed: HTTP {ch.status} {ch.text()}"
    challenge = ch.json()["challenge"]
    signed = Account.sign_message(encode_defunct(text=challenge), private_key=wallet.key)
    v = auth_ctx.post("/auth/verify", data={
        "wallet_address": address, "challenge": challenge, "signature": _hex(signed.signature),
    })
    assert v.ok, f"auth verify failed: HTTP {v.status} {v.text()}"
    return address, v.json()["access_token"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"write path (default {DEFAULT_OUT})")
    args = parser.parse_args()
    out = args.out

    cfg = resolve_env("uat")
    limiter = RateLimiter(DEFAULT_RPS)
    resilient_opts = ResilientOptions(max_retries=PATIENT_RETRIES, retry_on_5xx=True)

    with sync_playwright() as pw:
        auth_ctx = pw.request.new_context(base_url=cfg.auth_base)
        faucet_raw = pw.request.new_context(base_url=cfg.faucet_url)
        faucet_ctx = ResilientClient(faucet_raw, limiter, resilient_opts)

        subject_wallet, subject_mnemonic = Account.create_with_mnemonic()
        subject_address, subject_token = _mint_and_auth(auth_ctx, subject_wallet)
        subject_trading_raw = pw.request.new_context(base_url=cfg.trading_base,
                                                      extra_http_headers={"Authorization": f"Bearer {subject_token}"})
        subject_trading = ResilientClient(subject_trading_raw, limiter, resilient_opts)
        faucet_deposit(faucet_ctx, subject_address, funding.spot_usdt)
        wait_for_balance(lambda: get_spot_available(subject_trading, subject_address),
                         float(funding.spot_usdt) * 0.9, funding.settle_timeout_s)
        print(f"subject funded (spot only): {subject_address}")

        maker_wallet = Account.create()
        maker_address, maker_token = _mint_and_auth(auth_ctx, maker_wallet)
        maker_trading_raw = pw.request.new_context(base_url=cfg.trading_base,
                                                    extra_http_headers={"Authorization": f"Bearer {maker_token}"})
        maker_trading = ResilientClient(maker_trading_raw, limiter, resilient_opts)
        faucet_deposit(faucet_ctx, maker_address, funding.spot_usdt)
        wait_for_balance(lambda: get_spot_available(maker_trading, maker_address),
                         float(funding.spot_usdt) * 0.9, funding.settle_timeout_s)
        transfer_spot_to_perp(maker_trading, maker_address, funding.perp_usdt)
        wait_for_balance(lambda: get_perp_available(maker_trading, maker_address),
                         float(funding.perp_usdt) * 0.9, funding.settle_timeout_s)
        print(f"maker funded (spot+perp): {maker_address}")

        auth_ctx.dispose()
        faucet_raw.dispose()
        subject_trading_raw.dispose()
        maker_trading_raw.dispose()

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "env": {"trading_base": cfg.trading_base, "auth_base": cfg.auth_base},
        "market": perp_market,
        "subject": {"address": subject_address, "mnemonic": subject_mnemonic},
        # private_key too, not just access_token: the token's 60s TTL is very likely expired by
        # the time e2e_py's own cleanup runs (the UI flow alone can exceed that), and no
        # refresh_token is captured here either -- re-deriving a fresh token from the key is
        # the only reliable way for e2e_py to flatten maker's position at teardown.
        "maker": {"address": maker_address, "access_token": maker_token, "private_key": _hex(maker_wallet.key)},
    }, indent=2))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
