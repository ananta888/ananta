const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');

const resultsRoot = path.resolve(process.cwd(), process.env.E2E_RESULTS_DIR?.trim() || 'test-results');
const outPath = path.join(resultsRoot, 'failure-summary.md');
const fallbackPaths = [
  path.join(process.cwd(), 'failure-summary.md'),
  path.join(os.tmpdir(), `ananta-e2e-failure-summary-${Date.now()}.md`),
];

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

function collectResultsFiles(dirPath, acc = []) {
  if (!fs.existsSync(dirPath)) return acc;
  for (const entry of fs.readdirSync(dirPath, { withFileTypes: true })) {
    const fullPath = path.join(dirPath, entry.name);
    if (entry.isDirectory()) {
      collectResultsFiles(fullPath, acc);
      continue;
    }
    if (entry.isFile() && entry.name === 'results.json') {
      acc.push(fullPath);
    }
  }
  return acc;
}

function writeSummary(content) {
  const attempted = [outPath, ...fallbackPaths];
  for (const targetPath of attempted) {
    try {
      ensureDir(targetPath);
      fs.writeFileSync(targetPath, content, 'utf8');
      console.log(`[e2e-summary] Wrote ${targetPath}`);
      return;
    } catch (error) {
      const code = String(error?.code || '');
      if (!['EACCES', 'EPERM', 'EROFS'].includes(code)) {
        throw error;
      }
      console.warn(`[e2e-summary] Cannot write ${targetPath} (${code}), trying fallback...`);
    }
  }
  console.warn('[e2e-summary] Could not persist summary file; printing summary to stdout:');
  console.log(content);
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

const resultsFiles = collectResultsFiles(resultsRoot);

if (!resultsFiles.length) {
  writeSummary('# E2E Failure Summary\n\nNo `results.json` found.\n');
  process.exit(0);
}

try {
  const failures = [];
  const parseErrors = [];
  for (const resultsPath of resultsFiles) {
    try {
      const parsed = JSON.parse(fs.readFileSync(resultsPath, 'utf8'));
      collectFailures(parsed, failures);
    } catch (err) {
      parseErrors.push(`${resultsPath}: ${String(err.message || err)}`);
    }
  }

  if (!failures.length && !parseErrors.length) {
    writeSummary('# E2E Failure Summary\n\nNo failing tests detected.\n');
    process.exit(0);
  }

  const lines = ['# E2E Failure Summary', '', `Result files scanned: ${resultsFiles.length}`, `Failing specs: ${failures.length}`, ''];
  if (parseErrors.length) {
    lines.push('Parse issues:', ...parseErrors.map((entry) => `- ${entry}`), '');
  }
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
