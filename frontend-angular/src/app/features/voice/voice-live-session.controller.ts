import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import {
  VOICE_AUDIO_CAPTURE,
  VOICE_PCM_MEDIA_TYPE,
  VoiceAudioCapturePort,
  VoiceCaptureSource,
} from './voice-audio-capture';
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
  finalizing?(reason?: string): void;
  finalized?(response: VoiceStreamFinalizeResponse, reason?: string): void;
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
  private operationGeneration = 0;
  private starting = false;
  private finalizing: Promise<VoiceStreamFinalizeResponse> | null = null;

  get supported(): boolean {
    return this.capture.supported;
  }

  supportsSource(source: VoiceCaptureSource): boolean {
    return this.capture.supportsSource(source);
  }

  async refreshCaptureCapabilities(): Promise<void> {
    await this.capture.refreshCapabilities?.();
  }

  get active(): boolean {
    return Boolean(this.stream && this.capture.active);
  }

  get sessionId(): string {
    return this.stream?.session_id || '';
  }

  async prepareCapture(source: VoiceCaptureSource): Promise<void> {
    if (this.stream || this.starting) throw new Error('voice.stream.already_active');
    const generation = this.operationGeneration;
    await this.capture.prepare(source);
    if (generation !== this.operationGeneration) {
      await this.capture.stop().catch(() => undefined);
      throw new Error('voice.capture.cancelled');
    }
  }

  async start(
    hubUrl: string,
    request: Omit<VoiceStreamCreateRequest, 'media_type'>,
    idempotencyKey: string,
    observer: VoiceLiveSessionObserver = {},
    source: VoiceCaptureSource = 'microphone',
  ): Promise<VoiceStreamState> {
    if (this.stream || this.starting) throw new Error('voice.stream.already_active');
    const generation = this.operationGeneration;
    this.starting = true;
    this.hubUrl = hubUrl;
    this.observer = observer;
    this.nextSequence = 0;
    this.uploadFailure = null;
    this.failureCleanup = null;
    this.uploadQueue = Promise.resolve();

    try {
      if (!this.capture.prepared) await this.capture.prepare(source);
      this.ensureOperation(generation);
      const created = await firstValueFrom(this.api.createStream(hubUrl, {
        ...request,
        media_type: VOICE_PCM_MEDIA_TYPE,
      }, idempotencyKey));
      this.stream = created.stream;
      this.ensureOperation(generation);
      this.nextSequence = Number(created.stream.next_chunk_sequence || 0);
      await this.capture.start(
        (chunk) => this.enqueue(chunk),
        (error) => this.handleCaptureFailure(error),
        (reason) => this.handleCaptureStopped(reason),
      );
      this.ensureOperation(generation);
      return created.stream;
    } catch (error) {
      await this.capture.stop().catch(() => undefined);
      await this.cancelRemoteStream();
      throw error;
    } finally {
      this.starting = false;
    }
  }

  async finalize(): Promise<VoiceStreamFinalizeResponse> {
    if (this.finalizing) return this.finalizing;
    const operation = this.finalizeOnce();
    this.finalizing = operation;
    try {
      return await operation;
    } finally {
      if (this.finalizing === operation) this.finalizing = null;
    }
  }

  private async finalizeOnce(): Promise<VoiceStreamFinalizeResponse> {
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
    if (this.finalizing) {
      await this.finalizing.catch(() => undefined);
      return;
    }
    this.operationGeneration += 1;
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

  private ensureOperation(generation: number): void {
    if (generation !== this.operationGeneration) throw new Error('voice.capture.cancelled');
  }

  private handleCaptureFailure(error: unknown): void {
    this.operationGeneration += 1;
    this.uploadFailure = error;
    this.scheduleFailureCleanup(error, true);
  }

  private handleCaptureStopped(reason?: string): void {
    if (this.starting) {
      this.handleCaptureFailure(new Error('voice.capture.source_ended'));
      return;
    }
    if (this.uploadFailure || this.failureCleanup || !this.stream || this.finalizing) return;
    const completion = this.finalize();
    this.observer.finalizing?.(reason);
    void completion
      .then((response) => this.observer.finalized?.(response, reason))
      .catch((error) => this.observer.error?.(error));
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
