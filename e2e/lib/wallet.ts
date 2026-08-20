/** Injects a mock EIP-1193 wallet (real signatures, no MetaMask extension) -- see ../README.md
 * "Known issues" for why, and the eth_signTypedData_v4 gap to watch for. */
import { Page } from '@playwright/test';
import { installMockWallet } from '@johanneskares/wallet-mock';
import { mnemonicToAccount } from 'viem/accounts';
import { http } from 'viem';
import { mainnet } from 'viem/chains';

/** Installs the mock wallet for `mnemonic` on `page` -- call before page.goto(), addInitScript
 * only applies to the next navigation. Chain choice doesn't matter: this app never does a real
 * on-chain read/write, `mainnet` is just a stable default. */
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
