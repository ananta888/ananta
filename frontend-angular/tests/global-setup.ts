import { ChildProcess, spawn } from 'child_process';
import fs from 'fs';
import path from 'path';

type ProcInfo = { name: string; port: number; pid: number };

async function waitForHealth(url: string, timeoutMs = 60000) {
  const start = Date.now();
  let attempts = 0;
  while (Date.now() - start < timeoutMs) {
    attempts++;
    try {
      const res = await fetch(url);
      if (res.ok) return;
    } catch {}
    // Exponential backoff start 500ms -> 2s max
    const wait = Math.min(2000, 500 * (attempts / 2));
    await new Promise(r => setTimeout(r, wait));
  }
  throw new Error(`Timeout waiting for ${url} after ${timeoutMs}ms`);
}

function trySpawnPython(args: string[], env: NodeJS.ProcessEnv, cwd: string): ChildProcess {
  try {
    return spawn('python', args, { cwd, env, stdio: 'inherit' });
  } catch {
    return spawn('python3', args, { cwd, env, stdio: 'inherit' });
  }
}

async function ensurePip(root: string) {
  if (process.env.ANANTA_SKIP_PIP === '1') return;
  await new Promise<void>((resolve) => {
    const child = trySpawnPython(['-m', 'pip', 'install', '-r', 'requirements.txt'], process.env, root);
    child.on('exit', () => resolve());
    child.on('error', () => resolve());
  });
}

export default async function globalSetup() {
  const root = path.resolve(__dirname, '..', '..');
  await ensurePip(root);

  const existingPidFile = path.join(__dirname, '.pids.json');
  if (fs.existsSync(existingPidFile)) {
    try {
      const data = JSON.parse(fs.readFileSync(existingPidFile, 'utf-8')) as { pid: number }[];
      for (const p of data) {
        try { process.kill(p.pid, 'SIGTERM'); } catch {}
      }
    } catch {}
    try { fs.unlinkSync(existingPidFile); } catch {}
  }

  const procs: ProcInfo[] = [];

  const dataRoot = path.join(root, 'data_test_e2e');
  if (fs.existsSync(dataRoot)) {
    fs.rmSync(dataRoot, { recursive: true, force: true });
  }
  fs.mkdirSync(dataRoot, { recursive: true });

  const toStart = [
    { name: 'hub', port: 5000, env: { ROLE: 'hub', AGENT_NAME: 'hub', AGENT_TOKEN: 'hubsecret', PORT: '5000' } },
    { name: 'alpha', port: 5001, env: { AGENT_NAME: 'alpha', AGENT_TOKEN: 'secret1', PORT: '5001' } },
    { name: 'beta', port: 5002, env: { AGENT_NAME: 'beta', AGENT_TOKEN: 'secret2', PORT: '5002' } }
  ];

  // Start each agent if not already bound on its port
  for (const svc of toStart) {
    let already = false;
    try {
      await waitForHealth(`http://localhost:${svc.port}/health`, 2000);
      already = true;
    } catch {}
    if (already) continue;
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
    // Längeres Warten für Cold Starts (Docker hub healthcheck Äquivalent)
    await waitForHealth(`http://localhost:${svc.port}/health`, 60000);
  }

  // Persist spawned PIDs for teardown
  const pidFile = path.join(__dirname, '.pids.json');
  fs.writeFileSync(pidFile, JSON.stringify(procs, null, 2));
}
