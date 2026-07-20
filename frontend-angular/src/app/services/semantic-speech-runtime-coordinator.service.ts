import { Injectable, OnDestroy, inject } from '@angular/core';
import { BehaviorSubject, Observable, Subject, Subscription, firstValueFrom } from 'rxjs';

import { ReconstructedSpeechAudio } from '../features/voice/reconstruction/speech-reconstructor';
import {
  SPEECH_RECONSTRUCTION_ROUTER,
  SpeechPersonalizationActivation,
  SpeechReconstructionRouterPort,
} from '../features/voice/reconstruction/speech-reconstruction-router.service';
import { SpeechDelayBufferService } from './speech-delay-buffer.service';
import {
  DEFAULT_SEMANTIC_SPEECH_SETTINGS,
  SemanticSpeechSettings,
} from './semantic-speech-settings';
import {
  SemanticSpeechPayload,
  SemanticSpeechTransportContext,
  SemanticSpeechTransportService,
} from './semantic-speech-transport.service';
import {
  SemanticSpeechSourceCorrectionApiService,
  SemanticSpeechSourceCorrectionResponse,
} from './semantic-speech-source-correction-api.service';
import {
  SemanticSpeechQualityControllerService,
  SemanticSpeechQualityReport,
  SemanticSpeechQualityState,
} from './semantic-speech-quality-controller.service';
import { SpeechTranscriptRevisionStore } from './speech-transcript-revision.store';
import { WebrtcMediaHealthService } from './webrtc-media-health.service';

export interface SemanticSpeechRuntimeContext extends SemanticSpeechTransportContext {
  readonly hubUrl: string;
  readonly language?: string;
  readonly correctionConsent?: Readonly<{
    consentId: string;
    consentDigest: string;
    consentVersion: number;
    revocationEpoch: number;
    expiresAtMs: number;
  }>;
}

type FinalPayload = SemanticSpeechPayload & Readonly<{
  kind: 'transcript_revision'; authority: 'final'; text: string; source_digest: string;
}>;

interface WaitingFinal {
  readonly payload: FinalPayload;
  readonly timer: ReturnType<typeof setTimeout>;
}

type StagedSource = Readonly<Pick<
  SemanticSpeechPayload,
  'session_id' | 'epoch' | 'turn_id' | 'revision' | 'source_digest' | 'expires_at_ms'
>>;

const MAX_WAITING_FINALS = 5;
const MAX_STAGED_SOURCES = 5;
const MAX_INFLIGHT_CORRECTIONS = 5;
const MAX_ATTEMPT_KEYS = 2_048;
const MAX_SOURCE_ERRORS = 256;
const MAX_QUALITY_WINDOW = 20;
const SOURCE_WAIT_MS = 5_000;

/**
 * Product composition for transcript-first speech.
 *
 * The coordinator owns lifecycle and sequencing only. Audio is encrypted by
 * SpeechDelayBufferService; correction remains a Hub-owned worker operation
 * backed by the canonical Python fusion alignment; reconstruction remains a
 * receiver-local port. No second alignment implementation exists here.
 */
@Injectable()
export class SemanticSpeechRuntimeCoordinatorService implements OnDestroy {
  private readonly transport = inject(SemanticSpeechTransportService);
  private readonly buffer = inject(SpeechDelayBufferService);
  private readonly corrections = inject(SemanticSpeechSourceCorrectionApiService);
  private readonly transcripts = inject(SpeechTranscriptRevisionStore);
  private readonly reconstructor: SpeechReconstructionRouterPort = inject(SPEECH_RECONSTRUCTION_ROUTER);
  private readonly quality = inject(SemanticSpeechQualityControllerService);
  private readonly mediaHealth = inject(WebrtcMediaHealthService);
  private readonly subscription = new Subscription();
  private readonly waitingFinals = new Map<string, WaitingFinal>();
  private readonly stagedSources = new Map<string, StagedSource>();
  private readonly sourceErrors = new Map<string, string>();
  private readonly attempted = new Map<string, true>();
  private readonly inflight = new Map<string, FinalPayload>();
  private readonly features = new Map<string, readonly number[]>();
  private readonly activeAudio = new Map<string, ReconstructedSpeechAudio>();
  private readonly partialObservedAt = new Map<string, number>();
  private readonly correctionObservedAt = new Map<string, number>();
  private readonly sourceOutcomes = new Map<string, boolean>();
  private readonly featureOutcomes = new Map<string, boolean>();
  private readonly reconstructionOutcomes = new Map<string, boolean>();
  private context: SemanticSpeechRuntimeContext | null = null;
  private generation = 0;
  private networkLossRatio = 0;
  private qualityState: SemanticSpeechQualityState = this.quality.state$.value;
  private qualityAllowsCorrection = false;
  private qualityAllowsFeatures = false;

  readonly settings$ = new BehaviorSubject<SemanticSpeechSettings>(DEFAULT_SEMANTIC_SPEECH_SETTINGS);
  readonly fatalFailure$ = new Subject<string>();
  private readonly outboundCorrectionSubject = new Subject<SemanticSpeechPayload>();
  /**
   * Source-side corrections that have already been admitted to the local
   * revision store and must be published once by the capture producer.
   * Keeping transport publication outside this coordinator preserves its
   * single responsibility for transcript/correction state.
   */
  readonly outboundCorrection$: Observable<SemanticSpeechPayload> =
    this.outboundCorrectionSubject.asObservable();

  constructor() {
    this.subscription.add(this.transport.payload$.subscribe(payload => { void this.ingest(payload); }));
    this.subscription.add(this.transport.pressure$.subscribe(value => {
      if (this.context) this.evaluateQuality(Date.now(), value.pendingBytes);
    }));
    this.subscription.add(this.mediaHealth.window$.subscribe(window => {
      const context = this.context;
      if (!context || window.session_id !== context.sessionId || window.peer_id !== context.remotePeerId) return;
      this.networkLossRatio = window.packet_loss_ratio ?? 0;
      this.evaluateQuality(window.window_end_ms);
    }));
    this.subscription.add(this.quality.state$.subscribe(state => {
      this.qualityState = state;
      this.applyQualityState(state);
    }));
  }

  start(context: SemanticSpeechRuntimeContext): void {
    this.validateContext(context);
    const previous = this.context;
    if (previous && (
      previous.sessionId !== context.sessionId || previous.epoch !== context.epoch
    )) {
      this.transcripts.clear();
      this.stop('semantic_speech_context_replaced');
    } else if (previous && this.contextKey(previous) !== this.contextKey(context)) {
      this.generation += 1;
      this.buffer.revoke(previous.sessionId);
      this.clearWaiting('semantic_speech_consent_changed');
      this.clearInflight('semantic_speech_consent_changed');
      this.stagedSources.clear();
      void this.reconstructor.clearPersonalization('semantic_speech_consent_changed');
    }
    this.context = null;
    this.resetQualityMeasurements();
    this.quality.reset('semantic_speech_context_started');
    this.context = Object.freeze({ ...context });
    this.transcripts.setLiveMode(this.settings$.value.displayMode === 'live');
    this.transcripts.setOrdinaryOverride(this.settings$.value.ordinaryAudioOverride);
    this.evaluateQuality();
  }

  stop(reasonCode = 'semantic_speech_stopped'): void {
    this.generation += 1;
    const sessionId = this.context?.sessionId;
    if (sessionId) this.buffer.revoke(sessionId);
    this.context = null;
    this.clearWaiting(reasonCode);
    this.clearInflight(reasonCode);
    this.stagedSources.clear();
    this.sourceErrors.clear();
    this.features.clear();
    this.releaseAudio();
    void this.reconstructor.clearPersonalization(reasonCode);
    this.resetQualityMeasurements();
    this.quality.reset(reasonCode);
  }

  applySettings(settings: SemanticSpeechSettings): void {
    const normalized = this.validateSettings(settings);
    const previous = this.settings$.value;
    this.settings$.next(normalized);
    this.transcripts.setLiveMode(normalized.displayMode === 'live');
    this.transcripts.setOrdinaryOverride(
      normalized.ordinaryAudioOverride,
      normalized.ordinaryAudioOverride ? 'ordinary_audio_override' : 'ordinary_audio_fallback',
    );
    if (normalized.paused || normalized.ordinaryAudioOverride || !normalized.correctEachSegment) {
      this.releaseAudio();
    }
    if (normalized.paused || normalized.ordinaryAudioOverride) {
      void this.reconstructor.clearPersonalization('semantic_speech_runtime_inactive');
    }
    if (!normalized.correctEachSegment && previous.correctEachSegment) {
      this.generation += 1;
      for (const { payload } of this.waitingFinals.values()) {
        this.removeWaiting(payload.turn_id);
        this.transcripts.markCorrection(payload.turn_id, payload.revision, 'disabled', 'source_correction_disabled');
      }
      if (this.context) this.buffer.revoke(this.context.sessionId);
      this.clearInflight('source_correction_disabled');
      this.stagedSources.clear();
    }
    this.evaluateQuality();
  }

  reportNetworkLoss(lossRatio: number, measuredAtMs = Date.now()): SemanticSpeechQualityState {
    if (!Number.isFinite(lossRatio) || lossRatio < 0 || lossRatio > 1) {
      throw new Error('speech_quality_ratio_invalid');
    }
    this.networkLossRatio = lossRatio;
    return this.evaluateQuality(measuredAtMs);
  }

  /**
   * Activate one already approved receiver-local adapter explicitly.
   * Starting semantic speech never selects or activates an adapter by itself.
   */
  async activatePersonalization(activation: SpeechPersonalizationActivation, nowMs = Date.now()): Promise<void> {
    const context = this.requireContext();
    if (
      activation.metadata.pair_id !== context.sessionId
      || activation.context.pairId !== context.sessionId
      || activation.metadata.direction !== activation.context.direction
    ) throw new Error('speech_adapter_runtime_pair_binding_mismatch');
    await this.reconstructor.activatePersonalization(activation, nowMs);
  }

  async revokePersonalization(adapterId: string): Promise<void> {
    await this.reconstructor.revokePersonalization(adapterId);
  }

  async cleanupPersonalization(nowMs = Date.now()): Promise<boolean> {
    return this.reconstructor.cleanupExpired(nowMs);
  }

  /** Capture adapters can stage local bytes without receiving an E2EE source frame. */
  async stageSource(
    value: Readonly<{
      turnId: string; revision: number; sourceDigest: string; expiresAtMs: number; bytes: Uint8Array;
    }>,
  ): Promise<void> {
    const context = this.requireContext();
    try {
      await this.stageSourcePayload({
        session_id: context.sessionId,
        epoch: context.epoch,
        turn_id: value.turnId,
        revision: value.revision,
        source_digest: value.sourceDigest,
        expires_at_ms: value.expiresAtMs,
      }, value.bytes);
    } catch (error) {
      this.quality.containRuntimeFailure(this.reasonCode(error, 'source_audio_buffer_failed'));
      throw error;
    } finally {
      value.bytes.fill(0);
    }
  }

  /** Local capture adapters publish their final through the same receiver-store path. */
  async finalizeLocal(payload: SemanticSpeechPayload): Promise<boolean> {
    const context = this.requireContext();
    if (payload.sender_id !== context.localPeerId || payload.audience_id !== context.remotePeerId) {
      throw new Error('semantic_speech_local_direction_invalid');
    }
    return this.ingest(payload);
  }

  /** Apply the same bounded containment policy to Hub stream/capture failures. */
  reportCaptureTransportFailure(status: number, turnId: string): void {
    if (!Number.isSafeInteger(status) || status < 100 || status > 599) {
      this.quality.containRuntimeFailure('speech_transport_failed');
      return;
    }
    this.containTransportFailure(status, turnId);
  }

  snapshot(): Readonly<{
    active: boolean; waitingFinals: number; stagedSources: number; attempts: number; inflight: number; timers: number;
    qualityMode: string; qualityReason: string;
  }> {
    return Object.freeze({
      active: this.context !== null,
      waitingFinals: this.waitingFinals.size,
      stagedSources: this.stagedSources.size,
      attempts: this.attempted.size,
      inflight: this.inflight.size,
      timers: this.waitingFinals.size,
      qualityMode: this.qualityState.mode,
      qualityReason: this.qualityState.reasonCode,
    });
  }

  ngOnDestroy(): void {
    this.stop('semantic_speech_coordinator_destroyed');
    this.subscription.unsubscribe();
    this.settings$.complete();
    this.fatalFailure$.complete();
    this.outboundCorrectionSubject.complete();
  }

  private async ingest(payload: SemanticSpeechPayload): Promise<boolean> {
    const context = this.context;
    if (!context || !this.belongsToContext(payload, context)) return false;
    if (payload.kind === 'revoke') {
      const revokeReason = payload.reason_code || 'semantic_speech_revoked';
      this.fatalFailure$.next(revokeReason);
      if (this.context) {
        this.transport.stop();
        this.stop(revokeReason);
      }
      this.quality.ingest(this.qualityReport(Date.now()), 'ordinary_audio', { revoked: true });
      return true;
    }
    const settings = this.settings$.value;
    if (settings.paused || settings.ordinaryAudioOverride) return false;
    if (payload.kind === 'semantic_frame') {
      if (!this.qualityAllowsFeatures) return false;
      this.features.set(payload.turn_id, Object.freeze([...(payload.features ?? [])]));
      while (this.features.size > 256) this.features.delete(this.features.keys().next().value!);
      this.evaluateQuality();
      return true;
    }
    if (payload.kind === 'source_audio') {
      // Raw source audio is never accepted from a remote peer. Source-side
      // correction stages local capture bytes through stageSource() only.
      if (payload.sender_id !== context.localPeerId) return false;
      if (!settings.correctEachSegment || !this.qualityAllowsCorrection) return false;
      try {
        const bytes = this.fromBase64(payload.audio_ciphertext || '');
        await this.stageSourcePayload(payload, bytes);
        bytes.fill(0);
        return true;
      } catch (error) {
        const reason = this.reasonCode(error, 'source_audio_buffer_failed');
        this.rememberSourceError(payload.turn_id, reason);
        const waiting = this.waitingFinals.get(payload.turn_id)?.payload;
        if (waiting) this.failWaiting(waiting, reason, 'failed');
        this.quality.containRuntimeFailure(reason);
        return false;
      }
    }
    const applied = this.transcripts.apply(payload);
    if (!applied) return false;
    const observedAtMs = Date.now();
    if (payload.authority === 'provisional') {
      this.rememberTimestamp(this.partialObservedAt, payload.turn_id, observedAtMs, 256);
    } else {
      this.partialObservedAt.delete(payload.turn_id);
    }
    if (payload.kind === 'correction') {
      this.buffer.correctionComplete(payload.turn_id);
      this.removeWaiting(payload.turn_id);
      this.stagedSources.delete(payload.turn_id);
      this.correctionObservedAt.delete(payload.turn_id);
      this.rememberOutcome(this.sourceOutcomes, payload.turn_id, false);
      this.evaluateQuality(observedAtMs);
      void this.reconstruct(payload);
      return true;
    }
    if (payload.authority !== 'final' || !payload.source_digest) {
      this.evaluateQuality(observedAtMs);
      void this.reconstruct(payload);
      return true;
    }
    this.rememberOutcome(this.featureOutcomes, payload.turn_id, !this.features.has(payload.turn_id));
    this.evaluateQuality(observedAtMs);
    void this.reconstruct(payload);
    // A receiver displays and reconstructs a remote final, but must never run
    // a second correction over foreign source material. The sender remains the
    // sole owner of source correction and publishes the admitted revision.
    if (payload.sender_id !== context.localPeerId) {
      this.stagedSources.delete(payload.turn_id);
      this.buffer.confirm(payload.turn_id);
      return true;
    }
    if (!settings.correctEachSegment || !this.qualityAllowsCorrection) {
      const reason = settings.correctEachSegment
        ? this.qualityState.reasonCode
        : 'source_correction_disabled';
      this.transcripts.markCorrection(payload.turn_id, payload.revision, 'disabled', reason);
      this.buffer.confirm(payload.turn_id);
      return true;
    }
    const final = payload as FinalPayload;
    this.rememberTimestamp(this.correctionObservedAt, final.turn_id, observedAtMs, MAX_WAITING_FINALS);
    const sourceError = this.sourceErrors.get(final.turn_id);
    if (sourceError) {
      this.failWaiting(final, sourceError, 'failed');
      return true;
    }
    this.waitForSource(final);
    void this.tryCorrection(final);
    return true;
  }

  private async stageSourcePayload(payload: StagedSource, bytes: Uint8Array): Promise<void> {
    const context = this.requireContext();
    if (
      payload.session_id !== context.sessionId || payload.epoch !== context.epoch
      || !payload.source_digest || payload.expires_at_ms <= Date.now()
    ) throw new Error('source_audio_context_invalid');
    await this.buffer.put({
      sessionId: context.sessionId,
      epoch: context.epoch,
      segmentId: payload.turn_id,
      sourceDigest: payload.source_digest,
      expiresAtMs: payload.expires_at_ms,
    }, bytes);
    this.sourceErrors.delete(payload.turn_id);
    this.rememberStagedSource(Object.freeze({
      session_id: payload.session_id,
      epoch: payload.epoch,
      turn_id: payload.turn_id,
      revision: payload.revision,
      source_digest: payload.source_digest,
      expires_at_ms: payload.expires_at_ms,
    }));
    this.evaluateQuality();
    const final = this.waitingFinals.get(payload.turn_id)?.payload;
    if (final) void this.tryCorrection(final);
  }

  private waitForSource(payload: FinalPayload): void {
    this.removeWaiting(payload.turn_id);
    while (this.waitingFinals.size >= MAX_WAITING_FINALS) {
      const oldest = this.waitingFinals.values().next().value as WaitingFinal | undefined;
      if (!oldest) break;
      this.failWaiting(oldest.payload, 'source_wait_capacity_exceeded', 'missing_source');
    }
    const delay = Math.max(1, Math.min(SOURCE_WAIT_MS, payload.expires_at_ms - Date.now()));
    const timer = globalThis.setTimeout(() => {
      const current = this.waitingFinals.get(payload.turn_id)?.payload;
      if (current === payload) this.failWaiting(payload, 'source_missing_or_expired', 'missing_source');
    }, delay);
    this.waitingFinals.set(payload.turn_id, { payload, timer });
    this.transcripts.markCorrection(payload.turn_id, payload.revision, 'awaiting_source', 'source_audio_pending');
  }

  private async tryCorrection(final: FinalPayload): Promise<void> {
    const context = this.context;
    const source = this.stagedSources.get(final.turn_id);
    const attemptKey = this.attemptKey(final);
    if (!context || !source || this.attempted.has(attemptKey)) return;
    if (this.inflight.size >= MAX_INFLIGHT_CORRECTIONS) {
      this.failWaiting(final, 'source_correction_capacity_exceeded', 'failed');
      return;
    }
    const consent = context.correctionConsent;
    if (!consent || consent.expiresAtMs <= Date.now()) {
      this.failWaiting(final, 'source_correction_consent_required', 'failed');
      return;
    }
    if (source.source_digest !== final.source_digest) {
      this.failWaiting(final, 'source_digest_mismatch', 'failed');
      this.buffer.correctionComplete(final.turn_id);
      return;
    }
    const generation = this.generation;
    this.rememberAttempt(attemptKey);
    this.removeWaiting(final.turn_id);
    this.inflight.set(attemptKey, final);
    this.transcripts.markCorrection(final.turn_id, final.revision, 'pending', 'source_correction_pending');
    const used = await this.buffer.use(final.turn_id, async sourceAudio => {
      const deadlineAtMs = Math.min(final.expires_at_ms, source.expires_at_ms, Date.now() + 30_000);
      return firstValueFrom(this.corrections.correct({
        hubUrl: context.hubUrl,
        sessionId: context.sessionId,
        epoch: context.epoch,
        turnId: final.turn_id,
        finalRevision: final.revision,
        consentVersion: consent.consentVersion,
        consentId: consent.consentId,
        consentDigest: consent.consentDigest,
        consentRevocationEpoch: consent.revocationEpoch,
        contractDigest: context.contractDigest,
        sourceDigest: final.source_digest,
        sourceExpiresAtMs: Math.min(final.expires_at_ms, source.expires_at_ms),
        deadlineAtMs,
        finalText: final.text,
        sourceAudio,
        language: context.language,
      }));
    }).catch(error => {
      const status = this.httpStatus(error);
      const correctionReason = this.reasonCode(error, 'source_correction_request_failed');
      if (generation === this.generation && this.context === context) {
        this.transcripts.markCorrection(
          final.turn_id, final.revision, 'failed', correctionReason,
        );
        this.transcripts.ordinaryFallback(final.turn_id, correctionReason);
      }
      this.inflight.delete(attemptKey);
      if (status) this.containTransportFailure(status, final.turn_id);
      return null;
    });
    this.inflight.delete(attemptKey);
    this.buffer.correctionComplete(final.turn_id);
    this.stagedSources.delete(final.turn_id);
    if (!used || generation !== this.generation || this.context !== context) return;
    if (!used.available) {
      this.rememberOutcome(this.sourceOutcomes, final.turn_id, true);
      this.transcripts.markCorrection(final.turn_id, final.revision, 'missing_source', 'source_missing_or_expired');
      this.evaluateQuality();
      return;
    }
    this.applyCorrection(final, used.value);
  }

  private applyCorrection(final: FinalPayload, result: SemanticSpeechSourceCorrectionResponse): void {
    if (
      result.session_id !== final.session_id || result.epoch !== final.epoch
      || result.turn_id !== final.turn_id || result.supersedes_revision !== final.revision
      || result.revision !== final.revision + 1 || result.source_digest !== final.source_digest
    ) {
      this.transcripts.markCorrection(final.turn_id, final.revision, 'failed', 'source_correction_response_invalid');
      this.quality.containRuntimeFailure('source_correction_response_invalid');
      return;
    }
    if (result.authority === 'final') {
      this.transcripts.markCorrection(final.turn_id, final.revision, 'completed', result.reason_code);
      this.correctionObservedAt.delete(final.turn_id);
      this.rememberOutcome(this.sourceOutcomes, final.turn_id, false);
      this.evaluateQuality();
      return;
    }
    const correctionPayload: SemanticSpeechPayload = Object.freeze({
      ...final,
      kind: 'correction',
      revision: result.revision,
      authority: result.authority,
      text: result.text,
      reason_code: result.reason_code,
    });
    const applied = this.transcripts.apply(correctionPayload);
    if (applied && final.sender_id === this.context?.localPeerId) {
      this.outboundCorrectionSubject.next(correctionPayload);
    }
    this.correctionObservedAt.delete(final.turn_id);
    this.rememberOutcome(this.sourceOutcomes, final.turn_id, false);
    this.evaluateQuality();
  }

  private failWaiting(
    final: FinalPayload,
    reason: string,
    status: 'failed' | 'missing_source',
  ): void {
    this.rememberAttempt(this.attemptKey(final));
    this.removeWaiting(final.turn_id);
    this.stagedSources.delete(final.turn_id);
    this.buffer.correctionComplete(final.turn_id);
    this.transcripts.markCorrection(final.turn_id, final.revision, status, reason);
    this.correctionObservedAt.delete(final.turn_id);
    if (status === 'missing_source') this.rememberOutcome(this.sourceOutcomes, final.turn_id, true);
    this.evaluateQuality();
  }

  private async reconstruct(payload: SemanticSpeechPayload): Promise<void> {
    if (
      !payload.text || !payload.authority || !['provisional', 'final', 'corrected'].includes(payload.authority)
      || this.settings$.value.paused || this.settings$.value.ordinaryAudioOverride
      || !this.qualityState.semanticFeaturesEnabled
    ) return;
    const previous = this.activeAudio.get(payload.turn_id);
    previous?.release();
    this.activeAudio.delete(payload.turn_id);
    try {
      const result = await this.reconstructor.reconstruct({
        turnId: payload.turn_id,
        revision: payload.revision,
        text: payload.text,
        authority: payload.authority as 'provisional' | 'final' | 'corrected',
        features: this.features.get(payload.turn_id),
        deadlineAtMs: Date.now() + 2_000,
        ordinaryAudioAvailable: true,
      });
      const failed = !['generic', 'personalized'].includes(result.mode) || !result.audio
        || (result.quality !== null && result.quality.score < 0.4);
      this.rememberOutcome(this.reconstructionOutcomes, payload.turn_id, failed);
      if (failed) {
        if (result.mode === 'personalized') {
          void this.reconstructor.clearPersonalization(
            result.reasonCode || 'speech_adapter_quality_failed',
          );
        }
        this.transcripts.ordinaryFallback(payload.turn_id, result.reasonCode || 'generic_speech_runtime_failed');
        this.quality.containRuntimeFailure(result.reasonCode || 'generic_speech_runtime_failed');
        return;
      }
      this.evaluateQuality();
      if (this.settings$.value.ordinaryAudioOverride) {
        result.audio.release();
        return;
      }
      this.activeAudio.set(payload.turn_id, result.audio);
      await result.audio.play();
      result.audio.release();
      if (this.activeAudio.get(payload.turn_id) === result.audio) this.activeAudio.delete(payload.turn_id);
    } catch {
      // Ordinary E2EE media remains audible; transcript state is unaffected.
      void this.reconstructor.clearPersonalization('speech_adapter_runtime_failed');
      this.rememberOutcome(this.reconstructionOutcomes, payload.turn_id, true);
      this.transcripts.ordinaryFallback(payload.turn_id, 'generic_speech_runtime_failed');
      this.quality.containRuntimeFailure('generic_speech_runtime_failed');
    }
  }

  private clearWaiting(reason: string): void {
    for (const value of this.waitingFinals.values()) {
      globalThis.clearTimeout(value.timer);
      this.transcripts.markCorrection(value.payload.turn_id, value.payload.revision, 'failed', reason);
    }
    this.waitingFinals.clear();
  }

  private clearInflight(reason: string): void {
    for (const final of this.inflight.values()) {
      this.transcripts.markCorrection(final.turn_id, final.revision, 'failed', reason);
    }
    this.inflight.clear();
  }

  private removeWaiting(turnId: string): void {
    const waiting = this.waitingFinals.get(turnId);
    if (waiting) globalThis.clearTimeout(waiting.timer);
    this.waitingFinals.delete(turnId);
  }

  private releaseAudio(): void {
    for (const audio of this.activeAudio.values()) audio.release();
    this.activeAudio.clear();
  }

  private evaluateQuality(
    measuredAtMs = Date.now(),
    queueBytes = this.transport.snapshot().pendingBytes,
  ): SemanticSpeechQualityState {
    if (!this.context) return this.quality.state$.value;
    return this.quality.ingest(
      this.qualityReport(measuredAtMs, queueBytes),
      this.desiredQualityMode(),
      { userOrdinaryOverride: this.settings$.value.ordinaryAudioOverride },
    );
  }

  private qualityReport(
    measuredAtMs: number,
    queueBytes = this.transport.snapshot().pendingBytes,
  ): SemanticSpeechQualityReport {
    const partialAgeMs = this.maximumAge(this.partialObservedAt, measuredAtMs);
    const correctionLagMs = this.maximumAge(this.correctionObservedAt, measuredAtMs);
    return {
      measuredAtMs,
      lossRatio: this.networkLossRatio,
      queueBytes,
      partialAgeMs,
      correctionLagMs,
      sourceLossRatio: this.outcomeRatio(this.sourceOutcomes, 1),
      featureLossRatio: this.outcomeRatio(this.featureOutcomes, 4),
      reconstructionErrorRatio: this.outcomeRatio(this.reconstructionOutcomes, 5),
    };
  }

  private desiredQualityMode(): 'ordinary_audio' | 'semantic_reconstruction' | 'segment_only' {
    const settings = this.settings$.value;
    if (settings.paused || settings.ordinaryAudioOverride) return 'ordinary_audio';
    if (settings.displayMode === 'segment' && !settings.correctEachSegment) return 'segment_only';
    return 'semantic_reconstruction';
  }

  private applyQualityState(state: SemanticSpeechQualityState): void {
    const context = this.context;
    if (!context) return;
    const correctionWasAllowed = this.qualityAllowsCorrection;
    this.qualityAllowsCorrection = state.delayedSourceEnabled
      && this.settings$.value.correctEachSegment
      && !this.settings$.value.paused
      && !this.settings$.value.ordinaryAudioOverride;
    this.qualityAllowsFeatures = state.semanticFeaturesEnabled
      && !this.settings$.value.paused
      && !this.settings$.value.ordinaryAudioOverride;
    this.transcripts.setOrdinaryOverride(
      state.mode === 'ordinary_audio' || this.settings$.value.ordinaryAudioOverride,
      state.reasonCode,
    );
    if (!this.qualityAllowsFeatures) {
      this.features.clear();
      this.releaseAudio();
      void this.reconstructor.clearPersonalization(state.reasonCode);
    }
    if (correctionWasAllowed && !this.qualityAllowsCorrection) {
      this.generation += 1;
      this.buffer.revoke(context.sessionId);
      this.clearWaiting(state.reasonCode);
      this.clearInflight(state.reasonCode);
      this.stagedSources.clear();
      this.correctionObservedAt.clear();
    }
  }

  private containTransportFailure(status: number, turnId: string): void {
    this.buffer.containTransportFailure(status, turnId);
    if (status === 413) {
      const durations = [10, 30, 60, 90, 120] as const;
      const current = this.settings$.value.segmentDurationSeconds;
      const smaller = [...durations].reverse().find(value => value < current) ?? 10;
      if (smaller !== current) this.applySettings({ ...this.settings$.value, segmentDurationSeconds: smaller });
      this.quality.containRuntimeFailure('speech_segment_too_large');
      return;
    }
    if (status === 404 || status === 409) {
      const reason = 'speech_session_gone';
      this.fatalFailure$.next(reason);
      if (this.context) {
        this.transport.stop();
        this.stop(reason);
      }
      this.quality.containRuntimeFailure(reason);
      return;
    }
    this.quality.containRuntimeFailure('speech_transport_failed');
  }

  private resetQualityMeasurements(): void {
    this.networkLossRatio = 0;
    this.partialObservedAt.clear();
    this.correctionObservedAt.clear();
    this.sourceOutcomes.clear();
    this.featureOutcomes.clear();
    this.reconstructionOutcomes.clear();
    this.qualityAllowsCorrection = false;
    this.qualityAllowsFeatures = false;
  }

  private rememberTimestamp(
    values: Map<string, number>,
    turnId: string,
    observedAtMs: number,
    limit: number,
  ): void {
    values.delete(turnId);
    values.set(turnId, observedAtMs);
    while (values.size > limit) values.delete(values.keys().next().value!);
  }

  private rememberOutcome(values: Map<string, boolean>, turnId: string, failed: boolean): void {
    values.delete(turnId);
    values.set(turnId, failed);
    while (values.size > MAX_QUALITY_WINDOW) values.delete(values.keys().next().value!);
  }

  private outcomeRatio(values: Map<string, boolean>, minimumSamples: number): number {
    if (values.size < minimumSamples) return 0;
    return Array.from(values.values()).filter(Boolean).length / values.size;
  }

  private maximumAge(values: Map<string, number>, measuredAtMs: number): number {
    let maximum = 0;
    for (const observedAtMs of values.values()) {
      maximum = Math.max(maximum, measuredAtMs - observedAtMs);
    }
    return Math.max(0, Math.trunc(maximum));
  }

  private rememberAttempt(key: string): void {
    this.attempted.set(key, true);
    while (this.attempted.size > MAX_ATTEMPT_KEYS) this.attempted.delete(this.attempted.keys().next().value!);
  }

  private rememberSourceError(turnId: string, reason: string): void {
    this.sourceErrors.delete(turnId);
    this.sourceErrors.set(turnId, reason);
    while (this.sourceErrors.size > MAX_SOURCE_ERRORS) {
      this.sourceErrors.delete(this.sourceErrors.keys().next().value!);
    }
  }

  private rememberStagedSource(source: StagedSource): void {
    this.stagedSources.delete(source.turn_id);
    while (this.stagedSources.size >= MAX_STAGED_SOURCES) {
      const oldestTurnId = this.stagedSources.keys().next().value as string | undefined;
      if (!oldestTurnId) break;
      this.stagedSources.delete(oldestTurnId);
      this.buffer.confirm(oldestTurnId);
    }
    this.stagedSources.set(source.turn_id, source);
  }

  private attemptKey(payload: FinalPayload): string {
    return `${payload.session_id}\u001f${payload.epoch}\u001f${payload.turn_id}\u001f${payload.revision}\u001f${payload.source_digest}`;
  }

  private belongsToContext(payload: SemanticSpeechPayload, context: SemanticSpeechRuntimeContext): boolean {
    return payload.session_id === context.sessionId && payload.epoch === context.epoch
      && payload.consent_version === context.consentVersion && payload.contract_digest === context.contractDigest
      && [context.localPeerId, context.remotePeerId].includes(payload.sender_id)
      && [context.localPeerId, context.remotePeerId].includes(payload.audience_id);
  }

  private validateSettings(settings: SemanticSpeechSettings): SemanticSpeechSettings {
    if (!['live', 'segment'].includes(settings.displayMode)) throw new Error('semantic_speech_display_mode_invalid');
    if (![10, 30, 60, 90, 120].includes(settings.segmentDurationSeconds)) {
      throw new Error('semantic_speech_segment_duration_invalid');
    }
    return Object.freeze({
      displayMode: settings.displayMode,
      segmentDurationSeconds: settings.segmentDurationSeconds,
      correctEachSegment: Boolean(settings.correctEachSegment),
      paused: Boolean(settings.paused),
      ordinaryAudioOverride: Boolean(settings.ordinaryAudioOverride),
    });
  }

  private validateContext(context: SemanticSpeechRuntimeContext): void {
    if (!/^https?:\/\//.test(context.hubUrl) || !context.sessionId || context.epoch < 1) {
      throw new Error('semantic_speech_runtime_context_invalid');
    }
  }

  private contextKey(context: SemanticSpeechRuntimeContext): string {
    const consent = context.correctionConsent;
    return [
      context.sessionId, context.epoch, context.localPeerId, context.remotePeerId,
      context.consentVersion, context.contractDigest, consent?.consentId ?? '',
      consent?.consentVersion ?? 0, consent?.revocationEpoch ?? 0, consent?.consentDigest ?? '',
    ].join('\u001f');
  }

  private requireContext(): SemanticSpeechRuntimeContext {
    if (!this.context) throw new Error('semantic_speech_runtime_not_started');
    return this.context;
  }

  private fromBase64(value: string): Uint8Array {
    const decoded = globalThis.atob(value);
    return Uint8Array.from(decoded, character => character.charCodeAt(0));
  }

  private reasonCode(error: unknown, fallback: string): string {
    const nested = (error as { error?: { error?: { code?: unknown }; code?: unknown } } | null)?.error;
    const candidate = error instanceof Error ? error.message : nested?.error?.code ?? nested?.code;
    return typeof candidate === 'string' && /^[a-z][a-z0-9_]{2,119}$/.test(candidate) ? candidate : fallback;
  }

  private httpStatus(error: unknown): number {
    const status = Number((error as { status?: unknown } | null)?.status);
    return Number.isSafeInteger(status) ? status : 0;
  }
}
