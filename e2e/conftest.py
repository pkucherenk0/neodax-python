"""browser E2E fixtures — SESSION INJECTION. port of fixtures/e2e.ts. skip clicking
wallet-connect.

auth fresh wallet via API (same challenge -> sign -> verify real user does). seed tokens
into FE localStorage as playwright storage_state BEFORE app boots. FE token-persistence
middleware hydrates them, app comes up authenticated.

gives:
  fe_session — FeSession(address, access_token, refresh_token). use to API-ARRANGE (enroll /
               fund / drive volume via lib/) with SAME wallet browser is authenticated as.
  page       — pytest-playwright page on FE origin, pre-seeded with session.

covers everything BEHIND login. wallet-connect flow itself out of scope here.

run: pytest -m e2e e2e/   (needs a browser + the FE reachable; set NEODAX_FE_BASE)
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from config.e2e import FE_ACCESS_TOKEN_KEY, FE_REFRESH_TOKEN_KEY, fe_base_url


@dataclass(frozen=True)
class FeSession:
    address: str
    access_token: str
    refresh_token: str | None = None


def _sig_hex(signature: bytes) -> str:
    hexed = signature.hex()
    return hexed if hexed.startswith("0x") else "0x" + hexed


@pytest.fixture
def fe_session(env_cfg, _pw) -> FeSession:
    """fresh authenticated wallet (API auth). env from --env, same as API suites."""
    wallet = Account.create()
    ctx = _pw.request.new_context(base_url=env_cfg.auth_base)
    try:
        ch_res = ctx.post("/auth/challenge", data={"wallet_address": wallet.address})
        assert ch_res.ok, f"auth challenge failed: HTTP {ch_res.status} {ch_res.text()}"
        challenge = ch_res.json()["challenge"]
        signed = Account.sign_message(encode_defunct(text=challenge), private_key=wallet.key)
        v_res = ctx.post("/auth/verify", data={
            "wallet_address": wallet.address, "challenge": challenge, "signature": _sig_hex(signed.signature),
        })
        assert v_res.ok, f"auth verify failed: HTTP {v_res.status} {v_res.text()}"
        body = v_res.json()
        return FeSession(address=wallet.address, access_token=body["access_token"],
                         refresh_token=body.get("refresh_token"))
    finally:
        ctx.dispose()


@pytest.fixture
def browser_context_args(browser_context_args: dict, fe_session: FeSession) -> dict:
    """seed session tokens into FE origin localStorage before context loads any page.
    override pytest-playwright's context args, so default `page` authenticated for free."""
    origin = urlparse(fe_base_url)
    local_storage = [{"name": FE_ACCESS_TOKEN_KEY, "value": fe_session.access_token}]
    if fe_session.refresh_token:
        local_storage.append({"name": FE_REFRESH_TOKEN_KEY, "value": fe_session.refresh_token})
    return {
        **browser_context_args,
        "base_url": fe_base_url,
        "storage_state": {
            "cookies": [],
            "origins": [{"origin": f"{origin.scheme}://{origin.netloc}", "localStorage": local_storage}],
        },
    }
