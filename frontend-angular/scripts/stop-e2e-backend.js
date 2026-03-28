const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const cwd = process.cwd();
const pidFile = path.join(cwd, '.e2e-runtime', 'backend-pids.json');
const hubUrl = process.env.E2E_HUB_URL || 'http://localhost:5500';
const alphaUrl = process.env.E2E_ALPHA_URL || 'http://localhost:5501';
const betaUrl = process.env.E2E_BETA_URL || 'http://localhost:5502';

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function parsePort(url) {
  const parsed = new URL(url);
  return Number(parsed.port || (parsed.protocol === 'https:' ? 443 : 80));
}

function discoverListeningPids(ports) {
  const probe = spawnSync('ss', ['-ltnp'], { encoding: 'utf8' });
  if ((probe.status ?? 1) !== 0) {
    return [];
  }
  const pids = new Set();
  const text = String(probe.stdout || '');
  for (const line of text.split(/\r?\n/)) {
    if (!ports.some((port) => line.includes(`:${port}`))) continue;
    for (const match of line.matchAll(/pid=(\d+)/g)) {
      pids.add(Number(match[1]));
    }
  }
  return Array.from(pids);
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

async function terminatePidGracefully(pid) {
  if (!terminatePid(pid)) return false;
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    if (!isPidAlive(pid)) {
      return true;
    }
    await wait(150);
  }

  if (process.platform !== 'win32' && isPidAlive(pid)) {
    try {
      process.kill(pid, 'SIGKILL');
    } catch {}
  }

  const killDeadline = Date.now() + 2000;
  while (Date.now() < killDeadline) {
    if (!isPidAlive(pid)) {
      return true;
    }
    await wait(100);
  }

  return !isPidAlive(pid);
}

let entries = [];
try {
  entries = JSON.parse(fs.readFileSync(pidFile, 'utf8'));
} catch {}

async function main() {
  const targetPorts = [parsePort(hubUrl), parsePort(alphaUrl), parsePort(betaUrl)];
  const discoveredPids = discoverListeningPids(targetPorts);
  const trackedEntries = Array.isArray(entries) ? entries : [];
  const trackedPids = trackedEntries.map((entry) => Number(entry?.pid || 0)).filter(Boolean);
  const allPids = Array.from(new Set([...trackedPids, ...discoveredPids]));

  if (allPids.length === 0) {
    console.log('[ananta:e2e-backend] No backend pids found.');
    process.exit(0);
  }

  for (const pid of allPids) {
    if (!pid) continue;
    const stopped = await terminatePidGracefully(pid);
    const trackedEntry = trackedEntries.find((entry) => Number(entry?.pid || 0) === pid);
    const name = trackedEntry?.name || 'service';
    if (stopped) {
      console.log(`[ananta:e2e-backend] Stopped ${name} pid ${pid}.`);
      continue;
    }
    console.log(`[ananta:e2e-backend] Failed to stop ${name} pid ${pid}.`);
  }

  try {
    fs.unlinkSync(pidFile);
  } catch {}
}

main().catch((err) => {
  console.error(`[ananta:e2e-backend] ${err.message}`);
  process.exit(1);
});
