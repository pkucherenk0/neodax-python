"""open orders tab. ported from actions.ts assertOrderVisibleInOpenOrders/cancelAnyOpenOrder."""
from __future__ import annotations

from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError

from lib.screenshots import take_screenshot


class OpenOrdersPage:
    def __init__(self, page: Page) -> None:
        self.page = page

    def order_locator(self, market_base: str) -> Locator:
        """opens Open Orders tab, returns locator for `market_base` -- test asserts on it.
        not exact=True: count badge (e.g. "01") appends once order exists."""
        open_orders_tab = self.page.get_by_text("Open Orders", exact=False).first
        open_orders_tab.wait_for(state="visible", timeout=10_000)
        open_orders_tab.click(timeout=10_000)
        take_screenshot(self.page, "04c-open-orders-tab-clicked")
        locator = self.page.get_by_text(market_base, exact=False).first
        take_screenshot(self.page, "05-open-orders")
        return locator

    def cancel_any_open_order(self) -> None:
        """best-effort teardown: cancel via UI if still resting, don't leave trash on shared
        book. real wait not snapshot check -- wrong here means silently leaving an order."""
        try:
            self.page.get_by_text("Open Orders", exact=False).first.click(timeout=5000)
        except PlaywrightTimeoutError:
            return
        cancel_button = self.page.get_by_role("button", name="Cancel").first
        try:
            cancel_button.wait_for(state="visible", timeout=3000)
        except PlaywrightTimeoutError:
            return
        try:
            cancel_button.click()
        except PlaywrightTimeoutError:
            pass
