import { TestBed } from '@angular/core/testing';

import {
  PersonalizedSpeechReconstructorService,
  RECEIVER_SPEECH_ADAPTER_ENGINE,
  ReceiverLoadedSpeechAdapter,
  ReceiverSpeechAdapterEngine,
  ReceiverSpeechContext,
  SpeechAdapterMetadata,
} from './personalized-speech-reconstructor.service';

class FakeEngine implements ReceiverSpeechAdapterEngine {
  readonly implementationKind = 'test_mock' as const;
  failLoad = false;
  failBase = false;
  blockLoad = false;
  unloaded: string[] = [];
  cleared: string[] = [];
  private resolveLoadStarted!: () => void;
  private resolveLoad!: () => void;
  readonly loadStarted = new Promise<void>(resolve => { this.resolveLoadStarted = resolve; });
  private readonly loadGate = new Promise<void>(resolve => { this.resolveLoad = resolve; });

  async loadLocal(artifactRef: string, expectedSha256: string): Promise<ReceiverLoadedSpeechAdapter> {
    if (this.failLoad) throw new Error('load failed');
    if (this.blockLoad) {
      this.resolveLoadStarted();
      await this.loadGate;
    }
    return { adapterId: artifactRef.split('/').at(-1)!, artifactSha256: expectedSha256, handle: {} };
  }

  releaseBlockedLoad(): void { this.resolveLoad(); }

  async infer(_loaded: ReceiverLoadedSpeechAdapter, semanticPayload: Uint8Array): Promise<Uint8Array> {
    return new Uint8Array([1, ...semanticPayload]);
  }

  async reconstructBase(semanticPayload: Uint8Array): Promise<Uint8Array> {
    if (this.failBase) throw new Error('base failed');
    return new Uint8Array([2, ...semanticPayload]);
  }

  async unload(loaded: ReceiverLoadedSpeechAdapter): Promise<void> {
    this.unloaded.push(loaded.adapterId);
  }

  async clearLocalArtifact(artifactSha256: string): Promise<void> {
    this.cleared.push(artifactSha256);
  }
}

const metadata: SpeechAdapterMetadata = {
  adapter_id: 'speech-adapter-test',
  pair_id: 'pair-test',
  direction: 'sender_to_receiver',
  speaker_digest: 'a'.repeat(64),
  scope_digest: 'c3c9cff7ccbac1d31e076c10c881ecaf2b83947a81c32443939205d916554c1b',
  base_model_id: 'openvoice-v2-test',
  base_model_digest: 'c'.repeat(64),
  consent_digest: 'd'.repeat(64),
  artifact_ref: 'artifact://speech-adapters/test/speech-adapter-test',
  artifact_sha256: 'e'.repeat(64),
  expires_at_ms: 2_000,
  consent_expires_at_ms: 3_000,
  registry_version: 2,
  status: 'approved',
};

const context: ReceiverSpeechContext = {
  pairId: metadata.pair_id,
  direction: metadata.direction,
  speakerDigest: metadata.speaker_digest,
  scopeDigest: metadata.scope_digest,
  baseModelId: metadata.base_model_id,
  baseModelDigest: metadata.base_model_digest,
  consentDigest: metadata.consent_digest,
};

describe('PersonalizedSpeechReconstructorService', () => {
  let engine: FakeEngine;
  let service: PersonalizedSpeechReconstructorService;

  beforeEach(() => {
    engine = new FakeEngine();
    TestBed.configureTestingModule({
      providers: [
        PersonalizedSpeechReconstructorService,
        { provide: RECEIVER_SPEECH_ADAPTER_ENGINE, useValue: engine },
      ],
    });
    service = TestBed.inject(PersonalizedSpeechReconstructorService);
  });

  it('loads and infers only an approved current receiver-local adapter', async () => {
    const result = await service.reconstruct(metadata, context, new Uint8Array([9]), undefined, 1_000);
    expect(result.mode).toBe('adapted');
    expect(Array.from(result.audio)).toEqual([1, 9]);
    expect(result.adapterId).toBe(metadata.adapter_id);
  });

  it('unloads on expiry and deterministically falls back to the base model', async () => {
    await service.activate(metadata, context, 1_000);
    const result = await service.reconstruct(metadata, context, new Uint8Array([9]), undefined, 2_000);
    expect(result.mode).toBe('base');
    expect(result.reasonCode).toBe('speech_adapter_expired');
    expect(engine.unloaded).toContain(metadata.adapter_id);
    expect(engine.cleared).toContain(metadata.artifact_sha256);
  });

  it('uses ordinary audio when adapter and base reconstruction fail', async () => {
    engine.failLoad = true;
    engine.failBase = true;
    const result = await service.reconstruct(
      metadata,
      context,
      new Uint8Array([9]),
      new Uint8Array([7]),
      1_000,
    );
    expect(result.mode).toBe('ordinary_audio');
    expect(Array.from(result.audio)).toEqual([7]);
    expect(result.reasonCode).toBe('speech_adapter_runtime_failed');
  });

  it('revalidates a revoke that races a long load before publishing the handle', async () => {
    engine.blockLoad = true;
    const resultPromise = service.reconstruct(
      metadata,
      context,
      new Uint8Array([9]),
      undefined,
      1_000,
    );
    await engine.loadStarted;

    await service.revoke(metadata.adapter_id);
    engine.releaseBlockedLoad();
    const result = await resultPromise;

    expect(result.mode).toBe('base');
    expect(result.reasonCode).toBe('speech_adapter_authority_changed');
    expect(engine.unloaded).toContain(metadata.adapter_id);
    expect(engine.cleared).toContain(metadata.artifact_sha256);
    await expect(service.cleanupExpired(1_000)).resolves.toBe(false);
  });

  it('rejects a rollback registry-version change that races a long load', async () => {
    engine.blockLoad = true;
    const mutableMetadata = { ...metadata };
    const resultPromise = service.reconstruct(
      mutableMetadata,
      context,
      new Uint8Array([9]),
      undefined,
      1_000,
    );
    await engine.loadStarted;

    mutableMetadata.registry_version += 1;
    engine.releaseBlockedLoad();
    const result = await resultPromise;

    expect(result.mode).toBe('base');
    expect(result.reasonCode).toBe('speech_adapter_authority_changed');
    expect(engine.unloaded).toContain(metadata.adapter_id);
    expect(engine.cleared).toContain(metadata.artifact_sha256);
  });
});

describe('FailClosedReceiverSpeechAdapterEngine production default', () => {
  it('reports the unreleased browser runtime and leaves no reachable adapter handle', async () => {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ providers: [PersonalizedSpeechReconstructorService] });
    const service = TestBed.inject(PersonalizedSpeechReconstructorService);

    await expect(service.activate(metadata, context, 1_000))
      .rejects.toThrow('speech_adapter_browser_engine_not_released');
    await expect(service.cleanupExpired(1_000)).resolves.toBe(false);
  });
});
