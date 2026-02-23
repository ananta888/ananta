const fs = require('node:fs');
const path = require('node:path');

const resultsPath = path.join(process.cwd(), 'test-results', 'results.json');
const outPath = path.join(process.cwd(), 'test-results', 'failure-summary.md');

function collectFailures(node, acc) {
  if (!node) return;
  if (Array.isArray(node.specs)) {
    for (const spec of node.specs) {
      const tests = Array.isArray(spec.tests) ? spec.tests : [];
      const failed = tests.some((t) =>
        (t.results || []).some((r) => ['failed', 'timedOut', 'interrupted'].includes(r.status))
      );
      if (failed) {
        acc.push({
          file: spec.file || '(unknown)',
          title: spec.title || '(untitled)',
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
  }
  lines.push('');
  writeSummary(lines.join('\n'));
  process.exit(0);
} catch (err) {
  writeSummary(`# E2E Failure Summary\n\nCould not parse results: ${String(err.message || err)}\n`);
  process.exit(0);
}

