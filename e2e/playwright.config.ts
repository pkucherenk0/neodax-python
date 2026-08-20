import { defineConfig } from '@playwright/test';
import dotenv from 'dotenv';
import path from 'node:path';

// loads repo-root .env (not e2e/.env) so NIMBUS_FE_BASE etc. reach process.env before tests run.
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
    headless: true, // mock wallet, no real extension to render
    viewport: { width: 1440, height: 900 },
    screenshot: 'only-on-failure', // named checkpoints alone miss failures inside lib/*.ts
    trace: 'retain-on-failure', // full action timeline for post-mortem, see ../README.md
  },
});
