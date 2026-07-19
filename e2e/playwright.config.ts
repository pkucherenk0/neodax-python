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
  use: {
    headless: false,
    viewport: { width: 1440, height: 900 },
  },
});
