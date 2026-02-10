import { ChildProcess, spawn } from 'child_process';
import fs from 'fs';
import path from 'path';

type ProcInfo = { name: string; port: number; pid: number };

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function parseServiceUrl(url: string) {
  const parsed = new URL(url);
  return {
    host: parsed.hostname,
    port: Number(parsed.port || (parsed.protocol === 'https:' ? 443 : 80))
  };
}

async function waitForHealth(url: string, timeoutMs = 120000) {
  const start = Date.now();
  let attempts = 0;
  while (Date.now() - start < timeoutMs) {
    attempts++;
    try {
      const res = await fetch(url);
      if (res.ok) return;
    } catch {}
    const wait = Math.min(5000, 500 * (attempts / 2));
    await sleep(wait);
  }
  throw new Error(`Timeout waiting for ${url} after ${timeoutMs}ms`);
}

async function isHealthy(url: string) {
  try {
    const res = await fetch(url);
    return res.ok;
  } catch {
    return false;
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

function trySpawnPython(args: string[], env: NodeJS.ProcessEnv, cwd: string): ChildProcess {
  try {
    return spawn('python', args, { cwd, env, stdio: 'inherit' });
  } catch {
    return spawn('python3', args, { cwd, env, stdio: 'inherit' });
  }
}

async function ensurePip(root: string) {
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
  const allowExisting = process.env.ANANTA_E2E_USE_EXISTING === '1';

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

  const hubUrl = process.env.E2E_HUB_URL || 'http://localhost:5500';
  const alphaUrl = process.env.E2E_ALPHA_URL || 'http://localhost:5501';
  const betaUrl = process.env.E2E_BETA_URL || 'http://localhost:5502';
  const hub = parseServiceUrl(hubUrl);
  const alpha = parseServiceUrl(alphaUrl);
  const beta = parseServiceUrl(betaUrl);

  const toStart = [
    { name: 'hub', port: hub.port, host: hub.host, env: { ROLE: 'hub', AGENT_NAME: 'hub', AGENT_TOKEN: 'hubsecret', PORT: String(hub.port) } },
    { name: 'alpha', port: alpha.port, host: alpha.host, env: { AGENT_NAME: 'alpha', AGENT_TOKEN: 'secret1', PORT: String(alpha.port), HUB_URL: hubUrl } },
    { name: 'beta', port: beta.port, host: beta.host, env: { AGENT_NAME: 'beta', AGENT_TOKEN: 'secret2', PORT: String(beta.port), HUB_URL: hubUrl } }
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
  if (!allowExisting) {
    if (fs.existsSync(dataRoot)) {
      await removeDirWithRetries(dataRoot);
    }
    fs.mkdirSync(dataRoot, { recursive: true });
  } else if (!fs.existsSync(dataRoot)) {
    fs.mkdirSync(dataRoot, { recursive: true });
  }

  const procs: ProcInfo[] = [];
  for (const svc of toStart) {
    const already = running.has(svc.name);
    if (already) {
      console.log(`Service ${svc.name} already running on ${svc.host}:${svc.port}`);
      continue;
    }
    if (fs.existsSync('/.dockerenv')) {
      throw new Error(`Service ${svc.name} not found on ${svc.host}:${svc.port}, but we are in Docker. Cannot spawn local processes.`);
    }

    const dataDir = path.join(dataRoot, svc.name);
    fs.mkdirSync(dataDir, { recursive: true });
    const env = {
      ...process.env,
      ...svc.env,
      DATA_DIR: dataDir,
      DISABLE_LLM_CHECK: '1'
    };

    const child = trySpawnPython(['-m', 'agent.ai_agent'], env, root);
    procs.push({ name: svc.name, port: svc.port, pid: child.pid ?? -1 });
    await waitForHealth(`http://${svc.host}:${svc.port}/health`, 120000);
  }

  const pidFile = path.join(__dirname, '.pids.json');
  fs.writeFileSync(pidFile, JSON.stringify(procs, null, 2));
}
