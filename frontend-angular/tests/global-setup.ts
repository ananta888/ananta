import { ChildProcess, spawn, spawnSync } from 'child_process';
import dns from 'dns/promises';
import fs from 'fs';
import path from 'path';

type ProcInfo = { name: string; port: number; pid: number };

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function parseServiceUrl(url: string) {
  const parsed = new URL(url);
  return {
    host: parsed.hostname === 'localhost' ? '127.0.0.1' : parsed.hostname,
    port: Number(parsed.port || (parsed.protocol === 'https:' ? 443 : 80))
  };
}

function localhostCandidateUrls(rawUrl: string, paths: string[] = ['']): string[] {
  const parsed = new URL(rawUrl);
  const base = rawUrl.replace(/\/+$/, '');
  const normalizedPaths = paths.map((entry) => (entry.startsWith('/') ? entry : `/${entry}`));
  const out = new Set<string>(normalizedPaths.map((entry) => `${base}${entry === '/' ? '' : entry}`));

  if (parsed.hostname === 'localhost') {
    const ipBase = `${parsed.protocol}//127.0.0.1${parsed.port ? `:${parsed.port}` : ''}`;
    for (const entry of normalizedPaths) {
      out.add(`${ipBase}${entry === '/' ? '' : entry}`);
    }
  }

  return [...out];
}

async function waitForHealth(url: string, timeoutMs = 120000) {
  const start = Date.now();
  let attempts = 0;
  const candidateUrls = localhostCandidateUrls(url);
  while (Date.now() - start < timeoutMs) {
    attempts++;
    for (const candidateUrl of candidateUrls) {
      try {
        const res = await fetch(candidateUrl);
        if (res.ok) return;
      } catch {}
    }
    const wait = Math.min(5000, 500 * (attempts / 2));
    await sleep(wait);
  }
  throw new Error(`Timeout waiting for ${candidateUrls.join(', ')} after ${timeoutMs}ms`);
}

type ServiceSpec = {
  name: string;
  port: number;
  host: string;
  env: Record<string, string>;
};

async function waitForFrontendReady(baseUrl: string, timeoutMs = 120000) {
  const start = Date.now();
  let attempts = 0;
  const candidateUrls = await frontendCandidateUrls(baseUrl);

  while (Date.now() - start < timeoutMs) {
    attempts++;
    for (const url of candidateUrls) {
      try {
        const res = await fetch(url);
        if (res.ok) return;
      } catch {}
    }

    const wait = Math.min(4000, 400 + attempts * 120);
    await sleep(wait);
  }
  throw new Error(`Timeout waiting for frontend readiness at ${candidateUrls.join(', ')} after ${timeoutMs}ms`);
}

async function frontendCandidateUrls(baseUrl: string) {
  const normalizedBase = baseUrl.replace(/\/+$/, '');
  const urls = new Set<string>(localhostCandidateUrls(normalizedBase, ['/', '/login']));

  try {
    const parsed = new URL(normalizedBase);
    if (!['localhost', '127.0.0.1'].includes(parsed.hostname)) {
      const resolved = await dns.lookup(parsed.hostname);
      const ipBase = `${parsed.protocol}//${resolved.address}${parsed.port ? `:${parsed.port}` : ''}`;
      urls.add(`${ipBase}/`);
      urls.add(`${ipBase}/login`);
    }
  } catch {}

  return [...urls];
}

async function isHealthy(url: string) {
  for (const candidateUrl of localhostCandidateUrls(url)) {
    try {
      const res = await fetch(candidateUrl);
      if (res.ok) return true;
    } catch {}
  }
  return false;
}

async function getAdminToken(hubUrl: string, username: string, password: string): Promise<string | null> {
  try {
    const res = await fetch(`${hubUrl.replace(/\/+$/, '')}/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    if (!res.ok) return null;
    const body = await res.json().catch(() => ({} as any));
    return body?.data?.access_token || body?.access_token || null;
  } catch {
    return null;
  }
}

async function unwrapResponseList(res: Response): Promise<any[]> {
  const body = await res.json().catch(() => ({} as any));
  if (Array.isArray(body)) return body;
  if (Array.isArray(body?.data)) return body.data;
  if (Array.isArray(body?.items)) return body.items;
  return [];
}

async function ensureDeterministicScrumSeed(hubUrl: string, token: string, teamName: string) {
  const headers = { Authorization: `Bearer ${token}` };
  const teamsRes = await fetch(`${hubUrl.replace(/\/+$/, '')}/teams`, { headers });
  if (teamsRes.ok) {
    const teams = await unwrapResponseList(teamsRes);
    for (const team of teams) {
      const id = String(team?.id || '').trim();
      const name = String(team?.name || '').toLowerCase();
      if (!id) continue;
      if (!name.startsWith('e2e seed scrum team')) continue;
      try {
        await fetch(`${hubUrl.replace(/\/+$/, '')}/teams/${id}`, { method: 'DELETE', headers });
      } catch {}
    }
  }

  const seedRes = await fetch(`${hubUrl.replace(/\/+$/, '')}/teams/setup-scrum`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: teamName }),
  });
  if (!seedRes.ok) {
    const body = await seedRes.text().catch(() => '');
    throw new Error(`Deterministic scrum seed failed: status=${seedRes.status} body=${body}`);
  }
}

async function removeDirWithRetries(dirPath: string, attempts = 6, waitMs = 400) {
  for (let i = 0; i < attempts; i += 1) {
    try {
      fs.rmSync(dirPath, { recursive: true, force: true });
      return;
    } catch (err: any) {
      const code = err?.code;
      if ((code === 'EBUSY' || code === 'EPERM') && i < attempts - 1) {
        await sleep(waitMs * (i + 1));
        continue;
      }
      throw err;
    }
  }
}

function resolvePythonBinary(): string {
  const explicit = process.env.PYTHON_BIN?.trim();
  if (explicit) return explicit;

  const repoVenvPython = path.resolve(__dirname, '..', '..', '.venv', 'bin', 'python3');
  if (fs.existsSync(repoVenvPython)) return repoVenvPython;

  for (const candidate of ['python3', 'python']) {
    const probe = spawnSync(candidate, ['--version'], { stdio: 'ignore' });
    if (probe.status === 0) return candidate;
  }

  return 'python3';
}

function trySpawnPython(args: string[], env: NodeJS.ProcessEnv, cwd: string): ChildProcess {
  return spawn(resolvePythonBinary(), args, { cwd, env, stdio: 'inherit' });
}

async function startService(
  svc: ServiceSpec,
  envBase: NodeJS.ProcessEnv,
  root: string,
  dataRoot: string,
  healthTimeoutMs: number
): Promise<ProcInfo> {
  const dataDir = path.join(dataRoot, svc.name);
  fs.mkdirSync(dataDir, { recursive: true });
  const env = {
    ...envBase,
    ...svc.env,
    DATA_DIR: dataDir,
    DISABLE_LLM_CHECK: '1',
    AUTH_TEST_ENDPOINTS_ENABLED: '1'
  };

  const child = trySpawnPython(['-m', 'agent.ai_agent'], env, root);
  const procInfo = { name: svc.name, port: svc.port, pid: child.pid ?? -1 };
  await waitForHealth(`http://${svc.host}:${svc.port}/health`, healthTimeoutMs);
  return procInfo;
}

async function ensurePip(root: string) {
  if (process.env.ANANTA_E2E_INSTALL_DEPS !== '1') return;
  if (process.env.ANANTA_SKIP_PIP === '1' || fs.existsSync('/.dockerenv')) return;
  await new Promise<void>((resolve) => {
    const child = trySpawnPython(['-m', 'pip', 'install', '-r', 'requirements.txt'], process.env, root);
    child.on('exit', () => resolve());
    child.on('error', () => resolve());
  });
}

export default async function globalSetup() {
  const root = path.resolve(__dirname, '..', '..');
  await ensurePip(root);
  const e2ePort = Number(process.env.E2E_PORT || '4200');
  const frontendBaseUrl = process.env.E2E_FRONTEND_URL || `http://127.0.0.1:${e2ePort}`;
  const forceIsolated = process.env.ANANTA_E2E_FORCE_ISOLATED === '1';
  const allowExisting = !forceIsolated && process.env.ANANTA_E2E_USE_EXISTING === '1';

  const existingPidFile = path.join(__dirname, '.pids.json');
  if (fs.existsSync(existingPidFile)) {
    try {
      const data = JSON.parse(fs.readFileSync(existingPidFile, 'utf-8')) as { pid: number }[];
      for (const p of data) {
        try {
          process.kill(p.pid, 'SIGTERM');
        } catch {}
      }
    } catch {}
    try {
      fs.unlinkSync(existingPidFile);
    } catch {}
  }

  const hubUrl = process.env.E2E_HUB_URL || 'http://127.0.0.1:5500';
  const alphaUrl = process.env.E2E_ALPHA_URL || 'http://127.0.0.1:5501';
  const betaUrl = process.env.E2E_BETA_URL || 'http://127.0.0.1:5502';
  const adminUser = process.env.E2E_ADMIN_USER || 'admin';
  const adminPassword = process.env.E2E_ADMIN_PASSWORD || 'AnantaAdminPassword123!';
  const hub = parseServiceUrl(hubUrl);
  const alpha = parseServiceUrl(alphaUrl);
  const beta = parseServiceUrl(betaUrl);

  const toStart: ServiceSpec[] = [
    {
      name: 'hub',
      port: hub.port,
      host: hub.host,
      env: {
        ROLE: 'hub',
        AGENT_NAME: 'hub',
        AGENT_TOKEN: 'hubsecret',
        PORT: String(hub.port),
        INITIAL_ADMIN_USER: adminUser,
        INITIAL_ADMIN_PASSWORD: adminPassword,
      }
    },
    {
      name: 'alpha',
      port: alpha.port,
      host: alpha.host,
      env: {
        AGENT_NAME: 'alpha',
        AGENT_TOKEN: 'secret1',
        PORT: String(alpha.port),
        HUB_URL: hubUrl,
        INITIAL_ADMIN_USER: adminUser,
        INITIAL_ADMIN_PASSWORD: adminPassword,
      }
    },
    {
      name: 'beta',
      port: beta.port,
      host: beta.host,
      env: {
        AGENT_NAME: 'beta',
        AGENT_TOKEN: 'secret2',
        PORT: String(beta.port),
        HUB_URL: hubUrl,
        INITIAL_ADMIN_USER: adminUser,
        INITIAL_ADMIN_PASSWORD: adminPassword,
      }
    }
  ];

  const running = new Set<string>();
  for (const svc of toStart) {
    if (await isHealthy(`http://${svc.host}:${svc.port}/health`)) {
      running.add(svc.name);
    }
  }

  if (running.size > 0 && !allowExisting) {
    const entries = toStart
      .filter((svc) => running.has(svc.name))
      .map((svc) => `${svc.name}=${svc.host}:${svc.port}`)
      .join(', ');
    throw new Error(
      `Detected already running services (${entries}). ` +
        'E2E tests require isolated backend state by default. ' +
        'Stop external services or set ANANTA_E2E_USE_EXISTING=1 to reuse them.'
    );
  }

  const dataRoot = path.join(root, 'data_test_e2e');
  if (!allowExisting && running.size === 0) {
    if (fs.existsSync(dataRoot)) {
      await removeDirWithRetries(dataRoot);
    }
    fs.mkdirSync(dataRoot, { recursive: true });
  } else if (!fs.existsSync(dataRoot)) {
    fs.mkdirSync(dataRoot, { recursive: true });
  }

  const procs: ProcInfo[] = [];
  const healthTimeoutMs = Number(process.env.E2E_SERVICE_HEALTH_TIMEOUT_MS || '120000');
  const servicesToSpawn = toStart.filter((svc) => !running.has(svc.name));

  if (servicesToSpawn.length > 0 && fs.existsSync('/.dockerenv')) {
    const missing = servicesToSpawn.map((svc) => `${svc.name}=${svc.host}:${svc.port}`).join(', ');
    throw new Error(`Services not found (${missing}), but we are in Docker. Cannot spawn local processes.`);
  }

  for (const svc of toStart) {
    if (running.has(svc.name)) {
      console.log(`Service ${svc.name} already running on ${svc.host}:${svc.port}`);
    }
  }

  const hubSpec = servicesToSpawn.find((svc) => svc.name === 'hub');
  if (hubSpec) {
    procs.push(await startService(hubSpec, process.env, root, dataRoot, healthTimeoutMs));
  }

  const workerSpecs = servicesToSpawn.filter((svc) => svc.name !== 'hub');
  const workerProcs = await Promise.all(
    workerSpecs.map((svc) => startService(svc, process.env, root, dataRoot, healthTimeoutMs))
  );
  procs.push(...workerProcs);

  const pidFile = path.join(__dirname, '.pids.json');
  fs.writeFileSync(pidFile, JSON.stringify(procs, null, 2));

  const frontendWaitMs = Number(process.env.E2E_FRONTEND_WAIT_MS || '120000');
  await waitForFrontendReady(frontendBaseUrl, frontendWaitMs);

  if (process.env.E2E_DETERMINISTIC_SCRUM_SEED === '1') {
    const token = await getAdminToken(hubUrl, adminUser, adminPassword);
    if (!token) {
      throw new Error('Could not acquire admin token for deterministic scrum seed');
    }
    const seedTeamName = process.env.E2E_SCRUM_SEED_TEAM_NAME || 'E2E Seed Scrum Team';
    await ensureDeterministicScrumSeed(hubUrl, token, seedTeamName);
  }
}
