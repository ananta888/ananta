#!/usr/bin/env node
/**
 * T13: pair-view-sync load-budget simulator
 *
 * Synthesises a 5-minute session of typical pair-view-sync traffic
 * and reports the bandwidth budget per delta kind. This is an
 * audit artifact, not a test: it produces a deterministic JSON
 * report that the team can review when adjusting throttles or
 * when adding new delta kinds.
 *
 * Run with:
 *   node scripts/pair-view-sync-load-budget.mjs
 *
 * Output:
 *   artifacts/domain/pair-view-sync-budget.json
 *
 * Inputs (configurable via env):
 *   PAIR_BUDGET_DURATION_S   default 300  (5 minutes)
 *   PAIR_BUDGET_PARTNERS     default 1    (peer count)
 *   PAIR_BUDGET_SEED         default 42   (RNG seed for determinism)
 */

import { writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, '..');

const DURATION_S = Number(process.env.PAIR_BUDGET_DURATION_S || 300);
const PARTNERS = Number(process.env.PAIR_BUDGET_PARTNERS || 1);
const SEED = Number(process.env.PAIR_BUDGET_SEED || 42);

// ── Seeded RNG (mulberry32) — keeps the run deterministic ──
function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const rng = mulberry32(SEED);

// ── Synthetic payload shapes (mirror pair-view-sync.types.ts) ──
function makeScrollDelta() {
  return {
    x: Math.floor(rng() * 4000),
    y: Math.floor(rng() * 12000),
  };
}
function makeCursorDelta() {
  return {
    line: Math.floor(rng() * 200),
    column: Math.floor(rng() * 80),
    x: Math.floor(rng() * 1920),
    y: Math.floor(rng() * 1080),
  };
}
function makeSelectionDelta() {
  return {
    start: Math.floor(rng() * 1000),
    end: Math.floor(rng() * 1000) + 1000,
  };
}
function makeRouteDelta() {
  const routes = ['/dashboard', '/artifacts', '/ai-snake', '/settings', '/teams', '/audit'];
  return {
    route: routes[Math.floor(rng() * routes.length)],
    queryParams: { ts: String(Math.floor(rng() * 1e9)) },
    activeTab: ['details', 'logs', 'review'][Math.floor(rng() * 3)],
  };
}
function makeArtifactDelta() {
  return {
    activeArtifactId: 'art-' + Math.floor(rng() * 1000).toString(16),
    activeArtifactHash: Buffer.from(String(rng())).toString('base64').slice(0, 22),
  };
}

// ── Wire envelope — mirror RelayEnvelope ──
function envelopeFor(kind, payload, version = '1.0.0') {
  const inner = {
    version,
    sessionId: 'sess-sim',
    senderUserId: 'owner-sim',
    seq: 0, // not used for sizing
    baseHash: '',
    newHash: '',
    kind,
    ops: [],
    createdAt: Date.now(),
    payload,
  };
  // STUB1:: mirror the stub encrypter from pair-view-sync.service.ts
  const plaintext = JSON.stringify(inner);
  const encrypted = `STUB1::${plaintext}`;
  return {
    kind,
    bytes: Buffer.byteLength(encrypted, 'utf8'),
    payload_bytes: Buffer.byteLength(plaintext, 'utf8'),
  };
}

// ── Simulate 5 minutes of pair-dev activity ──
// The traffic profile is calibrated to mirror what we see in
// real pair-dev sessions: a lot of cursor and scroll, some
// route/tab changes, occasional artifact selections, no chat
// (chat goes through a different channel).
const PROFILE = {
  // Average rate of each delta kind per second per partner.
  scroll_hz: 8.0,
  cursor_hz: 15.0,
  selection_hz: 0.4,
  route_hz: 0.05,     // route changes are rare
  artifact_hz: 0.1,   // artifact selection ~ every 10 s
  snapshot_hz: 0.0033, // ~ one snapshot per 5 minutes (the initial)
};

const KINDS = [
  { kind: 'scroll',    hz: PROFILE.scroll_hz,    make: makeScrollDelta },
  { kind: 'cursor',    hz: PROFILE.cursor_hz,    make: makeCursorDelta },
  { kind: 'selection', hz: PROFILE.selection_hz, make: makeSelectionDelta },
  { kind: 'route',     hz: PROFILE.route_hz,     make: makeRouteDelta },
  { kind: 'artifact',  hz: PROFILE.artifact_hz,  make: makeArtifactDelta },
  { kind: 'snapshot',  hz: PROFILE.snapshot_hz,  make: () => null }, // full state, no payload
];

function simulate() {
  const totals = new Map();
  const peakBytesPerS = new Array(DURATION_S).fill(0);

  for (const k of KINDS) {
    totals.set(k.kind, { count: 0, bytes: 0, payload_bytes: 0 });
  }

  // Poisson-process generation: inter-arrival mean = 1/hz seconds.
  for (const k of KINDS) {
    let t = 0;
    while (t < DURATION_S) {
      // expovariate equivalent: -ln(1-u) / lambda
      const u = Math.max(1e-9, rng());
      t += -Math.log(u) / k.hz;
      if (t >= DURATION_S) break;
      const env = envelopeFor(k.kind, k.kind === 'snapshot' ? null : k.make());
      const sec = Math.floor(t);
      peakBytesPerS[sec] += env.bytes;
      const agg = totals.get(k.kind);
      agg.count += 1;
      agg.bytes += env.bytes;
      agg.payload_bytes += env.payload_bytes;
    }
  }

  return { totals, peakBytesPerS };
}

function main() {
  const { totals, peakBytesPerS } = simulate();

  // Per-kind summary
  const perKind = [];
  let totalBytes = 0;
  let totalCount = 0;
  for (const [kind, agg] of totals) {
    perKind.push({
      kind,
      count: agg.count,
      total_bytes: agg.bytes,
      avg_bytes: agg.count > 0 ? Math.round(agg.bytes / agg.count) : 0,
      payload_bytes: agg.payload_bytes,
    });
    totalBytes += agg.bytes;
    totalCount += agg.count;
  }
  perKind.sort((a, b) => b.total_bytes - a.total_bytes);

  const peak = peakBytesPerS.reduce((a, b) => Math.max(a, b), 0);
  const meanBps = totalBytes / DURATION_S;

  const report = {
    schema: 'pair-view-sync-budget/v1',
    generated_at: 'deterministic', // marker: this file is reproducible
    inputs: {
      duration_s: DURATION_S,
      partners: PARTNERS,
      seed: SEED,
      traffic_profile: PROFILE,
    },
    totals: {
      deltas_sent: totalCount,
      bytes_sent: totalBytes,
      avg_bytes_per_s: Math.round(meanBps),
      peak_bytes_per_s: peak,
    },
    by_kind: perKind,
    // Budget gates: these are advisory numbers the team agreed
    // on. They are NOT enforced; they are reference values the
    // load-budget check is compared against.
    budget_gates: {
      // Hub relay target: stay under 16 KB/s average so the
      // hub SSE fanout is comfortable on a typical server.
      avg_bytes_per_s_max: 16_000,
      // Peak should stay under 64 KB/s to survive a single
      // scroll/cursor burst without backing off the throttle.
      peak_bytes_per_s_max: 64_000,
      // Per-delta: any single encrypted envelope should be
      // under MAX_ENCRYPTED_PAYLOAD_BYTES (8 KB). 8 KB is the
      // hard cap in the validator. We do NOT include snapshot
      // deltas in this gate; those are handled separately.
      per_delta_bytes_max: 8_192,
    },
    pass: {
      avg: meanBps <= 16_000,
      peak: peak <= 64_000,
    },
  };

  const outPath = join(REPO_ROOT, 'artifacts', 'domain', 'pair-view-sync-budget.json');
  mkdirSync(dirname(outPath), { recursive: true });
  writeFileSync(outPath, JSON.stringify(report, null, 2) + '\n', 'utf8');

  // Friendly stdout summary
  const lines = [];
  lines.push(`[pair-view-sync-budget] wrote ${outPath}`);
  lines.push(`  duration: ${DURATION_S}s, partners: ${PARTNERS}, seed: ${SEED}`);
  lines.push(`  deltas:   ${totalCount} (avg ${(totalCount / DURATION_S).toFixed(2)}/s)`);
  lines.push(`  bytes:    ${totalBytes} (avg ${Math.round(meanBps)} B/s, peak ${peak} B/s)`);
  for (const k of perKind) {
    lines.push(`    ${k.kind.padEnd(10)} ${String(k.count).padStart(7)} × ${k.avg_bytes}B avg = ${k.total_bytes}B`);
  }
  lines.push(`  pass:     avg=${report.pass.avg} peak=${report.pass.peak}`);
  for (const l of lines) console.log(l);
}

main();
