import { createHash, webcrypto } from 'node:crypto';

import { SpeechDelayBufferService } from '../../frontend-angular/src/app/services/speech-delay-buffer.service';
import { SemanticSpeechQualityControllerService } from '../../frontend-angular/src/app/services/semantic-speech-quality-controller.service';
import { SpeechTranscriptRevisionStore } from '../../frontend-angular/src/app/services/speech-transcript-revision.store';
import { SemanticSpeechPayload } from '../../frontend-angular/src/app/services/semantic-speech-transport.service';

if (!globalThis.crypto) {
  Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: false });
}

const iterationsFlag = process.argv.indexOf('--iterations');
const iterations = Number(iterationsFlag >= 0 ? process.argv[iterationsFlag + 1] : 32);
if (!Number.isSafeInteger(iterations) || iterations < 8 || iterations > 256) {
  throw new Error('semantic_media_live_slo_iterations_invalid');
}

const delay = new SpeechDelayBufferService();
const revisions = new SpeechTranscriptRevisionStore();
const quality = new SemanticSpeechQualityControllerService();
const sound: number[] = [];
const text: number[] = [];
const ui: number[] = [];
const bytes = new Uint8Array(4096);
for (let index = 0; index < bytes.byteLength; index += 1) bytes[index] = (index * 17 + 31) % 256;
const digest = createHash('sha256').update(bytes).digest('hex');
const contractDigest = createHash('sha256').update('semantic-media-live-slo-contract-v1').digest('hex');
const fixedNow = 2_000_000_000_000;
const cpuStarted = process.cpuUsage();
let maximumRssBytes = process.memoryUsage().rss;

let projectionCount = 0;
const subscription = revisions.turns$.subscribe(turns => { projectionCount += turns.length; });
try {
  for (let index = 0; index < iterations; index += 1) {
    maximumRssBytes = Math.max(maximumRssBytes, process.memoryUsage().rss);
    const segmentId = `segment-${index}`;
    let started = performance.now();
    await delay.put({
      sessionId: 'benchmark-session', epoch: 1, segmentId,
      sourceDigest: digest, expiresAtMs: fixedNow + 60_000,
    }, bytes, fixedNow);
    const used = await delay.use(segmentId, async value => value.byteLength, fixedNow);
    delay.confirm(segmentId);
    if (!used.available || used.value !== bytes.byteLength) throw new Error('semantic_media_live_sound_probe_failed');
    sound.push(performance.now() - started);

    const message: SemanticSpeechPayload = {
      version: 'ananta.semantic-speech.v1', kind: 'transcript_revision',
      session_id: 'benchmark-session', epoch: 1, turn_id: `turn-${index}`,
      revision: 1, sender_id: 'benchmark-sender', audience_id: 'benchmark-audience',
      consent_version: 1, expires_at_ms: fixedNow + 60_000,
      contract_digest: contractDigest, source_digest: digest,
      authority: 'provisional', text: 'alpha beta gamma delta',
    };
    started = performance.now();
    if (!revisions.apply(message)) throw new Error('semantic_media_live_text_probe_failed');
    text.push(performance.now() - started);

    started = performance.now();
    const state = quality.ingest({
      measuredAtMs: index * 10_000,
      lossRatio: 0, queueBytes: 0, partialAgeMs: 0, correctionLagMs: 0,
      sourceLossRatio: 0, featureLossRatio: 0, reconstructionErrorRatio: 0,
    }, index % 2 ? 'transcript_live' : 'semantic_reconstruction');
    if (!state.ordinaryAudioAvailable) throw new Error('semantic_media_live_ui_probe_failed');
    // Read the product projection, not a synthetic timer-only callback.
    revisions.turn(`turn-${index}`);
    ui.push(performance.now() - started);
  }
} finally {
  subscription.unsubscribe();
  delay.revoke();
  revisions.clear();
}

if (projectionCount < iterations) throw new Error('semantic_media_live_projection_missing');
const cpu = process.cpuUsage(cpuStarted);

function percentile(values: readonly number[], fraction: number): number {
  const ordered = [...values].sort((left, right) => left - right);
  return Math.max(1, Math.ceil(ordered[Math.min(ordered.length - 1, Math.ceil(ordered.length * fraction) - 1)]));
}

process.stdout.write(`${JSON.stringify({
  schema: 'ananta.semantic-media-live-slo-probe.v1',
  iterations,
  sound_p50_ms: percentile(sound, 0.50),
  sound_p95_ms: percentile(sound, 0.95),
  sound_p99_ms: percentile(sound, 0.99),
  text_p50_ms: percentile(text, 0.50),
  text_p95_ms: percentile(text, 0.95),
  text_p99_ms: percentile(text, 0.99),
  ui_p50_ms: percentile(ui, 0.50),
  ui_p95_ms: percentile(ui, 0.95),
  ui_p99_ms: percentile(ui, 0.99),
  projection_count: projectionCount,
  probe_cpu_micros: cpu.user + cpu.system,
  probe_ram_bytes: maximumRssBytes,
})}\n`);
