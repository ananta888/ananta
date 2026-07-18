import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  testMatch: [
    'visual-process-assistant-patch.spec.ts',
    'visual-process-assistant-isolation.spec.ts',
  ],
  outputDir: process.env.VPA_FUNCTIONAL_RESULTS_DIR || '/tmp/ananta-vpa-functional-results',
  // The isolation journey boots two full Angular editor instances. Keep the
  // assertion timeout bounded, but allow CPU-contended acceptance runs to
  // finish without misclassifying infrastructure latency as state leakage.
  timeout: 180_000,
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
