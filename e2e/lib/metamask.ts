/**
 * Reusable MetaMask (dappwright) helpers, shared across tests/*.spec.ts.
 *
 * Findings this is built on (confirmed via manual recording during development, and via
 * screenshots + accessibility snapshots captured on live CI failures):
 * - dappwright's own wallet.approve()/sign() assume every popup step opens a NEW Playwright
 *   `page` and closes when done, always in the same way. This app's connect flow (its own
 *   connect approval, then a follow-up SIWE-style signature request) doesn't reliably behave
 *   the same way every run: sometimes the same popup navigates in place, sometimes a second
 *   popup opens separately. connectMetaMask() below handles both -- but unlike
 *   withOptionalApproval() (used for LATER actions, e.g. placing an order, where a signature
 *   prompt genuinely may or may not appear), the connect flow's signature step is NOT
 *   optional: skipping it leaves the account looking connected (the header renders normally)
 *   while every page underneath still shows "Connect your account to continue". Treating it
 *   as optional was a real regression introduced in this file's history -- it must be waited
 *   for, not raced against the popup merely closing.
 * - The header (including its "Deposit" link) renders identically whether or not the wallet
 *   is actually authenticated -- it is NOT a valid "connected" signal, despite looking like
 *   one. The only signal proven reliable by inspecting an actual failure's accessibility
 *   snapshot: an unauthenticated page shows a "Connect" button; an authenticated one shows
 *   none anywhere on the page.
 */
import { expect } from '@playwright/test';
import { BrowserContext, Page } from 'playwright-core';
import { bootstrap, Dappwright, getWallet } from '@tenkeylabs/dappwright';

// dappwright's downloader only sees each wallet's most recent ~100 GitHub releases (so very
// old versions 404). 13.17.0 (dappwright's own recommendedVersion) onboards fine via
// bootstrap() -- the connect flow itself just needed manual handling, see connectMetaMask().
export const METAMASK_VERSION = '13.17.0';

/** Onboard a fresh MetaMask instance directly with `mnemonic` as its ONE account -- no
 * "import a 2nd account then switch to it" step/ambiguity. Always use a freshly-generated
 * mnemonic per test run (see tools/arrange_metamask_e2e.py) -- never reuse one across runs,
 * a reused account accumulates leftover orders/positions from prior runs. */
export async function bootstrapMetaMask(mnemonic: string): Promise<{ wallet: Dappwright; context: BrowserContext }> {
  const [, , context] = await bootstrap('', {
    wallet: 'metamask',
    version: METAMASK_VERSION,
    seed: mnemonic,
    headless: false,
  });
  const wallet = await getWallet('metamask', context);
  return { wallet, context };
}

/** Click Connect -> MetaMask -> approve the connect popup -> approve its REQUIRED follow-up
 * signature request, then confirm the app actually finished authenticating. Both approval
 * steps are mandatory (see file header) -- the second one can show up either as the same
 * popup navigating in place, or as a genuinely separate new popup, so both are handled, but
 * neither is skippable. */
export async function connectMetaMask(page: Page, context: BrowserContext): Promise<void> {
  await page.getByRole('button', { name: 'Connect' }).first().click();
  await expect(page.getByText('MetaMask', { exact: true })).toBeVisible();

  const popupPromise = context.waitForEvent('page');
  await page.getByText('MetaMask', { exact: true }).click();
  let popup = await popupPromise;
  await popup.waitForLoadState();

  // Step 1: connect approval.
  await popup.getByRole('button', { name: 'Connect' }).click();

  // Step 2: the app's own SIWE-style signature request -- REQUIRED. Wait for whichever shape
  // it takes (same popup navigating, or a new one opening) instead of racing against close.
  const nextPopupPromise = context.waitForEvent('page', { timeout: 20000 }).catch(() => null);
  const navigatedInPlace = await popup.waitForURL(/signature-request/, { timeout: 20000 })
    .then(() => true)
    .catch(() => false);
  if (!navigatedInPlace) {
    const newPopup = await nextPopupPromise;
    if (newPopup) {
      popup = newPopup;
      await popup.waitForLoadState();
    }
  }
  await popup.getByRole('button', { name: /^(Confirm|Sign)$/ }).click({ timeout: 20000 });
  await popup.waitForEvent('close', { timeout: 20000 }).catch(() => {});
  await page.bringToFront();

  // Confirm the app actually finished authenticating. The header renders the same shell
  // either way (see file header) -- an unauthenticated page always shows a "Connect" button
  // somewhere; its absence is the real signal.
  await expect(page.getByRole('button', { name: 'Connect' })).toHaveCount(0);
}

/** Run `action`, then approve however many MetaMask popups it triggers (Confirm/Sign/Connect,
 * whichever label is present), one after another, until none shows up within `withinMs` of
 * the previous one closing. No-op (0 approvals) if the action needed no signature at all --
 * e.g. a pure REST call. Returns the number of popups approved, mainly for debugging/logging. */
export async function withOptionalApproval(
  page: Page,
  context: BrowserContext,
  action: () => Promise<void>,
  withinMs = 6000,
): Promise<number> {
  const firstPopupPromise = context.waitForEvent('page', { timeout: withinMs }).catch(() => null);
  await action();

  let approvals = 0;
  let popup = await firstPopupPromise;
  while (popup) {
    await popup.waitForLoadState();
    await popup.getByRole('button', { name: /^(Confirm|Sign|Connect)$/ }).click({ timeout: 10000 });
    approvals += 1;

    const nextPopupPromise = context.waitForEvent('page', { timeout: withinMs }).catch(() => null);
    await popup.waitForEvent('close', { timeout: withinMs }).catch(() => {});
    popup = await nextPopupPromise;
  }
  await page.bringToFront();
  return approvals;
}

export type Arrangement = {
  env: { trading_base: string; auth_base: string };
  market: string;
  subject: { address: string; mnemonic: string };
  // private_key: this env's JWT TTL is 60s, so access_token (minted once, at arrangement
  // time) is long expired by the time matchRestingOrderWithApiCounterparty needs it --
  // that step re-authenticates just before use (see tools/refresh_e2e_maker_token.py).
  maker: { address: string; access_token: string; private_key: string };
};
