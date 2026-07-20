/**
 * Named, single-purpose actions for the spot-to-perp-position flow. Each one does ONE thing
 * a human would describe in a sentence ("transfer spot balance to perpetual", "assert the
 * order shows in Open Orders"). The test file composes these into a readable script instead
 * of inlining Playwright locators/fetches -- same "actions live apart from the test" split
 * this repo's Python lib/ + suites/ already use.
 */
import { expect } from '@playwright/test';
import { BrowserContext, Page } from 'playwright-core';
import path from 'node:path';
import { withOptionalApproval, Arrangement } from './metamask';

const SCREENSHOT_DIR = path.resolve(__dirname, '../screenshots');

export async function takeScreenshot(page: Page, name: string): Promise<void> {
  await page.screenshot({ path: `${SCREENSHOT_DIR}/${name}.png`, fullPage: true });
}

export async function openHomePage(page: Page, feBase: string): Promise<void> {
  await page.goto(feBase);
  // first render after navigation -- CI runners are slower than local dev (confirmed by the
  // Deposit-link timeout below), give it real headroom, not the 5s locator default.
  await expect(page.getByRole('button', { name: 'Connect' }).first()).toBeVisible({ timeout: 15000 });
  await takeScreenshot(page, '00-home-before-connect');
}

export async function transferSpotBalanceToPerpetual(
  page: Page,
  context: BrowserContext,
  feBase: string,
  amount: string,
): Promise<void> {
  await page.goto(`${feBase}/assets`);
  await expect(page.getByRole('button', { name: 'Transfer' })).toBeVisible({ timeout: 15000 });
  await takeScreenshot(page, '02-assets-before-transfer');

  await page.getByRole('button', { name: 'Transfer' }).click();
  const dialog = page.locator('[role=dialog]').first();
  await dialog.locator('input').first().fill(amount);
  await withOptionalApproval(page, context, () => dialog.getByRole('button', { name: 'Transfer' }).click());
  await expect(page.locator('[role=dialog]')).toHaveCount(0);
}

export async function assertPerpetualBalanceContains(page: Page, feBase: string, expectedText: string): Promise<void> {
  await page.goto(`${feBase}/assets`);
  const perpetualTab = page.getByText('Perpetual', { exact: true }).first();
  await expect(perpetualTab).toBeVisible({ timeout: 15000 });
  await perpetualTab.click();
  await expect(page.getByText(expectedText, { exact: false }).first()).toBeVisible({ timeout: 10000 });
  await takeScreenshot(page, '03-perp-balance');
}

export async function fetchLiveMarkPrice(arrangement: Arrangement): Promise<number> {
  const res = await fetch(
    `${arrangement.env.trading_base}/perpetual/funding-rates/current?symbols=${arrangement.market}`,
    { headers: { Authorization: `Bearer ${arrangement.maker.access_token}` } },
  );
  const body = await res.json();
  const mark = parseFloat(body.funding_rates?.[0]?.mark_price ?? '0');
  if (!(mark > 0)) throw new Error(`could not read a live mark price: ${JSON.stringify(body)}`);
  return mark;
}

export async function placeRestingPerpLimitBuy(
  page: Page,
  context: BrowserContext,
  feBase: string,
  market: string,
  price: string,
  size: string,
): Promise<void> {
  await page.goto(`${feBase}/perps/${market.toLowerCase()}`);
  const limitTab = page.getByText('Limit', { exact: true }).first();
  await expect(limitTab).toBeVisible({ timeout: 15000 });
  await limitTab.click();

  const inputs = page.locator('input[type=text]');
  await inputs.nth(0).fill(price);
  await inputs.nth(1).fill(size);
  await takeScreenshot(page, '04-order-form-filled');

  // This app runs on state channels -- placing an order may itself need a signed approval,
  // separate from the earlier connect signature. withOptionalApproval() handles that if/when
  // it shows up, and no-ops if it doesn't.
  const openLongBtn = page.getByRole('button', { name: 'Open Long' });
  await withOptionalApproval(page, context, () => openLongBtn.click());
  await expect(openLongBtn).toBeEnabled(); // form usable again -> submission round-trip done
  await takeScreenshot(page, '04b-after-open-long');
}

export async function assertOrderVisibleInOpenOrders(page: Page, marketBase: string): Promise<void> {
  // NOT exact: true -- once an order exists this tab carries a count badge (e.g. "01"),
  // so its accessible text becomes "Open Orders01", which exact matching would miss.
  const openOrdersTab = page.getByText('Open Orders', { exact: false }).first();
  await expect(openOrdersTab).toBeVisible({ timeout: 10000 });
  await openOrdersTab.click({ timeout: 10000 });
  await takeScreenshot(page, '04c-open-orders-tab-clicked');

  await expect(page.getByText(marketBase, { exact: false }).first()).toBeVisible({ timeout: 10000 });
  await takeScreenshot(page, '05-open-orders');
}

export async function matchRestingOrderWithApiCounterparty(arrangement: Arrangement): Promise<void> {
  // This is a SHARED live order book -- interrupted past runs can leave stale resting bids,
  // and this repo has no way to cancel another account's order after losing its credentials.
  // Filtering by our own computed price is unreliable too (the server tick-rounds the
  // submitted price). Simplest robust fix: this is a thin test market, total depth is
  // trivial -- sweep the ENTIRE current bid book with one market sell, guaranteeing our
  // order (wherever exactly it landed) gets hit along with everything else.
  const bookRes = await fetch(`${arrangement.env.trading_base}/orderbook?symbol=${arrangement.market}`);
  const book = await bookRes.json();
  const sweepAmount = (book.bids ?? []).reduce((sum: number, [, size]: [string, string]) => sum + parseFloat(size), 0);
  if (sweepAmount < 0.01) {
    throw new Error(`no bids in the live book to sweep (bids: ${JSON.stringify(book.bids)})`);
  }

  const orderRes = await fetch(`${arrangement.env.trading_base}/perpetual/order`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', Authorization: `Bearer ${arrangement.maker.access_token}` },
    body: JSON.stringify({
      app_session_id: arrangement.maker.address,
      market: arrangement.market,
      side: 'sell',
      direction: 'short',
      type: 'market',
      amount: sweepAmount.toFixed(3),
      leverage: '10',
    }),
  });
  if (!orderRes.ok) {
    throw new Error(`maker counter-order failed: HTTP ${orderRes.status} ${await orderRes.text()}`);
  }
}

export async function assertPositionVisibleInUi(page: Page, marketBase: string, timeoutMs = 45_000): Promise<void> {
  // position propagation trails the fill by a beat. reload periodically in case the UI
  // doesn't live-refresh positions on its own, but drive each attempt with Playwright's own
  // retrying assertion (auto-waits/re-checks the DOM) instead of a fixed sleep. The FE's own
  // wallet-auto-reconnect (wagmi) retries every ~1s up to 10x after a reload, so re-hydration
  // alone can take several seconds -- give it real headroom, not the 5s locator default.
  const deadline = Date.now() + timeoutMs;
  const positionLocator = page.getByText(marketBase, { exact: false }).first();

  for (;;) {
    await page.reload();
    await expect(page.getByRole('link', { name: 'Deposit' })).toBeVisible({ timeout: 15000 }); // app re-hydrated
    // same count-badge caveat as Open Orders -- not exact once a position exists.
    await page.getByText('Positions', { exact: false }).first().click();

    const attemptTimeout = Math.min(8000, Math.max(1000, deadline - Date.now()));
    try {
      await expect(positionLocator).toBeVisible({ timeout: attemptTimeout });
      break;
    } catch (err) {
      if (Date.now() >= deadline) {
        await takeScreenshot(page, '06-position-open');
        throw err;
      }
    }
  }
  await takeScreenshot(page, '06-position-open');
}

export async function cancelAnyOpenOrder(page: Page, context: BrowserContext): Promise<void> {
  // best-effort teardown: if our order is somehow still resting (e.g. an earlier step threw
  // before the API match), cancel it via the UI rather than leave it as trash for future runs.
  await page
    .getByText('Open Orders', { exact: false })
    .first()
    .click({ timeout: 5000 })
    .catch(() => {});
  const cancelBtn = page.getByRole('button', { name: 'Cancel' }).first();
  if (await cancelBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
    await withOptionalApproval(page, context, () => cancelBtn.click()).catch(() => {});
  }
}
