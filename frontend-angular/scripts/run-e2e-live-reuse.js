const { spawn } = require('node:child_process');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');

const cwd = process.cwd();
const baseUrl = process.env.E2E_FRONTEND_URL || 'http://127.0.0.1:4200';
const loginUrl = `${baseUrl.replace(/\/+$/, '')}/login`;
const startupTimeoutMs = Number(process.env.E2E_FRONTEND_WAIT_MS || '90000');
const overallTimeoutMinutes = Number(process.env.E2E_LIVE_REUSE_TIMEOUT_MINUTES || '12');
const overallTimeoutMs = Math.max(1, overallTimeoutMinutes) * 60 * 1000;
const forwardedArgs = process.argv.slice(2);
const stateDir = path.join(cwd, '.e2e-runtime');
const pidFile = path.join(stateDir, 'frontend.pid');
const logFile = path.join(stateDir, 'frontend.log');

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

function isPidAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function readTrackedPid() {
  try {
    const parsed = JSON.parse(fs.readFileSync(pidFile, 'utf8'));
    return Number(parsed?.pid || 0);
  } catch {
    return 0;
  }
}

function writeTrackedPid(pid) {
  fs.mkdirSync(stateDir, { recursive: true });
  fs.writeFileSync(pidFile, JSON.stringify({ pid }, null, 2));
}

async function waitForFrontendReady() {
  const started = Date.now();
  while ((Date.now() - started) < startupTimeoutMs) {
    if (await isFrontendReady()) return true;
    await wait(500);
  }
  return false;
}

function ensureRuntimeDir() {
  fs.mkdirSync(stateDir, { recursive: true });
}

function startDetachedFrontend(env) {
  ensureRuntimeDir();
  const logFd = fs.openSync(logFile, 'a');
  const child = spawn('npm', ['run', 'start:e2e'], {
    cwd,
    env,
    detached: true,
    stdio: ['ignore', logFd, logFd],
    shell: false,
    windowsHide: true
  });
  child.unref();
  writeTrackedPid(child.pid || 0);
  return child.pid || 0;
}

async function ensureFrontend(env) {
  if (await isFrontendReady()) {
    return { reused: true, pid: readTrackedPid() };
  }

  const trackedPid = readTrackedPid();
  if (isPidAlive(trackedPid)) {
    const ready = await waitForFrontendReady();
    if (ready) {
      return { reused: true, pid: trackedPid };
    }
  }

  const pid = startDetachedFrontend(env);
  const ready = await waitForFrontendReady();
  if (!ready) {
    throw new Error(`Frontend not ready at ${loginUrl} after ${startupTimeoutMs}ms. See ${logFile}.`);
  }
  return { reused: false, pid };
}

async function main() {
  const env = { ...process.env };
  env.RUN_LIVE_LLM_TESTS = '1';
  env.ANANTA_E2E_USE_EXISTING = env.ANANTA_E2E_USE_EXISTING || '1';
  env.E2E_REUSE_SERVER = '1';
  env.E2E_REPORTER_MODE = env.E2E_REPORTER_MODE || 'compact';
  env.LIVE_LLM_PROVIDER = env.LIVE_LLM_PROVIDER || 'ollama';
  env.LMSTUDIO_URL = env.LMSTUDIO_URL || 'http://192.168.56.1:1234/v1';

  delete env.NO_COLOR;
  delete env.FORCE_COLOR;

  await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [path.join(cwd, 'scripts', 'start-e2e-backend.js')], {
      cwd,
      env,
      stdio: 'inherit',
      shell: false,
      windowsHide: true
    });
    child.on('exit', (code, signal) => {
      if (signal || code !== 0) {
        reject(new Error(`Backend startup failed with code ${code ?? 'signal'}.`));
        return;
      }
      resolve();
    });
    child.on('error', reject);
  });

  const frontend = await ensureFrontend(env);
  if (frontend.reused) {
    console.error(`[ananta:e2e-live] Reusing frontend at ${loginUrl}.`);
  } else {
    console.error(`[ananta:e2e-live] Started frontend pid=${frontend.pid}; reusing it for later runs. Logs: ${logFile}`);
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
    child.kill('SIGTERM');
    setTimeout(() => child.kill('SIGKILL'), 5000).unref();
  }, overallTimeoutMs);

  const finalize = (code) => {
    clearTimeout(timer);
    if (timedOut) {
      process.exit(124);
    }
    process.exit(code ?? 1);
  };

  child.on('exit', (code, signal) => finalize(signal ? 1 : code));
  child.on('error', (err) => {
    clearTimeout(timer);
    console.error(`[ananta:e2e-live] Failed to start Playwright: ${err.message}`);
    process.exit(1);
  });
}

main().catch((err) => {
  console.error(`[ananta:e2e-live] ${err.message}`);
  process.exit(1);
});
