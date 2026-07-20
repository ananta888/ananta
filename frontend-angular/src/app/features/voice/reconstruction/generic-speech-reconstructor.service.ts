import { Injectable, InjectionToken, OnDestroy, inject } from '@angular/core';

import {
  ReconstructedSpeechAudio,
  SpeechReconstructionInput,
  SpeechReconstructionQuality,
  SpeechReconstructionResult,
  SpeechReconstructor,
} from './speech-reconstructor';

export interface GenericSpeechEngineOutput {
  audio: ReconstructedSpeechAudio;
  quality: SpeechReconstructionQuality;
}

export interface GenericSpeechEngine {
  readonly available: boolean;
  synthesize(
    text: string,
    features: readonly number[],
    signal: AbortSignal,
  ): Promise<GenericSpeechEngineOutput>;
  cancel(): void;
  dispose(): Promise<void>;
}

class BrowserSpeechAudio implements ReconstructedSpeechAudio {
  readonly format = 'browser-speech-synthesis';
  private released = false;

  constructor(private readonly text: string) {}

  async play(): Promise<void> {
    if (this.released || !globalThis.speechSynthesis || !globalThis.SpeechSynthesisUtterance) {
      throw new Error('generic_speech_audio_released');
    }
    await new Promise<void>((resolve, reject) => {
      const utterance = new SpeechSynthesisUtterance(this.text);
      utterance.onend = () => resolve();
      utterance.onerror = () => reject(new Error('generic_speech_playback_failed'));
      speechSynthesis.speak(utterance);
    });
  }

  release(): void {
    this.released = true;
    globalThis.speechSynthesis?.cancel();
  }
}

class BrowserGenericSpeechEngine implements GenericSpeechEngine {
  readonly available = Boolean(globalThis.speechSynthesis && globalThis.SpeechSynthesisUtterance);

  async synthesize(
    text: string,
    features: readonly number[],
    signal: AbortSignal,
  ): Promise<GenericSpeechEngineOutput> {
    if (!this.available) throw new Error('generic_speech_model_unavailable');
    if (signal.aborted) throw signal.reason ?? new DOMException('aborted', 'AbortError');
    return {
      audio: new BrowserSpeechAudio(text),
      quality: {
        engine: 'browser-speech-synthesis',
        score: features.length ? 0.6 : 0.45,
        featureCoverage: Math.min(1, features.length / 32),
        provisional: true,
      },
    };
  }

  cancel(): void { globalThis.speechSynthesis?.cancel(); }
  async dispose(): Promise<void> { this.cancel(); }
}

export const GENERIC_SPEECH_ENGINE = new InjectionToken<GenericSpeechEngine>(
  'GENERIC_SPEECH_ENGINE',
  { providedIn: 'root', factory: () => new BrowserGenericSpeechEngine() },
);

interface ActiveReconstruction {
  revision: number;
  controller: AbortController;
  reasonCode: string;
}

@Injectable({ providedIn: 'root' })
export class GenericSpeechReconstructorService implements SpeechReconstructor, OnDestroy {
  private readonly engine = inject(GENERIC_SPEECH_ENGINE);
  private readonly active = new Map<string, ActiveReconstruction>();
  private destroyed = false;

  async reconstruct(input: SpeechReconstructionInput): Promise<SpeechReconstructionResult> {
    this.validate(input);
    if (this.destroyed) return this.fallback(input, 'generic_speech_destroyed');
    if (!this.engine.available) return this.fallback(input, 'generic_speech_model_unavailable');

    this.supersede(input.turnId, input.revision);
    const controller = new AbortController();
    const active: ActiveReconstruction = {
      revision: input.revision,
      controller,
      reasonCode: 'generic_speech_cancelled',
    };
    this.active.set(input.turnId, active);
    const onCallerAbort = () => {
      active.reasonCode = 'generic_speech_cancelled';
      controller.abort(input.signal?.reason);
    };
    input.signal?.addEventListener('abort', onCallerAbort, { once: true });
    const remainingMs = input.deadlineAtMs - Date.now();
    if (remainingMs <= 0) {
      this.active.delete(input.turnId);
      input.signal?.removeEventListener('abort', onCallerAbort);
      return this.fallback(input, 'generic_speech_deadline_elapsed');
    }
    const timeout = globalThis.setTimeout(() => {
      active.reasonCode = 'generic_speech_deadline_elapsed';
      controller.abort(new DOMException('deadline elapsed', 'TimeoutError'));
      this.engine.cancel();
    }, remainingMs);

    try {
      const output = await this.engine.synthesize(input.text, Object.freeze([...(input.features ?? [])]), controller.signal);
      if (controller.signal.aborted || this.active.get(input.turnId) !== active) {
        output.audio.release();
        return this.fallback(input, active.reasonCode);
      }
      this.active.delete(input.turnId);
      return {
        mode: 'generic',
        reasonCode: null,
        turnId: input.turnId,
        revision: input.revision,
        authoritativeText: input.text,
        audio: output.audio,
        quality: Object.freeze({ ...output.quality, provisional: true }),
      };
    } catch {
      return this.fallback(input, active.reasonCode === 'generic_speech_cancelled'
        ? 'generic_speech_runtime_failed' : active.reasonCode);
    } finally {
      globalThis.clearTimeout(timeout);
      input.signal?.removeEventListener('abort', onCallerAbort);
      if (this.active.get(input.turnId) === active) this.active.delete(input.turnId);
    }
  }

  supersede(turnId: string, revision: number): void {
    const current = this.active.get(turnId);
    if (!current || revision <= current.revision) return;
    current.reasonCode = 'generic_speech_revision_superseded';
    current.controller.abort(new DOMException('revision superseded', 'AbortError'));
    this.engine.cancel();
    this.active.delete(turnId);
  }

  async destroy(): Promise<void> {
    if (this.destroyed) return;
    this.destroyed = true;
    for (const current of this.active.values()) {
      current.reasonCode = 'generic_speech_destroyed';
      current.controller.abort(new DOMException('destroyed', 'AbortError'));
    }
    this.active.clear();
    this.engine.cancel();
    await this.engine.dispose();
  }

  ngOnDestroy(): void { void this.destroy(); }

  snapshot(): Readonly<{ active: number; timers: number; destroyed: boolean }> {
    return Object.freeze({ active: this.active.size, timers: this.active.size, destroyed: this.destroyed });
  }

  private validate(input: SpeechReconstructionInput): void {
    if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(input.turnId)) throw new Error('generic_speech_turn_invalid');
    if (!Number.isSafeInteger(input.revision) || input.revision < 1) throw new Error('generic_speech_revision_invalid');
    if (typeof input.text !== 'string' || new TextEncoder().encode(input.text).byteLength > 16_384) {
      throw new Error('generic_speech_text_invalid');
    }
    if (!Number.isSafeInteger(input.deadlineAtMs) || input.deadlineAtMs < 1) throw new Error('generic_speech_deadline_invalid');
    if (input.features && (
      input.features.length > 160
      || input.features.some(value => !Number.isFinite(value) || value < -1 || value > 1)
    )) throw new Error('generic_speech_features_invalid');
  }

  private fallback(input: SpeechReconstructionInput, reasonCode: string): SpeechReconstructionResult {
    return {
      mode: input.ordinaryAudioAvailable ? 'ordinary_audio' : 'unavailable',
      reasonCode,
      turnId: input.turnId,
      revision: input.revision,
      authoritativeText: input.text,
      audio: null,
      quality: null,
    };
  }
}
