"""Re-authenticate the e2e maker account and rewrite its access_token in .arrangement.json.

This env's JWT TTL is 60s (confirmed by decoding a live token's exp/iat claims) -- far
shorter than the real MetaMask e2e flow takes to reach matchRestingOrderWithApiCounterparty
(MetaMask onboarding + the full UI flow routinely takes 1-2+ minutes). The token minted at
arrangement time in arrange_metamask_e2e.py is long expired by then, so the Node test calls
this script right before it needs the maker's token, using the private_key saved alongside it.

run (repo root, venv active): python3 tools/refresh_e2e_maker_token.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eth_account import Account
from eth_account.messages import encode_defunct
from playwright.sync_api import sync_playwright

ARRANGEMENT = Path(__file__).resolve().parent.parent / "e2e" / ".arrangement.json"


def _sig_hex(sig: bytes) -> str:
    h = sig.hex()
    return h if h.startswith("0x") else "0x" + h


def main() -> None:
    arrangement = json.loads(ARRANGEMENT.read_text())
    maker = arrangement["maker"]
    wallet = Account.from_key(maker["private_key"])
    assert wallet.address == maker["address"], \
        f"private_key in {ARRANGEMENT} does not match its recorded maker address"

    with sync_playwright() as pw:
        auth_ctx = pw.request.new_context(base_url=arrangement["env"]["auth_base"])
        ch = auth_ctx.post("/auth/challenge", data={"wallet_address": wallet.address})
        assert ch.ok, f"auth challenge failed: HTTP {ch.status} {ch.text()}"
        challenge = ch.json()["challenge"]
        signed = Account.sign_message(encode_defunct(text=challenge), private_key=wallet.key)
        v = auth_ctx.post("/auth/verify", data={
            "wallet_address": wallet.address, "challenge": challenge, "signature": _sig_hex(signed.signature),
        })
        assert v.ok, f"auth verify failed: HTTP {v.status} {v.text()}"
        maker["access_token"] = v.json()["access_token"]
        auth_ctx.dispose()

    ARRANGEMENT.write_text(json.dumps(arrangement, indent=2))
    print(f"refreshed maker access_token for {wallet.address}")


if __name__ == "__main__":
    main()
