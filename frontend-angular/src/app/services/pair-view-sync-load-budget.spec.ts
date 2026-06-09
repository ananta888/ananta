/**
 * T13: pair-view-sync load-budget — reproducibility & gate tests.
 *
 * Verifies:
 *  - the artifact exists at artifacts/domain/pair-view-sync-budget.json
 *  - it has the documented schema version
 *  - it reports every delta kind from the source PROFILE
 *  - the per-kind byte budget stays under the per-delta cap
 *  - re-running with the same seed produces the same totals
 *    (the report is reproducible; no random timestamps, no
 *    random IDs in the JSON)
 */
import { describe, expect, it, beforeAll } from 'vitest';
import { readFileSync, existsSync, readFileSync as readFileSyncNode } from 'node:fs';
import { execSync } from 'node:child_process';
import { join } from 'node:path';

const ARTIFACT = '/home/krusty/ananta/artifacts/domain/pair-view-sync-budget.json';
const SCRIPT = '/home/krusty/ananta/scripts/pair-view-sync-load-budget.mjs';

describe('Pair-View-Sync load budget (T13)', () => {
  beforeAll(() => {
    if (!existsSync(ARTIFACT)) {
      execSync(`node ${SCRIPT}`, { stdio: 'inherit' });
    }
  });

  it('emits a JSON artifact at the documented path', () => {
    expect(existsSync(ARTIFACT)).toBe(true);
  });

  it('uses the documented schema version', () => {
    const r = JSON.parse(readFileSync(ARTIFACT, 'utf8'));
    expect(r.schema).toBe('pair-view-sync-budget/v1');
  });

  it('reports all six delta kinds', () => {
    const r = JSON.parse(readFileSync(ARTIFACT, 'utf8'));
    const kinds = new Set(r.by_kind.map((k: any) => k.kind));
    for (const k of ['cursor', 'scroll', 'selection', 'route', 'artifact', 'snapshot']) {
      expect(kinds.has(k)).toBe(true);
    }
  });

  it('stays under the per-delta 8 KB cap for every non-snapshot kind', () => {
    const r = JSON.parse(readFileSync(ARTIFACT, 'utf8'));
    for (const k of r.by_kind) {
      if (k.kind === 'snapshot') continue; // snapshots use a different cap
      // Allow up to 8 KB; the worst we expect is ~250 B.
      expect(k.avg_bytes).toBeLessThan(8192);
    }
  });

  it('stays under the 16 KB/s average gate', () => {
    const r = JSON.parse(readFileSync(ARTIFACT, 'utf8'));
    expect(r.pass.avg).toBe(true);
    expect(r.totals.avg_bytes_per_s).toBeLessThan(r.budget_gates.avg_bytes_per_s_max);
  });

  it('stays under the 64 KB/s peak gate', () => {
    const r = JSON.parse(readFileSync(ARTIFACT, 'utf8'));
    expect(r.pass.peak).toBe(true);
    expect(r.totals.peak_bytes_per_s).toBeLessThan(r.budget_gates.peak_bytes_per_s_max);
  });

  it('is reproducible: a second run with the same seed produces identical totals', () => {
    const before = JSON.parse(readFileSync(ARTIFACT, 'utf8'));
    execSync(`node ${SCRIPT}`, {
      cwd: '/home/krusty/ananta',
      env: { ...process.env, PAIR_BUDGET_SEED: String(before.inputs.seed) },
    });
    const after = JSON.parse(readFileSync(ARTIFACT, 'utf8'));
    expect(after.totals.deltas_sent).toBe(before.totals.deltas_sent);
    expect(after.totals.bytes_sent).toBe(before.totals.bytes_sent);
    expect(after.totals.avg_bytes_per_s).toBe(before.totals.avg_bytes_per_s);
    expect(after.totals.peak_bytes_per_s).toBe(before.totals.peak_bytes_per_s);
    for (let i = 0; i < after.by_kind.length; i++) {
      expect(after.by_kind[i].count).toBe(before.by_kind[i].count);
      expect(after.by_kind[i].total_bytes).toBe(before.by_kind[i].total_bytes);
    }
  });
});
