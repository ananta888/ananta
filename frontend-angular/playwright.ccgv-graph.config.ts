import { defineConfig, devices } from '@playwright/test';

const port = Number(process.env.CCGV_E2E_PORT || '4202');
const reuseExistingServer = process.env.E2E_REUSE_SERVER === '1';
const configuredBaseUrl = process.env.E2E_FRONTEND_URL || `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: './tests',
  testMatch: [
    'codecompass-graph-visualization-functional.spec.ts',
    'codecompass-graph-visualization-performance.spec.ts',
  ],
  outputDir: process.env.CCGV_E2E_RESULTS_DIR || '/tmp/ananta-ccgv-graph-results',
  timeout: 240_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    ...devices['Desktop Chrome'],
    baseURL: configuredBaseUrl,
    actionTimeout: 20_000,
    navigationTimeout: 45_000,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: reuseExistingServer
    ? undefined
    : {
        command: `E2E_PORT=${port} npm run start:e2e`,
        port,
        timeout: 120_000,
        reuseExistingServer: false,
      },
  // Performance gets its own worker/browser process so WebGL renderer state,
  // heap pressure and Axe scans from the functional journey cannot distort
  // the versioned p95 gate. Keeping it first also makes the environment class
  // reproducible when the complete config is executed.
  projects: [
    {
      name: 'ccgv-performance',
      testMatch: 'codecompass-graph-visualization-performance.spec.ts',
      use: {
        ...devices['Desktop Chrome'],
        trace: 'off',
        launchOptions: { args: ['--js-flags=--expose-gc'] },
      },
    },
    {
      name: 'ccgv-functional',
      testMatch: 'codecompass-graph-visualization-functional.spec.ts',
      use: {
        ...devices['Desktop Chrome'],
        launchOptions: { args: ['--enable-webgl', '--use-gl=swiftshader'] },
      },
    },
  ],
});
