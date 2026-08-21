"""Positions tab. Ported from e2e/lib/actions.ts's assertPositionVisibleInUi."""
from __future__ import annotations

import time

from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError

from lib.screenshots import take_screenshot


class PositionsPage:
    def __init__(self, page: Page) -> None:
        self.page = page

    def wait_until_visible(self, market_base: str, timeout_ms: float = 45_000) -> Locator:
        """Fill trails the fill by a beat, UI may not live-refresh -- bounded reload-retry loop
        (a real ACTION with a wait, not a plain assertion, hence it lives here rather than as a
        bare locator). Reload also re-triggers wagmi's wallet-auto-reconnect (retries ~1s up to
        10x), so give re-hydration real headroom, not the default locator timeout.

        Returns the locator once visible; the TEST still does the final explicit assert for
        clarity, same convention as everywhere else in this port."""
        deadline = time.monotonic() + timeout_ms / 1000
        position_locator = self.page.get_by_text(market_base, exact=False).first

        while True:
            self.page.reload()
            self.page.get_by_role("link", name="Deposit").wait_for(state="visible", timeout=15_000)  # re-hydrated
            # same count-badge caveat as Open Orders -- not exact once a position exists.
            self.page.get_by_text("Positions", exact=False).first.click()

            attempt_timeout = min(8000, max(1000, (deadline - time.monotonic()) * 1000))
            try:
                position_locator.wait_for(state="visible", timeout=attempt_timeout)
                break
            except PlaywrightTimeoutError:
                if time.monotonic() >= deadline:
                    take_screenshot(self.page, "06-position-open")
                    raise

        take_screenshot(self.page, "06-position-open")
        return position_locator
