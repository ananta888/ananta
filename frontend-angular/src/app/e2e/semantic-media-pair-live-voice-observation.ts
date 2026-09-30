import { firstValueFrom } from 'rxjs';

import { VoiceApiService } from '../features/voice/voice-api.service';
import {
  SemanticMediaPairProductPorts,
  SemanticVoiceObservationCursor,
  SemanticVoiceObservationResult,
} from './semantic-media-pair-live-driver.models';
import {
  asArrayBuffer,
  delay,
  observedHttpStatus,
  syntheticPcm,
  syntheticWav,
} from './semantic-media-pair-live-driver.support';

/**
 * Voice product observation for the Pair live gate: drives the real voice
 * stream and long-run APIs (reconnect, 404/409/413/429 paths, segment
 * rotation, correction after final) and reports only observed outcomes.
 */

export async function startVoiceProductObservation(
  requirePorts: () => SemanticMediaPairProductPorts,
  hubUrl: string,
): Promise<SemanticVoiceObservationCursor> {
  if (!/^https?:\/\/[^\s]+$/.test(hubUrl)) throw new Error('semantic_voice_hub_url_invalid');
  const api = requirePorts().voiceApi;
  const suffix = crypto.randomUUID();
  const profileId = `semantic-voice-${suffix}`;
  await firstValueFrom(api.saveConfiguration(hubUrl, {
    scope: 'profile',
    scope_id: profileId,
    delta: {
      // The live gate must complete even when the checked-in local model
      // cannot meet incremental latency on the current CPU. The stream still
      // traverses the product API; absence of a native partial is reported as
      // zero and keeps the release gate closed instead of being simulated.
      transport_mode: 'batch',
      recognition_strategy: 'single',
      routing_strategy: 'fixed',
      primary_backend: 'whisper_cpp',
      secondary_backends: [],
      correction_policy: 'generative_rewrite',
      generative_corrector_provider: 'embedded',
      generative_corrector_model: 'phi-3-mini-instruct',
      generative_corrector_max_edit_ratio: 0.35,
      feature_flags: { generative_corrector: true },
    },
  }, `semantic-e2e-voice-config-${suffix}`));
  const stream = await firstValueFrom(api.createStream(hubUrl, {
    filename: 'semantic-e2e-reconnect.pcm',
    media_type: 'audio/pcm;rate=16000;channels=1',
    profile_id: profileId,
    language: 'de',
    max_audio_seconds: 2,
  }, `semantic-e2e-voice-stream-${suffix}`));
  const partialStarted = performance.now();
  const firstChunk = await firstValueFrom(api.pushStreamChunk(
    hubUrl,
    stream.stream.session_id,
    0,
    asArrayBuffer(syntheticPcm(100)),
  ));
  const partialLatencyMs = Math.max(1, Math.round(performance.now() - partialStarted));
  const partialObserved = firstChunk.event?.event_type === 'partial'
    && typeof firstChunk.event.payload?.text === 'string';
  const lease = await firstValueFrom(api.acquireLongRunLease(hubUrl, profileId));
  const liveRun = await firstValueFrom(api.createLongRun(hubUrl, {
    source: 'system_audio',
    profile_id: profileId,
    language: 'de',
    segment_duration_seconds: 60,
    max_duration_seconds: 120,
    overlap_milliseconds: 0,
    lease_token: lease.lease_token,
  }, `semantic-e2e-live-run-${suffix}`));
  if (liveRun.run.status !== 'active') throw new Error('semantic_voice_live_run_not_active');
  return Object.freeze({
    hubUrl,
    profileId,
    streamId: stream.stream.session_id,
    liveRunId: liveRun.run.id,
    partialLatencyMs: partialObserved ? partialLatencyMs : 0,
    partialObserved,
  });
}

export async function resumeVoiceProductObservation(
  requirePorts: () => SemanticMediaPairProductPorts,
  cursor: SemanticVoiceObservationCursor,
): Promise<SemanticVoiceObservationResult> {
  validateVoiceObservationCursor(cursor);
  const ports = requirePorts();
  const api = ports.voiceApi;
  let stream404Count = 0;
  let chunk413Count = 0;
  let stop409Count = 0;
  let backpressureCount = 0;
  let ordinaryFallbackCount = 0;
  let partialObservationCount = Number(cursor.partialObserved);
  let partialLatencyMs = cursor.partialLatencyMs;

  const resumedAt = performance.now();
  const resumedChunk = await firstValueFrom(api.pushStreamChunk(
    cursor.hubUrl,
    cursor.streamId,
    1,
    asArrayBuffer(syntheticPcm(100, 7)),
  ));
  if (
    resumedChunk.event?.event_type === 'partial'
    && typeof resumedChunk.event.payload?.text === 'string'
  ) {
    partialObservationCount += 1;
    partialLatencyMs = Math.max(partialLatencyMs, Math.max(1, Math.round(performance.now() - resumedAt)));
  }
  const final = await firstValueFrom(api.finalizeStream(cursor.hubUrl, cursor.streamId));
  const streamFinal = final.stream.state === 'final'
    && final.event?.event_type === 'final';
  const transcriptContinuityCount = Number(streamFinal && Boolean(final.result?.text));
  const reconnectCount = Number(streamFinal && resumedChunk.stream.next_chunk_sequence >= 2);

  try {
    await firstValueFrom(api.cancelStream(
      cursor.hubUrl,
      `voice-stream-missing-${crypto.randomUUID().replaceAll('-', '')}`,
      { missingSessionIsExpected: true },
    ));
  } catch (error) {
    stream404Count = Number(observedHttpStatus(error) === 404);
  }

  const oversized = await firstValueFrom(api.createStream(cursor.hubUrl, {
    filename: 'semantic-e2e-oversized.pcm',
    media_type: 'audio/pcm;rate=16000;channels=1',
    profile_id: cursor.profileId,
    max_audio_seconds: 2,
  }, `semantic-e2e-oversized-${crypto.randomUUID()}`));
  try {
    await firstValueFrom(api.pushStreamChunk(
      cursor.hubUrl,
      oversized.stream.session_id,
      0,
      asArrayBuffer(new Uint8Array(1024 * 1024 + 1)),
    ));
  } catch (error) {
    chunk413Count = Number(observedHttpStatus(error) === 413);
  } finally {
    await firstValueFrom(api.cancelStream(cursor.hubUrl, oversized.stream.session_id)).catch(() => undefined);
  }

  backpressureCount = await observeVoiceBackpressure(api, cursor.hubUrl, cursor.profileId);

  const rotationStarted = performance.now();
  const segmentResponses = [];
  for (let sequence = 0; sequence < 2; sequence += 1) {
    segmentResponses.push(await firstValueFrom(api.uploadLongRunSegment(
      cursor.hubUrl,
      cursor.liveRunId,
      sequence,
      {
        file: syntheticWav(120, sequence + 1),
        fileName: `semantic-e2e-segment-${sequence}.wav`,
        startedAtMs: sequence * 120,
        endedAtMs: (sequence + 1) * 120,
        durationMs: 120,
        overlapMilliseconds: 0,
      },
      `semantic-e2e-live-segment-${cursor.liveRunId}-${sequence}`,
    )));
  }
  const acceleratedRotationCount = Number(
    segmentResponses.length === 2
    && segmentResponses.every((value, index) => value.segment?.sequence === index)
    && performance.now() - rotationStarted < 60_000,
  );

  let stopped = false;
  try {
    await firstValueFrom(api.stopLongRun(
      cursor.hubUrl,
      cursor.liveRunId,
      { last_sequence: 1, reason: 'semantic_e2e_observation' },
      `semantic-e2e-live-stop-${cursor.liveRunId}`,
    ));
    stopped = true;
  } catch (error) {
    stop409Count = Number(observedHttpStatus(error) === 409);
  }
  let correctionSnapshot = await firstValueFrom(api.getLongRun(cursor.hubUrl, cursor.liveRunId));
  if (!stopped && stop409Count) {
    for (let attempt = 0; attempt < 40 && hasPendingCorrections(correctionSnapshot); attempt += 1) {
      await delay(250);
      correctionSnapshot = await firstValueFrom(api.getLongRun(cursor.hubUrl, cursor.liveRunId));
    }
    if (!hasPendingCorrections(correctionSnapshot)) {
      await firstValueFrom(api.stopLongRun(
        cursor.hubUrl,
        cursor.liveRunId,
        { last_sequence: 1, reason: 'semantic_e2e_observation' },
        `semantic-e2e-live-stop-complete-${cursor.liveRunId}`,
      ));
    }
  }
  const correctionAfterFinalCount = (correctionSnapshot.segments ?? []).filter(segment => (
    segment.status === 'completed'
    && !['', 'not_requested'].includes(String(segment.correction_status || ''))
  )).length;
  const finalSegmentCount = (correctionSnapshot.segments ?? []).filter(segment => (
    segment.status === 'completed'
  )).length;

  if (chunk413Count && streamFinal) {
    const fallback = ports.speechQuality.containRuntimeFailure('voice_stream.invalid_chunk');
    ordinaryFallbackCount = Number(fallback.mode === 'ordinary_audio' && fallback.ordinaryAudioAvailable);
  }
  await firstValueFrom(api.cancelStream(cursor.hubUrl, cursor.streamId)).catch(() => undefined);
  return Object.freeze({
    partialLatencyMs,
    partialObservationCount,
    segmentModeCount: Number(streamFinal && partialObservationCount === 0),
    finalSegmentCount,
    correctionAfterFinalCount,
    acceleratedRotationCount,
    reconnectCount,
    stream404Count,
    stop409Count,
    chunk413Count,
    backpressureCount,
    ordinaryFallbackCount,
    transcriptContinuityCount,
  });
}

async function observeVoiceBackpressure(
  api: VoiceApiService,
  hubUrl: string,
  profileId: string,
): Promise<number> {
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const marker = crypto.randomUUID();
    const created = await firstValueFrom(api.createStream(hubUrl, {
      filename: `semantic-e2e-pressure-${attempt}.pcm`,
      media_type: 'audio/pcm;rate=16000;channels=1',
      profile_id: profileId,
      max_audio_seconds: 40,
    }, `semantic-e2e-pressure-${marker}`));
    const bytes = asArrayBuffer(syntheticPcm(30_000, attempt + 3));
    const outcomes = await Promise.allSettled([
      firstValueFrom(api.pushStreamChunk(hubUrl, created.stream.session_id, 0, bytes)),
      firstValueFrom(api.pushStreamChunk(hubUrl, created.stream.session_id, 0, bytes)),
    ]);
    await firstValueFrom(api.cancelStream(hubUrl, created.stream.session_id)).catch(() => undefined);
    if (outcomes.some(value => (
      value.status === 'rejected'
      && observedHttpStatus(value.reason) === 429
    ))) return 1;
  }
  return 0;
}

function validateVoiceObservationCursor(value: SemanticVoiceObservationCursor): void {
  if (
    !/^https?:\/\/[^\s]+$/.test(value.hubUrl)
    || !value.profileId
    || !value.streamId
    || !value.liveRunId
    || !Number.isSafeInteger(value.partialLatencyMs)
    || value.partialLatencyMs < 0
  ) throw new Error('semantic_voice_observation_cursor_invalid');
}

function hasPendingCorrections(response: { segments?: readonly { correction_status?: string }[] }): boolean {
  return (response.segments ?? []).some(segment => (
    ['queued', 'pending', 'processing'].includes(String(segment.correction_status || ''))
  ));
}
