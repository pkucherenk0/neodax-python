"""Open Orders tab. Ported from e2e/lib/actions.ts's assertOrderVisibleInOpenOrders/
cancelAnyOpenOrder."""
from __future__ import annotations

from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError

from lib.screenshots import take_screenshot


class OpenOrdersPage:
    def __init__(self, page: Page) -> None:
        self.page = page

    def order_locator(self, market_base: str) -> Locator:
        """Opens the Open Orders tab and returns the locator for `market_base` -- the TEST
        asserts on it. Not exact=True: a count badge (e.g. "01") gets appended once an order
        exists."""
        open_orders_tab = self.page.get_by_text("Open Orders", exact=False).first
        open_orders_tab.wait_for(state="visible", timeout=10_000)
        open_orders_tab.click(timeout=10_000)
        take_screenshot(self.page, "04c-open-orders-tab-clicked")
        locator = self.page.get_by_text(market_base, exact=False).first
        take_screenshot(self.page, "05-open-orders")
        return locator

    def cancel_any_open_order(self) -> None:
        """Best-effort teardown: cancel via UI if still resting, don't leave trash for future
        runs on the shared live book. A real wait (not a snapshot is_visible check) -- being
        wrong here means silently leaving an order on a shared book."""
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
