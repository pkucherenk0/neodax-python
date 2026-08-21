"""Landing page + wallet-connect handshake. Ported from e2e/lib/actions.ts's
openHomePage/waitForWalletConnected."""
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
        """The app auto-connects on its own once a wallet is discoverable -- no Connect-button
        click needed (confirmed live). Two signals accepted, not just one -- the top-nav
        'Deposit' link, or the welcome modal's own 'Connected as 0x...' text, whichever renders
        first (the modal can appear and cover the Deposit link before it would otherwise be
        visible)."""
        connected = self.page.get_by_role("link", name="Deposit").or_(
            self.page.get_by_text(re.compile("Connected as"))
        )
        connected.first.wait_for(state="visible", timeout=timeout_ms)
