import { defineConfig, devices } from '@playwright/test';
import { execSync } from 'child_process';

delete process.env.NO_COLOR;
delete process.env.FORCE_COLOR;

const defaultBrowsers = ['chromium'];
const envBrowsers = process.env.E2E_BROWSERS;
const e2ePort = Number(process.env.E2E_PORT || '4200');
function resolveFrontendBaseUrl(rawUrl: string) {
  try {
    const parsed = new URL(rawUrl);
    if (['localhost', '127.0.0.1'].includes(parsed.hostname)) return rawUrl;
    const resolvedHost = execSync(`getent hosts ${parsed.hostname} | awk '{print $1; exit}'`, { encoding: 'utf-8' }).trim();
    if (!resolvedHost) return rawUrl;
    return `${parsed.protocol}//${resolvedHost}${parsed.port ? `:${parsed.port}` : ''}`;
  } catch {
    return rawUrl;
  }
}

const configuredBaseUrl = process.env.E2E_FRONTEND_URL || `http://localhost:${e2ePort}`;
const baseUrl = resolveFrontendBaseUrl(configuredBaseUrl);
const reuseExistingServer = process.env.E2E_REUSE_SERVER === '1';
const compactReporter = process.env.E2E_REPORTER_MODE === 'compact';
const isLiveLlmRun = process.env.RUN_LIVE_LLM_TESTS === '1';
const webServerTimeoutMs = Number(process.env.E2E_WEBSERVER_TIMEOUT_MS || (isLiveLlmRun ? '90000' : '120000'));
const webServerEnv = process.env.CI ? { CI: 'true' } : {};
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
  timeout: isLiveLlmRun ? 90 * 1000 : 45 * 1000,
  expect: { timeout: 10 * 1000 },
  fullyParallel: false,
  retries: isLiveLlmRun ? 0 : (process.env.CI ? 2 : 1),
  workers: process.env.CI ? 2 : 1,
  reporter: reporters,
  use: {
    baseURL: baseUrl,
    trace: 'on-first-retry'
  },
  webServer: reuseExistingServer
    ? undefined
    : {
        command: 'npm run start:e2e',
        port: e2ePort,
        timeout: webServerTimeoutMs,
        env: webServerEnv
      },
  projects: browserProjects,
  globalSetup: './tests/global-setup.ts',
  globalTeardown: './tests/global-teardown.ts'
});
