import fs from 'fs';
import path from 'path';

export default async function globalTeardown() {
  const pidFile = path.join(__dirname, '.pids.json');
  let startedLocalServices = false;
  if (!fs.existsSync(pidFile)) return;
  try {
    const data = JSON.parse(fs.readFileSync(pidFile, 'utf-8')) as { pid: number }[];
    startedLocalServices = data.length > 0;
    for (const p of data) {
      try { process.kill(p.pid, 'SIGTERM'); } catch {}
    }
  } catch {}
  try { fs.unlinkSync(pidFile); } catch {}
  const dataRoot = path.join(__dirname, '..', '..', 'data_test_e2e');
  if (startedLocalServices && fs.existsSync(dataRoot)) {
    try { fs.rmSync(dataRoot, { recursive: true, force: true }); } catch {}
  }
}
