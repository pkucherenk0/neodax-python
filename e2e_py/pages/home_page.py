"""landing page + wallet-connect handshake. ported from actions.ts openHomePage/
waitForWalletConnected."""
from __future__ import annotations

import re

from playwright.sync_api import Page

from lib.screenshots import take_screenshot


class HomePage:
    def __init__(self, page: Page) -> None:
        self.page = page

    def open(self, fe_base: str) -> None:
        self.page.goto(fe_base)
        take_screenshot(self.page, "00-home-before-connect")

    def wait_for_wallet_connected(self, timeout_ms: float = 20_000) -> None:
        """app auto-connects once wallet discoverable, no click needed. accept either signal --
        'Deposit' link or modal's 'Connected as 0x...' text, whichever renders first (modal can
        cover Deposit link)."""
        connected = self.page.get_by_role("link", name="Deposit").or_(
            self.page.get_by_text(re.compile("Connected as"))
        )
        connected.first.wait_for(state="visible", timeout=timeout_ms)
