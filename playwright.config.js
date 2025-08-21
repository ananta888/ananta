// @ts-check
const { defineConfig, devices } = require('@playwright/test');

/**
 * @see https://playwright.dev/docs/test-configuration
 */
module.exports = defineConfig({
  testDir: './tests/e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  workers: process.env.CI ? 1 : undefined,
  retries: process.env.CI ? 2 : 0,
  timeout: 30000,
  reporter: 'html',
  use: {
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    {
      name: process.env.PLAYWRIGHT_BROWSER || 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        launchOptions: {
          args: [
            '--disable-gpu',
            '--disable-dev-shm-usage',
            '--disable-setuid-sandbox',
            '--no-sandbox',
            '--single-process'
          ]
        }
      },
    },
  ],
  webServer: {
    command: 'echo "Webserver läuft bereits in Docker"',
    url: 'http://localhost:8081',
    reuseExistingServer: true,
  },
});
