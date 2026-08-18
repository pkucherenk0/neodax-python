import { defineConfig } from '@playwright/test';
import dotenv from 'dotenv';
import path from 'node:path';

// load the repo-root .env (not e2e/.env -- there isn't one) so NIMBUS_FE_BASE etc. reach
// process.env before config/tests/global-setup read it. This is the ONLY thing that loads
// .env on the Node side -- the Python side (tools/arrange_metamask_e2e.py) loads it
// separately, so this must run regardless of cwd, hence the explicit path.
dotenv.config({ path: path.resolve(__dirname, '..', '.env') });

// dappwright/MetaMask popups need a real (non-headless) browser. On Linux CI, run this
// under xvfb-run. On macOS/Windows headless: false alone is enough (see dappwright's own docs).
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
  },
});
