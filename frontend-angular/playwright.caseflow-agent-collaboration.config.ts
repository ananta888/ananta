import { defineConfig, devices } from '@playwright/test';

const port = Number(process.env.CASEFLOW_COLLABORATION_E2E_PORT || '4217');
const baseURL = `http://127.0.0.1:${port}`;
const outputDir = process.env.CASEFLOW_COLLABORATION_E2E_RESULTS_DIR
  || '/tmp/ananta-caseflow-collaboration-results';
const jsonOutput = process.env.CASEFLOW_COLLABORATION_PLAYWRIGHT_JSON
  || `${outputDir}/results.json`;

export default defineConfig({
  testDir: './tests',
  testMatch: 'caseflow-agent-collaboration.spec.ts',
  outputDir,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: [
    ['line'],
    ['json', { outputFile: jsonOutput }],
  ],
  use: {
    ...devices['Desktop Chrome'],
    baseURL,
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
  },
  webServer: {
    command: `E2E_PORT=${port} npm run start:e2e`,
    port,
    timeout: 120_000,
    reuseExistingServer: false,
  },
  projects: [{ name: 'caseflow-chromium', use: { ...devices['Desktop Chrome'] } }],
});
