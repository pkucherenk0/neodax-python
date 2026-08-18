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
    // named takeScreenshot() checkpoints only cover steps we anticipated -- a failure
    // anywhere else (e.g. inside lib/metamask.ts, not this test file) left nothing to
    // inspect. 'only-on-failure' auto-captures every open page at the moment a test fails,
    // no instrumentation needed. 'retain-on-failure' trace additionally captures the full
    // action-by-action timeline (DOM snapshots, network, console) across ALL pages
    // including MetaMask's own popups -- the right tool for "a popup closed unexpectedly"
    // mysteries, since a single screenshot can't show what happened a moment before.
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
});
