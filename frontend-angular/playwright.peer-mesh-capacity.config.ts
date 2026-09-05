import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  testMatch: 'peer-mesh-browser-capacity.spec.ts',
  fullyParallel: false,
  workers: 1,
  timeout: 120_000,
  reporter: [['line']],
  outputDir: '/tmp/ananta-peer-mesh-playwright',
  use: { headless: true },
});
