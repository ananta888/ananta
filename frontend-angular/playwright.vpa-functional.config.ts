import { defineConfig, devices } from '@playwright/test';

const configuredBaseUrl = process.env.E2E_FRONTEND_URL;
const localPort = 4201;

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
    baseURL: configuredBaseUrl || `http://127.0.0.1:${localPort}`,
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: configuredBaseUrl
    ? undefined
    : {
        command: 'npm run start:e2e',
        port: localPort,
        timeout: 120_000,
        reuseExistingServer: true,
        env: { E2E_PORT: String(localPort) },
      },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
