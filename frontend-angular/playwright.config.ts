import { defineConfig, devices } from '@playwright/test';

const defaultBrowsers = ['chromium'];
const envBrowsers = process.env.E2E_BROWSERS;
const e2ePort = Number(process.env.E2E_PORT || '4200');
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
  reporter: [['list'], ['junit', { outputFile: 'test-results/junit-results.xml' }]],
  use: {
    baseURL: `http://localhost:${e2ePort}`,
    trace: 'on-first-retry'
  },
  webServer: {
    command: `npx ng serve --host 0.0.0.0 --port ${e2ePort} --poll 2000`,
    port: e2ePort,
    reuseExistingServer: true,
    env: { CI: 'true' }
  },
  projects: browserProjects,
  globalSetup: './tests/global-setup.ts',
  globalTeardown: './tests/global-teardown.ts'
});
