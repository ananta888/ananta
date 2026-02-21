import { defineConfig, devices } from '@playwright/test';

delete process.env.NO_COLOR;
delete process.env.FORCE_COLOR;

const defaultBrowsers = ['chromium'];
const envBrowsers = process.env.E2E_BROWSERS;
const e2ePort = Number(process.env.E2E_PORT || '4200');
const reuseExistingServer = process.env.E2E_REUSE_SERVER === '1';
const compactReporter = process.env.E2E_REPORTER_MODE === 'compact';
const reporters = compactReporter
  ? [['dot'], ['junit', { outputFile: 'test-results/junit-results.xml' }], ['json', { outputFile: 'test-results/results.json' }]] as any
  : [['list'], ['junit', { outputFile: 'test-results/junit-results.xml' }], ['json', { outputFile: 'test-results/results.json' }]] as any;
const browsers = envBrowsers ? envBrowsers.split(',').map(b => b.trim()).filter(Boolean) : defaultBrowsers;
const browserProjects = browsers.map((browser) => {
  if (browser === 'firefox') {
    return { name: 'firefox', use: { ...devices['Desktop Firefox'] } };
  }
  if (browser === 'webkit') {
    return { name: 'webkit', use: { ...devices['Desktop Safari'] } };
  }
  return { name: 'chromium', use: { ...devices['Desktop Chrome'] } };
});

export default defineConfig({
  testDir: './tests',
  timeout: 45 * 1000,
  expect: { timeout: 10 * 1000 },
  fullyParallel: false,
  retries: process.env.CI ? 2 : 1,
  workers: process.env.CI ? 2 : 1,
  reporter: reporters,
  use: {
    baseURL: `http://localhost:${e2ePort}`,
    trace: 'on-first-retry'
  },
  webServer: {
    command: `npx ng serve --host 0.0.0.0 --port ${e2ePort} --poll 2000`,
    port: e2ePort,
    // Default: always start a fresh dev-server to avoid stale bundles in E2E.
    // Opt-in for local speed: E2E_REUSE_SERVER=1
    reuseExistingServer,
    env: { CI: 'true' }
  },
  projects: browserProjects,
  globalSetup: './tests/global-setup.ts',
  globalTeardown: './tests/global-teardown.ts'
});
