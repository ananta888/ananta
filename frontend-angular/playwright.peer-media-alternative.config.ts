import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  testMatch: 'peer-media-alternative.spec.ts',
  fullyParallel: false,
  workers: 1,
  timeout: 120_000,
  reporter: [['line']],
  outputDir: '/tmp/ananta-peer-media-alternative-playwright',
  use: { headless: true },
});
