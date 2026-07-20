#!/usr/bin/env node
/** Deterministic capacity model for the predeclared visual spike scenarios. */
import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';

const args = process.argv.slice(2);
const outputFlag = args.indexOf('--output');
const output = resolve(outputFlag >= 0 ? args[outputFlag + 1] : 'artifacts/domain/semantic-visual-benchmark.json');
const spikePath = resolve('artifacts/domain/semantic-visual-feasibility.json');
const spikeRaw = readFileSync(spikePath, 'utf8');
const spike = JSON.parse(spikeRaw);
const scenarios = ['static_ui', 'text_scroll', 'cursor_animation', 'camera', 'scene_cut', 'strong_noise'];
const windows = [2, 10, 20];
const participants = [2, 10];

function percentile(values, fraction) {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.ceil(sorted.length * fraction) - 1)];
}
function round(value) { return Number(value.toFixed(3)); }

const rows = [];
for (const participantCount of participants) {
  for (const windowSeconds of windows) {
    const samples = [];
    for (const scenario of scenarios) {
      const source = spike.scenarios.find(item => item.scenario === scenario);
      if (!source) throw new Error(`missing spike scenario ${scenario}`);
      const semanticBytes = source.semantic.bytes;
      const ordinaryBytes = source.ordinary.bytes;
      const constrained = source.networks.find(item => item.profile === 'constrained');
      for (let second = 0; second < windowSeconds; second += 1) {
        // Deterministic concurrency overhead; no media content or wall clock is retained.
        const phase = 1 + ((second * 17 + participantCount * 7 + scenario.length) % 11) / 100;
        samples.push({
          semanticBytes: semanticBytes * participantCount * phase / 2,
          ordinaryBytes: ordinaryBytes * participantCount / 2,
          latencyMs: constrained.semantic.p95_latency_ms * (1 + participantCount / 50) * phase,
          workingBytes: source.semantic.memory_bytes * participantCount,
        });
      }
    }
    const ratios = samples.map(item => item.semanticBytes / item.ordinaryBytes);
    const latencies = samples.map(item => item.latencyMs);
    const bursts = samples.map(item => item.semanticBytes);
    rows.push({
      participants: participantCount,
      window_seconds: windowSeconds,
      samples: samples.length,
      byte_ratio: { mean: round(ratios.reduce((a, b) => a + b, 0) / ratios.length), p95: round(percentile(ratios, 0.95)) },
      latency_ms: { mean: round(latencies.reduce((a, b) => a + b, 0) / latencies.length), p95: round(percentile(latencies, 0.95)) },
      worst_burst_bytes: Math.round(Math.max(...bursts)),
      maximum_working_bytes: Math.max(...samples.map(item => item.workingBytes)),
    });
  }
}

const report = {
  schema: 'ananta.semantic-visual-benchmark.v1',
  benchmark_version: 'deterministic-capacity-model/1.0.0',
  source_spike_sha256: createHash('sha256').update(spikeRaw).digest('hex'),
  scenarios,
  windows_seconds: windows,
  participant_counts: participants,
  units: { byte_ratio: 'semantic_bytes/ordinary_bytes', latency: 'milliseconds', burst: 'bytes', memory: 'bytes' },
  rows,
  release_candidate: false,
  release_candidate_reason: spike.decision?.activation_allowed === true ? 'benchmark_requires_gate_evaluation' : 'spike_no_go',
};
mkdirSync(dirname(output), { recursive: true });
writeFileSync(output, `${JSON.stringify(report, null, 2)}\n`, 'utf8');
process.stdout.write(`${JSON.stringify({ output, rows: rows.length, release_candidate: false })}\n`);
