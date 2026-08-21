"""perp order form. ported from actions.ts placeRestingPerpLimitBuy."""
from __future__ import annotations

from playwright.sync_api import Page, expect

from lib.screenshots import take_screenshot


class PerpOrderPage:
    def __init__(self, page: Page) -> None:
        self.page = page

    def open(self, fe_base: str, market: str) -> None:
        self.page.goto(f"{fe_base}/perps/{market.lower()}")
        limit_tab = self.page.get_by_text("Limit", exact=True).first
        limit_tab.wait_for(state="visible", timeout=15_000)
        limit_tab.click()

    def place_resting_limit_buy(self, price: str, size: str) -> None:
        """nth(0)/nth(1) assumes fixed field order (price, size) -- unverified, no stable
        selector, FE reordered before. log + screenshot field set every run so a silent
        reorder is diagnosable, not just a rerun."""
        inputs = self.page.locator("input[type=text]")
        n = inputs.count()
        print(f"--- ORDER FORM: {n} input[type=text] elements ---")
        for i in range(n):
            el = inputs.nth(i)
            name = el.get_attribute("name")
            placeholder = el.get_attribute("placeholder")
            value = el.input_value()
            aria_label = el.get_attribute("aria-label")
            print(f"  [{i}] name={name} placeholder={placeholder} value={value!r} aria-label={aria_label}")
        print("--- END ---")
        take_screenshot(self.page, "04-order-form-before-fill")

        inputs.nth(0).fill(price)
        inputs.nth(1).fill(size)
        take_screenshot(self.page, "04-order-form-filled")

        open_long_button = self.page.get_by_role("button", name="Open Long")
        open_long_button.click()
        # form usable again (not just visible) -> round-trip done. expect() as bounded wait
        # here, not a test assertion -- see README convention.
        expect(open_long_button).to_be_enabled(timeout=10_000)
        take_screenshot(self.page, "04b-after-open-long")
