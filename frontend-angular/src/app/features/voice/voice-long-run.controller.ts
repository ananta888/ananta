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
  VoiceLongRunSpoolMetadata,
  VoiceLongRunSpoolPort,
  VoiceLongRunSpoolPutResult,
  VOICE_PROFILE_DELETION_EVENT,
  VOICE_PROFILE_DELETION_STORAGE_PREFIX,
} from './voice-long-run-spool';
import {
  VoiceLongRunCreateRequest,
  VoiceLongRunLease,
  VoiceLongRunResponse,
  VoiceLongRunSegmentUploadResponse,
  VoiceLongRunState,
} from './voice.models';
import {
  VoiceLongRunTimeline,
  VoiceLongRunTimelineSnapshot,
} from './voice-long-run-timeline';

export { appendVoiceLongRunTranscript } from './voice-long-run-timeline';

const HEARTBEAT_MILLISECONDS = 15_000;
const MAX_STOP_UPLOAD_ATTEMPTS = 6;
const RETRY_DELAYS_MILLISECONDS = [1_000, 2_000, 5_000, 10_000, 30_000] as const;
const MAX_PENDING_PLAINTEXT_SEGMENTS = 2;
const MAX_PENDING_PLAINTEXT_BYTES = 8 * 1024 * 1024;
const SPOOL_WRITE_TIMEOUT_MILLISECONDS = 10_000;
const REVISION_POLL_MILLISECONDS = 1_500;
const REVISION_POLL_LIMIT = 100;
const REVISION_RETRY_DELAYS_MILLISECONDS = [1_000, 2_000, 5_000, 10_000, 15_000] as const;
const MAX_STOP_CORRECTION_ATTEMPTS = 240;
const STOP_CORRECTION_POLL_MILLISECONDS = 2_500;

export interface VoiceLongRunObserver {
  runUpdated?(response: VoiceLongRunResponse): void;
  timelineUpdated?(snapshot: VoiceLongRunTimelineSnapshot): void;
  progress?(capturedMilliseconds: number): void;
  buffered?(metadata: VoiceLongRunSpoolMetadata, queuedSegments: number): void;
  segmentUploaded?(response: VoiceLongRunSegmentUploadResponse, queuedSegments: number): void;
  segmentFailed?(sequence: number, error: unknown): void;
  gap?(sequence: number): void;
  connection?(state: 'online' | 'retrying'): void;
  stopping?(reason: string): void;
  stopped?(response: VoiceLongRunResponse, reason: string): void;
  error?(error: unknown): void;
}

@Injectable()
export class VoiceLongRunController {
  private readonly api = inject(VoiceApiService);
  private readonly capture: VoiceAudioCapturePort = inject(VOICE_AUDIO_CAPTURE);
  private readonly spool: VoiceLongRunSpoolPort = inject(VOICE_LONG_RUN_SPOOL);
  private readonly recovery: VoiceLongRunRecoveryPort = inject(VOICE_LONG_RUN_RECOVERY);
  private readonly createSegmenter: VoiceLongRunSegmenterFactory = inject(VOICE_LONG_RUN_SEGMENTER_FACTORY);
  private readonly timeline = new VoiceLongRunTimeline();

  private hubUrl = '';
  private run: VoiceLongRunState | null = null;
  private segmenter: VoiceLongRunPcmSegmenter | null = null;
  private observer: VoiceLongRunObserver = {};
  private persistenceQueue: Promise<void> = Promise.resolve();
  private uploadOperation: Promise<void> | null = null;
  private stoppingOperation: Promise<VoiceLongRunResponse> | null = null;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private captureDeadlineTimer: ReturnType<typeof setTimeout> | null = null;
  private localGapSequences = new Set<number>();
  private retryAttempt = 0;
  private lastLocalSequence = -1;
  private operationGeneration = 0;
  private starting = false;
  private stopping = false;
  private pendingAutomaticStopReason = '';
  private currentRequest: VoiceLongRunCreateRequest | null = null;
  private createIdempotencyKey = '';
  private inFlightSequence: number | null = null;
  private deferredEvictions = new Set<number>();
  private secureStorageReady = false;
  private inspectedRecovery: { response: VoiceLongRunResponse; inspectedAt: number } | null = null;
  private pendingPersistenceSegments = 0;
  private pendingPersistenceBytes = 0;
  private latestTimelineMilliseconds = 0;
  private completedTimelineMilliseconds = 0;
  private durableNextSequence = 0;
  private durableTimelineMilliseconds = 0;
  private profileGeneration = 0;
  private profileDeletionAborting = false;
  private pendingProfileId = '';
  private revisionPollTimer: ReturnType<typeof setTimeout> | null = null;
  private revisionPollInFlight = false;
  private revisionPollingNeeded = false;
  private revisionPollingBlocked = false;
  private revisionPollFailure = 0;
  private timelineRevisionCursor = 0;

  private readonly profileDeletionListener = (event: StorageEvent) => {
    if (!event.key?.startsWith(VOICE_PROFILE_DELETION_STORAGE_PREFIX)) return;
    const profileId = event.key.slice(VOICE_PROFILE_DELETION_STORAGE_PREFIX.length);
    if (profileId && this.profileDeletionRelevant(profileId)) this.abortForProfileDeletion();
  };
  private readonly sameDocumentProfileDeletionListener = (event: Event) => {
    const profileId = String((event as CustomEvent<{ profileId?: string }>).detail?.profileId || '');
    if (profileId && this.profileDeletionRelevant(profileId)) this.abortForProfileDeletion();
  };
  private readonly visibilityListener = () => {
    if (globalThis.document?.visibilityState === 'hidden') {
      this.clearRevisionPollTimer();
      return;
    }
    this.scheduleRevisionPoll(0);
  };

  constructor() {
    globalThis.addEventListener?.('storage', this.profileDeletionListener);
    globalThis.addEventListener?.(VOICE_PROFILE_DELETION_EVENT, this.sameDocumentProfileDeletionListener);
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
    const descriptor = this.recovery.load();
    const inspected = this.inspectedRecovery;
    return Boolean(
      descriptor?.runId
      && inspected?.response.run.id === descriptor.runId
      && inspected.response.run.status === 'active'
      && !this.captureDeadlineExpired(inspected.response.run)
      && Date.now() - inspected.inspectedAt <= 30_000,
    );
  }

  recoveryDrainOnly(): boolean {
    const descriptor = this.recovery.load();
    const inspected = this.inspectedRecovery;
    return Boolean(
      descriptor?.runId
      && inspected?.response.run.id === descriptor.runId
      && inspected.response.run.status === 'active'
      && this.captureDeadlineExpired(inspected.response.run)
      && !this.runExpired(inspected.response.run)
      && Date.now() - inspected.inspectedAt <= 30_000,
    );
  }

  recoveryFinalizing(): boolean {
    const descriptor = this.recovery.load();
    return Boolean(
      descriptor?.runId
      && this.inspectedRecovery?.response.run.id === descriptor.runId
      && this.inspectedRecovery.response.run.status === 'finalizing',
    );
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
      if (!this.isNotFound(error)) throw error;
      await this.spool.clearRun(descriptor.runId);
      this.ensureOperation(generation);
      this.recovery.clear(descriptor.runId);
      this.inspectedRecovery = null;
      return null;
    }
    if (this.terminalRun(response.run) || this.runExpired(response.run)) {
      await this.spool.clearRun(descriptor.runId);
      this.ensureOperation(generation);
      this.recovery.clear(descriptor.runId);
      this.inspectedRecovery = null;
      return response;
    }
    this.inspectedRecovery = { response, inspectedAt: Date.now() };
    return response;
  }

  async prepareCapture(source: VoiceCaptureSource, profileId = ''): Promise<void> {
    if (this.run || this.starting || this.stoppingOperation) {
      throw new Error('voice.long_run.already_active');
    }
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
  ): Promise<VoiceLongRunState> {
    if (this.run || this.starting || this.stoppingOperation) {
      throw new Error('voice.long_run.already_active');
    }
    const generation = this.operationGeneration;
    this.starting = true;
    this.hubUrl = hubUrl;
    this.observer = observer;
    this.resetTimelineProjection();
    this.localGapSequences.clear();
    this.retryAttempt = 0;
    this.lastLocalSequence = -1;
    this.pendingAutomaticStopReason = '';
    this.persistenceQueue = Promise.resolve();
    this.currentRequest = request;
    this.pendingProfileId = request.profile_id;
    try {
      if (!this.secureStorageReady) await this.initializeSecureStorage();
      this.ensureOperation(generation);
      const pendingCreate = this.recovery.load();
      if (pendingCreate?.runId) throw new Error('voice.long_run.resume_required');
      if (pendingCreate && !this.sameRecoveryRequest(pendingCreate, hubUrl, request)) {
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
      const leaseToken = this.validLeaseToken(lease, request.profile_id);
      this.recovery.save({
        schemaVersion: 1,
        runId: '',
        hubUrl,
        createIdempotencyKey: this.createIdempotencyKey,
        profileGeneration: this.profileGeneration,
        request,
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
      if (created.run.status !== 'active' || this.captureDeadlineExpired(created.run)) {
        this.recovery.clear();
        throw new Error('voice.long_run.create_replay_terminal');
      }
      this.publishResponse(created);
      const buffered = await this.spool.list(created.run.id);
      this.ensureOperation(generation);
      const cursor = this.reconciledCursor(created, buffered);
      this.lastLocalSequence = cursor.nextSequence - 1;
      this.localGapSequences = new Set(created.gaps || []);
      for (const sequence of cursor.gaps) this.reportGap(sequence);
      this.applyCursorState(cursor);
      this.saveRecovery(cursor.nextSequence, cursor.timelineMilliseconds);
      this.segmenter = this.createSegmenter({
        segmentDurationSeconds: request.segment_duration_seconds,
        overlapMilliseconds: request.overlap_milliseconds,
        maxDurationSeconds: request.max_duration_seconds,
        initialSequence: cursor.nextSequence,
        initialTimelineMilliseconds: cursor.timelineMilliseconds,
      });
      this.startCaptureDeadline(created.run, request.max_duration_seconds, cursor.timelineMilliseconds);
      this.ensureOperation(generation);
      await this.capture.start(
        (chunk) => this.onCaptureChunk(chunk),
        (error) => this.requestAutomaticStop('capture_error', error),
        (reason) => this.requestAutomaticStop(reason || 'source_ended'),
        { maxDurationSeconds: request.max_duration_seconds },
      );
      this.ensureOperation(generation);
      this.pendingProfileId = '';
      this.startHeartbeat();
      this.kickUploader();
      const pendingReason = this.pendingAutomaticStopReason;
      if (pendingReason) queueMicrotask(() => void this.stop(pendingReason));
      return created.run;
    } catch (error) {
      const cancelled = generation !== this.operationGeneration;
      await this.capture.stop().catch(() => undefined);
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
    globalThis.removeEventListener?.('storage', this.profileDeletionListener);
    globalThis.removeEventListener?.(VOICE_PROFILE_DELETION_EVENT, this.sameDocumentProfileDeletionListener);
    globalThis.document?.removeEventListener('visibilitychange', this.visibilityListener);
    if (this.run) {
      await this.stop('ui_closed').catch(() => undefined);
      return;
    }
    await this.capture.stop().catch(() => undefined);
    this.clearTimers();
  }

  /** Retry encrypted records that survived a reload, without reopening capture. */
  async resumeBuffered(
    hubUrl: string,
    runId: string,
    observer: VoiceLongRunObserver = {},
  ): Promise<VoiceLongRunResponse> {
    if (this.run || this.starting || this.stoppingOperation) {
      throw new Error('voice.long_run.already_active');
    }
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
    this.resetTimelineProjection();
    this.currentRequest = this.requestFromRun(snapshot.run);
    this.createIdempotencyKey = descriptor?.createIdempotencyKey || '';
    const buffered = await this.spool.list(runId);
    this.ensureOperation(generation);
    this.lastLocalSequence = Math.max(
      Number(snapshot.run.last_local_sequence ?? -1),
      ...buffered.map((item) => item.sequence),
    );
    this.observer.runUpdated?.(snapshot);
    await this.drainAvailable();
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
    if (this.run || this.starting || this.stoppingOperation) {
      throw new Error('voice.long_run.already_active');
    }
    const descriptor = this.recovery.load();
    if (!descriptor?.runId) throw new Error('voice.long_run.recovery_not_found');
    if (!descriptor.profileGeneration) throw new Error('voice.long_run.recovery_generation_missing');
    const generation = this.operationGeneration;
    this.starting = true;
    this.hubUrl = descriptor.hubUrl;
    this.observer = observer;
    this.resetTimelineProjection();
    this.currentRequest = descriptor.request;
    this.pendingProfileId = descriptor.request.profile_id;
    this.createIdempotencyKey = descriptor.createIdempotencyKey;
    this.profileGeneration = descriptor.profileGeneration;
    try {
      if (!this.secureStorageReady) await this.initializeSecureStorage();
      this.ensureOperation(generation);
      if (!this.capture.prepared) await this.capture.prepare(descriptor.request.source);
      this.ensureOperation(generation);
      const inspected = this.inspectedRecovery;
      if (!this.recoveryReadyForConsent() || inspected?.response.run.id !== descriptor.runId) {
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
      if (this.captureDeadlineExpired(snapshot.run)) {
        throw new Error('voice.long_run.capture_deadline_expired');
      }
      const buffered = await this.spool.list(descriptor.runId);
      this.ensureOperation(generation);
      const cursor = this.reconciledCursor(snapshot, buffered, descriptor);
      this.run = snapshot.run;
      this.lastLocalSequence = cursor.nextSequence - 1;
      this.localGapSequences = new Set(snapshot.gaps || []);
      for (const sequence of cursor.gaps) this.reportGap(sequence);
      this.applyCursorState(cursor);
      this.segmenter = this.createSegmenter({
        segmentDurationSeconds: descriptor.request.segment_duration_seconds,
        overlapMilliseconds: descriptor.request.overlap_milliseconds,
        maxDurationSeconds: descriptor.request.max_duration_seconds,
        initialSequence: cursor.nextSequence,
        initialTimelineMilliseconds: cursor.timelineMilliseconds,
      });
      this.saveRecovery(cursor.nextSequence, cursor.timelineMilliseconds);
      this.publishResponse(snapshot);
      this.startCaptureDeadline(
        snapshot.run,
        descriptor.request.max_duration_seconds,
        cursor.timelineMilliseconds,
      );
      await this.capture.start(
        (chunk) => this.onCaptureChunk(chunk),
        (error) => this.requestAutomaticStop('capture_error', error),
        (reason) => this.requestAutomaticStop(reason || 'source_ended'),
        { maxDurationSeconds: descriptor.request.max_duration_seconds },
      );
      this.ensureOperation(generation);
      this.pendingProfileId = '';
      this.startHeartbeat();
      this.kickUploader();
      return snapshot.run;
    } catch (error) {
      await this.capture.stop().catch(() => undefined);
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
        } else if (this.terminalRun(snapshot.run) || this.runExpired(snapshot.run)) {
          hubConfirmedEnded = true;
        }
      } catch (error) {
        this.ensureOperation(generation);
        if (this.isNotFound(error)) hubConfirmedEnded = true;
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
    if (this.run || this.starting || this.stoppingOperation) {
      throw new Error('voice.long_run.already_active');
    }
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
        if (this.isNotFound(error)) {
          await this.spool.clearRun(descriptor.runId);
          this.ensureOperation(generation);
          this.recovery.clear(descriptor.runId);
          this.inspectedRecovery = null;
        }
        throw error;
      }
      if (this.terminalRun(snapshot.run) || this.runExpired(snapshot.run)) {
        await this.spool.clearRun(descriptor.runId);
        this.ensureOperation(generation);
        this.recovery.clear(descriptor.runId);
        this.inspectedRecovery = null;
        return snapshot;
      }
      if (snapshot.run.status !== 'active') {
        this.inspectedRecovery = { response: snapshot, inspectedAt: Date.now() };
        throw new Error('voice.long_run.recovery_finalizing');
      }
      if (!this.captureDeadlineExpired(snapshot.run)) {
        this.inspectedRecovery = { response: snapshot, inspectedAt: Date.now() };
        throw new Error('voice.long_run.capture_still_available');
      }

      this.hubUrl = descriptor.hubUrl;
      this.run = snapshot.run;
      this.observer = observer;
      this.resetTimelineProjection();
      this.currentRequest = descriptor.request;
      this.createIdempotencyKey = descriptor.createIdempotencyKey;
      this.profileGeneration = descriptor.profileGeneration;
      const buffered = await this.spool.list(descriptor.runId);
      this.ensureOperation(generation);
      const cursor = this.reconciledCursor(snapshot, buffered, descriptor);
      this.lastLocalSequence = cursor.nextSequence - 1;
      this.localGapSequences = new Set(snapshot.gaps || []);
      for (const sequence of cursor.gaps) this.reportGap(sequence);
      this.applyCursorState(cursor);
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
    this.observer.progress?.(this.segmenter.capturedDurationMs);
    this.latestTimelineMilliseconds = this.segmenter.capturedDurationMs;
    this.saveRecovery(this.lastLocalSequence + 1, this.latestTimelineMilliseconds);
    for (const segment of ready) this.persistSegment(segment);
    if (this.segmenter.reachedLimit) this.requestAutomaticStop('safety_limit');
  }

  private persistSegment(segment: VoiceLongRunPcmSegment): void {
    const runId = this.run?.id;
    if (!runId) return;
    const generation = this.operationGeneration;
    this.lastLocalSequence = Math.max(this.lastLocalSequence, segment.sequence);
    this.completedTimelineMilliseconds = Math.max(this.completedTimelineMilliseconds, segment.endedAtMs);
    this.saveRecovery(segment.sequence + 1, this.latestTimelineMilliseconds);
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
        idempotencyKey: this.segmentIdempotencyKey(runId, segment.sequence),
        audio: wav,
      });
      let result: VoiceLongRunSpoolPutResult;
      try {
        result = await this.withTimeout(write, SPOOL_WRITE_TIMEOUT_MILLISECONDS);
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
        if (evicted.sequence === this.inFlightSequence) {
          this.deferredEvictions.add(evicted.sequence);
        } else {
          this.reportGap(evicted.sequence);
        }
      }
      const stats = await this.spool.stats(runId);
      this.ensureOperation(generation);
      this.durableNextSequence = Math.max(this.durableNextSequence, segment.sequence + 1);
      this.durableTimelineMilliseconds = Math.max(this.durableTimelineMilliseconds, segment.endedAtMs);
      this.saveRecovery(this.lastLocalSequence + 1, this.latestTimelineMilliseconds);
      this.observer.buffered?.(result.stored, stats.segments);
      this.kickUploader();
    }).catch((error) => this.requestAutomaticStop('secure_spool_failed', error))
      .finally(() => {
        this.pendingPersistenceSegments = Math.max(0, this.pendingPersistenceSegments - 1);
        this.pendingPersistenceBytes = Math.max(0, this.pendingPersistenceBytes - plaintextBytes);
      });
  }

  private kickUploader(): void {
    if (!this.run || this.retryTimer || this.uploadOperation) return;
    void this.drainAvailable().catch((error) => this.requestAutomaticStop('secure_spool_failed', error));
  }

  private async drainAvailable(): Promise<void> {
    if (this.uploadOperation) return this.uploadOperation;
    const operation = this.uploadUntilFailure();
    this.uploadOperation = operation;
    try {
      await operation;
    } finally {
      if (this.uploadOperation === operation) this.uploadOperation = null;
    }
  }

  private async uploadUntilFailure(): Promise<void> {
    const runId = this.run?.id;
    if (!runId) return;
    const generation = this.operationGeneration;
    while (this.run?.id === runId && generation === this.operationGeneration) {
      const pending = await this.spool.list(runId);
      if (generation !== this.operationGeneration) return;
      const metadata = pending[0];
      if (!metadata) {
        this.retryAttempt = 0;
        this.observer.connection?.('online');
        return;
      }
      const segment = await this.spool.read(runId, metadata.sequence);
      if (generation !== this.operationGeneration) return;
      if (!segment) continue;
      this.inFlightSequence = segment.sequence;
      try {
        const response = await firstValueFrom(this.api.uploadLongRunSegment(
          this.hubUrl,
          runId,
          segment.sequence,
          {
            file: new Blob([segment.audio], { type: 'audio/wav' }),
            fileName: `voice-live-${String(segment.sequence).padStart(6, '0')}.wav`,
            startedAtMs: segment.startedAtMs,
            endedAtMs: segment.endedAtMs,
            durationMs: segment.durationMs,
            overlapMilliseconds: segment.overlapMilliseconds,
          },
          segment.idempotencyKey,
        ));
        if (generation !== this.operationGeneration) return;
        await this.spool.delete(runId, segment.sequence);
        if (generation !== this.operationGeneration) return;
        this.deferredEvictions.delete(segment.sequence);
        this.localGapSequences.delete(segment.sequence);
        this.run = response.run;
        this.retryAttempt = 0;
        const stats = await this.spool.stats(runId);
        if (generation !== this.operationGeneration) return;
        this.observer.connection?.('online');
        this.publishResponse(response);
        this.observer.segmentUploaded?.(response, stats.segments);
      } catch (error) {
        if (generation !== this.operationGeneration) return;
        const stillBuffered = Boolean(await this.spool.read(runId, segment.sequence));
        if (generation !== this.operationGeneration) return;
        if (!this.isRetriable(error) || !stillBuffered) {
          await this.spool.delete(runId, segment.sequence).catch(() => undefined);
          if (generation !== this.operationGeneration) return;
          this.deferredEvictions.delete(segment.sequence);
          this.reportGap(segment.sequence);
          this.observer.segmentFailed?.(segment.sequence, error);
          continue;
        }
        this.observer.connection?.('retrying');
        if (!this.stopping) this.scheduleRetry();
        return;
      } finally {
        this.inFlightSequence = null;
      }
    }
  }

  private publishResponse(response: VoiceLongRunResponse): void {
    const snapshot = this.timeline.apply(response);
    this.timelineRevisionCursor = Math.max(
      this.timelineRevisionCursor,
      snapshot.highestTimelineRevision,
      Number(response.page?.next_after_revision || 0),
    );
    const hasMoreRevisionPages = response.page?.after_revision != null
      && Boolean(response.page.has_more);
    this.revisionPollingNeeded = (snapshot.hasPendingRevisions || hasMoreRevisionPages)
      && !this.revisionPollingBlocked;
    if (!this.revisionPollingNeeded) this.clearRevisionPollTimer();
    this.observer.timelineUpdated?.(snapshot);
    this.observer.runUpdated?.(response);
    if (this.revisionPollingNeeded) this.scheduleRevisionPoll();
  }

  private scheduleRevisionPoll(delayMilliseconds = REVISION_POLL_MILLISECONDS): void {
    if (!this.revisionPollingNeeded || !this.run || this.stopping || this.revisionPollTimer
      || this.revisionPollInFlight || globalThis.document?.visibilityState === 'hidden') return;
    this.revisionPollTimer = setTimeout(() => {
      this.revisionPollTimer = null;
      void this.pollRevisions();
    }, Math.max(0, delayMilliseconds));
  }

  private async pollRevisions(): Promise<void> {
    if (this.revisionPollInFlight || !this.revisionPollingNeeded || !this.run) return;
    const generation = this.operationGeneration;
    const runId = this.run.id;
    this.revisionPollInFlight = true;
    let nextDelay = REVISION_POLL_MILLISECONDS;
    try {
      const response = await firstValueFrom(this.api.getLongRun(this.hubUrl, runId, {
        afterRevision: this.timelineRevisionCursor,
        limit: REVISION_POLL_LIMIT,
      }));
      if (generation !== this.operationGeneration || this.run?.id !== runId
        || globalThis.document?.visibilityState === 'hidden') return;
      this.run = response.run;
      const hasMore = Boolean(response.page?.has_more);
      this.publishResponse(response);
      this.revisionPollFailure = 0;
      this.observer.connection?.('online');
      nextDelay = hasMore ? 0 : REVISION_POLL_MILLISECONDS;
    } catch (error) {
      if (generation !== this.operationGeneration || this.run?.id !== runId) return;
      if (!this.isRetriable(error)) {
        this.revisionPollingBlocked = true;
        this.revisionPollingNeeded = false;
        this.observer.error?.(error);
        return;
      }
      this.observer.connection?.('retrying');
      nextDelay = REVISION_RETRY_DELAYS_MILLISECONDS[
        Math.min(this.revisionPollFailure, REVISION_RETRY_DELAYS_MILLISECONDS.length - 1)
      ];
      this.revisionPollFailure += 1;
    } finally {
      this.revisionPollInFlight = false;
      if (generation === this.operationGeneration && this.run?.id === runId && !this.stopping) {
        this.scheduleRevisionPoll(nextDelay);
      }
    }
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
      const finalSegment = this.segmenter?.flush();
      if (finalSegment) this.persistSegment(finalSegment);
      await this.persistenceQueue;
      this.ensureOperation(generation);
      for (let attempt = 0; attempt < MAX_STOP_UPLOAD_ATTEMPTS; attempt += 1) {
        await this.drainAvailable();
        this.ensureOperation(generation);
        const pending = await this.spool.list(runId);
        this.ensureOperation(generation);
        if (!pending.length) break;
        if (attempt < MAX_STOP_UPLOAD_ATTEMPTS - 1) {
          await this.delay(RETRY_DELAYS_MILLISECONDS[Math.min(attempt, RETRY_DELAYS_MILLISECONDS.length - 1)]);
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
        gaps: [...this.localGapSequences].sort((left, right) => left - right),
      }));
      if (generation !== this.operationGeneration) return;
      this.run = response.run;
      this.saveRecovery(
        this.lastLocalSequence + 1,
        this.segmenter?.capturedDurationMs || this.recovery.load()?.timelineMilliseconds || 0,
      );
      // Heartbeats intentionally omit transcript text. They may observe a
      // newer Hub revision, but must never advance the content cursor or
      // replace visible text before the text-bearing revision delta arrives.
      this.observer.runUpdated?.(response);
      this.kickUploader();
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
        if (this.terminalRun(response.run)) return response;
        this.run = response.run;
        this.publishResponse(response);
      } catch (error) {
        this.ensureOperation(generation);
        if (!this.isSegmentsInFlight(error)) throw error;
        lastInFlightError = error;
      }
      await this.refreshRevisionsWhileStopping(generation);
      if (attempt < MAX_STOP_CORRECTION_ATTEMPTS - 1) {
        await this.delay(STOP_CORRECTION_POLL_MILLISECONDS);
        this.ensureOperation(generation);
      }
    }
    throw lastInFlightError || new Error('voice.long_run.correction_drain_timeout');
  }

  private async refreshRevisionsWhileStopping(generation: number): Promise<void> {
    if (this.revisionPollInFlight || !this.run) return;
    const runId = this.run.id;
    this.revisionPollInFlight = true;
    try {
      const response = await firstValueFrom(this.api.getLongRun(this.hubUrl, runId, {
        afterRevision: this.timelineRevisionCursor,
        limit: REVISION_POLL_LIMIT,
      }));
      this.ensureOperation(generation);
      if (this.run?.id !== runId) return;
      this.run = response.run;
      this.publishResponse(response);
      this.observer.connection?.('online');
    } catch (error) {
      this.ensureOperation(generation);
      this.observer.connection?.('retrying');
      if (!this.isRetriable(error)) throw error;
    } finally {
      this.revisionPollInFlight = false;
    }
  }

  private scheduleRetry(): void {
    if (this.retryTimer || this.stopping || !this.run) return;
    const delay = RETRY_DELAYS_MILLISECONDS[
      Math.min(this.retryAttempt, RETRY_DELAYS_MILLISECONDS.length - 1)
    ];
    this.retryAttempt += 1;
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null;
      this.kickUploader();
    }, delay);
  }

  private segmentIdempotencyKey(runId: string, sequence: number): string {
    return `voice-ui:long-run-segment:${runId}:${sequence}`;
  }

  private saveRecovery(nextSequence: number, timelineMilliseconds: number): void {
    if (!this.run || !this.currentRequest || !this.createIdempotencyKey) return;
    this.recovery.save({
      schemaVersion: 1,
      runId: this.run.id,
      hubUrl: this.hubUrl,
      createIdempotencyKey: this.createIdempotencyKey,
      profileGeneration: this.profileGeneration,
      request: this.currentRequest,
      nextSequence,
      timelineMilliseconds,
      completedTimelineMilliseconds: this.completedTimelineMilliseconds,
      durableNextSequence: this.durableNextSequence,
      durableTimelineMilliseconds: this.durableTimelineMilliseconds,
      updatedAt: Date.now(),
    });
  }

  private reconciledCursor(
    response: VoiceLongRunResponse,
    buffered: VoiceLongRunSpoolMetadata[],
    descriptor?: VoiceLongRunRecoveryMetadata,
  ): {
    nextSequence: number;
    timelineMilliseconds: number;
    durableNextSequence: number;
    durableTimelineMilliseconds: number;
    completedTimelineMilliseconds: number;
    gaps: number[];
  } {
    const actualSegments = (response.segments || []).filter((item) => item.status !== 'gap');
    const durableNextSequence = Math.max(
      0,
      Number(response.resume?.next_sequence ?? 0),
      ...actualSegments.map((item) => item.sequence + 1),
      ...buffered.map((item) => item.sequence + 1),
      descriptor?.durableNextSequence || 0,
    );
    const durableTimelineMilliseconds = Math.max(
      0,
      descriptor?.durableTimelineMilliseconds || 0,
      ...buffered.map((item) => item.endedAtMs),
      ...actualSegments.map((item) => Number(item.ended_at_ms || 0)),
    );
    const completedTimelineMilliseconds = Math.max(
      durableTimelineMilliseconds,
      descriptor?.completedTimelineMilliseconds || 0,
    );
    const descriptorNext = Math.max(durableNextSequence, descriptor?.nextSequence || 0);
    const gaps = new Set<number>(response.gaps || []);
    for (let sequence = durableNextSequence; sequence < descriptorNext; sequence += 1) gaps.add(sequence);
    let nextSequence = descriptorNext;
    const timelineMilliseconds = Math.max(
      durableTimelineMilliseconds,
      descriptor?.timelineMilliseconds || 0,
    );
    if (timelineMilliseconds > completedTimelineMilliseconds) {
      gaps.add(nextSequence);
      nextSequence += 1;
    }
    nextSequence = Math.max(nextSequence, ...[...gaps].map((sequence) => sequence + 1));
    if (nextSequence > 0 && timelineMilliseconds <= 0) {
      throw new Error('voice.long_run.resume_cursor_invalid');
    }
    return {
      nextSequence,
      timelineMilliseconds,
      durableNextSequence,
      durableTimelineMilliseconds,
      completedTimelineMilliseconds,
      gaps: [...gaps].sort((left, right) => left - right),
    };
  }

  private applyCursorState(cursor: {
    timelineMilliseconds: number;
    durableNextSequence: number;
    durableTimelineMilliseconds: number;
    completedTimelineMilliseconds: number;
  }): void {
    this.latestTimelineMilliseconds = cursor.timelineMilliseconds;
    this.durableNextSequence = cursor.durableNextSequence;
    this.durableTimelineMilliseconds = cursor.durableTimelineMilliseconds;
    this.completedTimelineMilliseconds = cursor.completedTimelineMilliseconds;
  }

  private requestFromRun(run: VoiceLongRunState): VoiceLongRunCreateRequest {
    return {
      source: run.source === 'system_audio' ? 'system_audio' : 'microphone',
      profile_id: String(run.profile_id || 'default'),
      configuration_session_id: run.configuration_session_id || undefined,
      segment_duration_seconds: Number(run.segment_duration_seconds || 120),
      max_duration_seconds: Number(run.max_duration_seconds || 28_800),
      overlap_milliseconds: Number(run.overlap_milliseconds || 0),
    };
  }

  private sameRecoveryRequest(
    metadata: VoiceLongRunRecoveryMetadata,
    hubUrl: string,
    request: VoiceLongRunCreateRequest,
  ): boolean {
    return metadata.hubUrl === hubUrl && JSON.stringify(metadata.request) === JSON.stringify(request);
  }

  private validLeaseToken(lease: VoiceLongRunLease, profileId: string): string {
    const token = String(lease?.lease_token || '').trim();
    const expiresAt = this.timestampMilliseconds(lease?.expires_at);
    if (!token || lease?.profile_id !== profileId
      || !Number.isFinite(expiresAt) || expiresAt <= Date.now()) {
      throw new Error('voice.long_run.start_lease_invalid');
    }
    return token;
  }

  private captureDeadlineExpired(run: VoiceLongRunState): boolean {
    return this.timestampExpired(run.capture_deadline_at);
  }

  private runExpired(run: VoiceLongRunState): boolean {
    return this.timestampExpired(run.expires_at);
  }

  private terminalRun(run: VoiceLongRunState): boolean {
    return ['completed', 'completed_with_gaps', 'expired', 'failed', 'cancelled']
      .includes(run.status);
  }

  private timestampExpired(raw: string | number | null | undefined): boolean {
    const milliseconds = this.timestampMilliseconds(raw);
    return Number.isFinite(milliseconds) && milliseconds <= Date.now();
  }

  private timestampMilliseconds(raw: string | number | null | undefined): number {
    if (raw == null || raw === '') return Number.NaN;
    return typeof raw === 'number'
      ? (raw < 10_000_000_000 ? raw * 1_000 : raw)
      : Date.parse(raw);
  }

  private startCaptureDeadline(
    run: VoiceLongRunState,
    maxDurationSeconds: number,
    timelineMilliseconds: number,
  ): void {
    if (this.captureDeadlineTimer) clearTimeout(this.captureDeadlineTimer);
    const explicit = this.timestampMilliseconds(run.capture_deadline_at);
    const started = this.timestampMilliseconds(run.started_at);
    const remaining = Math.max(0, maxDurationSeconds * 1_000 - timelineMilliseconds);
    const deadline = Number.isFinite(explicit)
      ? explicit
      : Number.isFinite(started)
        ? started + maxDurationSeconds * 1_000
        : Date.now() + remaining;
    this.captureDeadlineTimer = setTimeout(
      () => this.requestAutomaticStop('capture_deadline'),
      Math.max(0, deadline - Date.now()),
    );
  }

  private ensureOperation(generation: number): void {
    if (generation !== this.operationGeneration) throw new Error('voice.capture.cancelled');
  }

  private clearTimers(): void {
    this.clearHeartbeat();
    this.clearRevisionPollTimer();
    if (this.retryTimer) clearTimeout(this.retryTimer);
    if (this.captureDeadlineTimer) clearTimeout(this.captureDeadlineTimer);
    this.retryTimer = null;
    this.captureDeadlineTimer = null;
  }

  private clearHeartbeat(): void {
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = null;
  }

  private clearRevisionPollTimer(): void {
    if (this.revisionPollTimer) clearTimeout(this.revisionPollTimer);
    this.revisionPollTimer = null;
  }

  private resetTimelineProjection(): void {
    this.clearRevisionPollTimer();
    this.timeline.reset();
    this.revisionPollingNeeded = false;
    this.revisionPollingBlocked = false;
    this.revisionPollFailure = 0;
    this.timelineRevisionCursor = 0;
  }

  private resetRuntime(): void {
    this.clearTimers();
    this.run = null;
    this.segmenter = null;
    this.uploadOperation = null;
    this.stopping = false;
    this.pendingAutomaticStopReason = '';
    this.localGapSequences.clear();
    this.retryAttempt = 0;
    this.lastLocalSequence = -1;
    this.currentRequest = null;
    this.createIdempotencyKey = '';
    this.inFlightSequence = null;
    this.deferredEvictions.clear();
    this.pendingPersistenceSegments = 0;
    this.pendingPersistenceBytes = 0;
    this.latestTimelineMilliseconds = 0;
    this.completedTimelineMilliseconds = 0;
    this.durableNextSequence = 0;
    this.durableTimelineMilliseconds = 0;
    this.profileGeneration = 0;
    this.pendingProfileId = '';
    this.resetTimelineProjection();
  }

  private delay(milliseconds: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, milliseconds));
  }

  private withTimeout<T>(operation: Promise<T>, milliseconds: number): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      const timeout = setTimeout(
        () => reject(new Error('voice.long_run.secure_spool_timeout')),
        milliseconds,
      );
      operation.then(
        (value) => {
          clearTimeout(timeout);
          resolve(value);
        },
        (error) => {
          clearTimeout(timeout);
          reject(error);
        },
      );
    });
  }

  private reportGap(sequence: number): void {
    if (this.localGapSequences.has(sequence)) return;
    this.localGapSequences.add(sequence);
    this.observer.gap?.(sequence);
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

  private isRetriable(error: unknown): boolean {
    const candidate = (error as any)?.error?.data?.error
      ?? (error as any)?.error?.error
      ?? (error as any)?.error
      ?? error;
    if (typeof candidate?.retriable === 'boolean') return candidate.retriable;
    const status = Number((error as any)?.status || candidate?.status || 0);
    return status === 0 || status === 408 || status === 425 || status === 429 || status >= 500;
  }

  private isNotFound(error: unknown): boolean {
    return Number((error as any)?.status || (error as any)?.error?.status || 0) === 404;
  }

  private isSegmentsInFlight(error: unknown): boolean {
    const candidate = (error as any)?.error?.data?.error
      ?? (error as any)?.error?.error
      ?? (error as any)?.error
      ?? error;
    return String(candidate?.code || '') === 'voice_live_run.segments_in_flight'
      && candidate?.retriable !== false;
  }
}
