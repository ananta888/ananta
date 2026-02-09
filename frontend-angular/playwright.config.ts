import { defineConfig, devices } from '@playwright/test';

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
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] }
    }
  ],
  globalSetup: './tests/global-setup.ts',
  globalTeardown: './tests/global-teardown.ts'
});
