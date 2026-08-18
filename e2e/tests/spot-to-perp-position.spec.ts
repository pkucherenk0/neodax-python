/**
 * Visual e2e: spot -> perp transfer, perp limit order via the real UI, matched by a
 * counterparty order placed via a raw API call, position visually confirmed.
 *
 * User 1 (this test, real UI via MetaMask/dappwright) rests a limit order; User 2 (separate
 * funded account, API-only) crosses it; we confirm the resulting position renders in User 1's
 * UI. Needs a real MetaMask connection (see ../README.md for why). Arrangement (funded
 * accounts) comes from ../.arrangement.json, generated fresh each run by global-setup.ts.
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
// CI secrets can carry a stray quote/whitespace -- strip before use, see ../README.md.
const FE_BASE = (process.env.NIMBUS_FE_BASE ?? 'https://uat.nimbus.example.com')
  .trim()
  .replace(/^['"`]+|['"`]+$/g, '');
const SCREENSHOT_DIR = path.resolve(__dirname, '../screenshots');

// fail loud with a diagnosis, not Chrome's opaque "invalid URL". never print the value (a secret).
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
  // two modals in a row for a fresh account -- dismiss both, see ../README.md.
  await dismissWelcomeModalIfPresent(page);
  await dismissWhatsNewModalIfPresent(page, '01b');
  await takeScreenshot(page, '01-connected');

  await transferSpotBalanceToPerpetual(page, context, FE_BASE, '20000');
  await assertPerpetualBalanceContains(page, FE_BASE, '20,000');

  const mark = await fetchLiveMarkPrice(arrangement);
  const restPrice = (mark * 0.9).toFixed(2); // 10% below mark -> rests, doesn't fill on its own
  await placeRestingPerpLimitBuy(page, context, FE_BASE, arrangement.market, restPrice, '0.01');

  // resting order now exists on a shared live book -- cancel on any throw, don't leave trash.
  try {
    await assertOrderVisibleInOpenOrders(page, marketBase);
    await matchRestingOrderWithApiCounterparty(arrangement);
    await assertPositionVisibleInUi(page, marketBase);
  } finally {
    await cancelAnyOpenOrder(page, context);
  }
});
