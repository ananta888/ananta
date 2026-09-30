import {
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  OnDestroy,
  OnInit,
  inject,
} from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { firstValueFrom, forkJoin } from 'rxjs';

import { AgentDirectoryService } from '../../services/agent-directory.service';
import {
  VOICE_BATCH_RECORDING,
  VoiceBatchRecordingPort,
  VoiceCaptureSource,
} from './voice-audio-capture';
import { VoiceApiService } from './voice-api.service';
import { VoiceCandidateReviewComponent } from './voice-candidate-review.component';
import { VoiceLiveSessionController, voicePartialText } from './voice-live-session.controller';
import {
  DEFAULT_VOICE_LONG_RUN_DISPLAY_MODE,
  VoiceLongRunDisplayMode,
  normalizeVoiceLongRunDisplayMode,
} from './voice-long-run-display-mode';
import { VoiceLongRunRecoveryMetadata } from './voice-long-run-recovery';
import {
  VoiceLongRunController,
  VoiceLongRunObserver,
} from './voice-long-run.controller';
import { VoiceLongRunTimelineSegment } from './voice-long-run-timeline';
import {
  VoiceLongRunTimelineRow,
  VoiceLongRunUploadLedger,
  applyVoiceLongRunResponse,
  buildVoiceLongRunTimelineRows,
  createVoiceConsoleLongRunObserver,
  formatVoiceLongRunDuration,
  validLongRunMaxHours,
  validLongRunSegmentSeconds,
} from './voice-console-long-run';
import {
  VoiceConfigurationTarget,
  effectiveVoiceSelection,
  voiceAsrBackends,
  voiceConfigurationDelta,
  voiceCorrectorModels,
  voiceFieldChoices,
  voiceScopeDelta,
  voiceScopeVersion,
} from './voice-console-configuration';
import {
  VoiceCapabilityStatus,
  VoiceConfiguration,
  VoiceConfigurationSchema,
  VoiceStreamEvent,
  VoiceStreamFinalizeResponse,
  VoiceLongRunResponse,
  VoiceTranscriptionResult,
} from './voice.models';
import {
  buildCorrectorProviders,
  correctionDefaultLabel as describeCorrectionDefault,
  correctorProviderSupportsManual as providerSupportsManual,
  validCorrectorModelId,
  VoiceChoice,
} from './voice-corrector-catalog';
import { VoiceConsoleBatchCapture } from './voice-console-batch-capture';
import { VoiceRuntimeStatusComponent } from './voice-runtime-status.component';
import { SemanticMediaProgramHostComponent } from './semantic-media-program-host.component';
import { VoiceTranscriptionResultComponent } from './voice-transcription-result.component';
import { voiceError, voiceMutationKey } from './voice-ui.helpers';
import { ShareSessionService } from '../../services/share-session.service';

type VoiceConsoleTab = 'live' | 'long' | 'batch';

@Component({
  selector: 'app-voice-console',
  standalone: true,
  imports: [
    FormsModule,
    RouterLink,
    VoiceRuntimeStatusComponent,
    VoiceCandidateReviewComponent,
    VoiceTranscriptionResultComponent,
    SemanticMediaProgramHostComponent,
  ],
  providers: [VoiceLiveSessionController, VoiceLongRunController],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './voice-console.component.html',
  styleUrl: './voice-console.component.css',
})
export class VoiceConsoleComponent implements OnInit, OnDestroy {
  private readonly directory = inject(AgentDirectoryService);
  private readonly api = inject(VoiceApiService);
  private readonly liveSession = inject(VoiceLiveSessionController);
  private readonly longRun = inject(VoiceLongRunController);
  private readonly batchRecorder: VoiceBatchRecordingPort = inject(VOICE_BATCH_RECORDING);
  private readonly cdr = inject(ChangeDetectorRef);
  private readonly shares = inject(ShareSessionService);
  private readonly publicPairRuntimeState = toSignal(this.shares.publicPairRuntimeState$, {
    initialValue: this.shares.publicPairRuntimeState$.value,
  });
  private destroyed = false;
  private liveOperationGeneration = 0;
  private longRunOperationGeneration = 0;
  private readonly batchCapture = new VoiceConsoleBatchCapture(this.batchRecorder, {
    view: this,
    destroyed: () => this.destroyed,
    markForCheck: () => this.cdr.markForCheck(),
    clearMessages: () => this.clearMessages(),
    fail: (error, cleanup) => this.fail(error, cleanup),
  });
  private readonly longRunLedger = new VoiceLongRunUploadLedger();

  hubUrl = '';
  activeTab: VoiceConsoleTab = 'live';
  language = 'de';
  profileId = 'default';
  sessionId = '';
  configurationTarget: VoiceConfigurationTarget = 'profile';

  capabilities: VoiceCapabilityStatus | null = null;
  schema: VoiceConfigurationSchema | null = null;
  configuration: VoiceConfiguration | null = null;
  loadingConfiguration = false;
  savingConfiguration = false;
  selectedRecognitionStrategy = 'single';
  selectedBackend = 'vosk';
  selectedSecondaryBackend = 'whisper_cpp';
  generativeCorrection = true;
  selectedCorrectorProvider = 'embedded';
  selectedCorrectorModel = '';
  manualCorrectorModel = false;
  manualCorrectorModelId = '';
  selectedCaptureSource: VoiceCaptureSource = 'microphone';

  liveActive = false;
  liveBusy = false;
  livePartial = '';
  liveChunkCount = 0;
  liveSessionId = '';
  liveResult: VoiceTranscriptionResult | null = null;

  longRunActive = false;
  longRunBusy = false;
  longRunId = '';
  longRunStatus = 'bereit';
  longRunSegmentSeconds = 120;
  longRunMaxHours = 8;
  longRunDisplayMode: VoiceLongRunDisplayMode = DEFAULT_VOICE_LONG_RUN_DISPLAY_MODE;
  longRunPreviewSequence = -1;
  longRunPreviewText = '';
  longRunPreviewStatus: 'idle' | 'connecting' | 'live' | 'unavailable' = 'idle';
  longRunCapturedMilliseconds = 0;
  longRunUploadedSegments = 0;
  longRunQueuedSegments = 0;
  longRunGapSequences: number[] = [];
  longRunTranscript = '';
  longRunTimeline: readonly VoiceLongRunTimelineSegment[] = [];
  longRunTimelineRows: readonly VoiceLongRunTimelineRow[] = [];
  longRunProvisionalSegments = 0;
  longRunCorrectedSegments = 0;
  longRunConnection: 'online' | 'retrying' = 'online';
  longRunRecovery: VoiceLongRunRecoveryMetadata | null = null;
  longRunStorageAvailable = true;
  longRunWarning = '';

  batchRecording = false;
  batchBusy = false;
  batchAudio: Blob | File | null = null;
  batchFileName = '';
  batchResult: VoiceTranscriptionResult | null = null;

  errorCode = '';
  errorMessage = '';
  successMessage = '';

  /** A Public Pair has one app-scoped media owner in AI Snake; never mount a competing owner here. */
  standaloneSemanticMediaAvailable(): boolean {
    this.publicPairRuntimeState();
    return !this.shares.hasPublicPairRuntime;
  }

  ngOnInit(): void {
    void this.refreshCaptureCapabilities();
    void this.initializeLongRun();
    this.longRunRecovery = this.longRun.recoveryMetadata();
    this.restoreLongRunDisplayMode(this.longRunRecovery);
    const hub = this.directory.list().find((entry) => entry.role === 'hub')
      || this.directory.list().find((entry) => entry.name === 'hub');
    this.hubUrl = String(hub?.url || '').replace(/\/$/, '');
    if (!this.hubUrl) {
      this.errorCode = 'voice.ui.hub_missing';
      this.errorMessage = 'In der Agenten-Konfiguration ist kein Hub eingetragen.';
      return;
    }
    this.loadVoiceContext();
  }

  ngOnDestroy(): void {
    this.destroyed = true;
    this.liveOperationGeneration += 1;
    this.batchCapture.invalidate();
    this.longRunOperationGeneration += 1;
    void this.liveSession.cancel().catch(() => undefined);
    void this.longRun.dispose().catch(() => undefined);
    void Promise.resolve(this.batchRecorder.cancel()).catch(() => undefined);
  }

  setTab(tab: VoiceConsoleTab): void {
    if (this.configurationInteractionLocked()) return;
    this.activeTab = tab;
    this.clearMessages();
  }

  loadVoiceContext(): void {
    if (!this.hubUrl || !this.profileId.trim()) return;
    this.loadingConfiguration = true;
    this.clearMessages();
    forkJoin({
      capabilities: this.api.getCapabilities(this.hubUrl),
      schema: this.api.getConfigurationSchema(this.hubUrl),
      configuration: this.api.getConfiguration(this.hubUrl, {
        profileId: this.profileId.trim(),
        sessionId: this.sessionId.trim() || undefined,
      }),
    }).subscribe({
      next: ({ capabilities, schema, configuration }) => {
        this.capabilities = capabilities;
        this.schema = schema;
        this.configuration = configuration;
        this.applyEffectiveConfiguration(configuration);
        this.loadingConfiguration = false;
        this.cdr.markForCheck();
      },
      error: (error) => this.fail(error, () => { this.loadingConfiguration = false; }),
    });
  }

  async saveConfiguration(): Promise<void> {
    if (this.configurationInteractionLocked()) return;
    if (!this.validConfigurationContext() || !this.validBackendSelection() || !this.validCorrectionSelection()) return;
    this.savingConfiguration = true;
    this.clearMessages();
    try {
      await this.persistSelectedConfiguration(this.activeTab === 'live' ? 'streaming' : 'batch');
      this.successMessage = `${this.configurationTarget === 'session' ? 'Session' : 'Profil'}-Konfiguration gespeichert.`;
      this.savingConfiguration = false;
      this.cdr.markForCheck();
    } catch (error) {
      this.fail(error, () => { this.savingConfiguration = false; });
    }
  }

  async startLive(): Promise<void> {
    if (this.configurationInteractionLocked()) return;
    if (!this.validConfigurationContext() || !this.validBackendSelection()
      || !this.validCorrectionSelection() || !this.captureSourceSupported('live')) return;
    const generation = ++this.liveOperationGeneration;
    const captureSource = this.selectedCaptureSource;
    this.liveBusy = true;
    this.livePartial = '';
    this.liveResult = null;
    this.liveChunkCount = 0;
    this.clearMessages();
    try {
      // getDisplayMedia/MediaProjection consent must be requested directly
      // from this user gesture, before configuration or Hub network awaits.
      await this.liveSession.prepareCapture(captureSource);
      this.ensureLiveOperation(generation);
      await this.persistSelectedConfiguration('streaming', true);
      this.ensureLiveOperation(generation);
      const stream = await this.liveSession.start(this.hubUrl, {
        filename: 'angular-live.pcm',
        language: this.language.trim() || undefined,
        profile_id: this.profileId.trim(),
        configuration_session_id: this.sessionId.trim() || undefined,
        // Total stream lifetime includes capture plus Hub finalization/correction.
        deadline_seconds: 300,
        max_audio_seconds: 120,
      }, voiceMutationKey('console-stream:create'), {
        event: (event, currentStream) => this.onStreamEvent(event, currentStream.session_id),
        chunkAccepted: () => {
          this.liveChunkCount += 1;
          this.cdr.markForCheck();
        },
        finalizing: (reason) => this.onLiveCaptureFinalizing(reason),
        finalized: (response, reason) => this.onLiveCaptureFinalized(response, reason),
        error: (error) => {
          if (this.destroyed) return;
          this.liveActive = false;
          this.liveBusy = false;
          this.fail(error);
        },
      }, captureSource);
      this.ensureLiveOperation(generation);
      this.liveSessionId = stream.session_id;
      this.liveActive = true;
      this.liveBusy = false;
      this.successMessage = 'Live-Erkennung läuft über den Hub.';
      this.cdr.markForCheck();
    } catch (error) {
      await this.liveSession.cancel().catch(() => undefined);
      this.liveActive = false;
      if (this.isLiveOperationCurrent(generation)) {
        this.fail(error, () => { this.liveBusy = false; });
      }
    }
  }

  async stopLive(): Promise<void> {
    if (!this.liveActive) return;
    this.liveBusy = true;
    this.clearMessages();
    try {
      const response = await this.liveSession.finalize();
      this.applyLiveFinalization(
        response,
        'Live-Aufnahme finalisiert. Vosk-Original und optionale LLM-Korrektur sind unten sichtbar.',
      );
    } catch (error) {
      this.liveActive = false;
      this.fail(error, () => { this.liveBusy = false; });
    }
  }

  async cancelLive(): Promise<void> {
    this.liveOperationGeneration += 1;
    this.liveBusy = true;
    try {
      await this.liveSession.cancel();
      this.liveActive = false;
      this.liveBusy = false;
      this.livePartial = '';
      this.successMessage = 'Live-Session verworfen.';
      this.cdr.markForCheck();
    } catch (error) {
      this.fail(error, () => { this.liveBusy = false; });
    }
  }

  async startLongRun(resume = false): Promise<void> {
    if (this.configurationInteractionLocked()) return;
    const recovery = this.longRun.recoveryMetadata();
    const request = recovery && (!recovery.runId || resume)
      ? recovery.request
      : {
          source: this.selectedCaptureSource,
          profile_id: this.profileId.trim(),
          configuration_session_id: this.sessionId.trim() || undefined,
          language: this.language.trim() || undefined,
          segment_duration_seconds: validLongRunSegmentSeconds(this.longRunSegmentSeconds),
          max_duration_seconds: validLongRunMaxHours(this.longRunMaxHours) * 3_600,
          overlap_milliseconds: 1_000,
        };
    const displayMode = normalizeVoiceLongRunDisplayMode(
      recovery && (!recovery.runId || resume) ? recovery.displayMode : this.longRunDisplayMode,
    );
    this.longRunDisplayMode = displayMode;
    if (!this.validConfigurationContext() || !this.validBackendSelection()
      || !this.validCorrectionSelection() || !this.longRun.supportsSource(request.source)) return;
    const generation = ++this.longRunOperationGeneration;
    this.longRunBusy = true;
    this.longRunTranscript = resume ? this.longRunTranscript : '';
    this.longRunTimeline = resume ? this.longRunTimeline : [];
    this.longRunProvisionalSegments = resume ? this.longRunProvisionalSegments : 0;
    this.longRunCorrectedSegments = resume ? this.longRunCorrectedSegments : 0;
    this.longRunGapSequences = [];
    if (!resume) {
      this.longRunLedger.reset();
      this.longRunCapturedMilliseconds = 0;
      this.longRunUploadedSegments = 0;
      this.longRunQueuedSegments = 0;
      this.longRunConnection = 'online';
      this.longRunWarning = '';
      this.longRunPreviewSequence = -1;
      this.longRunPreviewText = '';
      this.longRunPreviewStatus = 'idle';
      this.longRunId = '';
      this.longRunStatus = 'bereit';
      this.longRunRecovery = null;
    } else if (recovery) {
      this.longRunCapturedMilliseconds = recovery.timelineMilliseconds;
      this.longRunRecovery = recovery;
    }
    this.rebuildLongRunTimelineRows();
    this.clearMessages();
    try {
      if (resume && !this.longRun.recoveryReadyForConsent()) {
        throw new Error('voice.long_run.recovery_check_required');
      }
      // The secure spool and recovery snapshot are prepared on page load. The
      // capture permission itself remains directly tied to this user action.
      await this.longRun.prepareCapture(request.source, request.profile_id);
      this.ensureLongRunOperation(generation);
      if (!resume) await this.persistSelectedConfiguration('streaming', true);
      this.ensureLongRunOperation(generation);
      const observer = this.longRunObserver();
      const run = resume
        ? await this.longRun.resumeCapture(observer)
        : await this.longRun.start(
            this.hubUrl,
            request,
            voiceMutationKey('console-long-run:create'),
            observer,
            displayMode,
          );
      this.ensureLongRunOperation(generation);
      this.longRunId = run.id;
      this.longRunStatus = run.status;
      this.longRunActive = true;
      this.longRunBusy = false;
      this.longRunRecovery = this.longRun.recoveryMetadata();
      this.selectedCaptureSource = request.source;
      this.successMessage = displayMode === 'live'
        ? 'Langzeit-Transkription läuft. Die flüchtige Rohtext-Vorschau erscheint fortlaufend; jedes Segment wird anschließend separat transkribiert und korrigiert.'
        : 'Langzeit-Transkription läuft. Die Audiofreigabe bleibt bis zum Stoppen geöffnet; Text erscheint segmentweise.';
      this.cdr.markForCheck();
    } catch (error) {
      if (!this.isLongRunOperationCurrent(generation)) return;
      this.longRunActive = false;
      this.longRunRecovery = this.longRun.recoveryMetadata();
      this.fail(error, () => { this.longRunBusy = false; });
    }
  }

  onLongRunDisplayModeChange(value: unknown): void {
    this.longRunDisplayMode = normalizeVoiceLongRunDisplayMode(value);
  }

  longRunRecoveryReady(): boolean {
    return this.longRun.recoveryReadyForConsent();
  }

  longRunRecoveryDrainOnly(): boolean {
    return this.longRun.recoveryDrainOnly();
  }

  longRunRecoveryFinalizing(): boolean {
    return this.longRun.recoveryFinalizing();
  }

  async checkLongRunRecovery(): Promise<void> {
    if (this.captureInteractionLocked()) return;
    this.longRunBusy = true;
    try {
      await this.longRun.inspectRecovery();
      this.longRunRecovery = this.longRun.recoveryMetadata();
      this.longRunBusy = false;
      this.successMessage = this.longRunRecoveryDrainOnly()
        ? 'Die Aufnahmefrist ist beendet. Verschlüsselt gepufferte Segmente können noch gesendet und der Run abgeschlossen werden.'
        : this.longRunRecoveryFinalizing()
          ? 'Der Hub schließt den Run gerade ab. Der verschlüsselte Puffer bleibt erhalten; bitte den Status erneut prüfen.'
        : this.longRunRecovery
          ? 'Der Hub-Run ist aktiv. Die Audiofreigabe kann jetzt erneut erteilt werden.'
          : 'Der frühere Langzeit-Run ist bereits beendet oder abgelaufen; sein lokaler Puffer wurde gelöscht.';
      this.cdr.markForCheck();
    } catch (error) {
      this.fail(error, () => { this.longRunBusy = false; });
    }
  }

  async drainLongRunRecovery(): Promise<void> {
    if (this.captureInteractionLocked()) return;
    this.longRunBusy = true;
    this.clearMessages();
    try {
      const response = await this.longRun.drainRecovery(this.longRunObserver());
      this.applyLongRunResponse(response);
      this.longRunActive = false;
      this.longRunBusy = false;
      this.longRunRecovery = this.longRun.recoveryMetadata();
      this.successMessage = this.longRunGapSequences.length
        ? 'Der lokale Puffer wurde gesendet und der Run mit markierten Lücken abgeschlossen.'
        : 'Der lokale Puffer wurde gesendet und der Langzeit-Run abgeschlossen.';
      this.cdr.markForCheck();
    } catch (error) {
      this.longRunRecovery = this.longRun.recoveryMetadata();
      this.fail(error, () => { this.longRunBusy = false; });
    }
  }

  async stopLongRun(): Promise<void> {
    if (!this.longRunActive || this.longRunBusy) return;
    this.longRunBusy = true;
    this.clearMessages();
    try {
      const response = await this.longRun.stop('user_stop');
      this.applyLongRunResponse(response);
      this.longRunActive = false;
      this.longRunBusy = false;
      this.longRunRecovery = null;
      this.successMessage = this.longRunGapSequences.length
        ? 'Langzeit-Run abgeschlossen. Nicht wiederherstellbare Segmentlücken sind markiert.'
        : 'Langzeit-Run vollständig abgeschlossen.';
      this.cdr.markForCheck();
    } catch (error) {
      this.fail(error, () => { this.longRunBusy = false; });
    }
  }

  async discardLongRunRecovery(): Promise<void> {
    if (this.captureInteractionLocked()) return;
    const hadHubRun = Boolean(this.longRunRecovery?.runId);
    this.longRunBusy = true;
    this.clearMessages();
    try {
      const hubConfirmedEnded = await this.longRun.discardRecovery();
      this.longRunRecovery = null;
      this.longRunBusy = false;
      if (hadHubRun && !hubConfirmedEnded) {
        this.successMessage = 'Der lokale verschlüsselte Puffer wurde gelöscht.';
        this.longRunWarning = 'Der Hub war nicht erreichbar oder finalisiert noch; sein Run-Status konnte nicht bestätigt werden.';
      } else {
        this.successMessage = hadHubRun
          ? 'Der Hub-Run wurde beendet und sein lokaler verschlüsselter Puffer gelöscht.'
          : 'Der unterbrochene Startversuch und sein lokaler verschlüsselter Puffer wurden gelöscht.';
      }
      this.cdr.markForCheck();
    } catch (error) {
      this.fail(error, () => { this.longRunBusy = false; });
    }
  }

  async startBatchRecording(): Promise<void> {
    if (this.configurationInteractionLocked()) return;
    if (!this.captureSourceSupported('batch')) return;
    await this.batchCapture.start(this.selectedCaptureSource);
  }

  stopBatchRecording(): Promise<void> {
    return this.batchCapture.stop();
  }

  selectAudioFile(event: Event): void {
    const file = (event.target as HTMLInputElement).files?.[0] || null;
    this.batchAudio = file;
    this.batchFileName = file?.name || '';
    this.batchResult = null;
    this.clearMessages();
  }

  async transcribeBatch(): Promise<void> {
    if (!this.batchAudio || !this.validConfigurationContext() || !this.validBackendSelection() || !this.validCorrectionSelection()) return;
    this.batchBusy = true;
    this.batchResult = null;
    this.clearMessages();
    try {
      await this.persistSelectedConfiguration('batch', true);
      this.batchResult = await firstValueFrom(this.api.transcribe(this.hubUrl, {
        file: this.batchAudio,
        fileName: this.batchFileName || 'voice-recording.webm',
        language: this.language.trim() || undefined,
        profileId: this.profileId.trim(),
        sessionId: this.sessionId.trim() || undefined,
        idempotencyKey: voiceMutationKey('console-transcribe'),
      }));
      this.batchBusy = false;
      this.successMessage = 'Aufnahme über den Hub transkribiert.';
      this.cdr.markForCheck();
    } catch (error) {
      this.fail(error, () => { this.batchBusy = false; });
    }
  }

  clearBatchAudio(): void {
    this.batchAudio = null;
    this.batchFileName = '';
    this.batchResult = null;
  }

  captureSourceSupported(mode: VoiceConsoleTab = this.activeTab): boolean {
    return this.captureSourceOptionSupported(this.selectedCaptureSource, mode);
  }

  captureSourceOptionSupported(
    source: VoiceCaptureSource,
    mode: VoiceConsoleTab = this.activeTab,
  ): boolean {
    if (mode === 'live') return this.liveSession.supportsSource(source);
    if (mode === 'long') return this.longRun.supportsSource(source);
    return this.batchRecorder.supportsSource(source);
  }

  captureSourceReason(): string {
    if (this.captureSourceSupported()) return '';
    return this.selectedCaptureSource === 'system_audio'
      ? 'Systemaudio wird auf dieser Plattform nicht unterstützt.'
      : 'Mikrofonaufnahme wird auf dieser Plattform nicht unterstützt.';
  }

  captureInteractionLocked(): boolean {
    return this.liveActive || this.liveBusy || this.longRunActive || this.longRunBusy
      || this.batchRecording || this.batchBusy;
  }

  configurationInteractionLocked(): boolean {
    return this.captureInteractionLocked() || this.loadingConfiguration || this.savingConfiguration;
  }

  recognitionStrategies(): VoiceChoice[] {
    return this.fieldChoices('recognition_strategy', ['single', 'classic_then_correct', 'parallel_compare']);
  }

  asrBackends(): VoiceChoice[] {
    return voiceAsrBackends(this.schema, this.capabilities);
  }

  requiresSecondaryBackend(): boolean {
    return ['classic_then_correct', 'parallel_compare'].includes(this.selectedRecognitionStrategy);
  }

  longRunUsesVoskOnlyPath(): boolean {
    return this.selectedBackend === 'vosk' && !this.requiresSecondaryBackend();
  }

  validBackendSelection(): boolean {
    const choices = this.asrBackends();
    const primaryAvailable = choices.some((choice) => (
      choice.id === this.selectedBackend && choice.available
    ));
    if (!primaryAvailable) return false;
    if (!this.requiresSecondaryBackend()) return true;
    return this.selectedSecondaryBackend !== this.selectedBackend
      && choices.some((choice) => choice.id === this.selectedSecondaryBackend && choice.available);
  }

  backendReason(backendId: string): string {
    const choice = this.asrBackends().find((candidate) => candidate.id === backendId);
    return choice && !choice.available ? choice.reason : '';
  }

  correctorProviderReason(providerId: string): string {
    const choice = this.correctorProviders().find((candidate) => candidate.id === providerId);
    return choice && !choice.available ? choice.reason : '';
  }

  correctorReason(modelId: string): string {
    const choice = this.correctorModels(this.selectedCorrectorProvider).find((candidate) => candidate.id === modelId);
    return choice && !choice.available ? choice.reason : '';
  }

  correctorProviders(): VoiceChoice[] {
    return buildCorrectorProviders(this.capabilities, this.configuration);
  }

  correctorModels(providerId = this.selectedCorrectorProvider): VoiceChoice[] {
    return voiceCorrectorModels(this.capabilities, this.configuration, this.schema, providerId);
  }

  onCorrectorProviderChange(providerId: string): void {
    this.selectedCorrectorProvider = String(providerId || '').trim().toLowerCase();
    this.manualCorrectorModel = false;
    this.manualCorrectorModelId = '';
    this.selectedCorrectorModel = this.selectedCorrectorProvider === 'inherit'
      ? ''
      : this.correctorModels(this.selectedCorrectorProvider).find((choice) => choice.available)?.id || '';
  }

  setManualCorrectorModel(enabled: boolean): void {
    this.manualCorrectorModel = enabled;
    if (enabled) {
      this.manualCorrectorModelId = this.manualCorrectorModelId || this.selectedCorrectorModel;
      return;
    }
    this.selectedCorrectorModel = this.correctorModels(this.selectedCorrectorProvider)
      .find((choice) => choice.available)?.id || '';
  }

  correctorProviderSupportsManual(providerId = this.selectedCorrectorProvider): boolean {
    return providerSupportsManual(this.capabilities, providerId);
  }

  correctionDefaultLabel(): string {
    return describeCorrectionDefault(this.capabilities);
  }

  validCorrectionSelection(): boolean {
    if (!this.generativeCorrection) return true;
    const provider = this.correctorProviders().find((choice) => (
      choice.id === this.selectedCorrectorProvider
    ));
    if (!provider) return false;
    if (this.selectedCorrectorProvider === 'inherit') return provider.available;
    if (this.manualCorrectorModel) {
      return this.correctorProviderSupportsManual()
        && validCorrectorModelId(this.manualCorrectorModelId, this.selectedCorrectorProvider);
    }
    if (!provider.available) return false;
    return Boolean(this.correctorModels(this.selectedCorrectorProvider).find((choice) => (
      choice.id === this.selectedCorrectorModel.trim() && choice.available
    )));
  }

  resultForActiveTab(): VoiceTranscriptionResult | null {
    return this.activeTab === 'live' ? this.liveResult
      : this.activeTab === 'batch' ? this.batchResult
      : null;
  }

  private applyEffectiveConfiguration(configuration: VoiceConfiguration): void {
    Object.assign(this, effectiveVoiceSelection(configuration, this.capabilities, this.schema));
  }

  private async persistSelectedConfiguration(
    transportMode: 'batch' | 'streaming',
    operationScope = false,
  ): Promise<void> {
    const scope: VoiceConfigurationTarget = operationScope && this.sessionId.trim()
      ? 'session'
      : this.configurationTarget;
    const scopeId = scope === 'session' ? this.sessionId.trim() : this.profileId.trim();
    const delta = voiceConfigurationDelta(
      voiceScopeDelta(this.configuration, scope, scopeId),
      transportMode,
      this,
      this.requiresSecondaryBackend(),
    );
    await firstValueFrom(this.api.saveConfiguration(this.hubUrl, {
      scope,
      scope_id: scopeId,
      delta,
      expected_version: voiceScopeVersion(this.configuration, scope, scopeId),
    }, voiceMutationKey(`console-configuration:${scope}`)));
    const refreshed = await firstValueFrom(this.api.getConfiguration(this.hubUrl, {
      profileId: this.profileId.trim(),
      sessionId: this.sessionId.trim() || undefined,
    }));
    this.configuration = refreshed;
    this.applyEffectiveConfiguration(refreshed);
  }

  private fieldChoices(key: string, fallback: string[]): VoiceChoice[] {
    return voiceFieldChoices(this.schema, key, fallback);
  }

  private onLiveCaptureFinalized(response: VoiceStreamFinalizeResponse, reason?: string): void {
    if (this.destroyed) return;
    const message = reason === 'safety_limit'
      ? 'Die maximale Aufnahmedauer wurde erreicht. Das Live-Transkript wurde automatisch finalisiert.'
      : 'Die Audioquelle wurde beendet. Das bisherige Live-Transkript wurde automatisch finalisiert.';
    this.applyLiveFinalization(response, message);
  }

  private onLiveCaptureFinalizing(reason?: string): void {
    if (this.destroyed) return;
    this.liveBusy = true;
    this.successMessage = reason === 'safety_limit'
      ? 'Die maximale Aufnahmedauer wurde erreicht. Das Live-Transkript wird finalisiert …'
      : 'Die Audioquelle wurde beendet. Das Live-Transkript wird finalisiert …';
    this.cdr.markForCheck();
  }

  private applyLiveFinalization(response: VoiceStreamFinalizeResponse, message: string): void {
    this.liveResult = { ...response.result, result_ref: response.result.result_ref || response.result_ref };
    this.livePartial = response.result.text || this.livePartial;
    this.liveActive = false;
    this.liveBusy = false;
    this.successMessage = message;
    this.cdr.markForCheck();
  }

  private ensureLiveOperation(generation: number): void {
    if (!this.isLiveOperationCurrent(generation)) throw new Error('voice.capture.cancelled');
  }

  private isLiveOperationCurrent(generation: number): boolean {
    return !this.destroyed && generation === this.liveOperationGeneration;
  }

  private async refreshCaptureCapabilities(): Promise<void> {
    await Promise.allSettled([
      this.liveSession.refreshCaptureCapabilities(),
      this.longRun.refreshCaptureCapabilities(),
      this.batchRecorder.refreshCapabilities?.() || Promise.resolve(),
    ]);
    if (!this.destroyed) this.cdr.markForCheck();
  }

  private async initializeLongRun(): Promise<void> {
    try {
      await this.longRun.initializeSecureStorage();
    } catch (error) {
      if (this.destroyed) return;
      this.longRunStorageAvailable = false;
      const detail = voiceError(error);
      this.longRunWarning = detail.message;
      this.cdr.markForCheck();
      return;
    }
    this.longRunStorageAvailable = true;
    const recovery = this.longRun.recoveryMetadata();
    if (recovery?.runId) {
      try {
        await this.longRun.inspectRecovery();
      } catch {
        // The encrypted local spool is healthy. Keep recovery discoverable and
        // let the user retry the independent Hub status check.
        this.longRunWarning = 'Der verschlüsselte Puffer ist verfügbar, aber der Hub-Status konnte noch nicht geprüft werden.';
      }
    }
    if (!this.destroyed) {
      this.longRunRecovery = this.longRun.recoveryMetadata();
      this.restoreLongRunDisplayMode(this.longRunRecovery);
      this.cdr.markForCheck();
    }
  }

  private longRunObserver(): VoiceLongRunObserver {
    return createVoiceConsoleLongRunObserver(this, this.longRunLedger, {
      destroyed: () => this.destroyed,
      changed: () => this.cdr.markForCheck(),
      applyResponse: (response) => this.applyLongRunResponse(response),
      rebuildRows: () => this.rebuildLongRunTimelineRows(),
      restoreDisplayMode: (metadata) => this.restoreLongRunDisplayMode(metadata),
    });
  }

  private applyLongRunResponse(response: VoiceLongRunResponse): void {
    applyVoiceLongRunResponse(this, this.longRunLedger, response);
    this.cdr.markForCheck();
  }

  formatLongRunGapSequences(): string {
    return this.longRunGapSequences.map((sequence) => sequence + 1).join(', ');
  }

  private restoreLongRunDisplayMode(metadata: VoiceLongRunRecoveryMetadata | null): void {
    if (metadata) {
      this.longRunDisplayMode = normalizeVoiceLongRunDisplayMode(metadata.displayMode);
    }
  }

  private ensureLongRunOperation(generation: number): void {
    if (!this.isLongRunOperationCurrent(generation)) throw new Error('voice.capture.cancelled');
  }

  private isLongRunOperationCurrent(generation: number): boolean {
    return !this.destroyed && generation === this.longRunOperationGeneration;
  }

  formatLongRunDuration(milliseconds: number): string {
    return formatVoiceLongRunDuration(milliseconds);
  }

  private rebuildLongRunTimelineRows(): void {
    this.longRunTimelineRows = buildVoiceLongRunTimelineRows(this.longRunTimeline, this.longRunGapSequences);
  }

  private onStreamEvent(event: VoiceStreamEvent | null | undefined, sessionId: string): void {
    if (this.destroyed) return;
    const partial = voicePartialText(event);
    if (partial) this.livePartial = partial;
    this.liveSessionId = sessionId;
    this.cdr.markForCheck();
  }

  private validOperationContext(): boolean {
    if (!this.hubUrl || !this.profileId.trim()) {
      this.errorCode = 'voice.ui.profile_required';
      this.errorMessage = 'Hub und Profil-ID sind erforderlich.';
      return false;
    }
    return true;
  }

  private validConfigurationContext(): boolean {
    if (!this.validOperationContext()) return false;
    if (this.configurationTarget === 'session' && !this.sessionId.trim()) {
      this.errorCode = 'voice.ui.session_required';
      this.errorMessage = 'Für eine Session-Konfiguration ist eine Session-ID erforderlich.';
      return false;
    }
    return true;
  }

  private clearMessages(): void {
    this.errorCode = '';
    this.errorMessage = '';
    this.successMessage = '';
  }

  private fail(error: unknown, cleanup?: () => void): void {
    if (this.destroyed) return;
    const detail = voiceError(error);
    cleanup?.();
    this.errorCode = detail.code;
    this.errorMessage = detail.message;
    this.cdr.markForCheck();
  }
}

