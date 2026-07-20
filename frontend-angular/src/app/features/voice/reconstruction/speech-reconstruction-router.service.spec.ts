import { TestBed } from '@angular/core/testing';

import { GenericSpeechReconstructorService } from './generic-speech-reconstructor.service';
import {
  PersonalizedSpeechReconstructorService,
  RECEIVER_SPEECH_ADAPTER_ENGINE,
  ReceiverLoadedSpeechAdapter,
  ReceiverSpeechAdapterEngine,
  ReceiverSpeechContext,
  SpeechAdapterMetadata,
} from './personalized-speech-reconstructor.service';
import { ReceiverLocalSpeechReconstructionRouterService } from './speech-reconstruction-router.service';

class ExplicitTestMockAdapterEngine implements ReceiverSpeechAdapterEngine {
  readonly implementationKind = 'test_mock' as const;
  loaded: string[] = [];
  unloaded: string[] = [];
  cleared: string[] = [];
  failAdapterId: string | null = null;

  async loadLocal(artifactRef: string, expectedSha256: string): Promise<ReceiverLoadedSpeechAdapter> {
    const adapterId = artifactRef.split('/').at(-1)!;
    if (adapterId === this.failAdapterId) throw new Error('speech_adapter_test_load_failed');
    this.loaded.push(adapterId);
    return { adapterId, artifactSha256: expectedSha256, handle: { testOnly: true } };
  }

  async infer(_loaded: ReceiverLoadedSpeechAdapter, semanticPayload: Uint8Array): Promise<Uint8Array> {
    return new Uint8Array([82, 73, 70, 70, semanticPayload.byteLength % 255]);
  }

  async reconstructBase(): Promise<Uint8Array> { return new Uint8Array(); }

  async unload(loaded: ReceiverLoadedSpeechAdapter): Promise<void> {
    this.unloaded.push(loaded.adapterId);
  }

  async clearLocalArtifact(artifactSha256: string): Promise<void> {
    this.cleared.push(artifactSha256);
  }
}

const speakerDigest = 'a'.repeat(64);
const scopeDigest = 'c3c9cff7ccbac1d31e076c10c881ecaf2b83947a81c32443939205d916554c1b';

function metadata(adapterId: string, expiresAtMs = Date.now() + 60_000): SpeechAdapterMetadata {
  return {
    adapter_id: adapterId,
    pair_id: 'pair-test',
    direction: 'sender_to_receiver',
    speaker_digest: speakerDigest,
    scope_digest: scopeDigest,
    base_model_id: 'openvoice-v2-test',
    base_model_digest: 'b'.repeat(64),
    consent_digest: 'c'.repeat(64),
    artifact_ref: `artifact://speech-adapters/test/${adapterId}`,
    artifact_sha256: adapterId === 'speech-adapter-old' ? 'd'.repeat(64) : 'e'.repeat(64),
    expires_at_ms: expiresAtMs,
    consent_expires_at_ms: expiresAtMs + 1_000,
    registry_version: 2,
    status: 'approved',
  };
}

const context: ReceiverSpeechContext = {
  pairId: 'pair-test',
  direction: 'sender_to_receiver',
  speakerDigest,
  scopeDigest,
  baseModelId: 'openvoice-v2-test',
  baseModelDigest: 'b'.repeat(64),
  consentDigest: 'c'.repeat(64),
};

const input = {
  turnId: 'turn-test', revision: 1, text: 'Receiver lokal.', authority: 'final' as const,
  features: [0.1], deadlineAtMs: Date.now() + 10_000, ordinaryAudioAvailable: true,
};

describe('ReceiverLocalSpeechReconstructionRouterService', () => {
  let engine: ExplicitTestMockAdapterEngine;
  let router: ReceiverLocalSpeechReconstructionRouterService;
  const genericReconstruct = vi.fn(async (value: typeof input) => ({
    mode: 'generic' as const, reasonCode: null, turnId: value.turnId, revision: value.revision,
    authoritativeText: value.text,
    audio: { format: 'fixture', play: vi.fn(async () => undefined), release: vi.fn() },
    quality: { engine: 'generic-test', score: 0.8, featureCoverage: 1, provisional: true },
  }));

  beforeEach(() => {
    engine = new ExplicitTestMockAdapterEngine();
    genericReconstruct.mockClear();
    TestBed.configureTestingModule({ providers: [
      ReceiverLocalSpeechReconstructionRouterService,
      PersonalizedSpeechReconstructorService,
      { provide: RECEIVER_SPEECH_ADAPTER_ENGINE, useValue: engine },
      { provide: GenericSpeechReconstructorService, useValue: { reconstruct: genericReconstruct } },
    ] });
    router = TestBed.inject(ReceiverLocalSpeechReconstructionRouterService);
  });

  afterEach(async () => router.clearPersonalization('test_cleanup'));

  it('uses the explicitly activated approved receiver adapter and never calls Hub inference', async () => {
    await router.activatePersonalization({ metadata: metadata('speech-adapter-new'), context });
    const result = await router.reconstruct(input);

    expect(result).toMatchObject({ mode: 'personalized', reasonCode: null });
    expect(result.quality?.engine).toBe('receiver-local-approved-adapter');
    expect(engine.loaded).toEqual(['speech-adapter-new']);
    expect(genericReconstruct).not.toHaveBeenCalled();
    expect(router.snapshot()).toEqual({ personalized: true, adapterId: 'speech-adapter-new' });
    result.audio?.release();
  });

  it('cleans an expired adapter and falls back deterministically to Generic', async () => {
    const now = Date.now();
    await router.activatePersonalization({ metadata: metadata('speech-adapter-new', now + 10), context }, now);
    const cleaned = await router.cleanupExpired(now + 10);
    const result = await router.reconstruct(input);

    expect(cleaned).toBe(true);
    expect(result.mode).toBe('generic');
    expect(engine.unloaded).toContain('speech-adapter-new');
    expect(engine.cleared).toContain('e'.repeat(64));
    expect(router.snapshot().personalized).toBe(false);
  });

  it('unloads stale state on rollback, revoke and a metadata digest mismatch', async () => {
    await router.activatePersonalization({ metadata: metadata('speech-adapter-new'), context });
    await router.activatePersonalization({ metadata: metadata('speech-adapter-old'), context });
    expect(engine.unloaded).toContain('speech-adapter-new');
    expect(router.snapshot().adapterId).toBe('speech-adapter-old');

    await router.revokePersonalization('speech-adapter-old');
    expect(engine.unloaded).toContain('speech-adapter-old');
    expect(router.snapshot().personalized).toBe(false);

    await expect(router.activatePersonalization({
      metadata: { ...metadata('speech-adapter-new'), scope_digest: 'f'.repeat(64) },
      context,
    })).rejects.toThrow('speech_adapter_scope_mismatch');
    expect(router.snapshot().personalized).toBe(false);
  });

  it('cannot retain a previous adapter when explicit replacement activation fails', async () => {
    await router.activatePersonalization({ metadata: metadata('speech-adapter-old'), context });
    engine.failAdapterId = 'speech-adapter-new';

    await expect(router.activatePersonalization({
      metadata: metadata('speech-adapter-new'),
      context,
    })).rejects.toThrow('speech_adapter_test_load_failed');

    expect(engine.unloaded).toContain('speech-adapter-old');
    expect(router.snapshot()).toEqual({ personalized: false, adapterId: null });
    expect((await router.reconstruct(input)).mode).toBe('generic');
  });
});
