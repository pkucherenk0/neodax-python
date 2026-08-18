/**
 * Reusable MetaMask (dappwright) helpers, shared across tests/*.spec.ts.
 *
 * Findings this is built on (confirmed via manual recording during development):
 * - dappwright's own wallet.approve()/sign() assume every popup step opens a NEW Playwright
 *   `page` and closes when done. This app's popup instead NAVIGATES IN PLACE between steps
 *   (connect -> its own SIWE-style signature request), so those calls hang forever waiting
 *   for a 'close' event that only fires after a step they never handle. Handled manually here.
 * - This app runs on state channels: some actions (e.g. placing an order)
 *   sign a state update, not just a REST call, and pop a SECOND (or more) MetaMask
 *   confirmation independent of the connect flow. withOptionalApproval() below is generic
 *   over "however many popups this action happens to need, including zero".
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

/** One click-through of Connect -> MetaMask -> approve the connect popup -> approve its
 * follow-up signature request IF one shows up (same popup, navigates in place -- see file
 * header). Whether the follow-up signature request happens is app/session-state dependent --
 * CI runs observed the popup sometimes closing right after the connect approval with no second
 * step, where local dev runs always saw the two-step flow. Race both outcomes instead of
 * assuming the popup stays open. Does NOT verify the app actually ended up connected -- see
 * connectMetaMask(), which wraps this with that verification and a retry. */
async function connectAttempt(page: Page, context: BrowserContext): Promise<void> {
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
    // didn't close on its own -> the connect approval navigated to the follow-up signature
    // request, which still needs confirming. Confirmed live (CI trace + screenshot): the
    // popup itself can be a perfectly normal, well-formed "Approve Signature Request" at the
    // exact moment of failure -- correct network/domain/challenge, Confirm button visible and
    // enabled -- then close on its own in the gap between this check and the click actually
    // landing ("Target page, context or browser has been closed"). That's the SAME race the
    // check above already treats as a valid outcome for step 1 (popup closes without needing
    // a second step) -- extend the same tolerance here instead of failing the whole test on a
    // race we already know can happen and isn't actually wrong.
    try {
      await popup.getByRole('button', { name: 'Confirm' }).click({ timeout: 10000 });
      await popup.waitForEvent('close', { timeout: 15000 }).catch(() => {});
    } catch (err) {
      if (!popup.isClosed()) throw err; // only swallow if it's ACTUALLY gone, not some other failure
    }
  }
  await page.bringToFront();
}

/** did the app's own Connect button disappear (the real, terminal success signal -- see
 * connectMetaMask()) within `timeoutMs`? */
async function connectLanded(page: Page, timeoutMs: number): Promise<boolean> {
  return page.getByRole('button', { name: 'Connect' }).first()
    .waitFor({ state: 'hidden', timeout: timeoutMs })
    .then(() => true)
    .catch(() => false);
}

/** connectAttempt(), verified, with one bounded retry. The popup closing is NOT sufficient
 * evidence the connection actually landed -- confirmed live via a CI trace: the popup can close
 * right after the connect-approval click (the race connectAttempt() already tolerates) while
 * the app's OWN session handshake never completes, leaving it stuck on its in-page "Approve
 * Signature Request... Connecting" modal, then reverting to "Connect your account to continue"
 * many seconds later. That silent failure only surfaced as a confusing, unrelated "Transfer
 * button not found" timeout deep in the next step. Verify the app's own terminal signal instead
 * of trusting the popup: on a real success the original Connect button gets replaced (a
 * "Successfully connected with MetaMask" modal briefly shows too, but it's transient -- the
 * button swap is the stable one).
 *
 * Confirmed live (CI trace + ARIA snapshot) that a failed attempt reverts the app to a fresh
 * "Connect your account to continue" screen with a working Connect button, not a stuck/
 * half-broken one -- safe to just run the whole attempt again. This is an auth/session
 * handshake, not an order placement, so the "no retries" rail (real orders re-placed) doesn't
 * apply here the way it does to the Python trades/serial suites. */
export async function connectMetaMask(page: Page, context: BrowserContext): Promise<void> {
  const maxAttempts = 2;
  for (let attempt = 1; attempt < maxAttempts; attempt++) {
    await connectAttempt(page, context);
    if (await connectLanded(page, 20000)) return;
  }
  // final attempt: no retries left -- let this throw with Playwright's own rich diagnostics
  // (locator, timeout, DOM snapshot) if it still didn't land.
  await connectAttempt(page, context);
  await expect(page.getByRole('button', { name: 'Connect' }).first())
    .not.toBeVisible({ timeout: 20000 });
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
