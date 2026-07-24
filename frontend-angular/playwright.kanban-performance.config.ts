import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  outputDir: '/tmp/ananta-kanban-local-diagnostic',
  timeout: 120_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: [['line']],
  use: {
    baseURL: 'http://127.0.0.1:4200',
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
    trace: 'off',
    screenshot: 'only-on-failure',
    video: 'off',
  },
  webServer: {
    command: 'npm run start:e2e',
    port: 4200,
    timeout: 120_000,
    reuseExistingServer: true,
  },
  projects: [{
    name: 'chromium-kanban-local-diagnostic',
    use: {
      ...devices['Desktop Chrome'],
      launchOptions: {
        args: [
          '--enable-precise-memory-info',
          '--js-flags=--expose-gc',
        ],
      },
    },
  }],
});
