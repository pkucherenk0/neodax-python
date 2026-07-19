"""screen registry for POM audit — FE analogue of config/api-endpoints.json.
port of e2e/pages/index.ts.

each screen declares route + data-testid contract. audit test iterates this list to report
screen x testid coverage. add page object here as each screen mapped.
"""
from __future__ import annotations

from dataclasses import dataclass

from playwright.sync_api import Page

from e2e.pages.fee_tiers_page import FEE_TIERS_TESTIDS, FeeTiersPage

__all__ = ["FeeTiersPage", "FEE_TIERS_TESTIDS", "SCREENS", "ScreenSpec", "audit_screen_testids"]


@dataclass(frozen=True)
class ScreenSpec:
    name: str
    path: str
    testids: tuple[str, ...]


SCREENS: tuple[ScreenSpec, ...] = (
    ScreenSpec(name="Fee Tiers", path=FeeTiersPage.path, testids=tuple(FEE_TIERS_TESTIDS.values())),
)


def audit_screen_testids(page: Page, testids: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """report which of screen contract testids resolve in loaded page. lives here (not in the
    test) so test body free of loops/conditionals (CONVENTIONS §10)."""
    present: list[str] = []
    missing: list[str] = []
    for tid in testids:
        (present if page.get_by_test_id(tid).count() > 0 else missing).append(tid)
    return present, missing
