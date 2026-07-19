"""account funding. port of lib/funding.ts. UAT-only (need faucet).

faucet USDT -> spot, then transfer spot -> perps. both credits ASYNC, callers poll balance
until settle. every function take ResilientClient already scoped to right host.
"""
from __future__ import annotations

from urllib.parse import quote

from lib.http import ResilientClient
from lib.poll import wait_for_value
from lib.schemas import FaucetResponse, SpotAccount, TransferResponse, perp_balance_schema
from lib.validate import parsed_json

COLLATERAL = "USDT"

wait_for_balance = wait_for_value  # poll balance getter until reach min or timeout


def _assert_ok(res, op: str) -> None:
    # fail with clear message on non-2xx (e.g. transient 503 outlived retries)
    # instead of confusing schema-validation error on error body.
    if not res.ok:
        raise AssertionError(f"{op} failed: HTTP {res.status} {res.text()}")


def faucet_deposit(faucet_client: ResilientClient, app_session_id: str, amount: str, asset: str = COLLATERAL) -> None:
    """POST {faucetOrigin}/api/deposit — credit spot account (no auth). async, poll spot balance after."""
    res = faucet_client.post("/api/deposit", data={"app_session_id": app_session_id, "asset": asset, "amount": amount})
    _assert_ok(res, "faucet deposit")
    body = parsed_json(res, FaucetResponse)
    if not body.success:
        raise AssertionError(f"faucet deposit failed: {body}")


def transfer_spot_to_perp(trading_client: ResilientClient, app_session_id: str, amount: str, asset: str = COLLATERAL) -> str:
    """POST {tradingBase}/accounts/transfer — spot -> perps (auth). return 202 + transfer_id. async."""
    res = trading_client.post(
        "/accounts/transfer",
        data={
            "app_session_id": app_session_id,
            "source_account_type": "spot",
            "dest_account_type": "perps",
            "asset_symbol": asset,
            "amount": amount,
        },
    )
    _assert_ok(res, "spot->perp transfer")
    body = parsed_json(res, TransferResponse)
    return body.transfer_id


def get_spot_available(trading_client: ResilientClient, app_session_id: str, asset: str = COLLATERAL) -> float:
    res = trading_client.get(f"/spot/account?app_session_id={quote(app_session_id)}&asset={quote(asset)}")
    body = parsed_json(res, SpotAccount)
    entry = next((b for b in body.balances if b.asset_symbol == asset), None)
    return float(entry.available_balance) if entry else 0.0


def get_perp_available(trading_client: ResilientClient, app_session_id: str, asset: str = COLLATERAL) -> float:
    res = trading_client.get(f"/perpetual/balance?app_session_id={quote(app_session_id)}")
    body = parsed_json(res, perp_balance_schema)
    entry = next((b for b in body if b.asset_symbol == asset), None)
    return float(entry.available_balance) if entry else 0.0
