import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, Subject, of } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  VOICE_AUDIO_CAPTURE,
  VoiceAudioCapturePort,
} from '../features/voice/voice-audio-capture';
import { VoiceApiService } from '../features/voice/voice-api.service';
import { DEFAULT_SEMANTIC_SPEECH_SETTINGS } from './semantic-speech-settings';
import {
  SemanticSpeechCaptureProducerContext,
  SemanticSpeechCaptureProducerService,
} from './semantic-speech-capture-producer.service';
import { SemanticSpeechRuntimeCoordinatorService } from './semantic-speech-runtime-coordinator.service';
import { SemanticSpeechPayload, SemanticSpeechTransportService } from './semantic-speech-transport.service';

const context: SemanticSpeechCaptureProducerContext = Object.freeze({
  hubUrl: 'http://hub.test',
  profileId: 'default',
  sessionId: 'pair-a',
  epoch: 2,
  localPeerId: 'alice',
  remotePeerId: 'bob',
  consentVersion: 3,
  contractDigest: 'a'.repeat(64),
  correctionConsent: Object.freeze({
    consentId: 'consent-a', consentDigest: 'b'.repeat(64), consentVersion: 4,
    revocationEpoch: 1, expiresAtMs: Date.now() + 10 * 60_000,
  }),
});

describe('SemanticSpeechCaptureProducerService', () => {
  let service: SemanticSpeechCaptureProducerService;
  let captureChunk: ((chunk: ArrayBuffer) => void) | null;
  let captureActive: boolean;
  let capturePrepared: boolean;
  let streamNumber: number;
  const createStream = vi.fn();
  const pushStreamChunk = vi.fn();
  const finalizeStream = vi.fn();
  const cancelStream = vi.fn();
  const finalizeLocal = vi.fn();
  const stageSource = vi.fn();
  const reportCaptureTransportFailure = vi.fn();
  const send = vi.fn();
  const outboundCorrection$ = new Subject<SemanticSpeechPayload>();
  const settings$ = new BehaviorSubject(DEFAULT_SEMANTIC_SPEECH_SETTINGS);

  beforeEach(() => {
    vi.clearAllMocks();
    captureChunk = null;
    captureActive = false;
    capturePrepared = false;
    streamNumber = 0;
    settings$.next({
      displayMode: 'live', segmentDurationSeconds: 10, correctEachSegment: true,
      paused: false, ordinaryAudioOverride: false,
    });
    createStream.mockImplementation(() => {
      streamNumber += 1;
      return of({
        stream: {
          session_id: `vs_stream_${streamNumber}`, state: 'created', next_chunk_sequence: 0,
          max_audio_seconds: 10, max_audio_bytes: 320_000, accepted_audio_bytes: 0,
        },
      });
    });
    pushStreamChunk.mockImplementation((_hub: string, sessionId: string, sequence: number) => of({
      stream: { session_id: sessionId, state: 'active', next_chunk_sequence: sequence + 1 },
      event: { sequence, event_type: 'partial', payload: { text: `Hub partial ${sequence}` } },
    }));
    finalizeStream.mockImplementation((_hub: string, sessionId: string) => of({
      stream: { session_id: sessionId, state: 'final', next_chunk_sequence: 1 },
      result: { text: 'Hub final' }, result_ref: 'result-a',
      event: {
        sequence: 1, event_type: 'final', payload: { result: { text: 'Hub final' } },
      },
    }));
    cancelStream.mockReturnValue(of({
      stream: { session_id: 'cancelled', state: 'closed', next_chunk_sequence: 0 }, deleted: true,
    }));
    finalizeLocal.mockResolvedValue(true);
    stageSource.mockImplementation(async (value: { bytes: Uint8Array }) => {
      value.bytes.fill(0);
    });
    send.mockResolvedValue({ result: Promise.resolve() });

    const capture: VoiceAudioCapturePort = {
      supported: true,
      get active() { return captureActive; },
      get prepared() { return capturePrepared; },
      supportsSource: source => source === 'microphone',
      prepare: vi.fn(async () => { capturePrepared = true; }),
      start: vi.fn(async onChunk => {
        captureChunk = onChunk;
        captureActive = true;
      }),
      stop: vi.fn(async () => {
        captureActive = false;
        capturePrepared = false;
        captureChunk = null;
      }),
    };
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ providers: [
      SemanticSpeechCaptureProducerService,
      { provide: VOICE_AUDIO_CAPTURE, useValue: capture },
      { provide: VoiceApiService, useValue: {
        createStream, pushStreamChunk, finalizeStream, cancelStream,
      } },
      { provide: SemanticSpeechRuntimeCoordinatorService, useValue: {
        settings$, outboundCorrection$, finalizeLocal, stageSource, reportCaptureTransportFailure,
      } },
      { provide: SemanticSpeechTransportService, useValue: { send } },
    ] });
    service = TestBed.inject(SemanticSpeechCaptureProducerService);
  });

  it('publishes immediate Hub-authored partials independently from segment rotation', async () => {
    settings$.next({
      ...settings$.value,
      displayMode: 'segment',
    });
    await service.start(context);
    captureChunk!(pcm(16_000, 3));

    await vi.waitFor(() => expect(finalizeLocal).toHaveBeenCalledWith(expect.objectContaining({
      turn_id: 'vs_stream_1', revision: 1, authority: 'provisional', text: 'Hub partial 0',
      source_digest: null,
    })));
    await vi.waitFor(() => expect(send).toHaveBeenCalledWith(expect.objectContaining({
      revision: 1, authority: 'provisional', text: 'Hub partial 0',
    })));
    expect(finalizeStream).not.toHaveBeenCalled();
    expect(service.snapshot()).toMatchObject({ active: true, admittedBytes: 16_000 });
  });

  it('rotates at the bounded PCM budget, stages canonical WAV and publishes the true Hub final', async () => {
    let stagedWav = new Uint8Array();
    stageSource.mockImplementationOnce(async (value: { bytes: Uint8Array }) => {
      stagedWav = value.bytes.slice();
      value.bytes.fill(0);
    });
    await service.start(context);
    const input = pcm(320_000, 7);
    captureChunk!(input);

    await vi.waitFor(() => expect(stageSource).toHaveBeenCalledOnce());
    await vi.waitFor(() => expect(finalizeLocal).toHaveBeenCalledWith(expect.objectContaining({
      turn_id: 'vs_stream_1', revision: 2, authority: 'final', text: 'Hub final',
      source_digest: expect.stringMatching(/^[a-f0-9]{64}$/),
    })));
    await vi.waitFor(() => expect(createStream).toHaveBeenCalledTimes(2));
    expect(new TextDecoder().decode(stagedWav.slice(0, 4))).toBe('RIFF');
    expect(new TextDecoder().decode(stagedWav.slice(8, 12))).toBe('WAVE');
    expect(Array.from(new Uint8Array(input).slice(0, 8))).toEqual(Array(8).fill(0));
    expect(send.mock.calls.map(([payload]) => [payload.authority, payload.revision])).toEqual([
      ['provisional', 1], ['final', 2],
    ]);
  });

  it('publishes one source-side correction and suppresses a duplicate revision', async () => {
    await service.start(context);
    const correction: SemanticSpeechPayload = {
      version: 'ananta.semantic-speech.v1', kind: 'correction', session_id: 'pair-a', epoch: 2,
      turn_id: 'vs_stream_1', revision: 3, sender_id: 'alice', audience_id: 'bob', consent_version: 3,
      expires_at_ms: Date.now() + 30_000, contract_digest: 'a'.repeat(64), source_digest: 'c'.repeat(64),
      authority: 'corrected', text: 'Hub corrected', reason_code: 'corrected',
    };
    outboundCorrection$.next(correction);
    outboundCorrection$.next(correction);

    await vi.waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    expect(send).toHaveBeenCalledWith(correction);
  });

  it('keeps the admitted Hub final visible and falls back when transport publication fails', async () => {
    pushStreamChunk.mockImplementation((_hub: string, sessionId: string, sequence: number) => of({
      stream: { session_id: sessionId, state: 'active', next_chunk_sequence: sequence + 1 },
      event: { sequence, event_type: 'chunk_accepted', payload: {} },
    }));
    send.mockRejectedValueOnce(new Error('semantic_speech_send_failed'));
    const failures: string[] = [];
    service.failure$.subscribe(value => failures.push(value));
    await service.start(context);
    captureChunk!(pcm(320_000, 9));

    await vi.waitFor(() => expect(finalizeLocal).toHaveBeenCalledWith(expect.objectContaining({
      authority: 'final', text: 'Hub final', revision: 2,
    })));
    await vi.waitFor(() => expect(failures).toEqual(['semantic_speech_send_failed']));
    expect(finalizeLocal).toHaveBeenCalledOnce();
    expect(service.snapshot().active).toBe(false);
  });

  it('fails closed on a textual event without a Hub-owned revision', async () => {
    pushStreamChunk.mockImplementation((_hub: string, sessionId: string, sequence: number) => of({
      stream: { session_id: sessionId, state: 'active', next_chunk_sequence: sequence + 1 },
      event: { event_type: 'partial', payload: { text: 'Unversioned text' } },
    }));
    const failures: string[] = [];
    service.failure$.subscribe(value => failures.push(value));
    await service.start(context);
    captureChunk!(pcm(16_000, 1));

    await vi.waitFor(() => expect(failures).toEqual(['semantic_speech_backend_revision_missing']));
    expect(finalizeLocal).not.toHaveBeenCalled();
    expect(reportCaptureTransportFailure).toHaveBeenCalledWith(0, 'vs_stream_1');
  });

  it('bounds a stalled serial upload queue and stops without waiting for it', async () => {
    const stalled = new Subject<never>();
    pushStreamChunk.mockReturnValue(stalled);
    const failures: string[] = [];
    service.failure$.subscribe(value => failures.push(value));
    await service.start(context);
    for (let index = 0; index < 9; index += 1) captureChunk!(pcm(16_000, index + 1));

    await vi.waitFor(() => expect(failures).toEqual(['semantic_speech_capture_backpressure']));
    expect(service.snapshot().active).toBe(false);
    await vi.waitFor(() => expect(cancelStream).toHaveBeenCalledWith(
      'http://hub.test', 'vs_stream_1', { missingSessionIsExpected: true },
    ));
    stalled.complete();
  });
});

function pcm(bytes: number, value: number): ArrayBuffer {
  const result = new Uint8Array(bytes);
  result.fill(value);
  return result.buffer;
}
