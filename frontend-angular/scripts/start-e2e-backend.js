const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..', '..');
const runtimeDir = path.join(root, 'frontend-angular', '.e2e-runtime');
const pidFile = path.join(runtimeDir, 'backend-pids.json');
const logDir = path.join(runtimeDir, 'backend-logs');
const dataRoot = path.join(root, 'data_test_e2e');
const healthTimeoutMs = Number(process.env.E2E_SERVICE_HEALTH_TIMEOUT_MS || '45000');

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function resolvePythonBinary() {
  const explicit = process.env.PYTHON_BIN && process.env.PYTHON_BIN.trim();
  if (explicit) return explicit;

  const repoVenvPython = path.resolve(root, '.venv', 'bin', 'python3');
  if (fs.existsSync(repoVenvPython)) return repoVenvPython;

  for (const candidate of ['python3', 'python']) {
    const probe = spawnSync(candidate, ['--version'], { stdio: 'ignore' });
    if (probe.status === 0) return candidate;
  }

  return 'python3';
}

function parseServiceUrl(url) {
  const parsed = new URL(url);
  return {
    host: parsed.hostname,
    port: Number(parsed.port || (parsed.protocol === 'https:' ? 443 : 80))
  };
}

async function isHealthy(url) {
  try {
    const res = await fetch(url);
    return res.ok;
  } catch {
    return false;
  }
}

async function waitForHealth(url, timeoutMs = healthTimeoutMs) {
  const started = Date.now();
  while ((Date.now() - started) < timeoutMs) {
    if (await isHealthy(url)) return true;
    await wait(400);
  }
  return false;
}

function ensureDirs() {
  fs.mkdirSync(runtimeDir, { recursive: true });
  fs.mkdirSync(logDir, { recursive: true });
  fs.mkdirSync(dataRoot, { recursive: true });
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

function readTrackedPids() {
  try {
    return JSON.parse(fs.readFileSync(pidFile, 'utf8'));
  } catch {
    return [];
  }
}

function writeTrackedPids(entries) {
  ensureDirs();
  fs.writeFileSync(pidFile, JSON.stringify(entries, null, 2));
}

function serviceSpecs() {
  const hubUrl = process.env.E2E_HUB_URL || 'http://localhost:5500';
  const alphaUrl = process.env.E2E_ALPHA_URL || 'http://localhost:5501';
  const betaUrl = process.env.E2E_BETA_URL || 'http://localhost:5502';
  const adminUser = process.env.E2E_ADMIN_USER || 'admin';
  const adminPassword = process.env.E2E_ADMIN_PASSWORD || 'AnantaAdminPassword123!';
  const hub = parseServiceUrl(hubUrl);
  const alpha = parseServiceUrl(alphaUrl);
  const beta = parseServiceUrl(betaUrl);

  return [
    {
      name: 'hub',
      host: hub.host,
      port: hub.port,
      env: {
        ROLE: 'hub',
        AGENT_NAME: 'hub',
        AGENT_TOKEN: 'hubsecret',
        PORT: String(hub.port),
        INITIAL_ADMIN_USER: adminUser,
        INITIAL_ADMIN_PASSWORD: adminPassword
      }
    },
    {
      name: 'alpha',
      host: alpha.host,
      port: alpha.port,
      env: {
        AGENT_NAME: 'alpha',
        AGENT_TOKEN: 'secret1',
        PORT: String(alpha.port),
        HUB_URL: hubUrl,
        INITIAL_ADMIN_USER: adminUser,
        INITIAL_ADMIN_PASSWORD: adminPassword
      }
    },
    {
      name: 'beta',
      host: beta.host,
      port: beta.port,
      env: {
        AGENT_NAME: 'beta',
        AGENT_TOKEN: 'secret2',
        PORT: String(beta.port),
        HUB_URL: hubUrl,
        INITIAL_ADMIN_USER: adminUser,
        INITIAL_ADMIN_PASSWORD: adminPassword
      }
    }
  ];
}

function spawnService(spec) {
  ensureDirs();
  const logPath = path.join(logDir, `${spec.name}.log`);
  const logFd = fs.openSync(logPath, 'a');
  const env = {
    ...process.env,
    ...spec.env,
    DATA_DIR: path.join(dataRoot, spec.name),
    DISABLE_LLM_CHECK: '1',
    AUTH_TEST_ENDPOINTS_ENABLED: '1'
  };
  fs.mkdirSync(env.DATA_DIR, { recursive: true });

  const child = spawn(resolvePythonBinary(), ['-m', 'agent.ai_agent'], {
    cwd: root,
    env,
    detached: true,
    stdio: ['ignore', logFd, logFd],
    shell: false,
    windowsHide: true
  });
  child.unref();
  return { pid: child.pid || 0, logPath };
}

async function main() {
  const specs = serviceSpecs();
  const tracked = readTrackedPids();
  const trackedByName = new Map(tracked.map((entry) => [entry.name, entry]));
  const started = [];

  for (const spec of specs) {
    const healthUrl = `http://${spec.host}:${spec.port}/health`;
    if (await isHealthy(healthUrl)) {
      continue;
    }
    const trackedEntry = trackedByName.get(spec.name);
    if (trackedEntry && isPidAlive(Number(trackedEntry.pid || 0))) {
      const healthy = await waitForHealth(healthUrl, 5000);
      if (healthy) {
        continue;
      }
    }

    if (spec.name === 'hub') {
      const spawned = spawnService(spec);
      started.push({ name: spec.name, pid: spawned.pid, logPath: spawned.logPath });
      const healthy = await waitForHealth(healthUrl);
      if (!healthy) {
        throw new Error(`Hub not ready at ${healthUrl} after ${healthTimeoutMs}ms. See ${spawned.logPath}.`);
      }
      continue;
    }
  }

  const workers = specs.filter((spec) => spec.name !== 'hub');
  const workerStarts = [];
  for (const spec of workers) {
    const healthUrl = `http://${spec.host}:${spec.port}/health`;
    if (await isHealthy(healthUrl)) continue;
    const trackedEntry = trackedByName.get(spec.name);
    if (trackedEntry && isPidAlive(Number(trackedEntry.pid || 0))) {
      const healthy = await waitForHealth(healthUrl, 5000);
      if (healthy) continue;
    }
    const spawned = spawnService(spec);
    workerStarts.push({ spec, ...spawned });
    started.push({ name: spec.name, pid: spawned.pid, logPath: spawned.logPath });
  }

  for (const startedWorker of workerStarts) {
    const healthUrl = `http://${startedWorker.spec.host}:${startedWorker.spec.port}/health`;
    const healthy = await waitForHealth(healthUrl);
    if (!healthy) {
      throw new Error(`${startedWorker.spec.name} not ready at ${healthUrl} after ${healthTimeoutMs}ms. See ${startedWorker.logPath}.`);
    }
  }

  const merged = [];
  for (const spec of specs) {
    const startedEntry = started.find((entry) => entry.name === spec.name);
    const trackedEntry = trackedByName.get(spec.name);
    if (startedEntry) {
      merged.push(startedEntry);
    } else if (trackedEntry && isPidAlive(Number(trackedEntry.pid || 0))) {
      merged.push(trackedEntry);
    } else {
      merged.push({ name: spec.name, pid: 0 });
    }
  }
  writeTrackedPids(merged);

  if (started.length === 0) {
    console.log('[ananta:e2e-backend] Reusing hub/worker backend.');
    return;
  }

  for (const entry of started) {
    console.log(`[ananta:e2e-backend] Started ${entry.name} pid=${entry.pid}${entry.logPath ? ` log=${entry.logPath}` : ''}`);
  }
}

main().catch((err) => {
  console.error(`[ananta:e2e-backend] ${err.message}`);
  process.exit(1);
});
