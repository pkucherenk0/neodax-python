"""Perp order form. Ported from e2e/lib/actions.ts's placeRestingPerpLimitBuy."""
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
        """nth(0)/nth(1) below assumes fixed field order (price, size) -- unverified, no stable
        selector. The FE's modals have reordered before; a silent reorder would fill plausible-
        looking wrong values instead of failing loudly, so the actual field set is logged +
        screenshotted every run (diagnosable from the report, not just a rerun)."""
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
        # form usable again (not just visible) -> submission round-trip done. expect() used here
        # purely as a bounded wait primitive, not a test assertion -- see ../README.md convention.
        expect(open_long_button).to_be_enabled(timeout=10_000)
        take_screenshot(self.page, "04b-after-open-long")
