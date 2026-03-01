const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..', '..');
const fix = process.argv.includes('--fix');

function listEntriesSafe(dir) {
  try {
    return fs.readdirSync(dir);
  } catch {
    return [];
  }
}

function removeDirSafe(dir) {
  try {
    fs.rmSync(dir, { recursive: true, force: true });
    return true;
  } catch {
    return false;
  }
}

function removeFileSafe(file) {
  try {
    fs.rmSync(file, { force: true });
    return true;
  } catch {
    return false;
  }
}

const pidFile = path.join(root, 'frontend-angular', 'tests', '.pids.json');
const dataRoot = path.join(root, 'data_test_e2e');
const testResults = path.join(root, 'frontend-angular', 'test-results');

function collectChecks() {
  const dataEntries = fs.existsSync(dataRoot) ? listEntriesSafe(dataRoot) : [];
  const staleArtifacts = fs.existsSync(testResults)
    ? listEntriesSafe(testResults).filter(
        (n) => n.startsWith('.playwright-artifacts-') || n.includes('chromium') || n.includes('retry'),
      )
    : [];
  return [
    {
      name: 'pid_file_absent',
      ok: !fs.existsSync(pidFile),
      detail: pidFile,
      fix: () => removeFileSafe(pidFile),
    },
    {
      name: 'data_test_e2e_clean',
      ok: !fs.existsSync(dataRoot) || dataEntries.length === 0,
      detail: `${dataRoot} entries=${dataEntries.length}`,
      fix: () => removeDirSafe(dataRoot),
    },
    {
      name: 'stale_playwright_artifacts_clean',
      ok: staleArtifacts.length === 0,
      detail: `${testResults} stale=${staleArtifacts.length}`,
      fix: () => staleArtifacts.every((n) => removeDirSafe(path.join(testResults, n))),
    },
  ];
}

let checks = collectChecks();
let failed = checks.filter((c) => !c.ok);
if (failed.length && fix) {
  for (const c of failed) {
    try {
      c.fix && c.fix();
    } catch {}
  }
  checks = collectChecks();
  failed = checks.filter((c) => !c.ok);
}

for (const c of checks) {
  const status = c.ok ? 'OK' : 'FAIL';
  console.log(`[cleanup-selftest] ${status} ${c.name} :: ${c.detail}`);
}

if (failed.length) {
  console.error(`[cleanup-selftest] ${failed.length} check(s) failed.`);
  process.exit(1);
}

console.log('[cleanup-selftest] all checks passed.');
