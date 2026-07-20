import { defineConfig } from '@playwright/test';

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
  // CI (Xvfb + Chromium + a per-run MetaMask extension download, all sharing a runner) is
  // measurably slower and more variable than local dev -- individual assertions in lib/actions.ts
  // were seeing intermittent timeouts at the 5s default even after being bumped once to 15s.
  // Raise the default globally instead of chasing each call site as it happens to time out.
  expect: {
    timeout: 20_000,
  },
  use: {
    headless: false,
    viewport: { width: 1440, height: 900 },
  },
});
