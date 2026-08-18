/**
 * Reusable MetaMask (dappwright) helpers, shared across tests/*.spec.ts.
 * See ../README.md "Known issues / workarounds" for the why behind the popup handling below.
 */
import { expect } from '@playwright/test';
import { BrowserContext, Page } from 'playwright-core';
import { bootstrap, Dappwright, getWallet } from '@tenkeylabs/dappwright';

// dappwright's downloader only sees each wallet's most recent ~100 GitHub releases (so very
// old versions 404). 13.17.0 (dappwright's own recommendedVersion) onboards fine via
// bootstrap() -- the connect flow itself just needed manual handling, see connectMetaMask().
export const METAMASK_VERSION = '13.17.0';

/** Onboard a fresh MetaMask instance with `mnemonic` as its one account. Use a freshly-generated
 * mnemonic per run (see tools/arrange_metamask_e2e.py) -- a reused account accumulates leftover
 * orders/positions. */
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

/** Click Connect -> MetaMask -> approve the connect popup -> approve its follow-up signature
 * request IF one shows up (same popup, navigates in place). See ../README.md. */
export async function connectMetaMask(page: Page, context: BrowserContext): Promise<void> {
  await page.getByRole('button', { name: 'Connect' }).first().click();
  await expect(page.getByText('MetaMask', { exact: true })).toBeVisible();

  const popupPromise = context.waitForEvent('page');
  await page.getByText('MetaMask', { exact: true }).click();
  const popup = await popupPromise;
  await popup.waitForLoadState();

  await popup.getByRole('button', { name: 'Connect' }).click();

  await Promise.race([
    popup.waitForEvent('close', { timeout: 15000 }).catch(() => {}),
    popup.waitForURL(/signature-request/, { timeout: 15000 }).catch(() => {}),
  ]);

  if (!popup.isClosed()) {
    // follow-up signature request needs confirming. popup can self-close mid-click
    // (timing race, see ../README.md) -- tolerate that same as the check above.
    try {
      await popup.getByRole('button', { name: 'Confirm' }).click({ timeout: 10000 });
      await popup.waitForEvent('close', { timeout: 15000 }).catch(() => {});
    } catch (err) {
      if (!popup.isClosed()) throw err; // only swallow if it's actually gone
    }
  }
  await page.bringToFront();
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
  maker: { address: string; access_token: string };
};
