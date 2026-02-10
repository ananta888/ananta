import { defineConfig, devices } from '@playwright/test';

const defaultBrowsers = ['chromium'];
const envBrowsers = process.env.E2E_BROWSERS;
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
  timeout: 20 * 1000,
  expect: { timeout: 5 * 1000 },
  fullyParallel: true,
  retries: process.env.CI ? 2 : 1,
  workers: process.env.CI ? 2 : 4,
  reporter: [['list'], ['junit', { outputFile: 'test-results/junit-results.xml' }]],
  use: {
    baseURL: 'http://localhost:4200',
    trace: 'on-first-retry'
  },
  webServer: {
    command: 'npm start',
    port: 4200,
    reuseExistingServer: true,
    env: { CI: 'true' }
  },
  projects: browserProjects,
  globalSetup: './tests/global-setup.ts',
  globalTeardown: './tests/global-teardown.ts'
});
