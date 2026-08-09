import { defineConfig } from '@playwright/test';
import os from 'node:os';
import path from 'node:path';

const baseURL = process.env['E2E_PUBLIC_PAIR_MEDIA_BASE_URL']
  || process.env['E2E_FRONTEND_URL']
  || 'http://127.0.0.1:4200';
const outputDir = process.env['E2E_PUBLIC_PAIR_MEDIA_OUTPUT_DIR']
  || path.join(os.tmpdir(), `ananta-public-pair-media-live-${process.pid}`);

export default defineConfig({
  testDir: './tests',
  testMatch: 'public-pair-media-live.spec.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 300_000,
  expect: { timeout: 60_000 },
  outputDir,
  preserveOutput: 'never',
  reporter: [['line']],
  use: {
    baseURL,
    actionTimeout: 30_000,
    navigationTimeout: 120_000,
    trace: 'off',
    screenshot: 'off',
    video: 'off',
  },
  projects: [{ name: 'public-pair-media-live' }],
});
