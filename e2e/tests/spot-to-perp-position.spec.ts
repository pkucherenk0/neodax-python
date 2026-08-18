/**
 * Visual e2e: spot -> perp transfer, perp limit order via the real UI, matched by a
 * counterparty order placed via a raw API call, position visually confirmed.
 *
 * User 1 (this test, driving the real UI via MetaMask/dappwright) places a resting limit
 * order. User 2 (a separate funded account, API-only) places the crossing order. We then
 * check the resulting OPEN POSITION actually renders in User 1's UI.
 *
 * Drives a REAL MetaMask connection -- the Python-only session-injection e2e/ suite (JWT
 * seeded into localStorage) can't get past this app's wallet-connect gate: the perp order
 * form's Open Long/Short buttons stay `disabled`, and the Open Orders/Positions panels show
 * "Connect Wallet to Start" -- even though the underlying API calls succeed in the
 * background. See lib/metamask.ts for the connect-flow/popup-handling details this relies on,
 * and lib/actions.ts for what each step below actually does.
 *
 * Arrangement (funded accounts) comes from ../.arrangement.json, generated FRESH before every
 * run by global-setup.ts (never reused -- a reused account accumulates leftover orders from
 * prior runs).
 */
import { test as base } from '@playwright/test';
import { BrowserContext } from 'playwright-core';
import { Dappwright } from '@tenkeylabs/dappwright';
import fs from 'node:fs';
import path from 'node:path';
import { Arrangement, bootstrapMetaMask, connectMetaMask } from '../lib/metamask';
import {
  assertOrderVisibleInOpenOrders,
  assertPerpetualBalanceContains,
  assertPositionVisibleInUi,
  cancelAnyOpenOrder,
  dismissWelcomeModalIfPresent,
  dismissWhatsNewModalIfPresent,
  fetchLiveMarkPrice,
  matchRestingOrderWithApiCounterparty,
  openHomePage,
  placeRestingPerpLimitBuy,
  takeScreenshot,
  transferSpotBalanceToPerpetual,
} from '../lib/actions';

const ARRANGEMENT_PATH = path.resolve(__dirname, '../.arrangement.json');
// env/secret values can pick up a stray leading/trailing quote or whitespace depending on how
// they were set (e.g. `gh secret set --body "$url"` where $url still has quotes in it) --
// Chrome's Page.navigate rejects that outright ("Cannot navigate to invalid URL"), so strip
// both before use. Confirmed shape in CI: exactly one extra char, not whitespace -- a stray
// trailing quote is the leading suspect.
const FE_BASE = (process.env.NIMBUS_FE_BASE ?? 'https://uat.nimbus.example.com')
  .trim()
  .replace(/^['"`]+|['"`]+$/g, '');
const SCREENSHOT_DIR = path.resolve(__dirname, '../screenshots');

// fail loud with an actual diagnosis instead of Chrome's opaque "invalid URL". Never print the
// value itself (it may be a CI secret) -- structural facts only, safe regardless of masking.
try {
  new URL(FE_BASE);
} catch {
  throw new Error(
    `NIMBUS_FE_BASE is not a valid URL after stripping quotes/whitespace: length=${FE_BASE.length}. ` +
    'Check the env var/secret value directly.',
  );
}

function loadArrangement(): Arrangement {
  if (!fs.existsSync(ARRANGEMENT_PATH)) {
    throw new Error(`missing ${ARRANGEMENT_PATH} -- global-setup.ts should have generated it`);
  }
  return JSON.parse(fs.readFileSync(ARRANGEMENT_PATH, 'utf-8'));
}

const arrangement = loadArrangement();

const test = base.extend<{ wallet: Dappwright; walletContext: BrowserContext }>({
  walletContext: async ({}, use) => {
    const { context } = await bootstrapMetaMask(arrangement.subject.mnemonic);
    await use(context);
    await context.close();
  },
  wallet: async ({ walletContext }, use) => {
    const { getWallet } = await import('@tenkeylabs/dappwright');
    await use(await getWallet('metamask', walletContext));
  },
});

test.beforeAll(() => {
  fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
});

test('spot to perp transfer, UI limit order matched by API counterparty, position visible', async ({
  walletContext,
}) => {
  const context = walletContext;
  const page = await context.newPage();
  const marketBase = arrangement.market.replace(/-PERP$/, ''); // table/row displays drop "-PERP"

  await openHomePage(page, FE_BASE);
  await connectMetaMask(page, context);
  // two modals in a row for a fresh account: "Welcome to Yellow Pro" appears first (with a
  // ~3s delay after connect), then "What's new" -- dismiss both before navigating anywhere
  // else, or a later click can land on a still-open backdrop instead of its real target.
  await dismissWelcomeModalIfPresent(page);
  await dismissWhatsNewModalIfPresent(page, '01b');
  await takeScreenshot(page, '01-connected');

  await transferSpotBalanceToPerpetual(page, context, FE_BASE, '20000');
  await assertPerpetualBalanceContains(page, FE_BASE, '20,000');

  const mark = await fetchLiveMarkPrice(arrangement);
  const restPrice = (mark * 0.9).toFixed(2); // 10% below mark -> rests, doesn't fill on its own
  await placeRestingPerpLimitBuy(page, context, FE_BASE, arrangement.market, restPrice, '0.01');

  // From here on, a resting order exists on this SHARED live book. If anything below throws
  // before it gets matched, the `finally` cancels it -- a killed/failed run must not add MORE
  // permanent trash for future runs to trip over (see matchRestingOrderWithApiCounterparty's
  // comment for what trash from EARLIER such runs already looks like).
  try {
    await assertOrderVisibleInOpenOrders(page, marketBase);
    await matchRestingOrderWithApiCounterparty(arrangement);
    await assertPositionVisibleInUi(page, marketBase);
  } finally {
    await cancelAnyOpenOrder(page, context);
  }
});
