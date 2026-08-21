"""assets page: spot -> perp transfer dialog + perp balance display. ported from actions.ts
transferSpotBalanceToPerpetual/assertPerpetualBalanceContains/
refreshTransferFromBalanceViaDirectionToggle.
"""
from __future__ import annotations

from playwright.sync_api import Locator, Page

from components.modals import WhatsNewModal, click_robust_to_modal_race
from lib.screenshots import take_screenshot


class AssetsPage:
    def __init__(self, page: Page) -> None:
        self.page = page

    def open(self, fe_base: str) -> None:
        self.page.goto(f"{fe_base}/assets")
        WhatsNewModal(self.page).dismiss_if_present("02-assets")
        self.page.get_by_role("button", name="Transfer").wait_for(state="visible", timeout=15_000)
        take_screenshot(self.page, "02-assets-before-transfer")

    def transfer_spot_to_perpetual(self, amount: str) -> None:
        transfer_button = self.page.get_by_role("button", name="Transfer")
        click_robust_to_modal_race(self.page, transfer_button)
        # "what's new" can reappear right after this click -- dismiss again.
        WhatsNewModal(self.page).dismiss_if_present("02a-post-transfer-click")

        dialog = self.page.locator("[role=dialog]").filter(has_not_text="What's new").first
        dialog.wait_for(state="visible", timeout=10_000)
        take_screenshot(self.page, "02b-transfer-dialog-open")

        self._refresh_stale_from_balance(dialog)

        dialog.locator("input").first.fill(amount, timeout=10_000)
        take_screenshot(self.page, "02g-transfer-amount-filled")
        dialog.get_by_role("button", name="Transfer").click(timeout=10_000)
        take_screenshot(self.page, "02h-transfer-submitted")
        self.page.locator("[role=dialog]").wait_for(state="detached", timeout=15_000)

    def _refresh_stale_from_balance(self, dialog: Locator) -> None:
        """UAT FE bug: 'Transfer from' balance stale on open, keeps Transfer disabled despite
        real funds. toggle selector away/back forces refetch. picker is a portal, scope at
        page level not dialog."""
        timeout = 10_000  # bad locator fail fast, not ride whole test timeout
        picker = self.page.locator("[role=dialog]").filter(has_not_text="What's new").filter(has_not_text="Transfer funds")

        dialog.get_by_role("button", name="Spot Account").click(timeout=timeout)
        take_screenshot(self.page, "02c-transfer-from-picker-open")
        picker.get_by_role("button", name="Perpetuals Account").click(timeout=timeout)  # swap away
        take_screenshot(self.page, "02d-transfer-swapped-away")
        dialog.get_by_role("button", name="Perpetuals Account").click(timeout=timeout)  # reopen (now the From trigger)
        take_screenshot(self.page, "02e-transfer-from-picker-reopened")
        picker.get_by_role("button", name="Spot Account").click(timeout=timeout)  # swap back -> refetched
        take_screenshot(self.page, "02f-transfer-swapped-back")

    def perpetual_balance_locator(self, fe_base: str, expected_text: str) -> Locator:
        """fresh goto /assets, open Perpetual tab, return locator for `expected_text` --
        test asserts on it, this just gets there."""
        self.page.goto(f"{fe_base}/assets")
        perpetual_tab = self.page.get_by_text("Perpetual", exact=True).first
        perpetual_tab.wait_for(state="visible", timeout=15_000)
        perpetual_tab.click()
        locator = self.page.get_by_text(expected_text, exact=False).first
        take_screenshot(self.page, "03-perp-balance")
        return locator
