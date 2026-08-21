"""e2e_py fixtures -- mints/funds fresh throwaway UAT accounts before the run (via the repo
root's tools/arrange_metamask_e2e.py --out, using the ROOT .venv so eth_account/lib/configs
stay in one place, not duplicated into this project's own venv), then hands specs an
already-wallet-connected page. Mirrors the old e2e/global-setup.ts + lib/wallet.ts's roles.
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
    """Fresh accounts every session -- access_token TTL is 60s, never reuse a stale copy."""
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
    """`page` (from pytest-playwright) with the subject's mock wallet installed -- call
    page.goto() only after this fixture runs (add_init_script only applies to the NEXT
    navigation)."""
    install_wallet_for(page, arrangement.subject.mnemonic)
    return page
