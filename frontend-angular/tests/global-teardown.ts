import fs from 'fs';
import path from 'path';

export default async function globalTeardown() {
  const pidFile = path.join(__dirname, '.pids.json');
  if (!fs.existsSync(pidFile)) return;
  try {
    const data = JSON.parse(fs.readFileSync(pidFile, 'utf-8')) as { pid: number }[];
    for (const p of data) {
      try { process.kill(p.pid, 'SIGTERM'); } catch {}
    }
  } catch {}
  try { fs.unlinkSync(pidFile); } catch {}
  const dataRoot = path.join(__dirname, '..', '..', 'data_test_e2e');
  if (fs.existsSync(dataRoot)) {
    try { fs.rmSync(dataRoot, { recursive: true, force: true }); } catch {}
  }
}
