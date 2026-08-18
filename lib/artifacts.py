"""save generated account creds + order/trade ids to the current run's folder (CONVENTIONS §12).

results/runs/<runId>/artifacts/worker-<pid>.jsonl. one file per
worker process. throwaway UAT wallets only — private key saved on purpose (§8 exception).
framework-agnostic. best-effort: never fail a test.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from lib.run_context import run_dir

_current_test: str | None = None  # set per test by the conftest auto-fixture
_traded_orders: set[str] = set()  # dedupe trade records across fill polls
_file_path: Path | None = None  # resolved lazily on first write


def set_current_test(name: str | None) -> None:
    """which test is running now. lib order/trade records stamp this."""
    global _current_test
    _current_test = name


def _file() -> Path:
    global _file_path
    if _file_path is None:
        d = Path(run_dir()) / "artifacts"
        d.mkdir(parents=True, exist_ok=True)
        _file_path = d / f"worker-{os.getpid()}.jsonl"
    return _file_path


def _write(rec: dict) -> None:
    try:
        stamped = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "test": _current_test, **rec}
        with _file().open("a") as fh:
            fh.write(json.dumps(stamped) + "\n")
    except OSError:
        pass  # best-effort. drop on any fs error.


def record_account(*, role: str, address: str, private_key: str, jwt: str | None = None, app_session_id: str | None = None) -> None:
    """save a minted wallet. call at fixture creation."""
    _write({"type": "account", "role": role, "address": address, "privateKey": private_key, "jwt": jwt, "appSessionId": app_session_id})


def record_order(*, venue: str, market: str, order_uuid: str, side: str | None = None, direction: str | None = None,
                 order_type: str | None = None, amount: str | None = None, price: str | None = None,
                 app_session_id: str | None = None) -> None:
    """save a placed order. call after order accepted (has uuid)."""
    _write({"type": "order", "venue": venue, "market": market, "orderUuid": order_uuid, "side": side,
            "direction": direction, "orderType": order_type, "amount": amount, "price": price,
            "appSessionId": app_session_id})


def record_trade(*, venue: str, market: str, order_uuid: str, trade_ids: list[str], fills: int,
                 app_session_id: str | None = None) -> None:
    """save fills for an order. dedupe: once per order (fill readers get polled many times)."""
    if fills <= 0:
        return
    key = f"{venue}:{order_uuid}"
    if key in _traded_orders:
        return
    _traded_orders.add(key)
    _write({"type": "trade", "venue": venue, "market": market, "orderUuid": order_uuid,
            "tradeIds": trade_ids, "fills": fills, "appSessionId": app_session_id})
