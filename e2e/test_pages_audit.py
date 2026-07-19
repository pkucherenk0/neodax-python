"""page-object testid-contract audit (FE map coverage check — see TEST_STRATEGY.md §4b).
port of e2e/pages-audit.e2e.ts.

for each registered screen: confirm loads (authed via session injection), then REPORT which
data-testid contract selectors resolve vs still missing on FE. report (not assert) keeps
green while FE team implements contract. forcing function makes gap visible. once testids
ship, tighten `missing` into hard assert.

needs UAT FE reachable + browser: pytest -m e2e e2e/
"""
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect

from e2e.pages import SCREENS, audit_screen_testids
from lib.report import annotate


@pytest.mark.e2e
@pytest.mark.parametrize("screen", SCREENS, ids=[s.name for s in SCREENS])
class TestFePageObjectTestidContract:
    def test_screen_loads_and_testid_contract_status_reported(self, page, screen):
        # act — load screen (authed). URL match tolerates 'as-needed' locale prefix.
        page.goto(screen.path)

        # assert — screen actually loaded. robust selectors NOT depending on pending
        # testids: URL and real structural element.
        assert screen.path in urlparse(page.url).path
        expect(page.locator("main, body").first).to_be_visible()

        # report — contract status as note (present vs missing testids).
        present, missing = audit_screen_testids(page, screen.testids)
        annotate(f"testid-contract:{screen.name}: present=[{', '.join(present)}] missing=[{', '.join(missing)}]")
