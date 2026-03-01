import fs from 'fs';
import path from 'path';

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function terminatePid(pid: number) {
  try {
    process.kill(pid, 'SIGTERM');
  } catch {
    return;
  }

  const deadline = Date.now() + 4000;
  while (Date.now() < deadline) {
    try {
      process.kill(pid, 0);
      await sleep(120);
    } catch {
      return;
    }
  }

  try {
    process.kill(pid, 'SIGKILL');
  } catch {}
}

async function removeDirWithRetries(dirPath: string, attempts = 8, waitMs = 300) {
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

export default async function globalTeardown() {
  const pidFile = path.join(__dirname, '.pids.json');
  let startedLocalServices = false;

  if (fs.existsSync(pidFile)) {
    try {
      const data = JSON.parse(fs.readFileSync(pidFile, 'utf-8')) as { pid: number }[];
      startedLocalServices = data.length > 0;
      for (const p of data) {
        if (typeof p?.pid === 'number' && p.pid > 0) {
          await terminatePid(p.pid);
        }
      }
    } catch {}
    try {
      fs.unlinkSync(pidFile);
    } catch {}
  }

  const root = path.join(__dirname, '..', '..');
  const dataRoot = path.join(root, 'data_test_e2e');
  const artifactsRoot = path.join(root, 'frontend-angular', 'test-results');

  // Always cleanup test artifacts and ephemeral state; tests should be hermetic run-to-run.
  if (fs.existsSync(artifactsRoot)) {
    for (const name of fs.readdirSync(artifactsRoot)) {
      if (name.startsWith('.playwright-artifacts-') || name.includes('chromium') || name.includes('retry')) {
        try {
          await removeDirWithRetries(path.join(artifactsRoot, name));
        } catch {}
      }
    }
  }

  if (startedLocalServices && fs.existsSync(dataRoot)) {
    try {
      await removeDirWithRetries(dataRoot);
    } catch {}
  }
}
