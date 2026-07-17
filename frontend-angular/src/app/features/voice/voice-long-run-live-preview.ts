import { Injectable, InjectionToken, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import { VOICE_PCM_MEDIA_TYPE, VOICE_PCM_SAMPLE_RATE } from './voice-audio-capture';
import { VoiceApiService } from './voice-api.service';
import {
  VoiceStreamChunkResponse,
  VoiceStreamCreateRequest,
  VoiceStreamEvent,
  VoiceStreamState,
} from './voice.models';

const PCM_SAMPLE_BYTES = Int16Array.BYTES_PER_ELEMENT;
const PCM_BYTES_PER_SECOND = VOICE_PCM_SAMPLE_RATE * PCM_SAMPLE_BYTES;
let previewEpochCounter = 0;
/** whisper.cpp re-transcribes the growing window, so avoid a request per 500ms capture chunk. */
export const VOICE_LONG_RUN_LIVE_PREVIEW_BATCH_BYTES = PCM_BYTES_PER_SECOND * 2;

export interface VoiceLongRunLivePreviewContext {
  hubUrl: string;
  liveRunId: string;
  profileId: string;
  configurationSessionId?: string;
  language?: string;
  segmentDurationSeconds: number;
  initialSegmentSequence?: number;
}

export interface VoiceLongRunLivePreviewUpdate {
  liveRunId: string;
  segmentSequence: number;
  streamSessionId: string;
  text: string;
  event: VoiceStreamEvent;
}

export interface VoiceLongRunLivePreviewObserver {
  /** Fires before each segment capability is created, including rotations. */
  segmentStarted?(segmentSequence: number): void;
  preview?(update: VoiceLongRunLivePreviewUpdate): void;
  error?(error: unknown): void;
}

export interface VoiceLongRunLivePreviewPort {
  readonly active: boolean;
  readonly disabled: boolean;
  readonly segmentSequence: number | null;
  start(
    context: VoiceLongRunLivePreviewContext,
    observer?: VoiceLongRunLivePreviewObserver,
  ): Promise<void>;
  /** Mirrors an existing capture chunk; this port never owns a capture source. */
  acceptPcm(chunk: ArrayBuffer): void;
  /** Drains the current segment, deletes its preview stream, then opens the next. */
  endSegment(): Promise<void>;
  stop(): Promise<void>;
  dispose(): Promise<void>;
}

export interface VoiceLongRunLivePreviewLimits {
  maxQueuedChunks: number;
  maxQueuedBytes: number;
}

export const VOICE_LONG_RUN_LIVE_PREVIEW_LIMITS =
  new InjectionToken<VoiceLongRunLivePreviewLimits>('VOICE_LONG_RUN_LIVE_PREVIEW_LIMITS', {
    providedIn: 'root',
    factory: () => ({ maxQueuedChunks: 8, maxQueuedBytes: 512 * 1024 }),
  });

interface NormalizedPreviewContext {
  hubUrl: string;
  liveRunId: string;
  profileId: string;
  configurationSessionId?: string;
  language?: string;
  segmentDurationSeconds: number;
  initialSegmentSequence: number;
  previewEpoch: string;
}

interface PcmAccumulator {
  parts: ArrayBuffer[];
  byteLength: number;
}

interface PreviewWindow {
  generation: number;
  hubUrl: string;
  liveRunId: string;
  segmentSequence: number;
  sessionId: string;
  nextChunkSequence: number;
  maxAudioBytes: number;
  carryBudgetOverflow: boolean;
  admittedAudioBytes: number;
  accepting: boolean;
  accumulator: PcmAccumulator;
  queuedChunks: number;
  queuedBytes: number;
  uploadQueue: Promise<void>;
  deleteOperation: Promise<void> | null;
  lastPartialRevision: number | null;
  lastPartialText: string;
}

@Injectable({ providedIn: 'root' })
export class VoiceLongRunLivePreviewCoordinator implements VoiceLongRunLivePreviewPort {
  private readonly api = inject(VoiceApiService);
  private readonly configuredLimits = inject(VOICE_LONG_RUN_LIVE_PREVIEW_LIMITS);

  private readonly limits = normalizeLimits(this.configuredLimits);
  private context: NormalizedPreviewContext | null = null;
  private observer: VoiceLongRunLivePreviewObserver = {};
  private window: PreviewWindow | null = null;
  private rolloverBatches: ArrayBuffer[] = [];
  private rolloverBatchBytes = 0;
  private rolloverAccumulator: PcmAccumulator = emptyAccumulator();
  private runGeneration = 0;
  private runActive = false;
  private runDisabled = false;
  private stoppingRequested = false;
  private failureReported = false;
  private openingOperation: Promise<void> | null = null;
  private rolloverOperation: Promise<void> | null = null;
  private stopOperation: Promise<void> | null = null;

  get active(): boolean {
    return this.runActive && !this.runDisabled && !this.stoppingRequested;
  }

  get disabled(): boolean {
    return this.runDisabled;
  }

  get segmentSequence(): number | null {
    return this.window?.segmentSequence
      ?? (this.context ? this.context.initialSegmentSequence : null);
  }

  async start(
    context: VoiceLongRunLivePreviewContext,
    observer: VoiceLongRunLivePreviewObserver = {},
  ): Promise<void> {
    if (this.context || this.openingOperation || this.rolloverOperation || this.stopOperation) {
      throw new Error('voice.long_run.live_preview_already_active');
    }
    const normalized = normalizeContext(context);
    const generation = this.runGeneration + 1;
    this.runGeneration = generation;
    this.context = normalized;
    this.observer = observer;
    this.runActive = true;
    this.runDisabled = false;
    this.stoppingRequested = false;
    this.failureReported = false;
    this.clearRolloverBuffer();

    const opening = this.openWindow(normalized.initialSegmentSequence, generation);
    this.openingOperation = opening;
    try {
      await opening;
    } catch (error) {
      this.disableForRun(error, generation);
    } finally {
      if (this.openingOperation === opening) this.openingOperation = null;
    }
  }

  acceptPcm(chunk: ArrayBuffer): void {
    if (!this.active || !chunk.byteLength) return;
    if (chunk.byteLength % PCM_SAMPLE_BYTES !== 0) {
      this.disableForRun(new Error('voice.long_run.live_preview_invalid_pcm'), this.runGeneration);
      return;
    }

    const audio = chunk.slice(0);
    const window = this.window;
    if (window?.accepting) {
      this.acceptWindowPcm(window, audio);
      return;
    }
    this.bufferRolloverPcm(audio);
  }

  endSegment(): Promise<void> {
    if (!this.active) return Promise.resolve();
    if (this.rolloverOperation) return this.rolloverOperation;
    const window = this.window;
    if (!window) return this.openingOperation || Promise.resolve();
    window.accepting = false;
    this.flushWindowAccumulator(window);

    const operation = this.rotateWindow(window);
    this.rolloverOperation = operation;
    void operation
      .finally(() => {
        if (this.rolloverOperation === operation) this.rolloverOperation = null;
      })
      .catch(() => undefined);
    return operation;
  }

  stop(): Promise<void> {
    if (this.stopOperation) return this.stopOperation;
    if (!this.context && !this.openingOperation && !this.window) return Promise.resolve();

    const generation = this.runGeneration;
    this.stoppingRequested = true;
    this.clearRolloverBuffer();
    if (this.window) {
      this.window.accepting = false;
      this.flushWindowAccumulator(this.window);
    }
    const operation = this.stopRun(generation);
    this.stopOperation = operation;
    void operation
      .finally(() => {
        if (this.stopOperation === operation) this.stopOperation = null;
      })
      .catch(() => undefined);
    return operation;
  }

  dispose(): Promise<void> {
    return this.stop();
  }

  private async openWindow(segmentSequence: number, generation: number): Promise<void> {
    const context = this.context;
    if (!context || !this.isCurrentRun(generation)) return;
    try {
      this.observer.segmentStarted?.(segmentSequence);
    } catch {
      // Rendering callbacks never own the transport lifecycle.
    }
    const request: VoiceStreamCreateRequest = {
      filename: `voice-long-run-preview-${segmentSequence}.pcm`,
      language: context.language,
      profile_id: context.profileId,
      configuration_session_id: context.configurationSessionId,
      media_type: VOICE_PCM_MEDIA_TYPE,
      deadline_seconds: 300,
      max_audio_seconds: context.segmentDurationSeconds,
      live_run_id: context.liveRunId,
      live_run_segment_sequence: segmentSequence,
    };
    const response = await firstValueFrom(this.api.createStream(
      context.hubUrl,
      request,
      `voice-ui:long-run-preview:${context.liveRunId}:${context.previewEpoch}:${segmentSequence}`,
    ));
    const stream = response.stream;
    let window: PreviewWindow;
    try {
      window = this.createWindow(context, stream, segmentSequence, generation);
    } catch (error) {
      const sessionId = String(stream.session_id || '').trim();
      if (sessionId) {
        await firstValueFrom(this.api.cancelStream(context.hubUrl, sessionId)).catch(() => undefined);
      }
      throw error;
    }

    if (!this.isCurrentRun(generation)) {
      await this.deleteWindow(window).catch(() => undefined);
      return;
    }
    this.window = window;
    this.flushRolloverBuffer(window);
  }

  private createWindow(
    context: NormalizedPreviewContext,
    stream: VoiceStreamState,
    segmentSequence: number,
    generation: number,
  ): PreviewWindow {
    const sessionId = String(stream.session_id || '').trim();
    if (!sessionId) throw new Error('voice.long_run.live_preview_invalid_session');
    const maxAudioBytes = effectiveAudioBudget(stream, context.segmentDurationSeconds);
    const requestedAudioBytes = pcmBytesForSeconds(context.segmentDurationSeconds);
    const acceptedAudioBytes = nonNegativeInteger(stream['accepted_audio_bytes']) ?? 0;
    if (acceptedAudioBytes > maxAudioBytes) {
      throw new Error('voice.long_run.live_preview_invalid_audio_budget');
    }
    return {
      generation,
      hubUrl: context.hubUrl,
      liveRunId: context.liveRunId,
      segmentSequence,
      sessionId,
      nextChunkSequence: nonNegativeInteger(stream.next_chunk_sequence) ?? 0,
      maxAudioBytes,
      carryBudgetOverflow: maxAudioBytes === requestedAudioBytes,
      admittedAudioBytes: acceptedAudioBytes,
      accepting: true,
      accumulator: emptyAccumulator(),
      queuedChunks: 0,
      queuedBytes: 0,
      uploadQueue: Promise.resolve(),
      deleteOperation: null,
      lastPartialRevision: null,
      lastPartialText: '',
    };
  }

  private acceptWindowPcm(window: PreviewWindow, audio: ArrayBuffer): void {
    if (!this.isCurrentWindow(window) || !window.accepting) return;
    const remaining = window.maxAudioBytes - window.admittedAudioBytes;
    const boundedBytes = Math.min(audio.byteLength, remaining);
    const acceptedBytes = boundedBytes - boundedBytes % PCM_SAMPLE_BYTES;
    if (acceptedBytes > 0) {
      const acceptedAudio = acceptedBytes === audio.byteLength ? audio : audio.slice(0, acceptedBytes);
      appendPcm(window.accumulator, acceptedAudio);
      window.admittedAudioBytes += acceptedAudio.byteLength;
      while (window.accumulator.byteLength >= VOICE_LONG_RUN_LIVE_PREVIEW_BATCH_BYTES) {
        const batch = takePcm(window.accumulator, VOICE_LONG_RUN_LIVE_PREVIEW_BATCH_BYTES);
        if (!this.enqueueWindowBatch(window, batch)) return;
      }
    }
    if (window.carryBudgetOverflow && acceptedBytes < audio.byteLength && this.active) {
      this.bufferRolloverPcm(audio.slice(acceptedBytes));
    }
  }

  private flushWindowAccumulator(window: PreviewWindow): void {
    if (!window.accumulator.byteLength || !this.canFinishUploads(window.generation)) return;
    const batch = takePcm(window.accumulator, window.accumulator.byteLength);
    this.enqueueWindowBatch(window, batch);
  }

  private enqueueWindowBatch(window: PreviewWindow, audio: ArrayBuffer): boolean {
    if (this.window !== window || !this.canFinishUploads(window.generation)) return false;
    if (!this.reserveQueueCapacity(audio.byteLength)) return false;

    const sequence = window.nextChunkSequence;
    window.nextChunkSequence += 1;
    window.queuedChunks += 1;
    window.queuedBytes += audio.byteLength;
    const upload = window.uploadQueue.then(async () => {
      if (!this.canFinishUploads(window.generation)) return;
      const response = await firstValueFrom(this.api.pushStreamChunk(
        window.hubUrl,
        window.sessionId,
        sequence,
        audio,
      ));
      this.publishPartial(window, response);
    });
    window.uploadQueue = upload
      .catch((error) => {
        // Durable segment reservation may win the race and remove this fenced
        // preview capability while its final best-effort upload is draining.
        if (!window.accepting && isMissingCapability(error)) return;
        this.disableForRun(error, window.generation, window);
      })
      .finally(() => {
        window.queuedChunks -= 1;
        window.queuedBytes -= audio.byteLength;
      });
    return true;
  }

  private bufferRolloverPcm(audio: ArrayBuffer): void {
    appendPcm(this.rolloverAccumulator, audio);
    while (this.rolloverAccumulator.byteLength >= VOICE_LONG_RUN_LIVE_PREVIEW_BATCH_BYTES) {
      const batch = takePcm(this.rolloverAccumulator, VOICE_LONG_RUN_LIVE_PREVIEW_BATCH_BYTES);
      if (!this.reserveQueueCapacity(batch.byteLength)) return;
      this.rolloverBatches.push(batch);
      this.rolloverBatchBytes += batch.byteLength;
    }
  }

  private flushRolloverBuffer(window: PreviewWindow): void {
    while (this.active && this.isCurrentWindow(window) && window.accepting && this.rolloverBatches.length) {
      const batch = this.rolloverBatches.shift();
      if (!batch) break;
      this.rolloverBatchBytes -= batch.byteLength;
      this.acceptWindowPcm(window, batch);
    }
    if (this.active && this.isCurrentWindow(window) && window.accepting
      && this.rolloverAccumulator.byteLength) {
      this.acceptWindowPcm(
        window,
        takePcm(this.rolloverAccumulator, this.rolloverAccumulator.byteLength),
      );
    }
    if (!this.active) this.clearRolloverBuffer();
  }

  private reserveQueueCapacity(nextBytes: number): boolean {
    const queuedChunks = (this.window?.queuedChunks || 0) + this.rolloverBatches.length;
    const queuedBytes = (this.window?.queuedBytes || 0) + this.rolloverBatchBytes;
    if (queuedChunks + 1 <= this.limits.maxQueuedChunks
      && queuedBytes + nextBytes <= this.limits.maxQueuedBytes) {
      return true;
    }
    this.disableForRun(new Error('voice.long_run.live_preview_queue_exhausted'), this.runGeneration);
    return false;
  }

  private async rotateWindow(window: PreviewWindow): Promise<void> {
    try {
      await window.uploadQueue;
      await this.deleteWindow(window);
      if (this.window === window) this.window = null;
      if (!this.isCurrentRun(window.generation) || this.runDisabled) return;
      await this.openWindow(window.segmentSequence + 1, window.generation);
    } catch (error) {
      this.disableForRun(error, window.generation, window);
    }
  }

  private async stopRun(generation: number): Promise<void> {
    const opening = this.openingOperation;
    const rollover = this.rolloverOperation;
    if (opening) await opening.catch(() => undefined);
    if (rollover) await rollover.catch(() => undefined);
    const window = this.window;
    if (window) {
      window.accepting = false;
      this.flushWindowAccumulator(window);
      await window.uploadQueue.catch(() => undefined);
      await this.deleteWindow(window).catch((error) => this.reportFailure(error, generation));
    }
    this.runActive = false;
    if (this.window === window) this.window = null;
    this.context = null;
    this.observer = {};
    this.runDisabled = false;
    this.stoppingRequested = false;
    this.failureReported = false;
  }

  private deleteWindow(window: PreviewWindow): Promise<void> {
    if (!window.deleteOperation) {
      window.deleteOperation = firstValueFrom(
        this.api.cancelStream(window.hubUrl, window.sessionId),
      ).then(
        () => undefined,
        (error) => {
          // Hub-owned segment/run cleanup is allowed to close the same
          // ephemeral capability first. The client still issues one DELETE.
          if (!isMissingCapability(error)) throw error;
        },
      );
    }
    return window.deleteOperation;
  }

  private publishPartial(window: PreviewWindow, response: VoiceStreamChunkResponse): void {
    const event = response.event;
    if (!event || !window.accepting || !this.isCurrentWindow(window) || !this.active) return;
    const text = partialText(event);
    if (!text) return;
    const revision = partialRevision(event, response.stream);
    if (revision != null && window.lastPartialRevision != null
      && revision < window.lastPartialRevision) return;
    if (text === window.lastPartialText) return;
    window.lastPartialRevision = revision ?? window.lastPartialRevision;
    window.lastPartialText = text;
    this.observer.preview?.({
      liveRunId: window.liveRunId,
      segmentSequence: window.segmentSequence,
      streamSessionId: window.sessionId,
      text,
      event,
    });
  }

  private disableForRun(error: unknown, generation: number, window = this.window): void {
    if (generation !== this.runGeneration || this.runDisabled) return;
    this.runDisabled = true;
    this.runActive = false;
    this.clearRolloverBuffer();
    if (window) window.accepting = false;
    this.reportFailure(error, generation);
    if (window) {
      void window.uploadQueue
        .then(() => this.deleteWindow(window))
        .catch(() => undefined);
    }
  }

  private reportFailure(error: unknown, generation: number): void {
    if (generation !== this.runGeneration || this.failureReported) return;
    this.failureReported = true;
    try {
      this.observer.error?.(error);
    } catch {
      // Observer failures must not block remote preview cleanup or the long run.
    }
  }

  private isCurrentRun(generation: number): boolean {
    return generation === this.runGeneration && this.active;
  }

  private canFinishUploads(generation: number): boolean {
    return generation === this.runGeneration && this.runActive && !this.runDisabled;
  }

  private isCurrentWindow(window: PreviewWindow): boolean {
    return this.window === window && this.isCurrentRun(window.generation);
  }

  private clearRolloverBuffer(): void {
    this.rolloverBatches = [];
    this.rolloverBatchBytes = 0;
    this.rolloverAccumulator = emptyAccumulator();
  }
}

export const VOICE_LONG_RUN_LIVE_PREVIEW = new InjectionToken<VoiceLongRunLivePreviewPort>(
  'VOICE_LONG_RUN_LIVE_PREVIEW',
  {
    providedIn: 'root',
    factory: () => inject(VoiceLongRunLivePreviewCoordinator),
  },
);

function normalizeContext(context: VoiceLongRunLivePreviewContext): NormalizedPreviewContext {
  const hubUrl = context.hubUrl.trim();
  const liveRunId = context.liveRunId.trim();
  const profileId = context.profileId.trim();
  const segmentDurationSeconds = Number(context.segmentDurationSeconds);
  const initialSegmentSequence = context.initialSegmentSequence ?? 0;
  if (!hubUrl || !liveRunId || !profileId
    || !Number.isFinite(segmentDurationSeconds) || segmentDurationSeconds <= 0
    || !Number.isInteger(initialSegmentSequence) || initialSegmentSequence < 0) {
    throw new Error('voice.long_run.live_preview_invalid_context');
  }
  return {
    hubUrl,
    liveRunId,
    profileId,
    configurationSessionId: context.configurationSessionId?.trim() || undefined,
    language: context.language?.trim() || undefined,
    segmentDurationSeconds,
    initialSegmentSequence,
    previewEpoch: createPreviewEpoch(),
  };
}

function normalizeLimits(limits: VoiceLongRunLivePreviewLimits): VoiceLongRunLivePreviewLimits {
  const maxQueuedChunks = Number(limits.maxQueuedChunks);
  const maxQueuedBytes = Number(limits.maxQueuedBytes);
  if (!Number.isInteger(maxQueuedChunks) || maxQueuedChunks <= 0
    || !Number.isInteger(maxQueuedBytes) || maxQueuedBytes <= 0) {
    throw new Error('voice.long_run.live_preview_invalid_queue_limits');
  }
  return { maxQueuedChunks, maxQueuedBytes };
}

function effectiveAudioBudget(stream: VoiceStreamState, requestedSeconds: number): number {
  const requestedBytes = pcmBytesForSeconds(requestedSeconds);
  const hasAuthoritativeBudget = stream.max_audio_bytes != null;
  const authoritativeBytes = positiveInteger(stream.max_audio_bytes);
  if (hasAuthoritativeBudget && authoritativeBytes == null) {
    throw new Error('voice.long_run.live_preview_invalid_audio_budget');
  }
  const fallbackSeconds = positiveNumber(stream.max_audio_seconds) ?? requestedSeconds;
  const fallbackBytes = Math.floor(fallbackSeconds * PCM_BYTES_PER_SECOND / PCM_SAMPLE_BYTES)
    * PCM_SAMPLE_BYTES;
  const value = Math.min(requestedBytes, authoritativeBytes ?? fallbackBytes);
  if (value <= 0) throw new Error('voice.long_run.live_preview_invalid_audio_budget');
  return value;
}

function pcmBytesForSeconds(seconds: number): number {
  return Math.floor(seconds * PCM_BYTES_PER_SECOND / PCM_SAMPLE_BYTES) * PCM_SAMPLE_BYTES;
}

function partialText(event: VoiceStreamEvent): string {
  const payload = event.payload;
  return String(payload?.stable_text || payload?.finalized_text || payload?.text || '').trim();
}

function partialRevision(event: VoiceStreamEvent, stream: VoiceStreamState): number | null {
  return nonNegativeInteger(event.payload?.['text_revision'])
    ?? nonNegativeInteger(event['sequence'])
    ?? nonNegativeInteger(stream.next_chunk_sequence);
}

function positiveInteger(value: unknown): number | null {
  const numeric = Number(value);
  return Number.isInteger(numeric) && numeric > 0 ? numeric : null;
}

function nonNegativeInteger(value: unknown): number | null {
  const numeric = Number(value);
  return Number.isInteger(numeric) && numeric >= 0 ? numeric : null;
}

function positiveNumber(value: unknown): number | null {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric > 0 ? numeric : null;
}

function isMissingCapability(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false;
  return Number((error as { status?: unknown }).status) === 404;
}

function emptyAccumulator(): PcmAccumulator {
  return { parts: [], byteLength: 0 };
}

function appendPcm(accumulator: PcmAccumulator, audio: ArrayBuffer): void {
  if (!audio.byteLength) return;
  accumulator.parts.push(audio);
  accumulator.byteLength += audio.byteLength;
}

function takePcm(accumulator: PcmAccumulator, byteLength: number): ArrayBuffer {
  if (!Number.isInteger(byteLength) || byteLength <= 0 || byteLength > accumulator.byteLength) {
    throw new Error('voice.long_run.live_preview_invalid_pcm_batch');
  }
  const output = new Uint8Array(byteLength);
  let offset = 0;
  while (offset < byteLength) {
    const part = accumulator.parts[0];
    if (!part) throw new Error('voice.long_run.live_preview_invalid_pcm_batch');
    const copiedBytes = Math.min(part.byteLength, byteLength - offset);
    output.set(new Uint8Array(part, 0, copiedBytes), offset);
    offset += copiedBytes;
    if (copiedBytes === part.byteLength) accumulator.parts.shift();
    else accumulator.parts[0] = part.slice(copiedBytes);
  }
  accumulator.byteLength -= byteLength;
  return output.buffer;
}

function createPreviewEpoch(): string {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  const entropy = new Uint32Array(4);
  globalThis.crypto?.getRandomValues?.(entropy);
  previewEpochCounter += 1;
  return `${Date.now().toString(36)}-${previewEpochCounter.toString(36)}-${
    Array.from(entropy, (value) => value.toString(16).padStart(8, '0')).join('')
  }`;
}
