const { spawn, spawnSync } = require('node:child_process');
const http = require('node:http');
const path = require('node:path');

const cwd = process.cwd();
const baseUrl = process.env.E2E_FRONTEND_URL || 'http://127.0.0.1:4200';
const loginUrl = `${baseUrl.replace(/\/+$/, '')}/login`;
const startupTimeoutMs = Number(process.env.E2E_FRONTEND_WAIT_MS || '45000');
const overallTimeoutMinutes = Number(process.env.E2E_LIVE_REUSE_TIMEOUT_MINUTES || '12');
const overallTimeoutMs = Math.max(1, overallTimeoutMinutes) * 60 * 1000;
const forwardedArgs = process.argv.slice(2);

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function fetchStatus(url, timeoutMs = 2500) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, { timeout: timeoutMs }, (res) => {
      res.resume();
      resolve(res.statusCode || 0);
    });
    req.on('timeout', () => req.destroy(new Error('timeout')));
    req.on('error', reject);
  });
}

async function isFrontendReady() {
  try {
    const status = await fetchStatus(loginUrl);
    return status >= 200 && status < 500;
  } catch {
    return false;
  }
}

async function waitForFrontendReady() {
  const started = Date.now();
  while ((Date.now() - started) < startupTimeoutMs) {
    if (await isFrontendReady()) return true;
    await wait(500);
  }
  return false;
}

function terminateProcessTree(child) {
  if (!child || !child.pid) return;
  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'inherit' });
    return;
  }
  child.kill('SIGTERM');
  setTimeout(() => child.kill('SIGKILL'), 5000).unref();
}

async function main() {
  const env = { ...process.env };
  env.RUN_LIVE_LLM_TESTS = '1';
  env.ANANTA_E2E_USE_EXISTING = env.ANANTA_E2E_USE_EXISTING || '1';
  env.E2E_REUSE_SERVER = '1';
  env.E2E_REPORTER_MODE = env.E2E_REPORTER_MODE || 'compact';

  delete env.NO_COLOR;
  delete env.FORCE_COLOR;

  let serverChild = null;
  let startedServer = false;

  if (!(await isFrontendReady())) {
    serverChild = spawn('npm', ['run', 'start:e2e'], {
      cwd,
      env,
      stdio: 'inherit',
      shell: false,
      windowsHide: true
    });
    startedServer = true;

    const ready = await waitForFrontendReady();
    if (!ready) {
      terminateProcessTree(serverChild);
      console.error(`[ananta:e2e-live] Frontend not ready at ${loginUrl} after ${startupTimeoutMs}ms.`);
      process.exit(1);
    }
  }

  const playwrightCli = require.resolve('@playwright/test/cli');
  const child = spawn(process.execPath, [playwrightCli, 'test', 'tests/templates-ai-live.spec.ts', ...forwardedArgs], {
    cwd,
    env,
    stdio: 'inherit',
    shell: false,
    windowsHide: true
  });

  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    console.error(`\n[ananta:e2e-live] Timeout after ${overallTimeoutMinutes} minutes. Terminating Playwright process tree...`);
    terminateProcessTree(child);
  }, overallTimeoutMs);

  const finalize = (code) => {
    clearTimeout(timer);
    if (startedServer) {
      terminateProcessTree(serverChild);
    }
    if (timedOut) {
      process.exit(124);
    }
    process.exit(code ?? 1);
  };

  child.on('exit', (code, signal) => finalize(signal ? 1 : code));
  child.on('error', (err) => {
    clearTimeout(timer);
    if (startedServer) {
      terminateProcessTree(serverChild);
    }
    console.error(`[ananta:e2e-live] Failed to start Playwright: ${err.message}`);
    process.exit(1);
  });
}

main().catch((err) => {
  console.error(`[ananta:e2e-live] ${err.message}`);
  process.exit(1);
});
