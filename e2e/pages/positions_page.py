"""positions tab. ported from actions.ts assertPositionVisibleInUi."""
from __future__ import annotations

import time

from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError

from lib.screenshots import take_screenshot


class PositionsPage:
    def __init__(self, page: Page) -> None:
        self.page = page

    def wait_until_visible(self, market_base: str, timeout_ms: float = 45_000) -> Locator:
        """UI trails fill by a beat, may not live-refresh -- bounded reload-retry loop (real
        action+wait, not plain assertion, lives here not as bare locator). reload re-triggers
        wagmi auto-reconnect (~1s x10), give re-hydration real headroom.

        returns locator once visible -- test still does final assert, same convention as rest
        of this port."""
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
