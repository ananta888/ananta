import { defineConfig, devices } from '@playwright/test';

const frontendPort = Number(process.env['CROSS_SURFACE_FRONTEND_PORT'] || '4300');
const frontendUrl =
  process.env['CROSS_SURFACE_FRONTEND_URL'] || `http://127.0.0.1:${frontendPort}`;
const outputDirectory =
  process.env['CROSS_SURFACE_OUTPUT_DIR'] || '/tmp/ananta-cross-surface-playwright';

export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  expect: {
    timeout: 15_000,
  },
  outputDir: outputDirectory,
  reporter: [['line']],
  use: {
    ...devices['Desktop Chrome'],
    baseURL: frontendUrl,
    screenshot: 'off',
    trace: 'off',
    video: 'off',
  },
  projects: [
    {
      name: 'cross-surface-chromium',
      use: {
        ...devices['Desktop Chrome'],
      },
    },
  ],
  webServer: {
    command: 'npm run start:e2e',
    url: frontendUrl,
    reuseExistingServer: false,
    timeout: 180_000,
    stdout: 'pipe',
    stderr: 'pipe',
  },
});
