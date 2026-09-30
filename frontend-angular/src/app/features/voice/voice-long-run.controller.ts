import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import {
  VOICE_AUDIO_CAPTURE,
  VoiceAudioCapturePort,
  VoiceCaptureSource,
  pcm16ChunksToWav,
} from './voice-audio-capture';
import { VoiceApiService } from './voice-api.service';
import {
  DEFAULT_VOICE_LONG_RUN_DISPLAY_MODE,
  VoiceLongRunDisplayMode,
  normalizeVoiceLongRunDisplayMode,
} from './voice-long-run-display-mode';
import {
  VOICE_LONG_RUN_LIVE_PREVIEW,
  VoiceLongRunLivePreviewPort,
} from './voice-long-run-live-preview';
import {
  VOICE_LONG_RUN_RECOVERY,
  VoiceLongRunRecoveryMetadata,
  VoiceLongRunRecoveryPort,
} from './voice-long-run-recovery';
import {
  VOICE_LONG_RUN_SEGMENTER_FACTORY,
  VoiceLongRunPcmSegment,
  VoiceLongRunPcmSegmenter,
  VoiceLongRunSegmenterFactory,
} from './voice-long-run-segmenter';
import {
  VOICE_LONG_RUN_SPOOL,
  VoiceLongRunSpoolPort,
  VoiceLongRunSpoolPutResult,
} from './voice-long-run-spool';
import {
  VoiceLongRunCreateRequest,
  VoiceLongRunResponse,
  VoiceLongRunState,
} from './voice.models';
import { VoiceLongRunDurableCursor } from './voice-long-run-durable-cursor';
import { VoiceLongRunGapProjection } from './voice-long-run-gap-projection';
import { VoiceLongRunRecoveryInspection } from './voice-long-run-recovery-inspection';
import { VoiceLongRunRevisionFeed } from './voice-long-run-revision-feed';
import { VoiceLongRunSegmentUploader } from './voice-long-run-segment-uploader';
import {
  HEARTBEAT_MILLISECONDS,
  MAX_PENDING_PLAINTEXT_BYTES,
  MAX_PENDING_PLAINTEXT_SEGMENTS,
  MAX_STOP_CORRECTION_ATTEMPTS,
  MAX_STOP_UPLOAD_ATTEMPTS,
  RETRY_DELAYS_MILLISECONDS,
  SPOOL_WRITE_TIMEOUT_MILLISECONDS,
  STOP_CORRECTION_POLL_MILLISECONDS,
  VoiceLongRunCursor,
  VoiceLongRunObserver,
  captureDeadlineAt,
  captureDeadlineExpired,
  delay,
  ensureOperation,
  isNotFound,
  isSegmentsInFlight,
  reconciledCursor,
  requestFromRun,
  runExpired,
  sameRecoveryRequest,
  segmentIdempotencyKey,
  terminalRun,
  validLeaseToken,
  withTimeout,
} from './voice-long-run.policy';
import { startVoiceLongRunLivePreview } from './voice-long-run-preview-bridge';
import { VoiceProfileDeletionWatcher } from './voice-profile-deletion-watcher';

export { appendVoiceLongRunTranscript } from './voice-long-run-timeline';
export type { VoiceLongRunObserver } from './voice-long-run.policy';

/**
 * Owns one long-run capture session end to end: start/resume/drain/stop
 * lifecycle, capture segmentation into the encrypted spool, heartbeats and
 * the local recovery descriptor. Uploading, revision polling, gap
 * projection, recovery inspection and profile-deletion notifications are
 * delegated to the collaborators imported above (SRP).
 */
@Injectable()
export class VoiceLongRunController {
  private readonly api = inject(VoiceApiService);
  private readonly capture: VoiceAudioCapturePort = inject(VOICE_AUDIO_CAPTURE);
  private readonly spool: VoiceLongRunSpoolPort = inject(VOICE_LONG_RUN_SPOOL);
  private readonly recovery: VoiceLongRunRecoveryPort = inject(VOICE_LONG_RUN_RECOVERY);
  private readonly createSegmenter: VoiceLongRunSegmenterFactory = inject(VOICE_LONG_RUN_SEGMENTER_FACTORY);
  private readonly livePreview: VoiceLongRunLivePreviewPort = inject(VOICE_LONG_RUN_LIVE_PREVIEW);

  private hubUrl = '';
  private run: VoiceLongRunState | null = null;
  private segmenter: VoiceLongRunPcmSegmenter | null = null;
  private observer: VoiceLongRunObserver = {};
  private persistenceQueue: Promise<void> = Promise.resolve();
  private stoppingOperation: Promise<VoiceLongRunResponse> | null = null;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private captureDeadlineTimer: ReturnType<typeof setTimeout> | null = null;
  private lastLocalSequence = -1;
  private operationGeneration = 0;
  private starting = false;
  private stopping = false;
  private pendingAutomaticStopReason = '';
  private currentRequest: VoiceLongRunCreateRequest | null = null;
  private displayMode: VoiceLongRunDisplayMode = DEFAULT_VOICE_LONG_RUN_DISPLAY_MODE;
  private createIdempotencyKey = '';
  private secureStorageReady = false;
  private readonly inspection = new VoiceLongRunRecoveryInspection(this.recovery);
  private pendingPersistenceSegments = 0;
  private pendingPersistenceBytes = 0;
  private readonly durable = new VoiceLongRunDurableCursor();
  private profileGeneration = 0;
  private profileDeletionAborting = false;
  private pendingProfileId = '';
  private readonly gaps = new VoiceLongRunGapProjection((sequences) => this.observer.gapsUpdated?.(sequences));
  private readonly uploader = new VoiceLongRunSegmentUploader(this.api, this.spool, {
    hubUrl: () => this.hubUrl,
    runId: () => this.run?.id ?? null,
    generation: () => this.operationGeneration,
    stopping: () => this.stopping,
    observer: () => this.observer,
    publishResponse: (response) => this.publishResponse(response),
    reportGap: (sequence) => this.reportGap(sequence),
    clearLocalGap: (sequence) => this.gaps.clearLocal(sequence),
    failed: (error) => this.requestAutomaticStop('secure_spool_failed', error),
  });
  private readonly revisions = new VoiceLongRunRevisionFeed(this.api, {
    hubUrl: () => this.hubUrl,
    runId: () => this.run?.id ?? null,
    generation: () => this.operationGeneration,
    stopping: () => this.stopping,
    observer: () => this.observer,
    acceptRunProjection: (response) => this.updateHubGapProjection(response),
  });

  private readonly profileDeletions = new VoiceProfileDeletionWatcher((profileId) => {
    if (this.profileDeletionRelevant(profileId)) this.abortForProfileDeletion();
  });
  private readonly visibilityListener = () => {
    if (globalThis.document?.visibilityState === 'hidden') {
      this.revisions.clearTimer();
      return;
    }
    this.revisions.schedule(0);
  };

  constructor() {
    this.profileDeletions.attach();
    globalThis.document?.addEventListener('visibilitychange', this.visibilityListener);
  }

  get supported(): boolean {
    return this.capture.supported;
  }

  get active(): boolean {
    return Boolean(this.run && this.capture.active && !this.stopping);
  }

  get runId(): string {
    return this.run?.id || '';
  }

  recoveryMetadata(): VoiceLongRunRecoveryMetadata | null {
    return this.recovery.load();
  }

  recoveryReadyForConsent(): boolean {
    return this.inspection.readyForConsent();
  }

  recoveryDrainOnly(): boolean {
    return this.inspection.drainOnly();
  }

  recoveryFinalizing(): boolean {
    return this.inspection.finalizing();
  }

  supportsSource(source: VoiceCaptureSource): boolean {
    return this.capture.supportsSource(source);
  }

  async refreshCaptureCapabilities(): Promise<void> {
    await this.capture.refreshCapabilities?.();
  }

  async initializeSecureStorage(): Promise<void> {
    await this.spool.initialize();
    this.secureStorageReady = true;
  }

  async inspectRecovery(): Promise<VoiceLongRunResponse | null> {
    const generation = this.operationGeneration;
    const descriptor = this.recovery.load();
    if (!descriptor?.runId) return null;
    await this.initializeSecureStorage();
    this.ensureOperation(generation);
    let response: VoiceLongRunResponse;
    try {
      response = await firstValueFrom(this.api.getLongRun(
        descriptor.hubUrl,
        descriptor.runId,
        { includeText: false },
      ));
      this.ensureOperation(generation);
    } catch (error) {
      this.ensureOperation(generation);
      if (!isNotFound(error)) throw error;
      await this.spool.clearRun(descriptor.runId);
      this.ensureOperation(generation);
      this.recovery.clear(descriptor.runId);
      this.inspection.forget();
      return null;
    }
    if (terminalRun(response.run) || runExpired(response.run)) {
      await this.spool.clearRun(descriptor.runId);
      this.ensureOperation(generation);
      this.recovery.clear(descriptor.runId);
      this.inspection.forget();
      return response;
    }
    this.inspection.record(response);
    return response;
  }

  async prepareCapture(source: VoiceCaptureSource, profileId = ''): Promise<void> {
    this.assertIdle();
    const generation = this.operationGeneration;
    this.pendingProfileId = profileId.trim();
    try {
      // Long recording fails closed when encrypted IndexedDB storage is not
      // available. Do this before asking for microphone/MediaProjection consent.
      if (!this.secureStorageReady) await this.initializeSecureStorage();
      this.ensureOperation(generation);
      if (this.recovery.load()?.runId && !this.recoveryReadyForConsent()) {
        throw new Error('voice.long_run.recovery_check_required');
      }
      await this.capture.prepare(source);
      if (generation !== this.operationGeneration) {
        await this.capture.stop().catch(() => undefined);
        throw new Error('voice.capture.cancelled');
      }
    } catch (error) {
      this.pendingProfileId = '';
      throw error;
    }
  }

  async start(
    hubUrl: string,
    request: VoiceLongRunCreateRequest,
    idempotencyKey: string,
    observer: VoiceLongRunObserver = {},
    displayMode: VoiceLongRunDisplayMode = DEFAULT_VOICE_LONG_RUN_DISPLAY_MODE,
  ): Promise<VoiceLongRunState> {
    this.assertIdle();
    const generation = this.operationGeneration;
    this.starting = true;
    this.hubUrl = hubUrl;
    this.observer = observer;
    this.revisions.reset();
    this.resetGapProjection();
    this.uploader.resetRetries();
    this.lastLocalSequence = -1;
    this.pendingAutomaticStopReason = '';
    this.persistenceQueue = Promise.resolve();
    this.currentRequest = request;
    this.displayMode = normalizeVoiceLongRunDisplayMode(displayMode);
    this.pendingProfileId = request.profile_id;
    try {
      if (!this.secureStorageReady) await this.initializeSecureStorage();
      this.ensureOperation(generation);
      const pendingCreate = this.recovery.load();
      if (pendingCreate?.runId) throw new Error('voice.long_run.resume_required');
      if (pendingCreate && !sameRecoveryRequest(pendingCreate, hubUrl, request)) {
        throw new Error('voice.long_run.pending_create_conflict');
      }
      this.createIdempotencyKey = pendingCreate?.createIdempotencyKey || idempotencyKey;
      if (pendingCreate && !pendingCreate.profileGeneration) {
        throw new Error('voice.long_run.recovery_generation_missing');
      }
      this.profileGeneration = pendingCreate
        ? pendingCreate.profileGeneration!
        : await this.spool.allowProfile(request.profile_id);
      this.ensureOperation(generation);
      if (!this.capture.prepared) await this.capture.prepare(request.source);
      this.ensureOperation(generation);
      const lease = await firstValueFrom(this.api.acquireLongRunLease(hubUrl, request.profile_id));
      this.ensureOperation(generation);
      const leaseToken = validLeaseToken(lease, request.profile_id);
      this.recovery.save({
        schemaVersion: 1,
        runId: '',
        hubUrl,
        createIdempotencyKey: this.createIdempotencyKey,
        profileGeneration: this.profileGeneration,
        request,
        displayMode: this.displayMode,
        nextSequence: 0,
        timelineMilliseconds: 0,
        updatedAt: Date.now(),
      });
      const created = await firstValueFrom(this.api.createLongRun(
        hubUrl,
        { ...request, lease_token: leaseToken },
        this.createIdempotencyKey,
      ));
      this.ensureOperation(generation);
      this.run = created.run;
      if (created.run.status !== 'active' || captureDeadlineExpired(created.run)) {
        this.recovery.clear();
        throw new Error('voice.long_run.create_replay_terminal');
      }
      this.publishResponse(created);
      const buffered = await this.spool.list(created.run.id);
      this.ensureOperation(generation);
      const cursor = reconciledCursor(created, buffered);
      this.adoptCursor(cursor);
      this.saveRecovery(cursor.nextSequence, cursor.timelineMilliseconds);
      this.segmenter = this.createRunSegmenter(request, cursor);
      await this.startLivePreview(created.run, cursor.nextSequence);
      this.ensureOperation(generation);
      this.startCaptureDeadline(created.run, request.max_duration_seconds, cursor.timelineMilliseconds);
      this.ensureOperation(generation);
      await this.startCapture(request.max_duration_seconds);
      this.ensureOperation(generation);
      this.pendingProfileId = '';
      this.startHeartbeat();
      this.uploader.kick();
      const pendingReason = this.pendingAutomaticStopReason;
      if (pendingReason) queueMicrotask(() => void this.stop(pendingReason));
      return created.run;
    } catch (error) {
      const cancelled = generation !== this.operationGeneration;
      await this.capture.stop().catch(() => undefined);
      await this.livePreview.stop().catch(() => undefined);
      if (!cancelled && this.run) {
        const runId = this.run.id;
        if (this.run.status === 'active') {
          await this.stopRemote('capture_start_failed').catch(() => undefined);
        }
        await this.spool.clearRun(runId).catch(() => undefined);
        this.recovery.clear();
      }
      this.resetRuntime();
      throw error;
    } finally {
      this.starting = false;
    }
  }

  stop(reason = 'user_stop'): Promise<VoiceLongRunResponse> {
    if (this.stoppingOperation) return this.stoppingOperation;
    if (!this.run) return Promise.reject(new Error('voice.long_run.not_active'));
    const operation = this.stopOnce(reason);
    this.stoppingOperation = operation;
    void operation.finally(() => {
      if (this.stoppingOperation === operation) this.stoppingOperation = null;
    }).catch(() => undefined);
    return operation;
  }

  async dispose(): Promise<void> {
    this.operationGeneration += 1;
    this.profileDeletions.detach();
    globalThis.document?.removeEventListener('visibilitychange', this.visibilityListener);
    if (this.run) {
      await this.stop('ui_closed').catch(() => undefined);
      return;
    }
    await this.capture.stop().catch(() => undefined);
    await this.livePreview.dispose().catch(() => undefined);
    this.clearTimers();
  }

  /** Retry encrypted records that survived a reload, without reopening capture. */
  async resumeBuffered(
    hubUrl: string,
    runId: string,
    observer: VoiceLongRunObserver = {},
  ): Promise<VoiceLongRunResponse> {
    this.assertIdle();
    const generation = this.operationGeneration;
    const descriptor = this.recovery.load();
    this.pendingProfileId = descriptor?.runId === runId ? descriptor.request.profile_id : '';
    await this.spool.initialize();
    this.ensureOperation(generation);
    const snapshot = await firstValueFrom(this.api.getLongRun(hubUrl, runId, { includeText: false }));
    this.ensureOperation(generation);
    if (snapshot.run.status !== 'active') {
      this.pendingProfileId = '';
      return snapshot;
    }
    this.hubUrl = hubUrl;
    this.run = snapshot.run;
    this.observer = observer;
    this.revisions.reset();
    this.resetGapProjection();
    this.currentRequest = requestFromRun(snapshot.run);
    this.createIdempotencyKey = descriptor?.createIdempotencyKey || '';
    const buffered = await this.spool.list(runId);
    this.ensureOperation(generation);
    this.lastLocalSequence = Math.max(
      Number(snapshot.run.last_local_sequence ?? -1),
      ...buffered.map((item) => item.sequence),
    );
    if (this.updateHubGapProjection(snapshot)) this.observer.runUpdated?.(snapshot);
    await this.uploader.drain();
    this.ensureOperation(generation);
    await this.sendHeartbeat();
    this.ensureOperation(generation);
    const refreshed = await firstValueFrom(this.api.getLongRun(hubUrl, runId));
    this.ensureOperation(generation);
    this.publishResponse(refreshed);
    this.pendingProfileId = '';
    return refreshed;
  }

  /** Reconciles the Hub cursor and encrypted spool, then requests one new capture lease. */
  async resumeCapture(observer: VoiceLongRunObserver = {}): Promise<VoiceLongRunState> {
    this.assertIdle();
    const descriptor = this.recovery.load();
    if (!descriptor?.runId) throw new Error('voice.long_run.recovery_not_found');
    if (!descriptor.profileGeneration) throw new Error('voice.long_run.recovery_generation_missing');
    const generation = this.operationGeneration;
    this.starting = true;
    this.hubUrl = descriptor.hubUrl;
    this.observer = observer;
    this.revisions.reset();
    this.resetGapProjection();
    this.currentRequest = descriptor.request;
    this.displayMode = normalizeVoiceLongRunDisplayMode(descriptor.displayMode);
    this.pendingProfileId = descriptor.request.profile_id;
    this.createIdempotencyKey = descriptor.createIdempotencyKey;
    this.profileGeneration = descriptor.profileGeneration;
    try {
      if (!this.secureStorageReady) await this.initializeSecureStorage();
      this.ensureOperation(generation);
      if (!this.capture.prepared) await this.capture.prepare(descriptor.request.source);
      this.ensureOperation(generation);
      if (!this.recoveryReadyForConsent() || this.inspection.response?.run.id !== descriptor.runId) {
        throw new Error('voice.long_run.recovery_check_required');
      }
      const snapshot = await firstValueFrom(this.api.getLongRun(
        descriptor.hubUrl,
        descriptor.runId,
        { limit: 600 },
      ));
      this.ensureOperation(generation);
      if (snapshot.run.status !== 'active') {
        await this.spool.clearRun(descriptor.runId);
        this.ensureOperation(generation);
        this.recovery.clear(descriptor.runId);
        throw new Error('voice.long_run.recovery_terminal');
      }
      if (captureDeadlineExpired(snapshot.run)) {
        throw new Error('voice.long_run.capture_deadline_expired');
      }
      const buffered = await this.spool.list(descriptor.runId);
      this.ensureOperation(generation);
      const cursor = reconciledCursor(snapshot, buffered, descriptor);
      this.run = snapshot.run;
      this.adoptCursor(cursor);
      this.segmenter = this.createRunSegmenter(descriptor.request, cursor);
      this.saveRecovery(cursor.nextSequence, cursor.timelineMilliseconds);
      this.publishResponse(snapshot);
      await this.startLivePreview(snapshot.run, cursor.nextSequence);
      this.ensureOperation(generation);
      this.startCaptureDeadline(
        snapshot.run,
        descriptor.request.max_duration_seconds,
        cursor.timelineMilliseconds,
      );
      await this.startCapture(descriptor.request.max_duration_seconds);
      this.ensureOperation(generation);
      this.pendingProfileId = '';
      this.startHeartbeat();
      this.uploader.kick();
      return snapshot.run;
    } catch (error) {
      await this.capture.stop().catch(() => undefined);
      await this.livePreview.stop().catch(() => undefined);
      this.resetRuntime();
      throw error;
    } finally {
      this.starting = false;
    }
  }

  async discardRecovery(): Promise<boolean> {
    const generation = this.operationGeneration;
    const descriptor = this.recovery.load();
    if (!descriptor) return true;
    this.pendingProfileId = descriptor.request.profile_id;
    let remoteFailure: unknown = null;
    let hubConfirmedEnded = !descriptor.runId;
    if (descriptor.runId) {
      try {
        const snapshot = await firstValueFrom(this.api.getLongRun(
          descriptor.hubUrl,
          descriptor.runId,
          { includeText: false },
        ));
        this.ensureOperation(generation);
        if (snapshot.run.status === 'active') {
          await firstValueFrom(this.api.stopLongRun(
            descriptor.hubUrl,
            descriptor.runId,
            { last_sequence: descriptor.nextSequence - 1, reason: 'user_discard' },
            `voice-ui:long-run-stop:${descriptor.runId}`,
          ));
          this.ensureOperation(generation);
          hubConfirmedEnded = true;
        } else if (terminalRun(snapshot.run) || runExpired(snapshot.run)) {
          hubConfirmedEnded = true;
        }
      } catch (error) {
        this.ensureOperation(generation);
        if (isNotFound(error)) hubConfirmedEnded = true;
        else remoteFailure = error;
      }
      await this.spool.clearRun(descriptor.runId);
      this.ensureOperation(generation);
    }
    // Local discard is authoritative even when the Hub is offline or already
    // returned 404 (for example after a privacy deletion).
    this.recovery.clear();
    this.pendingProfileId = '';
    if (remoteFailure) this.observer.error?.(remoteFailure);
    return hubConfirmedEnded;
  }

  /**
   * Uploads an interrupted run's encrypted tail after its capture lease ended.
   * No microphone or MediaProjection permission is requested in this mode.
   */
  async drainRecovery(observer: VoiceLongRunObserver = {}): Promise<VoiceLongRunResponse> {
    this.assertIdle();
    const descriptor = this.recovery.load();
    if (!descriptor?.runId) throw new Error('voice.long_run.recovery_not_found');
    if (!descriptor.profileGeneration) throw new Error('voice.long_run.recovery_generation_missing');
    const generation = this.operationGeneration;
    this.starting = true;
    this.pendingProfileId = descriptor.request.profile_id;
    try {
      if (!this.secureStorageReady) await this.initializeSecureStorage();
      this.ensureOperation(generation);
      let snapshot: VoiceLongRunResponse;
      try {
        snapshot = await firstValueFrom(this.api.getLongRun(
          descriptor.hubUrl,
          descriptor.runId,
          { limit: 600 },
        ));
        this.ensureOperation(generation);
      } catch (error) {
        this.ensureOperation(generation);
        if (isNotFound(error)) {
          await this.spool.clearRun(descriptor.runId);
          this.ensureOperation(generation);
          this.recovery.clear(descriptor.runId);
          this.inspection.forget();
        }
        throw error;
      }
      if (terminalRun(snapshot.run) || runExpired(snapshot.run)) {
        await this.spool.clearRun(descriptor.runId);
        this.ensureOperation(generation);
        this.recovery.clear(descriptor.runId);
        this.inspection.forget();
        return snapshot;
      }
      if (snapshot.run.status !== 'active') {
        this.inspection.record(snapshot);
        throw new Error('voice.long_run.recovery_finalizing');
      }
      if (!captureDeadlineExpired(snapshot.run)) {
        this.inspection.record(snapshot);
        throw new Error('voice.long_run.capture_still_available');
      }

      this.hubUrl = descriptor.hubUrl;
      this.run = snapshot.run;
      this.observer = observer;
      this.revisions.reset();
      this.resetGapProjection();
      this.currentRequest = descriptor.request;
      this.displayMode = normalizeVoiceLongRunDisplayMode(descriptor.displayMode);
      this.createIdempotencyKey = descriptor.createIdempotencyKey;
      this.profileGeneration = descriptor.profileGeneration;
      const buffered = await this.spool.list(descriptor.runId);
      this.ensureOperation(generation);
      const cursor = reconciledCursor(snapshot, buffered, descriptor);
      this.adoptCursor(cursor);
      this.saveRecovery(cursor.nextSequence, cursor.timelineMilliseconds);
      this.publishResponse(snapshot);
      const response = await this.stopOnce('recovery_drain');
      this.ensureOperation(generation);
      return response;
    } catch (error) {
      // Keep encrypted records and the recovery descriptor retryable when the
      // Hub becomes unavailable during drain/finalization.
      this.resetRuntime();
      throw error;
    } finally {
      this.starting = false;
    }
  }

  private async startLivePreview(run: VoiceLongRunState, initialSegmentSequence: number): Promise<void> {
    if (this.displayMode !== 'live' || !this.currentRequest) return;
    await startVoiceLongRunLivePreview(this.livePreview, {
      hubUrl: this.hubUrl, runId: run.id, request: this.currentRequest, initialSegmentSequence,
    }, () => this.observer);
  }

  private assertIdle(): void {
    if (this.run || this.starting || this.stoppingOperation) {
      throw new Error('voice.long_run.already_active');
    }
  }

  private onCaptureChunk(chunk: ArrayBuffer): void {
    // Native stop drains one final partial PCM chunk before resolving. Keep
    // accepting chunks until capture.stop() has completed and the segmenter is
    // explicitly flushed below.
    if (!this.segmenter || !this.run) return;
    let ready: VoiceLongRunPcmSegment[];
    try {
      ready = this.segmenter.push(chunk);
    } catch (error) {
      this.requestAutomaticStop('capture_error', error);
      return;
    }
    if (this.displayMode === 'live') this.livePreview.acceptPcm(chunk);
    this.observer.progress?.(this.segmenter.capturedDurationMs);
    this.durable.latestTimelineMilliseconds = this.segmenter.capturedDurationMs;
    this.saveRecovery(this.lastLocalSequence + 1, this.durable.latestTimelineMilliseconds);
    for (const segment of ready) {
      if (this.displayMode === 'live' && this.livePreview.segmentSequence === segment.sequence) {
        void this.livePreview.endSegment().catch((error) => {
          this.observer.livePreviewUnavailable?.(error);
        });
      }
      this.persistSegment(segment);
    }
    if (this.segmenter.reachedLimit) this.requestAutomaticStop('safety_limit');
  }

  private persistSegment(segment: VoiceLongRunPcmSegment): void {
    const runId = this.run?.id;
    if (!runId) return;
    const generation = this.operationGeneration;
    this.lastLocalSequence = Math.max(this.lastLocalSequence, segment.sequence);
    this.durable.segmentCompleted(segment.endedAtMs);
    this.saveRecovery(segment.sequence + 1, this.durable.latestTimelineMilliseconds);
    const plaintextBytes = segment.pcmBytes + 44;
    if (this.pendingPersistenceSegments >= MAX_PENDING_PLAINTEXT_SEGMENTS
      || this.pendingPersistenceBytes + plaintextBytes > MAX_PENDING_PLAINTEXT_BYTES) {
      this.reportGap(segment.sequence);
      this.requestAutomaticStop(
        'secure_spool_backpressure',
        new Error('voice.long_run.secure_spool_backpressure'),
      );
      return;
    }
    this.pendingPersistenceSegments += 1;
    this.pendingPersistenceBytes += plaintextBytes;
    const wav = pcm16ChunksToWav(segment.chunks, segment.pcmBytes);
    this.persistenceQueue = this.persistenceQueue.then(async () => {
      const write = this.spool.put({
        runId,
        profileId: this.currentRequest?.profile_id || 'default',
        profileGeneration: this.profileGeneration,
        sequence: segment.sequence,
        startedAtMs: segment.startedAtMs,
        endedAtMs: segment.endedAtMs,
        durationMs: segment.durationMs,
        overlapMilliseconds: segment.overlapMs,
        idempotencyKey: segmentIdempotencyKey(runId, segment.sequence),
        audio: wav,
      });
      let result: VoiceLongRunSpoolPutResult;
      try {
        result = await withTimeout(write, SPOOL_WRITE_TIMEOUT_MILLISECONDS);
        this.ensureOperation(generation);
      } catch (error) {
        // A timed-out IndexedDB transaction may still complete. Remove that
        // late ciphertext so stop/privacy cleanup cannot be undone afterward.
        void write.then(() => this.spool.delete(runId, segment.sequence)).catch(() => undefined);
        this.reportGap(segment.sequence);
        throw error;
      }
      for (const evicted of result.evicted) {
        if (evicted.runId !== runId) continue;
        this.uploader.evicted(evicted.sequence);
      }
      const stats = await this.spool.stats(runId);
      this.ensureOperation(generation);
      this.durable.segmentDurable(segment.sequence, segment.endedAtMs);
      this.saveRecovery(this.lastLocalSequence + 1, this.durable.latestTimelineMilliseconds);
      this.observer.buffered?.(result.stored, stats.segments);
      this.uploader.kick();
    }).catch((error) => this.requestAutomaticStop('secure_spool_failed', error))
      .finally(() => {
        this.pendingPersistenceSegments = Math.max(0, this.pendingPersistenceSegments - 1);
        this.pendingPersistenceBytes = Math.max(0, this.pendingPersistenceBytes - plaintextBytes);
      });
  }

  private publishResponse(response: VoiceLongRunResponse): void {
    this.revisions.publish(response);
  }

  private async stopOnce(reason: string): Promise<VoiceLongRunResponse> {
    const runId = this.run?.id;
    if (!runId) throw new Error('voice.long_run.not_active');
    const generation = this.operationGeneration;
    this.stopping = true;
    this.observer.stopping?.(reason);
    this.clearTimers();
    try {
      await this.capture.stop();
      this.ensureOperation(generation);
      await this.livePreview.stop().catch((error) => {
        this.observer.livePreviewUnavailable?.(error);
      });
      this.ensureOperation(generation);
      const finalSegment = this.segmenter?.flush();
      if (finalSegment) this.persistSegment(finalSegment);
      await this.persistenceQueue;
      this.ensureOperation(generation);
      for (let attempt = 0; attempt < MAX_STOP_UPLOAD_ATTEMPTS; attempt += 1) {
        await this.uploader.drain();
        this.ensureOperation(generation);
        const pending = await this.spool.list(runId);
        this.ensureOperation(generation);
        if (!pending.length) break;
        if (attempt < MAX_STOP_UPLOAD_ATTEMPTS - 1) {
          await delay(RETRY_DELAYS_MILLISECONDS[Math.min(attempt, RETRY_DELAYS_MILLISECONDS.length - 1)]);
          this.ensureOperation(generation);
        }
      }
      const abandoned = await this.spool.list(runId);
      this.ensureOperation(generation);
      for (const segment of abandoned) {
        this.reportGap(segment.sequence);
      }
      await this.sendHeartbeat();
      this.ensureOperation(generation);
      const response = await this.stopRemoteAfterCorrections(reason, generation);
      this.ensureOperation(generation);
      await this.spool.clearRun(runId);
      this.ensureOperation(generation);
      this.recovery.clear(runId);
      this.publishResponse(response);
      this.observer.stopped?.(response, reason);
      this.resetRuntime();
      return response;
    } catch (error) {
      this.stopping = false;
      this.observer.error?.(error);
      throw error;
    }
  }

  private async sendHeartbeat(): Promise<void> {
    const runId = this.run?.id;
    if (!runId || this.run?.status !== 'active') return;
    const generation = this.operationGeneration;
    try {
      const response = await firstValueFrom(this.api.heartbeatLongRun(this.hubUrl, runId, {
        client_time_ms: Date.now(),
        last_local_sequence: this.lastLocalSequence,
        gaps: this.gaps.localSequences,
      }));
      if (generation !== this.operationGeneration) return;
      this.saveRecovery(
        this.lastLocalSequence + 1,
        this.segmenter?.capturedDurationMs || this.recovery.load()?.timelineMilliseconds || 0,
      );
      // Heartbeats intentionally omit transcript text. They may observe a
      // newer Hub revision, but must never advance the content cursor or
      // replace visible text before the text-bearing revision delta arrives.
      if (this.updateHubGapProjection(response)) this.observer.runUpdated?.(response);
      this.uploader.kick();
    } catch {
      if (generation !== this.operationGeneration) return;
      this.observer.connection?.('retrying');
    }
  }

  private startHeartbeat(): void {
    this.clearHeartbeat();
    this.heartbeatTimer = setInterval(() => void this.sendHeartbeat(), HEARTBEAT_MILLISECONDS);
  }

  private requestAutomaticStop(reason: string, error?: unknown): void {
    if (error) this.observer.error?.(error);
    if (this.pendingAutomaticStopReason || this.stopping || !this.run) return;
    this.pendingAutomaticStopReason = reason;
    if (this.starting) return;
    queueMicrotask(() => void this.stop(reason).catch(() => undefined));
  }

  private async stopRemote(reason: string): Promise<VoiceLongRunResponse> {
    const runId = this.run?.id;
    if (!runId) throw new Error('voice.long_run.not_active');
    return firstValueFrom(this.api.stopLongRun(
      this.hubUrl,
      runId,
      { last_sequence: this.lastLocalSequence, reason },
      `voice-ui:long-run-stop:${runId}`,
    ));
  }

  private async stopRemoteAfterCorrections(
    reason: string,
    generation: number,
  ): Promise<VoiceLongRunResponse> {
    let lastInFlightError: unknown = null;
    for (let attempt = 0; attempt < MAX_STOP_CORRECTION_ATTEMPTS; attempt += 1) {
      try {
        const response = await this.stopRemote(reason);
        this.ensureOperation(generation);
        if (terminalRun(response.run)) return response;
        this.publishResponse(response);
      } catch (error) {
        this.ensureOperation(generation);
        if (!isSegmentsInFlight(error)) throw error;
        lastInFlightError = error;
      }
      await this.revisions.refreshWhileStopping(generation);
      if (attempt < MAX_STOP_CORRECTION_ATTEMPTS - 1) {
        await delay(STOP_CORRECTION_POLL_MILLISECONDS);
        this.ensureOperation(generation);
      }
    }
    throw lastInFlightError || new Error('voice.long_run.correction_drain_timeout');
  }

  private saveRecovery(nextSequence: number, timelineMilliseconds: number): void {
    if (!this.run || !this.currentRequest || !this.createIdempotencyKey) return;
    const metadata: VoiceLongRunRecoveryMetadata = {
      schemaVersion: 1,
      runId: this.run.id,
      hubUrl: this.hubUrl,
      createIdempotencyKey: this.createIdempotencyKey,
      profileGeneration: this.profileGeneration,
      request: this.currentRequest,
      displayMode: this.displayMode,
      nextSequence,
      timelineMilliseconds,
      ...this.durable.descriptorFields(),
      updatedAt: Date.now(),
    };
    this.recovery.save(metadata);
    this.observer.recoveryUpdated?.(metadata);
  }

  private adoptCursor(cursor: VoiceLongRunCursor): void {
    this.lastLocalSequence = cursor.nextSequence - 1;
    for (const sequence of cursor.gaps) this.reportGap(sequence);
    this.durable.adopt(cursor);
  }

  private createRunSegmenter(request: VoiceLongRunCreateRequest, cursor: VoiceLongRunCursor): VoiceLongRunPcmSegmenter {
    return this.createSegmenter({
      segmentDurationSeconds: request.segment_duration_seconds,
      overlapMilliseconds: request.overlap_milliseconds,
      maxDurationSeconds: request.max_duration_seconds,
      initialSequence: cursor.nextSequence,
      initialTimelineMilliseconds: cursor.timelineMilliseconds,
    });
  }

  private startCapture(maxDurationSeconds: number): Promise<void> {
    return this.capture.start(
      (chunk) => this.onCaptureChunk(chunk),
      (error) => this.requestAutomaticStop('capture_error', error),
      (reason) => this.requestAutomaticStop(reason || 'source_ended'),
      { maxDurationSeconds },
    );
  }

  private startCaptureDeadline(
    run: VoiceLongRunState,
    maxDurationSeconds: number,
    timelineMilliseconds: number,
  ): void {
    if (this.captureDeadlineTimer) clearTimeout(this.captureDeadlineTimer);
    const deadline = captureDeadlineAt(run, maxDurationSeconds, timelineMilliseconds);
    this.captureDeadlineTimer = setTimeout(
      () => this.requestAutomaticStop('capture_deadline'),
      Math.max(0, deadline - Date.now()),
    );
  }

  private ensureOperation(generation: number): void {
    ensureOperation(generation, this.operationGeneration);
  }

  private clearTimers(): void {
    this.clearHeartbeat();
    this.revisions.clearTimer();
    this.uploader.clearRetryTimer();
    if (this.captureDeadlineTimer) clearTimeout(this.captureDeadlineTimer);
    this.captureDeadlineTimer = null;
  }

  private clearHeartbeat(): void {
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = null;
  }

  private resetGapProjection(notify = true): void {
    this.gaps.reset(notify);
  }

  private updateHubGapProjection(response: VoiceLongRunResponse): boolean {
    if (this.run?.id && response.run.id !== this.run.id) return false;
    return this.gaps.applyHubResponse(response, () => { this.run = response.run; });
  }

  private resetRuntime(): void {
    this.clearTimers();
    this.run = null;
    this.segmenter = null;
    this.uploader.reset();
    this.stopping = false;
    this.pendingAutomaticStopReason = '';
    this.resetGapProjection(false);
    this.lastLocalSequence = -1;
    this.currentRequest = null;
    this.displayMode = DEFAULT_VOICE_LONG_RUN_DISPLAY_MODE;
    this.createIdempotencyKey = '';
    this.pendingPersistenceSegments = 0;
    this.pendingPersistenceBytes = 0;
    this.durable.reset();
    this.profileGeneration = 0;
    this.pendingProfileId = '';
    this.revisions.reset();
  }

  private reportGap(sequence: number): void {
    if (!this.gaps.reportLocal(sequence)) return;
    this.observer.gap?.(sequence);
    this.gaps.emitMerged();
  }

  private abortForProfileDeletion(): void {
    const descriptor = this.recovery.load();
    const runId = this.run?.id || descriptor?.runId || '';
    if (this.profileDeletionAborting
      || (!this.starting && !this.stopping && !this.capture.prepared
        && !runId && !this.currentRequest && !this.pendingProfileId)) return;
    this.profileDeletionAborting = true;
    this.operationGeneration += 1;
    this.stopping = true;
    this.clearTimers();
    // Discard the incomplete plaintext segment and native tail. The Hub's
    // privacy endpoint owns remote revocation; this controller must only make
    // local resurrection impossible.
    this.segmenter = null;
    void (async () => {
      try {
        await this.capture.stop().catch(() => undefined);
        await this.livePreview.dispose().catch(() => undefined);
        if (runId) await this.spool.clearRun(runId).catch(() => undefined);
        try {
          this.recovery.clear();
        } catch {
          // The profile tombstone remains authoritative when localStorage is blocked.
        }
        this.observer.error?.(new Error('voice.long_run.profile_deleted'));
      } finally {
        this.resetRuntime();
        this.profileDeletionAborting = false;
      }
    })();
  }

  private profileDeletionRelevant(profileId: string): boolean {
    return profileId === this.currentRequest?.profile_id
      || profileId === this.recovery.load()?.request.profile_id
      || profileId === this.pendingProfileId;
  }

}
