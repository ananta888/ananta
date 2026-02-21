const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const cwd = process.cwd();
const resultsPath = path.join(cwd, 'test-results', 'results.json');
const timeoutMinutes = Number(process.env.E2E_LITE_TIMEOUT_MINUTES || '25');
const timeoutMs = Math.max(1, timeoutMinutes) * 60 * 1000;

const env = { ...process.env };
env.ANANTA_E2E_USE_EXISTING = '1';
env.E2E_REUSE_SERVER = '1';
env.E2E_REPORTER_MODE = env.E2E_REPORTER_MODE || 'compact';

// Avoid noisy node warnings about NO_COLOR/FORCE_COLOR conflicts in CI wrappers.
delete env.NO_COLOR;
delete env.FORCE_COLOR;

const forwardedArgs = process.argv.slice(2);
const playwrightCli = require.resolve('@playwright/test/cli');
const child = spawn(process.execPath, [playwrightCli, 'test', ...forwardedArgs], {
  cwd,
  env,
  stdio: 'inherit',
  shell: false,
  windowsHide: true
});

let timedOut = false;
const timer = setTimeout(() => {
  timedOut = true;
  console.error(
    `\n[ananta:e2e-lite] Timeout after ${timeoutMinutes} minutes. Terminating Playwright process tree...`
  );
  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'inherit' });
  } else {
    child.kill('SIGTERM');
    setTimeout(() => child.kill('SIGKILL'), 5000);
  }
}, timeoutMs);

function collectFailures(suite, acc) {
  if (!suite) return acc;
  if (Array.isArray(suite.specs)) {
    for (const spec of suite.specs) {
      const failedTests = (spec.tests || []).filter((t) =>
        (t.results || []).some((r) => r.status === 'failed' || r.status === 'timedOut' || r.status === 'interrupted')
      );
      if (failedTests.length > 0) {
        const file = spec.file || '';
        const title = [spec.titlePath ? spec.titlePath.join(' > ') : '', spec.title || '']
          .filter(Boolean)
          .join(' ');
        acc.push({ file, title: title.trim() });
      }
    }
  }
  if (Array.isArray(suite.suites)) {
    for (const childSuite of suite.suites) {
      collectFailures(childSuite, acc);
    }
  }
  return acc;
}

function printFailureSummary() {
  if (!fs.existsSync(resultsPath)) return;
  try {
    const parsed = JSON.parse(fs.readFileSync(resultsPath, 'utf8'));
    const failures = collectFailures(parsed, []);
    if (failures.length === 0) return;
    console.error('\n[ananta:e2e-lite] Failure summary:');
    for (const item of failures.slice(0, 20)) {
      const loc = item.file ? `${item.file}: ` : '';
      console.error(`- ${loc}${item.title}`);
    }
    if (failures.length > 20) {
      console.error(`- ... ${failures.length - 20} more`);
    }
  } catch (err) {
    console.error(`[ananta:e2e-lite] Could not parse ${resultsPath}: ${err.message}`);
  }
}

child.on('exit', (code, signal) => {
  clearTimeout(timer);
  printFailureSummary();
  if (timedOut) {
    process.exit(124);
  }
  if (signal) {
    process.exit(1);
  }
  process.exit(code ?? 1);
});

child.on('error', (err) => {
  clearTimeout(timer);
  console.error(`[ananta:e2e-lite] Failed to start Playwright: ${err.message}`);
  process.exit(1);
});
