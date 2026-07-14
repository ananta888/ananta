import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import { VOICE_AUDIO_CAPTURE, VOICE_PCM_MEDIA_TYPE, VoiceAudioCapturePort } from './voice-audio-capture';
import { VoiceApiService } from './voice-api.service';
import {
  VoiceStreamChunkResponse,
  VoiceStreamCreateRequest,
  VoiceStreamEvent,
  VoiceStreamFinalizeResponse,
  VoiceStreamState,
} from './voice.models';

export interface VoiceLiveSessionObserver {
  event?(event: VoiceStreamEvent | null | undefined, stream: VoiceStreamState): void;
  chunkAccepted?(sequence: number, response: VoiceStreamChunkResponse): void;
  error?(error: unknown): void;
}

@Injectable()
export class VoiceLiveSessionController {
  private readonly api = inject(VoiceApiService);
  private readonly capture: VoiceAudioCapturePort = inject(VOICE_AUDIO_CAPTURE);

  private hubUrl = '';
  private stream: VoiceStreamState | null = null;
  private nextSequence = 0;
  private uploadQueue: Promise<void> = Promise.resolve();
  private uploadFailure: unknown = null;
  private failureCleanup: Promise<void> | null = null;
  private observer: VoiceLiveSessionObserver = {};

  get supported(): boolean {
    return this.capture.supported;
  }

  get active(): boolean {
    return Boolean(this.stream && this.capture.active);
  }

  get sessionId(): string {
    return this.stream?.session_id || '';
  }

  async start(
    hubUrl: string,
    request: Omit<VoiceStreamCreateRequest, 'media_type'>,
    idempotencyKey: string,
    observer: VoiceLiveSessionObserver = {},
  ): Promise<VoiceStreamState> {
    if (this.stream) throw new Error('voice.stream.already_active');
    this.hubUrl = hubUrl;
    this.observer = observer;
    this.nextSequence = 0;
    this.uploadFailure = null;
    this.failureCleanup = null;
    this.uploadQueue = Promise.resolve();

    const created = await firstValueFrom(this.api.createStream(hubUrl, {
      ...request,
      media_type: VOICE_PCM_MEDIA_TYPE,
    }, idempotencyKey));
    this.stream = created.stream;
    this.nextSequence = Number(created.stream.next_chunk_sequence || 0);
    try {
      await this.capture.start(
        (chunk) => this.enqueue(chunk),
        (error) => {
          this.uploadFailure = error;
          this.scheduleFailureCleanup(error, true);
        },
      );
    } catch (error) {
      await this.cancelRemoteStream();
      throw error;
    }
    return created.stream;
  }

  async finalize(): Promise<VoiceStreamFinalizeResponse> {
    const sessionId = this.requireSessionId();
    try {
      await this.capture.stop();
    } catch (error) {
      await this.cancelRemoteStream();
      throw error;
    }
    await this.uploadQueue;
    if (this.uploadFailure) {
      const failure = this.uploadFailure;
      await this.cancelRemoteStream();
      throw failure;
    }
    try {
      const response = await firstValueFrom(this.api.finalizeStream(this.hubUrl, sessionId));
      if (response.event) this.observer.event?.(response.event, response.stream);
      this.stream = null;
      return response;
    } catch (error) {
      await this.cancelRemoteStream();
      throw error;
    }
  }

  async cancel(): Promise<void> {
    let captureFailure: unknown = null;
    try {
      await this.capture.stop();
    } catch (error) {
      captureFailure = error;
    } finally {
      await this.uploadQueue.catch(() => undefined);
      await this.cancelRemoteStream();
    }
    if (captureFailure) throw captureFailure;
  }

  private enqueue(chunk: ArrayBuffer): void {
    if (!chunk.byteLength || !this.stream || this.uploadFailure) return;
    const sequence = this.nextSequence;
    this.nextSequence += 1;
    const sessionId = this.stream.session_id;
    this.uploadQueue = this.uploadQueue.then(async () => {
      if (this.uploadFailure) return;
      try {
        const response = await firstValueFrom(
          this.api.pushStreamChunk(this.hubUrl, sessionId, sequence, chunk),
        );
        this.stream = response.stream;
        this.observer.chunkAccepted?.(sequence, response);
        this.observer.event?.(response.event, response.stream);
      } catch (error) {
        this.uploadFailure = error;
        this.scheduleFailureCleanup(error, false);
      }
    });
  }

  private requireSessionId(): string {
    const sessionId = this.stream?.session_id;
    if (!sessionId) throw new Error('voice.stream.not_active');
    return sessionId;
  }

  private async cancelRemoteStream(): Promise<void> {
    const sessionId = this.stream?.session_id;
    this.stream = null;
    if (!sessionId || !this.hubUrl) return;
    try {
      await firstValueFrom(this.api.cancelStream(this.hubUrl, sessionId));
    } catch {
      // Preserve the original capture/finalization error. Hub cleanup is retryable.
    }
  }

  private scheduleFailureCleanup(error: unknown, waitForUploadQueue: boolean): void {
    if (this.failureCleanup) return;
    this.failureCleanup = (async () => {
      await this.capture.stop().catch(() => undefined);
      if (waitForUploadQueue) await this.uploadQueue.catch(() => undefined);
      await this.cancelRemoteStream();
    })().finally(() => {
      this.failureCleanup = null;
      this.observer.error?.(error);
    });
  }
}

export function voicePartialText(event: VoiceStreamEvent | null | undefined): string {
  const payload = event?.payload;
  return String(payload?.stable_text || payload?.finalized_text || payload?.text || '').trim();
}
