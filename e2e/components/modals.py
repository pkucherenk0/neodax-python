"""onboarding modals. ported from e2e/lib/actions.ts dismiss*/clickRobustToModalRace.
wait_for(), not is_visible(timeout=) -- latter one-shot snapshot, not real poll. see README."""
from __future__ import annotations

from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError

from lib.screenshots import take_screenshot


def _wait_visible(locator: Locator, timeout: float) -> bool:
    try:
        locator.wait_for(state="visible", timeout=timeout)
        return True
    except PlaywrightTimeoutError:
        return False


def _wait_visible_gone(locator: Locator, timeout: float) -> None:
    """best-effort. swallow timeout, matches original's .catch(() => {})."""
    try:
        locator.wait_for(state="hidden", timeout=timeout)
    except PlaywrightTimeoutError:
        pass


class WelcomeModal:
    """first-time modal. own CTA 'Start trading' not 'Got it'. best-effort, no-op if absent."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self.start_trading_button = page.get_by_role("button", name="Start trading")

    def dismiss_if_present(self) -> None:
        if _wait_visible(self.start_trading_button, 5000):
            take_screenshot(self.page, "01a-welcome-modal-present")
            self.start_trading_button.click()
            _wait_visible_gone(self.start_trading_button, 5000)  # best-effort, no-op on timeout


class WhatsNewModal:
    """release modal, reappears at multiple call sites. caller names the screenshot."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self.got_it_button = page.get_by_role("button", name="Got it")

    def dismiss_if_present(self, screenshot_label: str) -> None:
        if _wait_visible(self.got_it_button, 5000):
            take_screenshot(self.page, f"{screenshot_label}-whats-new-modal-present")
            self.got_it_button.click()
            _wait_visible_gone(self.got_it_button, 5000)  # best-effort, no-op on timeout


def click_robust_to_modal_race(page: Page, target: Locator, timeout: float = 8000) -> None:
    """modal can render between dismiss check and click -- fixed 10min CI hang in original.
    try click, dismiss both modals if blocked, retry once."""
    try:
        target.click(timeout=timeout)
    except PlaywrightTimeoutError:
        WelcomeModal(page).dismiss_if_present()
        WhatsNewModal(page).dismiss_if_present("race-retry")
        target.click(timeout=timeout)
