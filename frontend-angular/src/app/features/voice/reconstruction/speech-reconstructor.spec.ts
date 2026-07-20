import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  GENERIC_SPEECH_ENGINE,
  GenericSpeechEngine,
  GenericSpeechEngineOutput,
  GenericSpeechReconstructorService,
} from './generic-speech-reconstructor.service';
import { ReconstructedSpeechAudio, SpeechReconstructionInput } from './speech-reconstructor';

class FakeAudio implements ReconstructedSpeechAudio {
  readonly format = 'fixture';
  released = false;
  async play(): Promise<void> {}
  release(): void { this.released = true; }
}

class FakeEngine implements GenericSpeechEngine {
  available = true;
  cancelled = 0;
  disposed = 0;
  pending = false;
  outputs: FakeAudio[] = [];

  async synthesize(text: string, features: readonly number[], signal: AbortSignal): Promise<GenericSpeechEngineOutput> {
    if (this.pending) {
      await new Promise<void>((resolve, reject) => {
        signal.addEventListener('abort', () => reject(signal.reason), { once: true });
      });
    }
    const audio = new FakeAudio();
    this.outputs.push(audio);
    return {
      audio,
      quality: { engine: 'fixture', score: 0.8, featureCoverage: features.length / 32, provisional: true },
    };
  }

  cancel(): void { this.cancelled += 1; }
  async dispose(): Promise<void> { this.disposed += 1; }
}

function request(revision = 1): SpeechReconstructionInput {
  return {
    turnId: 'turn-a', revision, text: 'Unveränderter Text', authority: 'provisional',
    features: [0.2], deadlineAtMs: Date.now() + 10_000, ordinaryAudioAvailable: true,
  };
}

describe('GenericSpeechReconstructorService', () => {
  let engine: FakeEngine;
  let service: GenericSpeechReconstructorService;

  beforeEach(() => {
    vi.useRealTimers();
    engine = new FakeEngine();
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ providers: [
      GenericSpeechReconstructorService,
      { provide: GENERIC_SPEECH_ENGINE, useValue: engine },
    ] });
    service = TestBed.inject(GenericSpeechReconstructorService);
  });

  it('returns receiver-local generic audio without changing authoritative words', async () => {
    const result = await service.reconstruct(request());
    expect(result.mode).toBe('generic');
    expect(result.authoritativeText).toBe('Unveränderter Text');
    expect(result.quality).toMatchObject({ engine: 'fixture', provisional: true });
    expect(result.audio?.format).toBe('fixture');
    expect(service.snapshot()).toEqual({ active: 0, timers: 0, destroyed: false });
  });

  it('falls back immediately when the local generic engine is unavailable', async () => {
    engine.available = false;
    const result = await service.reconstruct(request());
    expect(result.mode).toBe('ordinary_audio');
    expect(result.reasonCode).toBe('generic_speech_model_unavailable');
  });

  it('cancels an older revision and leaves no active synthesis resources', async () => {
    engine.pending = true;
    const older = service.reconstruct(request(1));
    await Promise.resolve();
    const newer = service.reconstruct(request(2));
    await expect(older).resolves.toMatchObject({ mode: 'ordinary_audio' });
    service.supersede('turn-a', 3);
    await expect(newer).resolves.toMatchObject({ mode: 'ordinary_audio' });
    expect(engine.cancelled).toBeGreaterThanOrEqual(2);
    expect(service.snapshot().active).toBe(0);
  });

  it('honours caller cancellation, deadlines and destruction cleanup', async () => {
    engine.pending = true;
    const controller = new AbortController();
    const cancelled = service.reconstruct({ ...request(), signal: controller.signal });
    controller.abort();
    await expect(cancelled).resolves.toMatchObject({ mode: 'ordinary_audio' });

    await expect(service.reconstruct({ ...request(2), deadlineAtMs: Date.now() - 1 }))
      .resolves.toMatchObject({ reasonCode: 'generic_speech_deadline_elapsed' });
    await service.destroy();
    expect(engine.disposed).toBe(1);
    expect(service.snapshot()).toEqual({ active: 0, timers: 0, destroyed: true });
  });
});
