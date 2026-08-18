/**
 * Named, single-purpose actions for the spot-to-perp-position flow. Each one does ONE thing
 * a human would describe in a sentence ("transfer spot balance to perpetual", "assert the
 * order shows in Open Orders"). The test file composes these into a readable script instead
 * of inlining Playwright locators/fetches -- same "actions live apart from the test" split
 * this repo's Python lib/ + suites/ already use.
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
  // first render after navigation -- CI runners are slower than local dev (confirmed by the
  // Deposit-link timeout below), give it real headroom, not the 5s locator default.
  await expect(page.getByRole('button', { name: 'Connect' }).first()).toBeVisible({ timeout: 15000 });
  await takeScreenshot(page, '00-home-before-connect');
}

export async function dismissWelcomeModalIfPresent(page: Page): Promise<void> {
  // brand-new accounts get a first-time "Welcome to Yellow Pro" onboarding modal -- separate
  // from, and appearing BEFORE, the "What's new" release modal below. It shows up with a
  // delay (confirmed: not there immediately after connect, present ~3s later), and has no
  // "Got it" button -- its own CTA is "Start trading". Best-effort: no-op if not showing.
  // known-issue spot -- screenshot when actually caught, not just at the surrounding step's
  // usual checkpoints, so a report shows what the modal looked like, not just whatever page
  // state came after it stopped blocking things.
  const startTrading = page.getByRole('button', { name: 'Start trading' });
  if (await startTrading.isVisible({ timeout: 5000 }).catch(() => false)) {
    await takeScreenshot(page, '01a-welcome-modal-present');
    await startTrading.click();
    await expect(startTrading).not.toBeVisible({ timeout: 5000 }).catch(() => {});
  }
}

export async function dismissWhatsNewModalIfPresent(page: Page, stepLabel: string): Promise<void> {
  // every FRESH wallet (this test mints a new one each run, see tools/arrange_metamask_e2e.py)
  // gets a one-time "What's new" release modal on first authenticated render. It sits on top
  // of the page and blocks clicks underneath it -- e.g. the Transfer button on /assets. Also
  // confirmed re-appearing right after clicking Transfer (a second, later call site) -- NOT
  // just a one-time thing at connect. `stepLabel` disambiguates the screenshot between call
  // sites; best-effort, no-op if it's not showing.
  const gotIt = page.getByRole('button', { name: 'Got it' });
  if (await gotIt.isVisible({ timeout: 5000 }).catch(() => false)) {
    await takeScreenshot(page, `${stepLabel}-whats-new-modal-present`);
    await gotIt.click();
    await expect(gotIt).not.toBeVisible({ timeout: 5000 }).catch(() => {});
  }
}

export async function clickRobustToModalRace(page: Page, target: Locator, timeout = 8000): Promise<void> {
  // known-issue spot, confirmed live via a CI trace: "What's new" (and "Welcome") can appear
  // with a DELAY after page load -- a one-shot "check for it, then click" has a real gap where
  // the modal renders AFTER the check finds nothing and BEFORE the click lands, blocking it.
  // Caught directly: dismissWhatsNewModalIfPresent ran immediately after page.goto('/assets')
  // and found nothing, then the Transfer click hung for the full 10-minute test timeout on a
  // "What's new" backdrop that rendered in that gap. A plain retry on the click alone can't
  // fix this -- Playwright's own auto-retry waits for the element to become clickable, it has
  // no notion of dismissing an unrelated overlay for us. Try the click with a short bounded
  // timeout; if that's what's actually blocking it, dismiss both known modals and retry once
  // -- bounded, so a genuine failure surfaces in seconds, not after riding the whole test
  // timeout the way this one did.
  try {
    await target.click({ timeout });
  } catch (err) {
    await dismissWelcomeModalIfPresent(page);
    await dismissWhatsNewModalIfPresent(page, 'race-retry');
    await target.click({ timeout });
  }
}

export async function refreshTransferFromBalanceViaDirectionToggle(dialog: Locator): Promise<void> {
  // WORKAROUND for a known UAT FE bug: the Transfer dialog's "Transfer from" balance can be
  // stale on open -- the Transfer button silently stays rejected/disabled even though the
  // account genuinely has the funds (confirmed independently: same balance visible in the UI,
  // correct via GET /spot/account, and the identical transfer succeeds instantly via POST
  // /accounts/transfer -- this is FE-only, not a real balance issue). Toggling the "Transfer
  // from" selector away and back forces a refetch that picks up the real balance.
  //
  // the picker shows as a NESTED [role=dialog] in the ARIA SNAPSHOT, but that reflects the
  // accessibility tree, not necessarily DOM containment -- it may render via a portal outside
  // `dialog`'s actual DOM subtree. Scope it at the PAGE level instead, disambiguated by
  // content (no "What's new" / "Transfer funds" text) so it resolves regardless of where in
  // the DOM it actually lives.
  // explicit timeouts throughout -- a bad locator here must fail fast, not silently ride the
  // whole 600s test timeout.
  const timeout = 10_000;
  const page = dialog.page();
  const picker = page.locator('[role=dialog]')
    .filter({ hasNotText: "What's new" })
    .filter({ hasNotText: 'Transfer funds' });

  // known-issue spot -- this whole sequence previously had ZERO screenshot coverage (jumped
  // straight from '02-assets-before-transfer' to '03-perp-balance'), so any failure here left
  // nothing useful to inspect afterward. Screenshot after every click.
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
  // the "What's new" modal isn't just a one-time thing at connect -- confirmed re-appearing
  // right after THIS click too (unrelated re-trigger, not a leftover). Dismiss it again in
  // case it raced this click, and exclude it from the dialog match regardless of timing so a
  // future re-appearance can never get mistaken for the real Transfer dialog again.
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

  // known-issue spot -- nth(0)/nth(1) below assumes a FIXED field order (price then size).
  // The FE has changed its modals/dialogs more than once this session; if it ever reorders
  // this form too, we'd silently fill the wrong fields with plausible-looking numbers instead
  // of failing loudly. Log + screenshot the actual field set every run so a "bad input"
  // symptom is diagnosable from the report alone, not just a rerun.
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
