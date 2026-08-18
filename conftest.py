"""wires the fixtures/ package into pytest + top-level CLI/run-lifecycle hooks.

specs get accounts, JWTs, and API clients from fixtures/ — never construct wallets or
request contexts inline in a spec (CONVENTIONS §4). env is chosen with --env=uat|stage.
the actual fixture/hook implementations live in fixtures/clients.py, fixtures/accounts.py,
fixtures/reporting.py — this file only wires them up and owns the two CLI/session hooks
that have to live at the true pytest root.
"""
from __future__ import annotations

import pytest
from dotenv import load_dotenv

from lib.run_context import create_run_context

load_dotenv()

pytest_plugins = (
    "fixtures.clients",
    "fixtures.accounts",
    "fixtures.reporting",
)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--env", action="store", default="uat", choices=["uat", "stage"],
                     help="target environment (uat = fresh auto-funded wallets, stage = pre-funded pool)")


def pytest_configure(config: pytest.Config) -> None:
    # mint run dir once in the controlling process; xdist workers read it back via env/pointer.
    if not hasattr(config, "workerinput"):
        run_id, run_path = create_run_context()
        print(f"\nrun id: {run_id}  ->  {run_path}")
