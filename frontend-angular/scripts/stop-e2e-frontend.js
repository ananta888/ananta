const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const cwd = process.cwd();
const pidFile = path.join(cwd, '.e2e-runtime', 'frontend.pid');

function isPidAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function terminatePid(pid) {
  if (!isPidAlive(pid)) return false;
  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/PID', String(pid), '/T', '/F'], { stdio: 'inherit' });
    return true;
  }
  try {
    process.kill(pid, 'SIGTERM');
  } catch {
    return false;
  }
  return true;
}

let pid = 0;
try {
  const parsed = JSON.parse(fs.readFileSync(pidFile, 'utf8'));
  pid = Number(parsed?.pid || 0);
} catch {}

if (!pid) {
  console.log('[ananta:e2e-live] No tracked frontend pid.');
  process.exit(0);
}

const stopped = terminatePid(pid);
try {
  fs.unlinkSync(pidFile);
} catch {}

if (!stopped) {
  console.log(`[ananta:e2e-live] Tracked frontend pid ${pid} was not running.`);
  process.exit(0);
}

console.log(`[ananta:e2e-live] Stopped frontend pid ${pid}.`);
