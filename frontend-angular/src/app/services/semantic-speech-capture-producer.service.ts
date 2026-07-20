import { Injectable, OnDestroy, inject } from '@angular/core';
import { Subject, Subscription, firstValueFrom } from 'rxjs';

import {
  VOICE_AUDIO_CAPTURE,
  VOICE_PCM_MEDIA_TYPE,
  VOICE_PCM_SAMPLE_RATE,
  VoiceAudioCapturePort,
  pcm16ChunksToWav,
} from '../features/voice/voice-audio-capture';
import { VoiceApiService } from '../features/voice/voice-api.service';
import {
  VoiceStreamChunkResponse,
  VoiceStreamEvent,
  VoiceStreamFinalizeResponse,
  VoiceStreamState,
} from '../features/voice/voice.models';
import { sha256Bytes } from '../shared/crypto/sha256';
import {
  SemanticSpeechRuntimeContext,
  SemanticSpeechRuntimeCoordinatorService,
} from './semantic-speech-runtime-coordinator.service';
import { SemanticSpeechSettings } from './semantic-speech-settings';
import {
  SemanticSpeechPayload,
  SemanticSpeechTransportService,
} from './semantic-speech-transport.service';

export interface SemanticSpeechCaptureProducerContext extends SemanticSpeechRuntimeContext {
  readonly profileId: string;
  readonly configurationSessionId?: string;
}

interface CaptureSegment {
  readonly generation: number;
  readonly context: SemanticSpeechCaptureProducerContext;
  readonly sessionId: string;
  readonly serverMaxBytes: number;
  readonly sourceParts: ArrayBuffer[];
  uploadQueue: Promise<void>;
  uploadFailure: unknown;
  nextChunkSequence: number;
  targetBytes: number;
  admittedBytes: number;
  sourceBytes: number;
  lastPublishedRevision: number;
  lastPublishedText: string;
  accepting: boolean;
}

const PCM_BYTES_PER_SECOND = VOICE_PCM_SAMPLE_RATE * Int16Array.BYTES_PER_ELEMENT;
const MAX_QUEUED_CHUNKS = 8;
const MAX_QUEUED_BYTES = 512 * 1024;
const MAX_INFLIGHT_FINALIZATIONS = 2;
const MAX_PUBLISHED_REVISION_KEYS = 2_048;
const MAX_RUN_MS = 8 * 60 * 60 * 1_000;
const PAYLOAD_TTL_MS = 5 * 60 * 1_000;
const MAX_TRANSCRIPT_BYTES = 16_384;
const SEGMENT_DURATIONS = new Set([10, 30, 60, 90, 120]);

/**
 * Productive microphone-to-semantic-speech producer.
 *
 * Capture stays behind the existing Browser/Capacitor VoiceAudioCapturePort;
 * transcription and revision authority stay in the Hub Voice Runtime. This
 * adapter only rotates bounded PCM streams, stages a local encrypted WAV for
 * explicitly consented source correction, and publishes admitted transcript
 * revisions through the already-authorized E2EE semantic transport.
 */
@Injectable()
export class SemanticSpeechCaptureProducerService implements OnDestroy {
  private readonly capture: VoiceAudioCapturePort = inject(VOICE_AUDIO_CAPTURE);
  private readonly api = inject(VoiceApiService);
  private readonly runtime = inject(SemanticSpeechRuntimeCoordinatorService);
  private readonly transport = inject(SemanticSpeechTransportService);
  private readonly subscription = new Subscription();
  private readonly finalizingSegments = new Set<CaptureSegment>();
  private readonly publishedRevisionKeys = new Set<string>();
  private context: SemanticSpeechCaptureProducerContext | null = null;
  private settings: SemanticSpeechSettings = this.runtime.settings$.value;
  private segment: CaptureSegment | null = null;
  private rollover: ArrayBuffer[] = [];
  private generation = 0;
  private active = false;
  private failureReported = false;
  private queuedChunks = 0;
  private queuedBytes = 0;
  private openingOperation: Promise<void> | null = null;
  private rotationOperation: Promise<void> | null = null;
  private stopOperation: Promise<void> | null = null;
  private admissionQueue: Promise<void> = Promise.resolve();
  private transportQueue: Promise<void> = Promise.resolve();
  private runTimer: ReturnType<typeof setTimeout> | null = null;

  readonly failure$ = new Subject<string>();

  constructor() {
    this.subscription.add(this.runtime.outboundCorrection$.subscribe(payload => {
      const generation = this.generation;
      if (!this.isCurrent(generation)) return;
      this.enqueueTransport(payload, generation);
    }));
  }

  async start(context: SemanticSpeechCaptureProducerContext): Promise<void> {
    if (this.stopOperation) await this.stopOperation;
    if (this.context || this.active || this.openingOperation || this.rotationOperation) {
      throw new Error('semantic_speech_capture_already_active');
    }
    const normalized = this.validateContext(context);
    if (!this.capture.supported || !this.capture.supportsSource('microphone')) {
      throw new Error('semantic_speech_capture_unsupported');
    }
    const generation = ++this.generation;
    this.context = normalized;
    this.settings = this.validateSettings(this.runtime.settings$.value);
    this.active = true;
    this.failureReported = false;
    this.publishedRevisionKeys.clear();
    this.admissionQueue = Promise.resolve();
    this.transportQueue = Promise.resolve();
    try {
      await this.capture.prepare('microphone');
      this.ensureCurrent(generation);
      await this.openNextSegment(generation);
      this.ensureCurrent(generation);
      this.armRunTimer(generation);
      await this.capture.start(
        chunk => this.acceptCaptureChunk(chunk, generation),
        error => this.fail(error, generation),
        reason => this.fail(new Error(reason || 'semantic_speech_capture_stopped'), generation),
        { maxDurationSeconds: MAX_RUN_MS / 1_000 },
      );
      this.ensureCurrent(generation);
    } catch (error) {
      await this.stop('semantic_speech_capture_start_failed').catch(() => undefined);
      throw error;
    }
  }

  /** Update display/correction policy immediately and duration at a safe boundary. */
  applySettings(settings: SemanticSpeechSettings): void {
    this.settings = this.validateSettings(settings);
    const segment = this.segment;
    if (!segment || !segment.accepting) return;
    segment.targetBytes = Math.min(
      segment.serverMaxBytes,
      this.settings.segmentDurationSeconds * PCM_BYTES_PER_SECOND,
    );
    if (segment.admittedBytes >= segment.targetBytes) this.requestRotation(segment);
  }

  /** Refresh consent without reopening an otherwise identical stream. */
  rebind(context: SemanticSpeechCaptureProducerContext): void {
    const normalized = this.validateContext(context);
    const current = this.context;
    if (!current) return;
    if (this.transportContextKey(current) !== this.transportContextKey(normalized)) {
      this.fail(new Error('semantic_speech_capture_context_changed'), this.generation);
      return;
    }
    this.context = normalized;
  }

  stop(reasonCode = 'semantic_speech_capture_stopped'): Promise<void> {
    if (this.stopOperation) return this.stopOperation;
    if (!this.context && !this.active && !this.segment && !this.openingOperation) return Promise.resolve();
    const operation = this.stopRun(reasonCode);
    this.stopOperation = operation;
    void operation.finally(() => {
      if (this.stopOperation === operation) this.stopOperation = null;
    }).catch(() => undefined);
    return operation;
  }

  snapshot(): Readonly<{
    active: boolean;
    streamSessionId: string | null;
    admittedBytes: number;
    rolloverChunks: number;
    queuedChunks: number;
    queuedBytes: number;
    finalizingSegments: number;
    timers: number;
  }> {
    return Object.freeze({
      active: this.active,
      streamSessionId: this.segment?.sessionId ?? null,
      admittedBytes: this.segment?.admittedBytes ?? 0,
      rolloverChunks: this.rollover.length,
      queuedChunks: this.queuedChunks,
      queuedBytes: this.queuedBytes,
      finalizingSegments: this.finalizingSegments.size,
      timers: this.runTimer === null ? 0 : 1,
    });
  }

  ngOnDestroy(): void {
    void this.stop('semantic_speech_capture_destroyed');
    this.subscription.unsubscribe();
    this.failure$.complete();
  }

  private async openNextSegment(generation: number): Promise<void> {
    const context = this.requireContext();
    const settings = this.settings;
    const nonce = randomToken();
    const opening = firstValueFrom(this.api.createStream(context.hubUrl, {
      filename: `semantic-speech-${nonce}.pcm`,
      language: context.language,
      profile_id: context.profileId,
      configuration_session_id: context.configurationSessionId,
      media_type: VOICE_PCM_MEDIA_TYPE,
      deadline_seconds: Math.max(300, settings.segmentDurationSeconds + 60),
      max_audio_seconds: settings.segmentDurationSeconds,
    }, `semantic-speech:${context.sessionId}:${context.epoch}:${nonce}`)).then(async response => {
      const stream = response.stream;
      const sessionId = String(stream?.session_id || '').trim();
      if (!sessionId || !Number.isSafeInteger(stream.next_chunk_sequence) || stream.next_chunk_sequence < 0) {
        throw new Error('semantic_speech_stream_contract_invalid');
      }
      if (!this.isCurrent(generation)) {
        await this.cancelRemote(context.hubUrl, sessionId);
        return;
      }
      const serverMaxBytes = this.streamAudioBudget(stream, settings.segmentDurationSeconds);
      const acceptedBytes = this.nonNegativeInteger(stream['accepted_audio_bytes']) ?? 0;
      if (acceptedBytes !== 0) throw new Error('semantic_speech_stream_replay_unsafe');
      this.segment = {
        generation,
        context,
        sessionId,
        serverMaxBytes,
        sourceParts: [],
        uploadQueue: Promise.resolve(),
        uploadFailure: null,
        nextChunkSequence: stream.next_chunk_sequence,
        targetBytes: Math.min(serverMaxBytes, this.settings.segmentDurationSeconds * PCM_BYTES_PER_SECOND),
        admittedBytes: 0,
        sourceBytes: 0,
        lastPublishedRevision: 0,
        lastPublishedText: '',
        accepting: true,
      };
      this.flushRollover(generation);
    });
    this.openingOperation = opening;
    try {
      await opening;
    } finally {
      if (this.openingOperation === opening) this.openingOperation = null;
    }
  }

  private acceptCaptureChunk(chunk: ArrayBuffer, generation: number): void {
    if (!(chunk instanceof ArrayBuffer) || chunk.byteLength === 0) return;
    const owned = chunk.slice(0);
    new Uint8Array(chunk).fill(0);
    if (owned.byteLength % Int16Array.BYTES_PER_ELEMENT !== 0) {
      wipe(owned);
      this.fail(new Error('semantic_speech_pcm_alignment_invalid'), generation);
      return;
    }
    if (!this.isCurrent(generation)) {
      wipe(owned);
      return;
    }
    const segment = this.segment;
    if (!segment?.accepting) {
      this.bufferRollover(owned, generation);
      return;
    }
    this.acceptIntoSegment(segment, owned);
  }

  private acceptIntoSegment(segment: CaptureSegment, audio: ArrayBuffer): void {
    let offset = 0;
    try {
      while (offset < audio.byteLength && this.isCurrent(segment.generation)) {
        if (this.segment !== segment || !segment.accepting) {
          this.bufferRollover(audio.slice(offset), segment.generation);
          return;
        }
        const remaining = Math.max(0, segment.targetBytes - segment.admittedBytes);
        if (remaining === 0) {
          this.requestRotation(segment);
          continue;
        }
        const take = Math.min(remaining, audio.byteLength - offset);
        const alignedTake = take - take % Int16Array.BYTES_PER_ELEMENT;
        if (alignedTake <= 0) throw new Error('semantic_speech_pcm_alignment_invalid');
        const accepted = audio.slice(offset, offset + alignedTake);
        const sourceCopy = accepted.slice(0);
        segment.sourceParts.push(sourceCopy);
        segment.sourceBytes += sourceCopy.byteLength;
        segment.admittedBytes += sourceCopy.byteLength;
        if (!this.enqueueUpload(segment, accepted)) return;
        offset += alignedTake;
        if (segment.admittedBytes >= segment.targetBytes) this.requestRotation(segment);
      }
    } catch (error) {
      this.fail(error, segment.generation, segment.sessionId);
    } finally {
      wipe(audio);
    }
  }

  private enqueueUpload(segment: CaptureSegment, audio: ArrayBuffer): boolean {
    if (!this.reserveQueue(audio.byteLength, segment.generation, segment.sessionId)) {
      wipe(audio);
      return false;
    }
    const sequence = segment.nextChunkSequence++;
    const operation = segment.uploadQueue.then(async () => {
      if (!this.canFinish(segment.generation) || segment.uploadFailure) return;
      const response = await firstValueFrom(this.api.pushStreamChunk(
        segment.context.hubUrl,
        segment.sessionId,
        sequence,
        audio,
      ));
      this.publishPartial(segment, response);
    });
    segment.uploadQueue = operation.catch(error => {
      segment.uploadFailure = error;
      this.fail(error, segment.generation, segment.sessionId);
    }).finally(() => {
      wipe(audio);
      this.queuedChunks = Math.max(0, this.queuedChunks - 1);
      this.queuedBytes = Math.max(0, this.queuedBytes - audio.byteLength);
    });
    return true;
  }

  private publishPartial(segment: CaptureSegment, response: VoiceStreamChunkResponse): void {
    if (!this.canFinish(segment.generation) || segment.uploadFailure) return;
    const event = response.event;
    const text = eventText(event);
    if (!text) return;
    const revision = backendRevision(event);
    if (revision <= segment.lastPublishedRevision) return;
    this.assertTranscript(text);
    if (text === segment.lastPublishedText) return;
    segment.lastPublishedRevision = revision;
    segment.lastPublishedText = text;
    const payload = this.transcriptPayload(segment, revision, 'provisional', text, null);
    void this.enqueueAdmission(payload, segment.generation);
  }

  private requestRotation(segment: CaptureSegment): void {
    if (this.segment !== segment || !segment.accepting || this.rotationOperation) return;
    segment.accepting = false;
    this.segment = null;
    const operation = this.rotate(segment);
    this.rotationOperation = operation;
    void operation.catch(error => this.fail(error, segment.generation, segment.sessionId)).finally(() => {
      if (this.rotationOperation === operation) this.rotationOperation = null;
    });
  }

  private async rotate(segment: CaptureSegment): Promise<void> {
    if (this.finalizingSegments.size >= MAX_INFLIGHT_FINALIZATIONS) {
      throw new Error('semantic_speech_finalization_backpressure');
    }
    const source = this.prepareSource(segment);
    const finalization = this.finalizeSegment(segment, source);
    this.finalizingSegments.add(segment);
    void finalization.catch(error => this.fail(error, segment.generation, segment.sessionId)).finally(() => {
      this.finalizingSegments.delete(segment);
    });
    await this.openNextSegment(segment.generation);
  }

  private async prepareSource(segment: CaptureSegment): Promise<Readonly<{
    digest: string;
    expiresAtMs: number;
  }> | null> {
    const context = this.context;
    const shouldCorrect = this.settings.correctEachSegment
      && Boolean(context?.correctionConsent)
      && Number(context?.correctionConsent?.expiresAtMs || 0) > Date.now();
    if (!shouldCorrect || segment.sourceBytes === 0) {
      wipeParts(segment.sourceParts);
      segment.sourceBytes = 0;
      return null;
    }
    const wav = new Uint8Array(pcm16ChunksToWav(segment.sourceParts, segment.sourceBytes));
    wipeParts(segment.sourceParts);
    segment.sourceBytes = 0;
    try {
      const digestBytes = await sha256Bytes(wav);
      const digest = hex(digestBytes);
      digestBytes.fill(0);
      const expiresAtMs = Math.min(
        Date.now() + PAYLOAD_TTL_MS,
        Number(context!.correctionConsent!.expiresAtMs),
      );
      if (expiresAtMs <= Date.now()) return null;
      await this.runtime.stageSource({
        turnId: segment.sessionId,
        revision: Math.max(1, segment.lastPublishedRevision + 1),
        sourceDigest: digest,
        expiresAtMs,
        bytes: wav,
      });
      return Object.freeze({ digest, expiresAtMs });
    } catch {
      // The authoritative Hub final remains publishable without raw source.
      return null;
    } finally {
      wav.fill(0);
    }
  }

  private async finalizeSegment(
    segment: CaptureSegment,
    sourceOperation: Promise<Readonly<{ digest: string; expiresAtMs: number }> | null>,
  ): Promise<void> {
    await segment.uploadQueue;
    if (segment.uploadFailure) throw segment.uploadFailure;
    const source = await sourceOperation;
    const response = await firstValueFrom(
      this.api.finalizeStream(segment.context.hubUrl, segment.sessionId),
    );
    this.ensureCurrent(segment.generation);
    const payload = this.finalPayload(segment, response, source);
    await this.enqueueAdmission(payload, segment.generation);
  }

  private finalPayload(
    segment: CaptureSegment,
    response: VoiceStreamFinalizeResponse,
    source: Readonly<{ digest: string; expiresAtMs: number }> | null,
  ): SemanticSpeechPayload {
    const event = response.event;
    if (!event || !['final', 'final_replayed'].includes(String(event.event_type))) {
      throw new Error('semantic_speech_final_event_missing');
    }
    const revision = backendRevision(event);
    if (revision <= segment.lastPublishedRevision) {
      throw new Error('semantic_speech_final_revision_stale');
    }
    const text = response.result?.text;
    if (typeof text !== 'string') throw new Error('semantic_speech_final_result_missing');
    this.assertTranscript(text);
    const eventTextValue = event.payload?.result?.text;
    if (eventTextValue !== undefined && eventTextValue !== text) {
      throw new Error('semantic_speech_final_result_mismatch');
    }
    segment.lastPublishedRevision = revision;
    const usableSource = source && source.expiresAtMs > Date.now() ? source : null;
    return this.transcriptPayload(
      segment,
      revision,
      'final',
      text,
      usableSource?.digest ?? null,
    );
  }

  private transcriptPayload(
    segment: CaptureSegment,
    revision: number,
    authority: 'provisional' | 'final',
    text: string,
    sourceDigest: string | null,
    expiresAtMs = Date.now() + PAYLOAD_TTL_MS,
  ): SemanticSpeechPayload {
    const context = this.context;
    if (!context || this.transportContextKey(context) !== this.transportContextKey(segment.context)) {
      throw new Error('semantic_speech_capture_context_stale');
    }
    return Object.freeze({
      version: 'ananta.semantic-speech.v1',
      kind: 'transcript_revision',
      session_id: context.sessionId,
      epoch: context.epoch,
      turn_id: segment.sessionId,
      revision,
      sender_id: context.localPeerId,
      audience_id: context.remotePeerId,
      consent_version: context.consentVersion,
      expires_at_ms: expiresAtMs,
      contract_digest: context.contractDigest,
      source_digest: sourceDigest,
      authority,
      text,
    });
  }

  private async admitAndPublish(payload: SemanticSpeechPayload, generation: number): Promise<void> {
    if (!this.isCurrent(generation)) return;
    const admitted = await this.runtime.finalizeLocal(payload);
    if (!admitted || !this.isCurrent(generation)) return;
    this.enqueueTransport(payload, generation);
  }

  private enqueueAdmission(payload: SemanticSpeechPayload, generation: number): Promise<void> {
    const operation = this.admissionQueue.then(() => this.admitAndPublish(payload, generation));
    this.admissionQueue = operation.catch(error => {
      this.fail(error, generation, payload.turn_id);
    });
    return operation;
  }

  private enqueueTransport(payload: SemanticSpeechPayload, generation: number): void {
    const revisionKey = [
      payload.session_id, payload.epoch, payload.turn_id, payload.revision, payload.kind,
    ].join('\u001f');
    if (this.publishedRevisionKeys.has(revisionKey)) return;
    this.publishedRevisionKeys.add(revisionKey);
    while (this.publishedRevisionKeys.size > MAX_PUBLISHED_REVISION_KEYS) {
      this.publishedRevisionKeys.delete(this.publishedRevisionKeys.values().next().value!);
    }
    const operation = this.transportQueue.then(async () => {
      if (!this.isCurrent(generation)) return;
      await this.transport.send(payload);
    });
    this.transportQueue = operation.catch(error => {
      this.fail(error, generation, payload.turn_id);
    });
  }

  private bufferRollover(audio: ArrayBuffer, generation: number): void {
    if (!this.reserveQueue(audio.byteLength, generation, this.segment?.sessionId || 'capture')) {
      wipe(audio);
      return;
    }
    this.rollover.push(audio);
  }

  private flushRollover(generation: number): void {
    while (this.rollover.length && this.isCurrent(generation) && this.segment?.accepting) {
      const audio = this.rollover.shift()!;
      this.queuedChunks = Math.max(0, this.queuedChunks - 1);
      this.queuedBytes = Math.max(0, this.queuedBytes - audio.byteLength);
      this.acceptIntoSegment(this.segment, audio);
    }
    if (!this.isCurrent(generation)) this.clearRollover();
  }

  private reserveQueue(bytes: number, generation: number, turnId: string): boolean {
    if (
      this.queuedChunks + 1 > MAX_QUEUED_CHUNKS
      || this.queuedBytes + bytes > MAX_QUEUED_BYTES
    ) {
      this.fail(new Error('semantic_speech_capture_backpressure'), generation, turnId);
      return false;
    }
    this.queuedChunks += 1;
    this.queuedBytes += bytes;
    return true;
  }

  private fail(error: unknown, generation: number, turnId = 'capture'): void {
    if (!this.isCurrent(generation) || this.failureReported) return;
    this.failureReported = true;
    const status = httpStatus(error);
    this.runtime.reportCaptureTransportFailure(status, turnId);
    const failure = reasonCode(error, 'semantic_speech_capture_failed');
    void this.stop(failure);
    this.failure$.next(failure);
  }

  private async stopRun(_reasonCode: string): Promise<void> {
    ++this.generation;
    this.active = false;
    this.clearRunTimer();
    const context = this.context;
    const segment = this.segment;
    this.segment = null;
    if (segment) {
      segment.accepting = false;
      wipeParts(segment.sourceParts);
      segment.sourceBytes = 0;
    }
    this.clearRollover();
    await this.capture.stop().catch(() => undefined);
    // In-flight Hub operations are generation-fenced and clean themselves up;
    // stop must not wait for a stalled create/upload request.
    if (segment && context) {
      await this.cancelRemote(context.hubUrl, segment.sessionId);
    }
    for (const finalizing of this.finalizingSegments) {
      await this.cancelRemote(finalizing.context.hubUrl, finalizing.sessionId);
    }
    this.context = null;
    this.queuedChunks = 0;
    this.queuedBytes = 0;
    this.publishedRevisionKeys.clear();
    this.admissionQueue = Promise.resolve();
    this.transportQueue = Promise.resolve();
  }

  private async cancelRemote(hubUrl: string, sessionId: string): Promise<void> {
    await firstValueFrom(this.api.cancelStream(
      hubUrl,
      sessionId,
      { missingSessionIsExpected: true },
    )).then(() => undefined, () => undefined);
  }

  private clearRollover(): void {
    for (const audio of this.rollover) wipe(audio);
    this.rollover = [];
  }

  private armRunTimer(generation: number): void {
    this.clearRunTimer();
    this.runTimer = globalThis.setTimeout(() => {
      this.fail(new Error('semantic_speech_max_runtime_reached'), generation);
    }, MAX_RUN_MS);
  }

  private clearRunTimer(): void {
    if (this.runTimer !== null) globalThis.clearTimeout(this.runTimer);
    this.runTimer = null;
  }

  private streamAudioBudget(stream: VoiceStreamState, requestedSeconds: number): number {
    const bytes = this.positiveInteger(stream.max_audio_bytes)
      ?? Math.floor((this.positiveNumber(stream.max_audio_seconds) ?? requestedSeconds) * PCM_BYTES_PER_SECOND);
    const aligned = bytes - bytes % Int16Array.BYTES_PER_ELEMENT;
    if (aligned <= 0) throw new Error('semantic_speech_stream_audio_budget_invalid');
    return Math.min(aligned, requestedSeconds * PCM_BYTES_PER_SECOND);
  }

  private validateContext(context: SemanticSpeechCaptureProducerContext): SemanticSpeechCaptureProducerContext {
    if (
      !/^https?:\/\//.test(String(context.hubUrl || ''))
      || !String(context.profileId || '').trim()
      || !String(context.sessionId || '').trim()
      || !Number.isSafeInteger(context.epoch) || context.epoch < 1
    ) throw new Error('semantic_speech_capture_context_invalid');
    return Object.freeze({
      ...context,
      hubUrl: context.hubUrl.trim().replace(/\/+$/, ''),
      profileId: context.profileId.trim(),
      ...(context.configurationSessionId?.trim()
        ? { configurationSessionId: context.configurationSessionId.trim() }
        : {}),
    });
  }

  private validateSettings(settings: SemanticSpeechSettings): SemanticSpeechSettings {
    if (!['live', 'segment'].includes(settings.displayMode)
      || !SEGMENT_DURATIONS.has(settings.segmentDurationSeconds)) {
      throw new Error('semantic_speech_capture_settings_invalid');
    }
    return Object.freeze({ ...settings });
  }

  private transportContextKey(context: SemanticSpeechCaptureProducerContext): string {
    return [
      context.hubUrl, context.sessionId, context.epoch, context.localPeerId,
      context.remotePeerId, context.consentVersion, context.contractDigest,
    ].join('\u001f');
  }

  private assertTranscript(text: string): void {
    if (new TextEncoder().encode(text).byteLength > MAX_TRANSCRIPT_BYTES) {
      throw new Error('semantic_speech_transcript_too_large');
    }
  }

  private requireContext(): SemanticSpeechCaptureProducerContext {
    if (!this.context) throw new Error('semantic_speech_capture_not_started');
    return this.context;
  }

  private isCurrent(generation: number): boolean {
    return this.active && generation === this.generation && this.context !== null;
  }

  private canFinish(generation: number): boolean {
    return generation === this.generation && this.context !== null && !this.failureReported;
  }

  private ensureCurrent(generation: number): void {
    if (!this.isCurrent(generation)) throw new Error('semantic_speech_capture_invalidated');
  }

  private positiveInteger(value: unknown): number | null {
    const numeric = Number(value);
    return Number.isSafeInteger(numeric) && numeric > 0 ? numeric : null;
  }

  private nonNegativeInteger(value: unknown): number | null {
    const numeric = Number(value);
    return Number.isSafeInteger(numeric) && numeric >= 0 ? numeric : null;
  }

  private positiveNumber(value: unknown): number | null {
    const numeric = Number(value);
    return Number.isFinite(numeric) && numeric > 0 ? numeric : null;
  }
}

function backendRevision(event: VoiceStreamEvent | null | undefined): number {
  const sequence = Number(event?.sequence);
  if (!Number.isSafeInteger(sequence) || sequence < 0 || sequence >= 2_147_483_647) {
    throw new Error('semantic_speech_backend_revision_missing');
  }
  return sequence + 1;
}

function eventText(event: VoiceStreamEvent | null | undefined): string {
  const payload = event?.payload;
  return typeof payload?.stable_text === 'string' && payload.stable_text.trim()
    ? payload.stable_text.trim()
    : typeof payload?.finalized_text === 'string' && payload.finalized_text.trim()
      ? payload.finalized_text.trim()
      : typeof payload?.text === 'string'
        ? payload.text.trim()
        : '';
}

function randomToken(): string {
  const bytes = new Uint8Array(12);
  globalThis.crypto.getRandomValues(bytes);
  return hex(bytes);
}

function hex(bytes: Uint8Array): string {
  return Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
}

function wipe(value: ArrayBuffer): void {
  new Uint8Array(value).fill(0);
}

function wipeParts(parts: ArrayBuffer[]): void {
  for (const part of parts) wipe(part);
  parts.length = 0;
}

function httpStatus(error: unknown): number {
  const status = Number((error as { status?: unknown } | null)?.status);
  return Number.isSafeInteger(status) ? status : 0;
}

function reasonCode(error: unknown, fallback: string): string {
  const nested = (error as { error?: { error?: { code?: unknown }; code?: unknown } } | null)?.error;
  const value = error instanceof Error ? error.message : nested?.error?.code ?? nested?.code;
  return typeof value === 'string' && /^[a-z][a-z0-9_.-]{2,119}$/.test(value) ? value : fallback;
}
