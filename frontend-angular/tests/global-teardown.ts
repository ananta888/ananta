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
}
