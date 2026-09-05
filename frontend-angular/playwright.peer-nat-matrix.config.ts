import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  testMatch: 'peer-nat-matrix.spec.ts',
  fullyParallel: false,
  workers: 1,
  timeout: 120_000,
  reporter: [['line']],
  outputDir: '/tmp/ananta-peer-nat-matrix-playwright',
  use: { headless: true },
});
