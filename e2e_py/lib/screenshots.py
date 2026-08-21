"""named checkpoint screenshots, happy path. separate from pytest-playwright's own on-failure
screenshot (pytest.ini --screenshot only-on-failure) -- that alone misses a failure inside a
page-object method, not just the test.
"""
from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Page

SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "screenshots"


def take_screenshot(page: Page, name: str) -> None:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREENSHOT_DIR / f"{name}.png"), full_page=True)
