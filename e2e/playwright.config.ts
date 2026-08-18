import { defineConfig } from '@playwright/test';
import dotenv from 'dotenv';
import path from 'node:path';

// loads repo-root .env -- the only thing that does so on the Node side (Python side loads it
// separately in tools/arrange_metamask_e2e.py). must run regardless of cwd, see README.md.
dotenv.config({ path: path.resolve(__dirname, '..', '.env') });

// dappwright/MetaMask needs a real (non-headless) browser -- xvfb-run on Linux CI.
export default defineConfig({
  testDir: './tests',
  globalSetup: require.resolve('./global-setup.ts'),
  timeout: 600_000, // first run downloads the MetaMask extension; can be slow
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    headless: false,
    viewport: { width: 1440, height: 900 },
    screenshot: 'only-on-failure', // named checkpoints alone miss failures inside lib/*.ts
    trace: 'retain-on-failure', // full timeline incl. MetaMask popups -- see README.md
  },
});
