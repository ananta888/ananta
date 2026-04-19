const fs = require('node:fs');
const path = require('node:path');

const resultsPath = path.join(process.cwd(), 'test-results', 'results.json');
const outPath = path.join(process.cwd(), 'test-results', 'failure-summary.md');

function collectFailures(node, acc) {
  if (!node) return;
  if (Array.isArray(node.specs)) {
    for (const spec of node.specs) {
      const tests = Array.isArray(spec.tests) ? spec.tests : [];
      const failedResults = tests.flatMap((t) =>
        (t.results || [])
          .filter((r) => ['failed', 'timedOut', 'interrupted'].includes(r.status))
          .map((r) => ({
            projectName: t.projectName || '(unknown project)',
            status: r.status,
            error: r.error?.message || r.errors?.[0]?.message || '',
            retry: r.retry,
          }))
      );
      if (failedResults.length) {
        acc.push({
          file: spec.file || '(unknown)',
          title: spec.title || '(untitled)',
          failures: failedResults,
        });
      }
    }
  }
  if (Array.isArray(node.suites)) {
    for (const suite of node.suites) {
      collectFailures(suite, acc);
    }
  }
}

function ensureDir(filePath) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
}

function writeSummary(content) {
  ensureDir(outPath);
  fs.writeFileSync(outPath, content, 'utf8');
  console.log(`[e2e-summary] Wrote ${outPath}`);
}

function formatError(value) {
  return String(value || '')
    .replace(/\x1b\][^\x07]*(?:\x07|\x1b\\)/g, '')
    .replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .slice(0, 3)
    .join(' ');
}

if (!fs.existsSync(resultsPath)) {
  writeSummary('# E2E Failure Summary\n\nNo `results.json` found.\n');
  process.exit(0);
}

try {
  const parsed = JSON.parse(fs.readFileSync(resultsPath, 'utf8'));
  const failures = [];
  collectFailures(parsed, failures);

  if (!failures.length) {
    writeSummary('# E2E Failure Summary\n\nNo failing tests detected.\n');
    process.exit(0);
  }

  const lines = ['# E2E Failure Summary', '', `Failing specs: ${failures.length}`, ''];
  for (const f of failures) {
    lines.push(`- \`${f.file}\` - ${f.title}`);
    for (const result of f.failures.slice(0, 3)) {
      const retry = Number.isInteger(result.retry) ? ` retry=${result.retry}` : '';
      const error = formatError(result.error);
      lines.push(`  - ${result.projectName}: ${result.status}${retry}${error ? ` - ${error}` : ''}`);
    }
  }
  lines.push('');
  writeSummary(lines.join('\n'));
  process.exit(0);
} catch (err) {
  writeSummary(`# E2E Failure Summary\n\nCould not parse results: ${String(err.message || err)}\n`);
  process.exit(0);
}
