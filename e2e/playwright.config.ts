import { defineConfig } from '@playwright/test';
import dotenv from 'dotenv';
import path from 'node:path';

// load the repo-root .env (not e2e/.env -- there isn't one) so NIMBUS_FE_BASE etc. reach
// process.env before config/tests/global-setup read it. This is the ONLY thing that loads
// .env on the Node side -- the Python side (tools/arrange_metamask_e2e.py) loads it
// separately, so this must run regardless of cwd, hence the explicit path.
dotenv.config({ path: path.resolve(__dirname, '..', '.env') });

export default defineConfig({
  testDir: './tests',
  globalSetup: require.resolve('./global-setup.ts'),
  timeout: 180_000,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    headless: true, // no real extension to render anymore -- confirmed via 2 clean headed runs first
    viewport: { width: 1440, height: 900 },
    // named takeScreenshot() checkpoints only cover steps we anticipated -- a failure
    // anywhere else left nothing to inspect. 'only-on-failure' auto-captures every open
    // page at the moment a test fails, no instrumentation needed. 'retain-on-failure' trace
    // additionally captures the full action-by-action timeline (DOM snapshots, network,
    // console) -- the right tool for a mystery, since a single screenshot can't show what
    // happened a moment before.
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
});
