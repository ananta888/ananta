import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  testMatch: 'peer-overlay-churn.spec.ts',
  fullyParallel: false,
  workers: 1,
  timeout: 120_000,
  reporter: [['line']],
  outputDir: '/tmp/ananta-peer-overlay-churn-playwright',
  use: { headless: true },
});
