"""e2e_py fixtures. mints/funds fresh throwaway UAT accounts before run (via root's
tools/arrange_metamask_e2e.py --out, root .venv -- keeps eth_account/lib/configs in one
place). hands specs an already-wallet-connected page. mirrors old global-setup.ts + wallet.ts.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from dotenv import load_dotenv
from playwright.sync_api import Page

from lib.arrangement import Arrangement, ARRANGEMENT_PATH, load_arrangement
from lib.wallet import install_wallet_for

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")  # NIMBUS_FE_BASE etc., same root .env every other suite uses


@pytest.fixture(scope="session", autouse=True)
def _arrange() -> None:
    """fresh accounts every session. access_token ttl 60s, never reuse stale copy."""
    root_python = REPO_ROOT / ".venv" / "bin" / "python3"
    subprocess.run(
        [str(root_python), "tools/arrange_metamask_e2e.py", "--out", str(ARRANGEMENT_PATH)],
        cwd=REPO_ROOT, check=True,
    )


@pytest.fixture(scope="session")
def arrangement(_arrange: None) -> Arrangement:
    return load_arrangement()


@pytest.fixture
def fe_base() -> str:
    base = os.environ.get("NIMBUS_FE_BASE")
    if not base:
        raise RuntimeError("NIMBUS_FE_BASE is not set -- copy .env.example to .env and fill it in.")
    return base.strip().strip('"').strip("'")  # stray quote/whitespace, see old e2e/README.md


@pytest.fixture
def wallet_page(page: Page, arrangement: Arrangement) -> Page:
    """pytest-playwright's page + subject's mock wallet installed. goto() only after this --
    add_init_script only applies to next navigation."""
    install_wallet_for(page, arrangement.subject.mnemonic)
    return page
