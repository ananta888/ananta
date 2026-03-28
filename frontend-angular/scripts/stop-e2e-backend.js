const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const cwd = process.cwd();
const pidFile = path.join(cwd, '.e2e-runtime', 'backend-pids.json');

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

let entries = [];
try {
  entries = JSON.parse(fs.readFileSync(pidFile, 'utf8'));
} catch {}

if (!Array.isArray(entries) || entries.length === 0) {
  console.log('[ananta:e2e-backend] No tracked backend pids.');
  process.exit(0);
}

for (const entry of entries) {
  const pid = Number(entry?.pid || 0);
  if (!pid) continue;
  if (terminatePid(pid)) {
    console.log(`[ananta:e2e-backend] Stopped ${entry.name || 'service'} pid ${pid}.`);
  }
}

try {
  fs.unlinkSync(pidFile);
} catch {}
