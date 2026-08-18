"""Auth: refresh_token rotation. this env's access-token TTL is 60s (confirmed by decoding a
live token's exp/iat claims) -- short enough that any account whose provisioning or own test
flow spans that long needs to re-authenticate mid-flow. challenge/verify (the initial mint)
stays in fixtures/accounts.py; this is just the /auth/refresh half.
"""
from __future__ import annotations

from lib.http import ResilientClient
from lib.schemas import AuthVerify
from lib.validate import parsed_json


def refresh_access_token(auth_client: ResilientClient, refresh_token: str) -> AuthVerify:
    """POST /auth/refresh -> new access_token + refresh_token. refresh tokens ROTATE
    (single-use, revoked the moment they're used) -- always persist the new one this returns,
    the old one stops working immediately."""
    res = auth_client.post("/auth/refresh", data={"refresh_token": refresh_token})
    assert res.ok, f"token refresh failed: HTTP {res.status} {res.text()}"
    return parsed_json(res, AuthVerify)
