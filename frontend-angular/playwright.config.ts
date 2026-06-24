import { defineConfig, devices } from '@playwright/test';
import { execSync } from 'child_process';
import fs from 'fs';
import os from 'os';
import path from 'path';

delete process.env.NO_COLOR;
delete process.env.FORCE_COLOR;

const defaultBrowsers = ['chromium'];
const envBrowsers = process.env.E2E_BROWSERS;
const e2ePort = Number(process.env.E2E_PORT || '4200');
function resolveFrontendBaseUrl(rawUrl: string) {
  try {
    const parsed = new URL(rawUrl);
    if (parsed.hostname === '127.0.0.1') return rawUrl;
    if (parsed.hostname === 'localhost') {
      return `${parsed.protocol}//127.0.0.1${parsed.port ? `:${parsed.port}` : ''}`;
    }
    const resolvedHost = execSync(`getent hosts ${parsed.hostname} | awk '{print $1; exit}'`, { encoding: 'utf-8' }).trim();
    if (!resolvedHost) return rawUrl;
    return `${parsed.protocol}//${resolvedHost}${parsed.port ? `:${parsed.port}` : ''}`;
  } catch {
    return rawUrl;
  }
}

const configuredBaseUrl = process.env.E2E_FRONTEND_URL || `http://127.0.0.1:${e2ePort}`;
// When E2E_FRONTEND_URL is explicitly set (e.g. in Docker compose with a hostname like
// angular-frontend:4200), skip IP resolution so CORS works — the browser resolves
// Docker service names correctly without pre-resolving to a numeric IP, which would
// produce a different origin and trip the hub's CORS policy.
const baseUrl = process.env.E2E_FRONTEND_URL ? configuredBaseUrl : resolveFrontendBaseUrl(configuredBaseUrl);
const reuseExistingServer = process.env.E2E_REUSE_SERVER === '1';
const compactReporter = process.env.E2E_REPORTER_MODE === 'compact';
const isLiveLlmRun = process.env.RUN_LIVE_LLM_TESTS === '1';
const retainEvidenceArtifacts = process.env.E2E_RETAIN_EVIDENCE_ARTIFACTS === '1';
function resolveResultsDir(): string {
  const configured = process.env.E2E_RESULTS_DIR?.trim();
  if (configured) return configured;
  const defaultDir = 'test-results';
  try {
    fs.mkdirSync(defaultDir, { recursive: true });
    const probe = path.join(defaultDir, `.write-probe-${process.pid}`);
    fs.writeFileSync(probe, 'ok');
    fs.unlinkSync(probe);
    return defaultDir;
  } catch {
    return path.join(os.tmpdir(), `ananta-frontend-e2e-results-${typeof process.getuid === 'function' ? process.getuid() : 'user'}`);
  }
}
const resultsDir = resolveResultsDir();
const webServerTimeoutMs = Number(process.env.E2E_WEBSERVER_TIMEOUT_MS || (isLiveLlmRun ? '90000' : '120000'));
const testTimeoutMs = Number(process.env.E2E_TEST_TIMEOUT_MS || (isLiveLlmRun ? '120000' : '60000'));
const expectTimeoutMs = Number(process.env.E2E_EXPECT_TIMEOUT_MS || '15000');
const actionTimeoutMs = Number(process.env.E2E_ACTION_TIMEOUT_MS || '15000');
const navigationTimeoutMs = Number(process.env.E2E_NAV_TIMEOUT_MS || '30000');
const webServerEnv = process.env.CI ? { CI: 'true' } : {};
const configuredWorkers = Number(process.env.E2E_WORKERS || '1');
const workerCount = Number.isFinite(configuredWorkers) && configuredWorkers > 0 ? configuredWorkers : 1;
const resultsPath = (fileName: string) => path.join(resultsDir, fileName);
const reporters = compactReporter
  ? [['dot'], ['junit', { outputFile: resultsPath('junit-results.xml') }], ['json', { outputFile: resultsPath('results.json') }]] as any
  : [['list'], ['junit', { outputFile: resultsPath('junit-results.xml') }], ['json', { outputFile: resultsPath('results.json') }]] as any;
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
  outputDir: resultsDir,
  timeout: testTimeoutMs,
  expect: { timeout: expectTimeoutMs },
  fullyParallel: false,
  retries: isLiveLlmRun ? 0 : (process.env.CI ? 2 : 1),
  workers: workerCount,
  reporter: reporters,
  use: {
    baseURL: baseUrl,
    actionTimeout: actionTimeoutMs,
    navigationTimeout: navigationTimeoutMs,
    trace: retainEvidenceArtifacts ? 'on' : 'on-first-retry',
    screenshot: retainEvidenceArtifacts ? 'on' : 'only-on-failure',
    video: retainEvidenceArtifacts ? 'on' : 'retain-on-failure'
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
