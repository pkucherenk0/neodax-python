/**
 * Mock EIP-1193 wallet for e2e -- replaces the real MetaMask extension (dappwright), driven by
 * clicking through its popup UI. Every failure investigated this session (modal races, popup
 * self-close, a connect that silently never landed, a retry that hung) traced back to that one
 * thing: Playwright driving a REAL browser extension's popup is inherently racy against its own
 * internal timing, independent of anything in this test or the app under test. Confirmed this
 * is a known, still-unresolved pattern industry-wide (microsoft/playwright-python#1316,
 * synpress-io/synpress#1308 -- MetaMask v13 specifically), not something specific to this repo.
 *
 * @johanneskares/wallet-mock injects a real EIP-1193 provider via addInitScript, backed by an
 * actual viem local account (real signatures, same throwaway key tools/arrange_metamask_e2e.py
 * already mints) -- announced via EIP-6963 so the app's wallet list (Reown AppKit) discovers it
 * the same way it would a real extension. No extension, no popup, no click-timing race: every
 * signing request resolves synchronously in-page.
 *
 * Known gap: wallet-mock's `personal_sign` support covers the SIWE-style connect signature
 * (confirmed live: that popup was always a plain "Approve Signature Request", not a typed-data
 * screen). It does NOT implement `eth_signTypedData_v4` -- if the state-channel order-signing
 * step turns out to need that (unconfirmed, never captured a screenshot of that specific popup),
 * extend the wallet object via wallet-mock's `{ wallet }` install option rather than assume.
 */
import { Page } from '@playwright/test';
import { installMockWallet } from '@johanneskares/wallet-mock';
import { mnemonicToAccount } from 'viem/accounts';
import { http } from 'viem';
import { mainnet } from 'viem/chains';

/** Install the mock wallet for `mnemonic` on `page`. MUST run before the first navigation --
 * addInitScript only takes effect on the page's NEXT load, so call this before page.goto().
 * Chain choice shouldn't matter: this app's connect/order flow goes through its own REST/state-
 * channel backend, not real on-chain reads/writes (confirmed: the real-MetaMask flow never did
 * a network-switch step either). `mainnet` is just a stable default. */
export async function installWalletFor(page: Page, mnemonic: string): Promise<void> {
  const account = mnemonicToAccount(mnemonic);
  await installMockWallet({
    page,
    account,
    defaultChain: mainnet,
    transports: { [mainnet.id]: http() },
  });
}

export type Arrangement = {
  env: { trading_base: string; auth_base: string };
  market: string;
  subject: { address: string; mnemonic: string };
  maker: { address: string; access_token: string };
};
