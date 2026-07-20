import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, Subject, of, throwError } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { GenericSpeechReconstructorService } from '../features/voice/reconstruction/generic-speech-reconstructor.service';
import { SpeechDelayBufferService } from './speech-delay-buffer.service';
import {
  SemanticSpeechRuntimeContext,
  SemanticSpeechRuntimeCoordinatorService,
} from './semantic-speech-runtime-coordinator.service';
import { SemanticSpeechSourceCorrectionApiService } from './semantic-speech-source-correction-api.service';
import { SemanticSpeechPayload, SemanticSpeechTransportService } from './semantic-speech-transport.service';
import { SpeechTranscriptRevisionStore } from './speech-transcript-revision.store';

const payload$ = new Subject<SemanticSpeechPayload>();
const pressure$ = new BehaviorSubject({ pendingMessages: 0, pendingBytes: 0, timers: 0 });
const correction = vi.fn();
const reconstructedAudio = {
  format: 'fixture', play: vi.fn(async () => undefined), release: vi.fn(),
};
const reconstruct = vi.fn(async (input: any) => ({
  mode: 'generic', reasonCode: null, turnId: input.turnId, revision: input.revision,
  authoritativeText: input.text, audio: reconstructedAudio,
  quality: { engine: 'fixture', score: 0.9, featureCoverage: 1, provisional: true },
}));

const context: SemanticSpeechRuntimeContext = Object.freeze({
  hubUrl: 'http://hub.test',
  sessionId: 'session-a',
  epoch: 2,
  localPeerId: 'bob',
  remotePeerId: 'alice',
  consentVersion: 1,
  contractDigest: 'a'.repeat(64),
  correctionConsent: Object.freeze({
    consentId: 'consent-a', consentDigest: 'b'.repeat(64), consentVersion: 4,
    revocationEpoch: 1, expiresAtMs: Date.now() + 60_000,
  }),
});

async function digest(bytes: Uint8Array): Promise<string> {
  const value = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(value), byte => byte.toString(16).padStart(2, '0')).join('');
}

function base(
  kind: SemanticSpeechPayload['kind'],
  sourceDigest: string,
  revision = 2,
): SemanticSpeechPayload {
  return {
    version: 'ananta.semantic-speech.v1', kind, session_id: 'session-a', epoch: 2,
    turn_id: 'turn-a', revision, sender_id: 'alice', audience_id: 'bob', consent_version: 1,
    expires_at_ms: Date.now() + 30_000, contract_digest: 'a'.repeat(64), source_digest: sourceDigest,
  };
}

describe('SemanticSpeechRuntimeCoordinatorService', () => {
  let coordinator: SemanticSpeechRuntimeCoordinatorService;
  let store: SpeechTranscriptRevisionStore;
  let buffer: SpeechDelayBufferService;

  beforeEach(() => {
    correction.mockReset();
    reconstruct.mockClear();
    reconstructedAudio.play.mockClear();
    reconstructedAudio.release.mockClear();
    pressure$.next({ pendingMessages: 0, pendingBytes: 0, timers: 0 });
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ providers: [
      SemanticSpeechRuntimeCoordinatorService,
      SpeechDelayBufferService,
      SpeechTranscriptRevisionStore,
      { provide: SemanticSpeechTransportService, useValue: {
        payload$, pressure$, snapshot: () => pressure$.value, stop: vi.fn(),
      } },
      { provide: SemanticSpeechSourceCorrectionApiService, useValue: { correct: correction } },
      { provide: GenericSpeechReconstructorService, useValue: { reconstruct, supersede: vi.fn() } },
    ] });
    coordinator = TestBed.inject(SemanticSpeechRuntimeCoordinatorService);
    store = TestBed.inject(SpeechTranscriptRevisionStore);
    buffer = TestBed.inject(SpeechDelayBufferService);
    coordinator.start(context);
  });

  afterEach(() => {
    coordinator.ngOnDestroy();
    vi.useRealTimers();
  });

  it('runs exactly one bounded Hub correction for one final and deletes encrypted source', async () => {
    const outbound: SemanticSpeechPayload[] = [];
    coordinator.outboundCorrection$.subscribe(payload => outbound.push(payload));
    const bytes = new Uint8Array([1, 2, 3, 4]);
    const sourceDigest = await digest(bytes);
    correction.mockReturnValue(of({
      session_id: 'session-a', epoch: 2, turn_id: 'turn-a', revision: 3, supersedes_revision: 2,
      text: 'Korrigierter Text', authority: 'corrected', reason_code: 'corrected', source_digest: sourceDigest,
      correction_attempted: true, operations: [], task_id: 'task-a', idempotent_replay: false,
    }));

    await coordinator.stageSource({
      turnId: 'turn-a', revision: 2, sourceDigest, expiresAtMs: Date.now() + 30_000, bytes,
    });
    expect(Array.from(bytes)).toEqual([0, 0, 0, 0]);
    const final = { ...base('transcript_revision', sourceDigest), authority: 'final' as const, text: 'Finaler Text' };
    expect(await coordinator.finalizeLocal({ ...final, sender_id: 'bob', audience_id: 'alice' })).toBe(true);
    expect(await coordinator.finalizeLocal({ ...final, sender_id: 'bob', audience_id: 'alice' })).toBe(false);

    await vi.waitFor(() => expect(correction).toHaveBeenCalledOnce());
    await vi.waitFor(() => expect(store.turns$.value[0]).toMatchObject({
      revision: 3, text: 'Korrigierter Text', state: 'corrected', correctionStatus: 'completed',
    }));
    expect(outbound).toEqual([expect.objectContaining({
      kind: 'correction', revision: 3, authority: 'corrected', text: 'Korrigierter Text',
    })]);
    expect(correction).toHaveBeenCalledWith(expect.objectContaining({
      consentId: 'consent-a', consentVersion: 4, sourceDigest, finalRevision: 2,
    }));
    expect(buffer.snapshot()).toMatchObject({ segments: 0, plaintextBytes: 0 });
    expect(coordinator.snapshot()).toMatchObject({
      attempts: 1, timers: 0, qualityMode: 'semantic_reconstruction', qualityReason: 'quality_healthy',
    });
  });

  it('keeps display mode independent of duration and turns correction off productively', async () => {
    coordinator.applySettings({
      displayMode: 'segment', segmentDurationSeconds: 30, correctEachSegment: false,
      paused: false, ordinaryAudioOverride: false,
    });
    payload$.next({ ...base('transcript_revision', 'c'.repeat(64), 1), authority: 'provisional', text: 'Partial' });
    expect(store.turns$.value).toHaveLength(0);
    await coordinator.finalizeLocal({
      ...base('transcript_revision', 'c'.repeat(64), 2), sender_id: 'bob', audience_id: 'alice',
      authority: 'final', text: 'Final',
    });

    expect(store.turns$.value[0]).toMatchObject({
      text: 'Final', correctionStatus: 'disabled', correctionReason: 'source_correction_disabled',
    });
    expect(coordinator.settings$.value).toMatchObject({ displayMode: 'segment', segmentDurationSeconds: 30 });
    expect(correction).not.toHaveBeenCalled();
  });

  it('makes pause and ordinary override affect runtime instead of only labels', () => {
    coordinator.applySettings({
      displayMode: 'live', segmentDurationSeconds: 120, correctEachSegment: true,
      paused: true, ordinaryAudioOverride: false,
    });
    payload$.next({ ...base('transcript_revision', 'c'.repeat(64), 1), authority: 'provisional', text: 'Ignored' });
    expect(store.turns$.value).toHaveLength(0);

    coordinator.applySettings({
      displayMode: 'live', segmentDurationSeconds: 120, correctEachSegment: true,
      paused: false, ordinaryAudioOverride: false,
    });
    payload$.next({ ...base('transcript_revision', 'c'.repeat(64), 1), authority: 'provisional', text: 'Visible' });
    coordinator.applySettings({
      displayMode: 'live', segmentDurationSeconds: 120, correctEachSegment: true,
      paused: false, ordinaryAudioOverride: true,
    });
    expect(store.turns$.value[0]).toMatchObject({ state: 'ordinary_fallback', revision: 1 });
    expect(buffer.snapshot().segments).toBe(0);
  });

  it('reports a missing source after one bounded wait without retrying', async () => {
    vi.useFakeTimers();
    const now = Date.now();
    vi.setSystemTime(now);
    await coordinator.finalizeLocal({
      ...base('transcript_revision', 'c'.repeat(64), 2), sender_id: 'bob', audience_id: 'alice',
      expires_at_ms: now + 30_000, authority: 'final', text: 'Final bleibt sichtbar',
    });
    expect(store.turns$.value[0].correctionStatus).toBe('awaiting_source');

    await vi.advanceTimersByTimeAsync(5_001);
    expect(store.turns$.value[0]).toMatchObject({
      text: 'Final bleibt sichtbar', state: 'final', correctionStatus: 'missing_source',
      correctionReason: 'source_missing_or_expired',
    });
    expect(correction).not.toHaveBeenCalled();
    expect(coordinator.snapshot()).toMatchObject({ attempts: 1, timers: 0 });
  });

  it('displays a remote final without attempting foreign source correction', async () => {
    payload$.next({
      ...base('transcript_revision', 'c'.repeat(64), 2),
      authority: 'final', text: 'Remote final bleibt sichtbar',
    });

    expect(store.turns$.value[0]).toMatchObject({
      text: 'Remote final bleibt sichtbar', state: 'final', correctionStatus: 'not_requested',
    });
    expect(coordinator.snapshot()).toMatchObject({ attempts: 0, timers: 0, inflight: 0 });
    expect(correction).not.toHaveBeenCalled();
  });

  it.each(['correction_failed', 'missing_source'] as const)(
    'publishes one Hub-authored %s correction result for a local final',
    async authority => {
      const bytes = new Uint8Array([31, authority.length]);
      const sourceDigest = await digest(bytes);
      const outbound: SemanticSpeechPayload[] = [];
      coordinator.outboundCorrection$.subscribe(payload => outbound.push(payload));
      correction.mockReturnValue(of({
        session_id: 'session-a', epoch: 2, turn_id: 'turn-a', revision: 3, supersedes_revision: 2,
        text: 'Unverändertes Final', authority, reason_code: authority, source_digest: sourceDigest,
        correction_attempted: true, operations: [], task_id: 'task-a', idempotent_replay: false,
      }));
      await coordinator.stageSource({
        turnId: 'turn-a', revision: 2, sourceDigest, expiresAtMs: Date.now() + 30_000, bytes,
      });
      const final = {
        ...base('transcript_revision', sourceDigest), sender_id: 'bob', audience_id: 'alice',
        authority: 'final' as const, text: 'Unverändertes Final',
      };
      await coordinator.finalizeLocal(final);
      await vi.waitFor(() => expect(outbound).toHaveLength(1));
      await coordinator.finalizeLocal(final);

      expect(outbound).toEqual([expect.objectContaining({
        kind: 'correction', revision: 3, authority, text: 'Unverändertes Final',
      })]);
      expect(correction).toHaveBeenCalledOnce();
    },
  );

  it('fails closed when bilateral raw-audio correction consent is absent', async () => {
    coordinator.stop('test-rebind');
    coordinator.start({ ...context, correctionConsent: undefined });
    const bytes = new Uint8Array([7, 8]);
    const sourceDigest = await digest(bytes);
    await coordinator.stageSource({
      turnId: 'turn-a', revision: 2, sourceDigest, expiresAtMs: Date.now() + 30_000, bytes,
    });
    await coordinator.finalizeLocal({
      ...base('transcript_revision', sourceDigest), sender_id: 'bob', audience_id: 'alice',
      authority: 'final', text: 'Final',
    });

    expect(store.turns$.value[0]).toMatchObject({
      correctionStatus: 'failed', correctionReason: 'source_correction_consent_required',
    });
    expect(correction).not.toHaveBeenCalled();
    expect(buffer.snapshot().segments).toBe(0);
  });

  it('contains a Hub transport failure and does not retry the finalized segment', async () => {
    const bytes = new Uint8Array([9, 10]);
    const sourceDigest = await digest(bytes);
    correction.mockReturnValue(throwError(() => ({ status: 413, error: { error: { code: 'source_too_large' } } })));
    await coordinator.stageSource({
      turnId: 'turn-a', revision: 2, sourceDigest, expiresAtMs: Date.now() + 30_000, bytes,
    });
    const final = {
      ...base('transcript_revision', sourceDigest), sender_id: 'bob', audience_id: 'alice',
      authority: 'final' as const, text: 'Final bleibt',
    };
    await coordinator.finalizeLocal(final);
    await vi.waitFor(() => expect(store.turns$.value[0]).toMatchObject({
      text: 'Final bleibt', correctionStatus: 'failed', correctionReason: 'source_too_large',
    }));
    await coordinator.finalizeLocal(final);
    expect(correction).toHaveBeenCalledOnce();
    expect(buffer.snapshot().segments).toBe(0);
    expect(coordinator.settings$.value.segmentDurationSeconds).toBe(30);
    expect(store.turns$.value[0].state).toBe('ordinary_fallback');
  });

  it.each([404, 409])('purges and exits the semantic session on Hub status %i', async status => {
    const failures: string[] = [];
    coordinator.fatalFailure$.subscribe(value => failures.push(value));
    const bytes = new Uint8Array([11, status - 400]);
    const sourceDigest = await digest(bytes);
    correction.mockReturnValue(throwError(() => ({
      status, error: { error: { code: status === 404 ? 'source_session_missing' : 'source_epoch_stale' } },
    })));
    await coordinator.stageSource({
      turnId: 'turn-a', revision: 2, sourceDigest, expiresAtMs: Date.now() + 30_000, bytes,
    });
    await coordinator.finalizeLocal({
      ...base('transcript_revision', sourceDigest), sender_id: 'bob', audience_id: 'alice',
      authority: 'final', text: 'Final bleibt im Ordinary-Fallback',
    });

    await vi.waitFor(() => expect(coordinator.snapshot().active).toBe(false));
    expect(failures).toEqual(['speech_session_gone']);
    expect(buffer.snapshot().segments).toBe(0);
    expect(store.turns$.value[0]).toMatchObject({
      text: 'Final bleibt im Ordinary-Fallback', state: 'ordinary_fallback', correctionStatus: 'failed',
    });
    expect(correction).toHaveBeenCalledOnce();
  });

  it('feeds transport backpressure into quality policy and preserves live transcript with Ordinary fallback', () => {
    vi.useFakeTimers();
    vi.setSystemTime(Date.now() + 6_000);
    pressure$.next({ pendingMessages: 100, pendingBytes: 3 * 1024 * 1024 + 1, timers: 0 });

    expect(coordinator.snapshot()).toMatchObject({
      qualityMode: 'ordinary_audio', qualityReason: 'speech_queue_high',
    });
    payload$.next({
      ...base('transcript_revision', 'c'.repeat(64), 1), authority: 'provisional', text: 'Live bleibt sichtbar',
    });
    expect(store.turns$.value[0]).toMatchObject({ text: 'Live bleibt sichtbar', state: 'ordinary_fallback' });
    expect(correction).not.toHaveBeenCalled();
  });

  it('feeds reconstruction failure into Ordinary fallback without changing authoritative words', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(Date.now() + 6_000);
    reconstruct.mockResolvedValueOnce({
      mode: 'ordinary_audio', reasonCode: 'generic_speech_model_unavailable',
      turnId: 'turn-a', revision: 1, authoritativeText: 'Autoritativer Text', audio: null, quality: null,
    });
    payload$.next({
      ...base('transcript_revision', 'c'.repeat(64), 1), authority: 'provisional', text: 'Autoritativer Text',
    });

    await vi.waitFor(() => expect(store.turns$.value[0]).toMatchObject({
      text: 'Autoritativer Text', state: 'ordinary_fallback', playbackReason: 'generic_speech_model_unavailable',
    }));
    expect(coordinator.snapshot().qualityMode).toBe('ordinary_audio');
  });

  it('applies revoke immediately, purges runtime state and requests the Facade fallback', () => {
    const failures: string[] = [];
    coordinator.fatalFailure$.subscribe(value => failures.push(value));
    payload$.next({
      ...base('revoke', 'c'.repeat(64), 1), reason_code: 'consent_revoked', source_digest: null,
    });

    expect(failures).toEqual(['consent_revoked']);
    expect(coordinator.snapshot()).toMatchObject({
      active: false, qualityMode: 'ordinary_audio', qualityReason: 'consent_revoked',
    });
    expect(buffer.snapshot().segments).toBe(0);
  });
});
