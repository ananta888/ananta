#!/usr/bin/env node

/**
 * Reproducible observe-only semantic-visual feasibility spike.
 *
 * No model is involved. Ordinary uses VP9/WebM through ffmpeg; Semantic uses
 * deterministic 16x16 change tiles compressed with standard DEFLATE. Both see
 * the exact same generated grayscale frames and deterministic network models.
 */

import { spawnSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { performance } from 'node:perf_hooks';
import { deflateRawSync } from 'node:zlib';

const WIDTH = 320;
const HEIGHT = 180;
const FPS = 12;
const FRAME_COUNT = 24;
const BLOCK = 16;
const CHANGE_THRESHOLD = 6;
const SCENE_CUT_THRESHOLD = 55;
const SEED = 0x5eed1234;

export const THRESHOLDS = Object.freeze({
  safety: Object.freeze({ minimum_psnr_db: 30, maximum_drift_mae: 6 }),
  go: Object.freeze({
    suitable_scenario_byte_ratio: 0.70,
    maximum_camera_byte_ratio: 1.10,
    maximum_noise_byte_ratio: 1.25,
    maximum_cpu_ratio: 2.0,
    maximum_memory_bytes: 64 * 1024 * 1024,
    maximum_latency_ratio: 1.25,
    minimum_beneficial_scenarios: 3,
  }),
  no_go: Object.freeze({
    aggregate_byte_ratio: 1.25,
    maximum_cpu_ratio: 3.0,
    maximum_memory_bytes: 128 * 1024 * 1024,
    maximum_latency_ratio: 1.75,
    minimum_beneficial_scenarios: 2,
  }),
});

const NETWORKS = Object.freeze([
  { id: 'lan', bandwidth_bps: 50_000_000, base_rtt_ms: 20 },
  { id: 'wan', bandwidth_bps: 5_000_000, base_rtt_ms: 80 },
  { id: 'constrained', bandwidth_bps: 1_000_000, base_rtt_ms: 150 },
]);

const SCENARIOS = Object.freeze([
  'static_ui', 'text_scroll', 'cursor_animation', 'camera', 'scene_cut', 'strong_noise',
]);

function rng(seed) {
  let state = seed >>> 0;
  return () => {
    state ^= state << 13;
    state ^= state >>> 17;
    state ^= state << 5;
    return (state >>> 0) / 0x1_0000_0000;
  };
}

function frameFor(scenario, index) {
  const frame = new Uint8Array(WIDTH * HEIGHT);
  const random = rng(SEED ^ (SCENARIOS.indexOf(scenario) * 0x9e3779b9) ^ index);
  for (let y = 0; y < HEIGHT; y += 1) {
    for (let x = 0; x < WIDTH; x += 1) {
      let value = 28 + ((x / 40 | 0) % 2) * 5 + ((y / 30 | 0) % 2) * 3;
      if (scenario === 'static_ui') {
        if (x > 20 && x < 300 && y > 18 && y < 44) value = 92;
        if (x > 30 && x < 145 && y > 65 && y < 160) value = 58;
        if (x > 160 && x < 292 && y > 65 && y < 160) value = 74;
      } else if (scenario === 'text_scroll') {
        const offset = index * 3;
        if (x > 25 && x < 295 && y > 25 && y < 165) {
          value = ((y + offset) % 18 < 3 && (x + y) % 31 > 4) ? 210 : 45;
        }
      } else if (scenario === 'cursor_animation') {
        if (x > 25 && x < 295 && y > 25 && y < 165) value = 55;
        const cx = 35 + (index * 9) % 245;
        const cy = 45 + (index * 5) % 100;
        if (Math.abs(x - cx) <= 3 || Math.abs(y - cy) <= 3) value = 245;
      } else if (scenario === 'camera') {
        const shiftedX = (x + index * 2) % WIDTH;
        const shiftedY = (y + index) % HEIGHT;
        value = 110 + 55 * Math.sin(shiftedX / 18) + 40 * Math.cos(shiftedY / 13);
        value += (random() - 0.5) * 14;
      } else if (scenario === 'scene_cut') {
        if (index < FRAME_COUNT / 2) value = x < WIDTH / 2 ? 45 : 170;
        else value = y < HEIGHT / 2 ? 205 : 35;
      } else if (scenario === 'strong_noise') {
        value = random() * 255;
      }
      frame[y * WIDTH + x] = Math.max(0, Math.min(255, Math.round(value)));
    }
  }
  return frame;
}

function generateFrames(scenario) {
  return Array.from({ length: FRAME_COUNT }, (_, index) => frameFor(scenario, index));
}

function ordinaryEncode(frames) {
  const input = Buffer.concat(frames.map(frame => Buffer.from(frame)));
  const started = performance.now();
  const result = spawnSync('ffmpeg', [
    '-hide_banner', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'gray',
    '-s', `${WIDTH}x${HEIGHT}`, '-r', String(FPS), '-i', 'pipe:0', '-an',
    '-c:v', 'libvpx-vp9', '-deadline', 'good', '-cpu-used', '4', '-threads', '1',
    '-b:v', '0', '-crf', '35', '-f', 'webm', 'pipe:1',
  ], { input, maxBuffer: 32 * 1024 * 1024 });
  const cpuMs = performance.now() - started;
  if (result.status !== 0) {
    throw new Error(`ffmpeg_vp9_failed:${String(result.stderr).slice(0, 300)}`);
  }
  return { bytes: result.stdout.byteLength, cpu_ms: cpuMs, frame_bytes: equalFrameBytes(result.stdout.byteLength) };
}

function semanticEncode(frames) {
  const started = performance.now();
  let reference = new Uint8Array(frames[0]);
  let reconstructed = new Uint8Array(reference);
  let referenceAge = 0;
  let peakWorkingBytes = reference.byteLength + reconstructed.byteLength;
  const frameBytes = [];
  const psnrs = [];
  const drifts = [];
  let references = 0;
  let deltas = 0;

  for (let index = 0; index < frames.length; index += 1) {
    const frame = frames[index];
    const globalMae = mae(frame, reconstructed);
    const referenceRequired = index === 0 || globalMae >= SCENE_CUT_THRESHOLD || referenceAge >= 23;
    if (referenceRequired) {
      const payload = deflateRawSync(frame, { level: 6 });
      frameBytes.push(payload.byteLength + 160);
      reference = new Uint8Array(frame);
      reconstructed = new Uint8Array(frame);
      referenceAge = 0;
      references += 1;
    } else {
      const tiles = [];
      for (let top = 0; top < HEIGHT; top += BLOCK) {
        for (let left = 0; left < WIDTH; left += BLOCK) {
          const width = Math.min(BLOCK, WIDTH - left);
          const height = Math.min(BLOCK, HEIGHT - top);
          if (tileMae(frame, reconstructed, left, top, width, height) < CHANGE_THRESHOLD) continue;
          const tile = extractTile(frame, left, top, width, height);
          tiles.push(tile);
          applyTile(reconstructed, tile, left, top, width, height);
        }
      }
      const raw = Buffer.concat(tiles.map(tile => Buffer.from(tile)));
      const compressed = raw.byteLength ? deflateRawSync(raw, { level: 6 }) : Buffer.alloc(0);
      frameBytes.push(compressed.byteLength + 96 + tiles.length * 12);
      referenceAge += 1;
      deltas += 1;
      peakWorkingBytes = Math.max(
        peakWorkingBytes,
        reference.byteLength + reconstructed.byteLength + raw.byteLength + compressed.byteLength,
      );
    }
    const error = mse(frame, reconstructed);
    psnrs.push(error === 0 ? 99 : 10 * Math.log10((255 * 255) / error));
    drifts.push(mae(frame, reconstructed));
  }
  return {
    bytes: frameBytes.reduce((sum, value) => sum + value, 0),
    cpu_ms: performance.now() - started,
    gpu_ms: null,
    memory_bytes: peakWorkingBytes,
    frame_bytes: frameBytes,
    psnr_db: mean(psnrs),
    drift_mae: mean(drifts),
    references,
    deltas,
    standard_codec: 'DEFLATE',
  };
}

function equalFrameBytes(total) {
  const base = Math.floor(total / FRAME_COUNT);
  return Array.from({ length: FRAME_COUNT }, (_, index) => base + (index < total % FRAME_COUNT ? 1 : 0));
}

function networkMetrics(frameBytes, profile) {
  let availableAt = 0;
  const latencies = [];
  const frameInterval = 1000 / FPS;
  let worstBurst = 0;
  for (let index = 0; index < frameBytes.length; index += 1) {
    const producedAt = index * frameInterval;
    const transmissionMs = frameBytes[index] * 8 * 1000 / profile.bandwidth_bps;
    const startedAt = Math.max(producedAt, availableAt);
    availableAt = startedAt + transmissionMs;
    latencies.push(profile.base_rtt_ms / 2 + availableAt - producedAt);
    worstBurst = Math.max(worstBurst, frameBytes[index]);
  }
  return { mean_latency_ms: mean(latencies), p95_latency_ms: percentile(latencies, 0.95), worst_burst_bytes: worstBurst };
}

function tileMae(a, b, left, top, width, height) {
  let total = 0;
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      total += Math.abs(a[(top + y) * WIDTH + left + x] - b[(top + y) * WIDTH + left + x]);
    }
  }
  return total / (width * height);
}

function extractTile(frame, left, top, width, height) {
  const result = new Uint8Array(width * height);
  let target = 0;
  for (let y = 0; y < height; y += 1) {
    const source = (top + y) * WIDTH + left;
    result.set(frame.subarray(source, source + width), target);
    target += width;
  }
  return result;
}

function applyTile(frame, tile, left, top, width, height) {
  let source = 0;
  for (let y = 0; y < height; y += 1) {
    frame.set(tile.subarray(source, source + width), (top + y) * WIDTH + left);
    source += width;
  }
}

function mse(a, b) {
  let total = 0;
  for (let index = 0; index < a.length; index += 1) {
    const delta = a[index] - b[index];
    total += delta * delta;
  }
  return total / a.length;
}

function mae(a, b) {
  let total = 0;
  for (let index = 0; index < a.length; index += 1) total += Math.abs(a[index] - b[index]);
  return total / a.length;
}

function mean(values) { return values.reduce((sum, value) => sum + value, 0) / Math.max(1, values.length); }
function percentile(values, fraction) {
  const ordered = [...values].sort((a, b) => a - b);
  return ordered[Math.min(ordered.length - 1, Math.ceil(ordered.length * fraction) - 1)];
}
function round(value) { return Math.round(value * 1000) / 1000; }

function classify(scenarios) {
  const aggregateByteRatio = mean(scenarios.map(item => item.ratios.bytes));
  const maximumCpuRatio = Math.max(...scenarios.map(item => item.ratios.cpu));
  const maximumLatencyRatio = Math.max(...scenarios.flatMap(item => item.networks.map(net => net.latency_ratio)));
  const maximumMemory = Math.max(...scenarios.map(item => item.semantic.memory_bytes));
  const minimumPsnr = Math.min(...scenarios.map(item => item.semantic.psnr_db));
  const maximumDrift = Math.max(...scenarios.map(item => item.semantic.drift_mae));
  const beneficial = scenarios.filter(item => item.ratios.bytes <= 1).length;
  const safetyPassed = minimumPsnr >= THRESHOLDS.safety.minimum_psnr_db
    && maximumDrift <= THRESHOLDS.safety.maximum_drift_mae;
  const noGo = !safetyPassed
    || aggregateByteRatio > THRESHOLDS.no_go.aggregate_byte_ratio
    || maximumCpuRatio > THRESHOLDS.no_go.maximum_cpu_ratio
    || maximumMemory > THRESHOLDS.no_go.maximum_memory_bytes
    || maximumLatencyRatio > THRESHOLDS.no_go.maximum_latency_ratio
    || beneficial < THRESHOLDS.no_go.minimum_beneficial_scenarios;
  const byId = Object.fromEntries(scenarios.map(item => [item.scenario, item]));
  const go = safetyPassed
    && ['static_ui', 'text_scroll', 'cursor_animation'].every(
      id => byId[id].ratios.bytes <= THRESHOLDS.go.suitable_scenario_byte_ratio,
    )
    && byId.camera.ratios.bytes <= THRESHOLDS.go.maximum_camera_byte_ratio
    && byId.strong_noise.ratios.bytes <= THRESHOLDS.go.maximum_noise_byte_ratio
    && maximumCpuRatio <= THRESHOLDS.go.maximum_cpu_ratio
    && maximumMemory <= THRESHOLDS.go.maximum_memory_bytes
    && maximumLatencyRatio <= THRESHOLDS.go.maximum_latency_ratio
    && beneficial >= THRESHOLDS.go.minimum_beneficial_scenarios;
  return {
    verdict: noGo ? 'no_go' : go ? 'go' : 'conditional_go',
    activation_allowed: go,
    ordinary_webrtc_unchanged: true,
    followup_visual_flags_enabled: go,
    summary: {
      aggregate_byte_ratio: round(aggregateByteRatio), maximum_cpu_ratio: round(maximumCpuRatio),
      maximum_latency_ratio: round(maximumLatencyRatio), maximum_memory_bytes: maximumMemory,
      minimum_psnr_db: round(minimumPsnr), maximum_drift_mae: round(maximumDrift),
      beneficial_scenarios: beneficial,
    },
  };
}

function main() {
  const scenarios = [];
  for (const scenario of SCENARIOS) {
    const frames = generateFrames(scenario);
    const ordinary = ordinaryEncode(frames);
    const semantic = semanticEncode(frames);
    const networks = NETWORKS.map(profile => {
      const baseline = networkMetrics(ordinary.frame_bytes, profile);
      const observed = networkMetrics(semantic.frame_bytes, profile);
      return {
        profile: profile.id,
        ordinary: mapRounded(baseline),
        semantic: mapRounded(observed),
        latency_ratio: round(observed.p95_latency_ms / baseline.p95_latency_ms),
      };
    });
    scenarios.push({
      scenario,
      source: { width_px: WIDTH, height_px: HEIGHT, fps: FPS, frames: FRAME_COUNT, seed: SEED },
      ordinary: { codec: 'VP9/WebM', bytes: ordinary.bytes, cpu_ms: round(ordinary.cpu_ms), gpu_ms: null },
      semantic: {
        codec: semantic.standard_codec, bytes: semantic.bytes, cpu_ms: round(semantic.cpu_ms),
        gpu_ms: semantic.gpu_ms, memory_bytes: semantic.memory_bytes, psnr_db: round(semantic.psnr_db),
        drift_mae: round(semantic.drift_mae), references: semantic.references, deltas: semantic.deltas,
      },
      ratios: {
        bytes: round(semantic.bytes / ordinary.bytes),
        cpu: round(semantic.cpu_ms / Math.max(ordinary.cpu_ms, 0.001)),
      },
      networks,
    });
  }
  const decision = classify(scenarios);
  const artifact = {
    schema: 'ananta.semantic-visual-feasibility.v1',
    generated_by: 'scripts/spikes/semantic_visual_feasibility.mjs',
    deterministic_input_version: 'synthetic-scenes-v1',
    observe_only: true,
    model_used: false,
    thresholds: THRESHOLDS,
    network_profiles: NETWORKS,
    scenarios,
    decision,
  };
  const output = resolve(process.argv[2] || 'artifacts/domain/semantic-visual-feasibility.json');
  mkdirSync(dirname(output), { recursive: true });
  writeFileSync(output, `${JSON.stringify(artifact, null, 2)}\n`, 'utf8');
  process.stdout.write(`${decision.verdict}: ${output}\n`);
}

function mapRounded(value) { return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, round(item)])); }

if (import.meta.url === `file://${process.argv[1]}`) main();
