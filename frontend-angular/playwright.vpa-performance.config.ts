import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  testMatch: 'visual-process-assistant-performance.spec.ts',
  outputDir: process.env.VPA_PERFORMANCE_RESULTS_DIR || '/tmp/ananta-vpa-performance-results',
  timeout: 120_000,
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    ...devices['Desktop Chrome'],
    baseURL: process.env.E2E_FRONTEND_URL || 'http://127.0.0.1:4201',
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
