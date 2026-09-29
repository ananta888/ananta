import { defineConfig } from '@playwright/test';

import base from './playwright.config';
import { MOCKED_SPECS } from './tests/mocked-specs';

/**
 * The mocked Playwright suite: specs whose backend calls are all route-mocked.
 * Unlike the full config it starts no Hub/worker processes (no globalSetup), runs fully parallel and
 * retries only in CI, so a flaky spec shows up instead of silently costing a second run.
 */
// shared login helpers use a mocked session instead of the Hub (tests/utils.ts)
process.env.E2E_MOCK_AUTH = '1';

const workers = Number(process.env.E2E_WORKERS || '4');

export default defineConfig({
  ...base,
  testMatch: MOCKED_SPECS,
  testIgnore: [],
  fullyParallel: true,
  workers: Number.isFinite(workers) && workers > 0 ? workers : 4,
  retries: process.env.CI ? 1 : 0,
  globalSetup: undefined,
  globalTeardown: undefined,
});
