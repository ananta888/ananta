const { spawn } = require('node:child_process');

const env = { ...process.env };
env.ANANTA_E2E_USE_EXISTING = '1';
env.E2E_REUSE_SERVER = '1';
env.E2E_REPORTER_MODE = env.E2E_REPORTER_MODE || 'compact';
env.E2E_FRONTEND_URL = env.E2E_FRONTEND_URL || 'http://localhost:4200';
env.E2E_HUB_URL = env.E2E_HUB_URL || 'http://localhost:5000';
env.E2E_ALPHA_URL = env.E2E_ALPHA_URL || 'http://localhost:5001';
env.E2E_BETA_URL = env.E2E_BETA_URL || 'http://localhost:5002';

delete env.NO_COLOR;
delete env.FORCE_COLOR;

const forwardedArgs = process.argv.slice(2);
const playwrightCli = require.resolve('@playwright/test/cli');
const child = spawn(process.execPath, [playwrightCli, 'test', ...forwardedArgs], {
  cwd: process.cwd(),
  env,
  stdio: 'inherit',
  shell: false,
  windowsHide: true
});

child.on('exit', (code, signal) => process.exit(signal ? 1 : (code ?? 1)));
child.on('error', (err) => {
  console.error(`[ananta:e2e-compose] Failed to start Playwright: ${err.message}`);
  process.exit(1);
});
