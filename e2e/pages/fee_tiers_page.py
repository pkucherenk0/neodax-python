"""page object — Fee Tiers screen. port of e2e/pages/FeeTiersPage.ts.

(yellow-neodax-client route /fee-tiers. i18n localePrefix 'as-needed' so default locale NOT
prefixed). shows account CURRENT effective tier + spot/perp maker/taker rates — UI mirror of
API /account/fee-tier-effective, so natural first FE-integrated assert target.

data-testid CONTRACT — FE does NOT expose these yet. cross-repo contract FE team should
implement. key POM on stable testids (not CSS/text, which churn + i18n-dependent) -> durable.
audit test reports which resolve, so gap visible + tracked. flip audit from report->assert
once FE ships them.
"""
from __future__ import annotations

from playwright.sync_api import Locator, Page

FEE_TIERS_TESTIDS: dict[str, str] = {
    "root": "fee-tiers-page",
    "current_tier_name": "current-tier-name",
    "current_spot_taker_fee": "current-spot-taker-fee",
    "current_spot_maker_fee": "current-spot-maker-fee",
    "current_perp_taker_fee": "current-perp-taker-fee",
    "current_perp_maker_fee": "current-perp-maker-fee",
    "table": "fee-tiers-table",
}


class FeeTiersPage:
    path = "/fee-tiers"

    def __init__(self, page: Page) -> None:
        self._page = page

    def goto(self) -> None:
        self._page.goto(self.path)

    @property
    def root(self) -> Locator:
        return self._page.get_by_test_id(FEE_TIERS_TESTIDS["root"])

    @property
    def current_tier_name(self) -> Locator:
        return self._page.get_by_test_id(FEE_TIERS_TESTIDS["current_tier_name"])

    @property
    def current_spot_taker_fee(self) -> Locator:
        return self._page.get_by_test_id(FEE_TIERS_TESTIDS["current_spot_taker_fee"])

    @property
    def current_perp_taker_fee(self) -> Locator:
        return self._page.get_by_test_id(FEE_TIERS_TESTIDS["current_perp_taker_fee"])

    @property
    def table(self) -> Locator:
        return self._page.get_by_test_id(FEE_TIERS_TESTIDS["table"])
