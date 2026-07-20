import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, Subject, of } from 'rxjs';

import {
  SPEECH_RECONSTRUCTION_ROUTER,
  SpeechPersonalizationActivation,
} from '../features/voice/reconstruction/speech-reconstruction-router.service';
import { SpeechDelayBufferService } from './speech-delay-buffer.service';
import { SemanticSpeechRuntimeCoordinatorService } from './semantic-speech-runtime-coordinator.service';
import { SemanticSpeechSourceCorrectionApiService } from './semantic-speech-source-correction-api.service';
import { SemanticSpeechPayload, SemanticSpeechTransportService } from './semantic-speech-transport.service';
import { SpeechTranscriptRevisionStore } from './speech-transcript-revision.store';

describe('SemanticSpeechRuntimeCoordinatorService personalization seam', () => {
  const payload$ = new Subject<SemanticSpeechPayload>();
  const pressure$ = new BehaviorSubject({ pendingMessages: 0, pendingBytes: 0, timers: 0 });
  const activatePersonalization = vi.fn(async () => undefined);
  const clearPersonalization = vi.fn(async () => undefined);
  const revokePersonalization = vi.fn(async () => undefined);
  const cleanupExpired = vi.fn(async () => false);
  const reconstruct = vi.fn(async (input: any) => ({
    mode: 'personalized', reasonCode: null, turnId: input.turnId, revision: input.revision,
    authoritativeText: input.text,
    audio: { format: 'fixture', play: vi.fn(async () => undefined), release: vi.fn() },
    quality: { engine: 'explicit-personalized-test', score: 0.9, featureCoverage: 1, provisional: true },
  }));

  const activation: SpeechPersonalizationActivation = {
    metadata: {
      adapter_id: 'speech-adapter-test', pair_id: 'session-a', direction: 'sender_to_receiver',
      speaker_digest: 'a'.repeat(64), scope_digest: 'b'.repeat(64), base_model_id: 'base-test',
      base_model_digest: 'c'.repeat(64), consent_digest: 'd'.repeat(64),
      artifact_ref: 'artifact://speech-adapters/test/speech-adapter-test', artifact_sha256: 'e'.repeat(64),
      expires_at_ms: Date.now() + 60_000, consent_expires_at_ms: Date.now() + 60_000,
      registry_version: 2, status: 'approved',
    },
    context: {
      pairId: 'session-a', direction: 'sender_to_receiver', speakerDigest: 'a'.repeat(64),
      scopeDigest: 'b'.repeat(64), baseModelId: 'base-test', baseModelDigest: 'c'.repeat(64),
      consentDigest: 'd'.repeat(64),
    },
  };

  it('routes reachable runtime reconstruction only after explicit pair-bound activation and cleans on stop', async () => {
    TestBed.configureTestingModule({ providers: [
      SemanticSpeechRuntimeCoordinatorService,
      SpeechDelayBufferService,
      SpeechTranscriptRevisionStore,
      { provide: SemanticSpeechTransportService, useValue: {
        payload$, pressure$, snapshot: () => pressure$.value, stop: vi.fn(),
      } },
      { provide: SemanticSpeechSourceCorrectionApiService, useValue: { correct: () => of(null) } },
      { provide: SPEECH_RECONSTRUCTION_ROUTER, useValue: {
        reconstruct, activatePersonalization, clearPersonalization, revokePersonalization, cleanupExpired,
      } },
    ] });
    const coordinator = TestBed.inject(SemanticSpeechRuntimeCoordinatorService);
    coordinator.start({
      hubUrl: 'http://hub.test', sessionId: 'session-a', epoch: 2, localPeerId: 'bob', remotePeerId: 'alice',
      consentVersion: 1, contractDigest: 'f'.repeat(64),
    });

    await coordinator.activatePersonalization(activation);
    expect(activatePersonalization).toHaveBeenCalledWith(activation, expect.any(Number));
    payload$.next({
      version: 'ananta.semantic-speech.v1', kind: 'transcript_revision', session_id: 'session-a', epoch: 2,
      turn_id: 'turn-a', revision: 1, sender_id: 'alice', audience_id: 'bob', consent_version: 1,
      expires_at_ms: Date.now() + 30_000, contract_digest: 'f'.repeat(64), authority: 'provisional',
      text: 'Explizit personalisiert.',
    });
    await vi.waitFor(() => expect(reconstruct).toHaveBeenCalledOnce());

    await coordinator.revokePersonalization('speech-adapter-test');
    expect(revokePersonalization).toHaveBeenCalledWith('speech-adapter-test');
    coordinator.stop('test_stop');
    expect(clearPersonalization).toHaveBeenCalledWith('test_stop');
    coordinator.ngOnDestroy();
  });

  it('rejects foreign Pair metadata before invoking the receiver router', async () => {
    TestBed.configureTestingModule({ providers: [
      SemanticSpeechRuntimeCoordinatorService,
      SpeechDelayBufferService,
      SpeechTranscriptRevisionStore,
      { provide: SemanticSpeechTransportService, useValue: {
        payload$, pressure$, snapshot: () => pressure$.value, stop: vi.fn(),
      } },
      { provide: SemanticSpeechSourceCorrectionApiService, useValue: { correct: () => of(null) } },
      { provide: SPEECH_RECONSTRUCTION_ROUTER, useValue: {
        reconstruct, activatePersonalization, clearPersonalization, revokePersonalization, cleanupExpired,
      } },
    ] });
    const coordinator = TestBed.inject(SemanticSpeechRuntimeCoordinatorService);
    coordinator.start({
      hubUrl: 'http://hub.test', sessionId: 'session-a', epoch: 2, localPeerId: 'bob', remotePeerId: 'alice',
      consentVersion: 1, contractDigest: 'f'.repeat(64),
    });
    activatePersonalization.mockClear();

    await expect(coordinator.activatePersonalization({
      ...activation,
      metadata: { ...activation.metadata, pair_id: 'session-foreign' },
    })).rejects.toThrow('speech_adapter_runtime_pair_binding_mismatch');
    expect(activatePersonalization).not.toHaveBeenCalled();
    coordinator.ngOnDestroy();
  });
});
