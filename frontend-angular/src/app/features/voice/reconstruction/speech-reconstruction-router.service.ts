import { Injectable, InjectionToken, inject } from '@angular/core';

import {
  PersonalizedSpeechReconstructorService,
  ReceiverSpeechContext,
  SpeechAdapterMetadata,
} from './personalized-speech-reconstructor.service';
import { GenericSpeechReconstructorService } from './generic-speech-reconstructor.service';
import {
  ReconstructedSpeechAudio,
  SpeechReconstructionInput,
  SpeechReconstructionResult,
  SpeechReconstructor,
} from './speech-reconstructor';

export interface SpeechPersonalizationActivation {
  readonly metadata: SpeechAdapterMetadata;
  readonly context: ReceiverSpeechContext;
}

export interface SpeechReconstructionRouterPort extends SpeechReconstructor {
  activatePersonalization(activation: SpeechPersonalizationActivation, nowMs?: number): Promise<void>;
  clearPersonalization(reasonCode?: string): Promise<void>;
  revokePersonalization(adapterId: string): Promise<void>;
  cleanupExpired(nowMs?: number): Promise<boolean>;
}

export const SPEECH_RECONSTRUCTION_ROUTER = new InjectionToken<SpeechReconstructionRouterPort>(
  'SPEECH_RECONSTRUCTION_ROUTER',
  {
    providedIn: 'root',
    factory: () => inject(ReceiverLocalSpeechReconstructionRouterService),
  },
);

/** Selects receiver-local personalization only after an explicit activation. */
@Injectable({ providedIn: 'root' })
export class ReceiverLocalSpeechReconstructionRouterService implements SpeechReconstructionRouterPort {
  private readonly generic = inject(GenericSpeechReconstructorService);
  private readonly personalized = inject(PersonalizedSpeechReconstructorService);
  private activation: SpeechPersonalizationActivation | null = null;

  async activatePersonalization(
    activation: SpeechPersonalizationActivation,
    nowMs = Date.now(),
  ): Promise<void> {
    const candidate = Object.freeze({
      metadata: Object.freeze({ ...activation.metadata }),
      context: Object.freeze({ ...activation.context }),
    });
    this.activation = null;
    try {
      await this.personalized.activate(candidate.metadata, candidate.context, nowMs);
      this.activation = candidate;
    } catch (error) {
      await this.personalized.unload();
      throw error;
    }
  }

  async reconstruct(input: SpeechReconstructionInput): Promise<SpeechReconstructionResult> {
    const activation = this.activation;
    if (!activation) return this.generic.reconstruct(input);
    if (await this.cleanupExpired()) return this.generic.reconstruct(input);
    const semanticPayload = Uint8Array.from(new TextEncoder().encode(JSON.stringify({
      authority: input.authority,
      features: [...(input.features ?? [])],
      revision: input.revision,
      text: input.text,
      turn_id: input.turnId,
      version: 'ananta.receiver-speech-reconstruction-input.v1',
    })));
    const result = await this.personalized.reconstruct(
      activation.metadata,
      activation.context,
      semanticPayload,
      undefined,
    );
    semanticPayload.fill(0);
    if (result.mode === 'adapted' && result.audio.byteLength > 0) {
      return {
        mode: 'personalized',
        reasonCode: null,
        turnId: input.turnId,
        revision: input.revision,
        authoritativeText: input.text,
        audio: new ReceiverAdapterSpeechAudio(result.audio),
        quality: Object.freeze({
          engine: 'receiver-local-approved-adapter',
          score: 0.6,
          featureCoverage: input.features?.length ? Math.min(1, input.features.length / 32) : 0,
          provisional: true,
        }),
      };
    }
    const reasonCode = result.reasonCode || 'speech_adapter_runtime_failed';
    await this.clearPersonalization(reasonCode);
    const fallback = await this.generic.reconstruct(input);
    return fallback.mode === 'generic' ? { ...fallback, reasonCode } : fallback;
  }

  async clearPersonalization(_reasonCode = 'speech_adapter_deactivated'): Promise<void> {
    this.activation = null;
    await this.personalized.unload();
  }

  async revokePersonalization(adapterId: string): Promise<void> {
    if (this.activation?.metadata.adapter_id !== adapterId) return;
    this.activation = null;
    await this.personalized.revoke(adapterId);
  }

  async cleanupExpired(nowMs = Date.now()): Promise<boolean> {
    if (!this.activation) return false;
    const cleaned = await this.personalized.cleanupExpired(nowMs);
    if (cleaned) this.activation = null;
    return cleaned;
  }

  supersede(turnId: string, revision: number): void {
    this.generic.supersede(turnId, revision);
  }

  async destroy(): Promise<void> {
    await this.clearPersonalization('speech_reconstruction_router_destroyed');
    await this.generic.destroy();
  }

  snapshot(): Readonly<{ personalized: boolean; adapterId: string | null }> {
    return Object.freeze({
      personalized: this.activation !== null,
      adapterId: this.activation?.metadata.adapter_id ?? null,
    });
  }
}

class ReceiverAdapterSpeechAudio implements ReconstructedSpeechAudio {
  readonly format = 'audio/wav';
  private bytes: Uint8Array | null;
  private objectUrl: string | null = null;
  private element: HTMLAudioElement | null = null;

  constructor(bytes: Uint8Array) {
    this.bytes = bytes.slice();
  }

  async play(): Promise<void> {
    if (!this.bytes) throw new Error('speech_adapter_audio_released');
    if (typeof globalThis.Audio !== 'function') throw new Error('speech_adapter_audio_playback_unavailable');
    const copy = new Uint8Array(this.bytes.byteLength);
    copy.set(this.bytes);
    this.objectUrl ??= URL.createObjectURL(new Blob([copy.buffer], { type: this.format }));
    this.element = new Audio(this.objectUrl);
    await this.element.play();
  }

  release(): void {
    this.element?.pause();
    this.element = null;
    this.bytes?.fill(0);
    this.bytes = null;
    if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
    this.objectUrl = null;
  }
}
