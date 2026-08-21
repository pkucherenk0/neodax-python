"""phase 2 validation, not real FE flow. proves pytest plumbing end-to-end: conftest's
session-scoped arrange (real throwaway-UAT mint+fund), arrangement fixture reading it back,
wallet_page installing mock wallet -- all through fixture chain, not a standalone script.
"""
from lib.arrangement import Arrangement
from playwright.sync_api import Page

DISCOVER_AND_CALL_JS = """
async (request) => {
  const found = await new Promise((resolve) => {
    window.addEventListener("eip6963:announceProvider", (e) => resolve(e.detail.provider));
    window.dispatchEvent(new Event("eip6963:requestProvider"));
  });
  return await found.request(request);
}
"""


def test_mock_wallet_reports_the_minted_subject_address(wallet_page: Page, arrangement: Arrangement) -> None:
    # arrange -- wallet_page already has the subject's mnemonic installed (see conftest.py).
    wallet_page.goto("data:text/html,<html><body>plumbing check</body></html>")

    # act -- a "dapp" discovers the wallet exactly like this app's real wagmi/AppKit would.
    accounts = wallet_page.evaluate(DISCOVER_AND_CALL_JS, {"method": "eth_requestAccounts"})

    # assert -- the address matches what tools/arrange_metamask_e2e.py actually minted.
    assert accounts == [arrangement.subject.address]
