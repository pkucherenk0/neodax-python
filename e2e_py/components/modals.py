"""Onboarding modals -- ported from e2e/lib/actions.ts's dismiss*/clickRobustToModalRace. See
../README.md "Known issues" (once ported) for the why behind each workaround.

Uses Locator.wait_for(), not is_visible(timeout=...) -- the latter is a one-shot snapshot check
in Playwright, not a real poll, and these modals are confirmed to render a beat after load/
click (see the original e2e/README.md), so a real bounded wait is needed to tell "absent" from
"not rendered yet".
"""
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
    """Best-effort: swallow the timeout, same as the original's `.catch(() => {})`."""
    try:
        locator.wait_for(state="hidden", timeout=timeout)
    except PlaywrightTimeoutError:
        pass


class WelcomeModal:
    """First-time onboarding modal, own CTA 'Start trading' not 'Got it'. Best-effort, no-op
    if absent."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self.start_trading_button = page.get_by_role("button", name="Start trading")

    def dismiss_if_present(self) -> None:
        if _wait_visible(self.start_trading_button, 5000):
            take_screenshot(self.page, "01a-welcome-modal-present")
            self.start_trading_button.click()
            _wait_visible_gone(self.start_trading_button, 5000)  # best-effort, no-op on timeout


class WhatsNewModal:
    """Release modal, can reappear at more than one call site -- caller names the screenshot."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self.got_it_button = page.get_by_role("button", name="Got it")

    def dismiss_if_present(self, screenshot_label: str) -> None:
        if _wait_visible(self.got_it_button, 5000):
            take_screenshot(self.page, f"{screenshot_label}-whats-new-modal-present")
            self.got_it_button.click()
            _wait_visible_gone(self.got_it_button, 5000)  # best-effort, no-op on timeout


def click_robust_to_modal_race(page: Page, target: Locator, timeout: float = 8000) -> None:
    """A modal can render in the gap between a dismiss check and this click -- fixed a 10min
    CI hang in the original. Try the click, dismiss both modals if that's what blocked it,
    retry once."""
    try:
        target.click(timeout=timeout)
    except PlaywrightTimeoutError:
        WelcomeModal(page).dismiss_if_present()
        WhatsNewModal(page).dismiss_if_present("race-retry")
        target.click(timeout=timeout)
