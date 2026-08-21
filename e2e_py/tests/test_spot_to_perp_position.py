"""visual e2e: spot -> perp transfer, perp limit order via real UI, matched by raw-API
counterparty order, position visually confirmed. ported from spot-to-perp-position.spec.ts.
"""
import pytest
from playwright.sync_api import Page, Playwright, expect

from components.modals import WelcomeModal, WhatsNewModal
from lib.api import fetch_live_mark_price, match_resting_order_with_api_counterparty
from lib.arrangement import Arrangement
from lib.screenshots import take_screenshot
from pages.assets_page import AssetsPage
from pages.home_page import HomePage
from pages.open_orders_page import OpenOrdersPage
from pages.perp_order_page import PerpOrderPage
from pages.positions_page import PositionsPage


@pytest.mark.trades
def test_spot_to_perp_transfer_ui_limit_order_matched_by_api_counterparty_position_visible(
    wallet_page: Page, arrangement: Arrangement, fe_base: str, playwright: Playwright,
) -> None:
    market_base = arrangement.market.removesuffix("-PERP")  # table/row displays drop "-PERP"

    # arrange -- connect (wallet already installed on wallet_page fixture, before first nav).
    home = HomePage(wallet_page)
    home.open(fe_base)
    home.wait_for_wallet_connected()
    # two modals in a row for a fresh account -- dismiss both.
    WelcomeModal(wallet_page).dismiss_if_present()
    WhatsNewModal(wallet_page).dismiss_if_present("01b")
    take_screenshot(wallet_page, "01-connected")

    # act -- transfer spot -> perp, confirm the balance actually moved.
    assets = AssetsPage(wallet_page)
    assets.open(fe_base)
    assets.transfer_spot_to_perpetual("20000")
    expect(assets.perpetual_balance_locator(fe_base, "20,000")).to_be_visible()

    # act -- rest a limit buy 10% below mark (won't fill on its own).
    mark = fetch_live_mark_price(playwright, arrangement)
    rest_price = f"{mark * 0.9:.2f}"
    order_page = PerpOrderPage(wallet_page)
    order_page.open(fe_base, arrangement.market)
    order_page.place_resting_limit_buy(rest_price, "0.01")

    # resting order now exists on a shared live book -- cancel on any throw, don't leave trash.
    open_orders = OpenOrdersPage(wallet_page)
    try:
        expect(open_orders.order_locator(market_base)).to_be_visible()

        match_resting_order_with_api_counterparty(playwright, arrangement)

        positions = PositionsPage(wallet_page)
        expect(positions.wait_until_visible(market_base)).to_be_visible()
    finally:
        open_orders.cancel_any_open_order()
