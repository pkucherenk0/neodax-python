/**
 * Named, single-purpose actions for the spot-to-perp-position flow. Each one does one thing
 * a human would describe in a sentence. See ../README.md for the known-issue workarounds below.
 */
import { expect } from '@playwright/test';
import { BrowserContext, Locator, Page } from 'playwright-core';
import path from 'node:path';
import { withOptionalApproval, Arrangement } from './metamask';

const SCREENSHOT_DIR = path.resolve(__dirname, '../screenshots');

export async function takeScreenshot(page: Page, name: string): Promise<void> {
  await page.screenshot({ path: `${SCREENSHOT_DIR}/${name}.png`, fullPage: true });
}

export async function openHomePage(page: Page, feBase: string): Promise<void> {
  await page.goto(feBase);
  // CI runners are slower than local -- give first render real headroom, not the 5s default.
  await expect(page.getByRole('button', { name: 'Connect' }).first()).toBeVisible({ timeout: 15000 });
  await takeScreenshot(page, '00-home-before-connect');
}

export async function dismissWelcomeModalIfPresent(page: Page): Promise<void> {
  // first-time onboarding modal, separate from "What's new" below. see ../README.md.
  const startTrading = page.getByRole('button', { name: 'Start trading' });
  if (await startTrading.isVisible({ timeout: 5000 }).catch(() => false)) {
    await takeScreenshot(page, '01a-welcome-modal-present');
    await startTrading.click();
    await expect(startTrading).not.toBeVisible({ timeout: 5000 }).catch(() => {});
  }
}

export async function dismissWhatsNewModalIfPresent(page: Page, stepLabel: string): Promise<void> {
  // release modal, can appear at more than one call site (not just once at connect). see ../README.md.
  // stepLabel disambiguates the screenshot between call sites.
  const gotIt = page.getByRole('button', { name: 'Got it' });
  if (await gotIt.isVisible({ timeout: 5000 }).catch(() => false)) {
    await takeScreenshot(page, `${stepLabel}-whats-new-modal-present`);
    await gotIt.click();
    await expect(gotIt).not.toBeVisible({ timeout: 5000 }).catch(() => {});
  }
}

export async function clickRobustToModalRace(page: Page, target: Locator, timeout = 8000): Promise<void> {
  // modal can render in the gap between a dismiss check and this click. see ../README.md
  // (this is what fixed a 10-minute CI hang). bounded, so a real failure surfaces in seconds.
  try {
    await target.click({ timeout });
  } catch (err) {
    await dismissWelcomeModalIfPresent(page);
    await dismissWhatsNewModalIfPresent(page, 'race-retry');
    await target.click({ timeout });
  }
}

export async function refreshTransferFromBalanceViaDirectionToggle(dialog: Locator): Promise<void> {
  // workaround for a known UAT FE bug: "Transfer from" balance can be stale on open, keeping
  // Transfer disabled despite real funds. toggling the selector away/back forces a refetch.
  // picker is a portal, not a DOM descendant of `dialog` -- scoped at page level. see ../README.md.
  const timeout = 10_000; // bad locator must fail fast, not ride the whole test timeout
  const page = dialog.page();
  const picker = page.locator('[role=dialog]')
    .filter({ hasNotText: "What's new" })
    .filter({ hasNotText: 'Transfer funds' });

  await dialog.getByRole('button', { name: 'Spot Account' }).click({ timeout });
  await takeScreenshot(page, '02c-transfer-from-picker-open');
  await picker.getByRole('button', { name: 'Perpetuals Account' }).click({ timeout }); // swap away
  await takeScreenshot(page, '02d-transfer-swapped-away');
  await dialog.getByRole('button', { name: 'Perpetuals Account' }).click({ timeout }); // reopen (now the From trigger)
  await takeScreenshot(page, '02e-transfer-from-picker-reopened');
  await picker.getByRole('button', { name: 'Spot Account' }).click({ timeout }); // swap back -> Spot -> Perpetual, refetched
  await takeScreenshot(page, '02f-transfer-swapped-back');
}

export async function transferSpotBalanceToPerpetual(
  page: Page,
  context: BrowserContext,
  feBase: string,
  amount: string,
): Promise<void> {
  await page.goto(`${feBase}/assets`);
  await dismissWhatsNewModalIfPresent(page, '02-assets');
  await expect(page.getByRole('button', { name: 'Transfer' })).toBeVisible({ timeout: 15000 });
  await takeScreenshot(page, '02-assets-before-transfer');

  await clickRobustToModalRace(page, page.getByRole('button', { name: 'Transfer' }));
  // "What's new" can re-appear right after this click too -- dismiss again, exclude from dialog match.
  await dismissWhatsNewModalIfPresent(page, '02a-post-transfer-click');
  const dialog = page.locator('[role=dialog]').filter({ hasNotText: "What's new" }).first();
  await expect(dialog).toBeVisible({ timeout: 10000 });
  await takeScreenshot(page, '02b-transfer-dialog-open');

  await refreshTransferFromBalanceViaDirectionToggle(dialog);

  await dialog.locator('input').first().fill(amount, { timeout: 10000 });
  await takeScreenshot(page, '02g-transfer-amount-filled');
  await withOptionalApproval(page, context, () => dialog.getByRole('button', { name: 'Transfer' }).click({ timeout: 10000 }));
  await takeScreenshot(page, '02h-transfer-submitted');
  await expect(page.locator('[role=dialog]')).toHaveCount(0, { timeout: 15000 });
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

  // nth(0)/nth(1) below assumes fixed field order (price, size) -- unverified. see ../README.md.
  const inputs = page.locator('input[type=text]');
  const n = await inputs.count();
  console.log(`--- ORDER FORM: ${n} input[type=text] elements ---`);
  for (let i = 0; i < n; i++) {
    const el = inputs.nth(i);
    const [name, placeholder, value, ariaLabel] = await Promise.all([
      el.getAttribute('name'),
      el.getAttribute('placeholder'),
      el.inputValue(),
      el.getAttribute('aria-label'),
    ]);
    console.log(`  [${i}] name=${name} placeholder=${placeholder} value=${JSON.stringify(value)} aria-label=${ariaLabel}`);
  }
  console.log('--- END ---');
  await takeScreenshot(page, '04-order-form-before-fill');

  await inputs.nth(0).fill(price);
  await inputs.nth(1).fill(size);
  await takeScreenshot(page, '04-order-form-filled');

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
  // shared live book, thin market -- sweep the entire bid book rather than target our own order.
  // see ../README.md.
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
  // fill trails propagation by a beat -- bounded reload retry loop. see ../README.md.
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
