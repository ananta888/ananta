import {
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  OnDestroy,
  OnInit,
  inject,
} from '@angular/core';
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
  VoiceCapabilityStatus,
  VoiceConfiguration,
  VoiceConfigurationSchema,
  VoiceStreamEvent,
  VoiceStreamFinalizeResponse,
  VoiceTranscriptionResult,
} from './voice.models';
import {
  buildCorrectorModels,
  buildCorrectorProviders,
  correctionDefaultLabel as describeCorrectionDefault,
  correctorProviderSupportsManual as providerSupportsManual,
  isReportedCorrectorModel,
  isVoiceCorrectionModel,
  validCorrectorModelId,
  VoiceChoice,
} from './voice-corrector-catalog';
import { VoiceRuntimeStatusComponent } from './voice-runtime-status.component';
import { VoiceTranscriptionResultComponent } from './voice-transcription-result.component';
import { configurationFields, valueAtPath, voiceError, voiceMutationKey } from './voice-ui.helpers';

type VoiceConsoleTab = 'live' | 'batch';
type VoiceConfigurationTarget = 'profile' | 'session';

@Component({
  selector: 'app-voice-console',
  standalone: true,
  imports: [
    FormsModule,
    RouterLink,
    VoiceRuntimeStatusComponent,
    VoiceCandidateReviewComponent,
    VoiceTranscriptionResultComponent,
  ],
  providers: [VoiceLiveSessionController],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './voice-console.component.html',
  styleUrl: './voice-console.component.css',
})
export class VoiceConsoleComponent implements OnInit, OnDestroy {
  private readonly directory = inject(AgentDirectoryService);
  private readonly api = inject(VoiceApiService);
  private readonly liveSession = inject(VoiceLiveSessionController);
  private readonly batchRecorder: VoiceBatchRecordingPort = inject(VOICE_BATCH_RECORDING);
  private readonly cdr = inject(ChangeDetectorRef);
  private destroyed = false;
  private liveOperationGeneration = 0;
  private batchOperationGeneration = 0;
  private batchOperation: { generation: number; ending: boolean } | null = null;

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

  batchRecording = false;
  batchBusy = false;
  batchAudio: Blob | File | null = null;
  batchFileName = '';
  batchResult: VoiceTranscriptionResult | null = null;

  errorCode = '';
  errorMessage = '';
  successMessage = '';

  ngOnInit(): void {
    void this.refreshCaptureCapabilities();
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
    this.batchOperationGeneration += 1;
    void this.liveSession.cancel().catch(() => undefined);
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

  async startBatchRecording(): Promise<void> {
    if (this.configurationInteractionLocked()) return;
    if (!this.captureSourceSupported('batch')) return;
    const generation = ++this.batchOperationGeneration;
    const operation = { generation, ending: false };
    const captureSource = this.selectedCaptureSource;
    this.batchOperation = operation;
    this.batchBusy = true;
    this.batchResult = null;
    this.batchAudio = null;
    this.batchFileName = '';
    this.clearMessages();
    try {
      await this.batchRecorder.start(captureSource, {
        ended: (reason) => this.onBatchCaptureEnded(generation, reason),
        error: (error) => this.onBatchCaptureError(generation, error),
      });
      this.ensureBatchOperation(generation);
      if (operation.ending) return;
      this.batchRecording = true;
      this.batchBusy = false;
      this.successMessage = captureSource === 'system_audio'
        ? 'Systemaudio-Aufnahme läuft lokal. Erst mit „Über Hub transkribieren“ wird Audio an den Hub gesendet.'
        : 'Mikrofon-Aufnahme läuft lokal. Erst mit „Über Hub transkribieren“ wird Audio an den Hub gesendet.';
      this.cdr.markForCheck();
    } catch (error) {
      if (this.isBatchOperationCurrent(generation) && !operation.ending) {
        this.batchOperation = null;
        this.fail(error, () => { this.batchBusy = false; });
      }
    }
  }

  async stopBatchRecording(): Promise<void> {
    if (!this.batchRecording) return;
    const operation = this.batchOperation;
    if (!operation || operation.ending) return;
    operation.ending = true;
    await this.finishBatchRecording(operation.generation, false);
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
    return mode === 'live'
      ? this.liveSession.supportsSource(source)
      : this.batchRecorder.supportsSource(source);
  }

  captureSourceReason(): string {
    if (this.captureSourceSupported()) return '';
    return this.selectedCaptureSource === 'system_audio'
      ? 'Systemaudio wird auf dieser Plattform nicht unterstützt.'
      : 'Mikrofonaufnahme wird auf dieser Plattform nicht unterstützt.';
  }

  captureInteractionLocked(): boolean {
    return this.liveActive || this.liveBusy || this.batchRecording || this.batchBusy;
  }

  configurationInteractionLocked(): boolean {
    return this.captureInteractionLocked() || this.loadingConfiguration || this.savingConfiguration;
  }

  recognitionStrategies(): VoiceChoice[] {
    return this.fieldChoices('recognition_strategy', ['single', 'classic_then_correct', 'parallel_compare']);
  }

  asrBackends(): VoiceChoice[] {
    const schemaChoices = this.fieldChoices('primary_backend', []);
    const runtimeModels = [
      ...(this.capabilities?.models || []),
      ...(this.capabilities?.model_catalog || []),
    ].filter((model) => !isVoiceCorrectionModel(model));
    const runtimeChoices = runtimeModels.map((model) => ({
      id: String(model.backend || model.engine || model.id),
      label: String(model.backend || model.engine || model.id),
      available: modelIsAvailable(model),
      reason: String(model.reason_code || (modelIsAvailable(model) ? '' : model.status || 'voice.backend.unavailable')),
    }));
    const ids = new Set([
      ...schemaChoices.map((choice) => choice.id),
      ...runtimeChoices.map((choice) => choice.id),
    ]);
    return [...ids].map((id) => {
      const schema = schemaChoices.find((choice) => choice.id === id);
      const matching = runtimeChoices.filter((choice) => choice.id === id);
      const ready = matching.find((choice) => choice.available);
      const unavailable = matching.find((choice) => !choice.available);
      return {
        id,
        label: asrBackendLabel(id, schema?.label || ready?.label || unavailable?.label),
        available: schema?.available !== false && Boolean(ready),
        reason: schema?.reason || ready?.reason || unavailable?.reason || 'voice.backend.not_reported',
      };
    });
  }

  requiresSecondaryBackend(): boolean {
    return ['classic_then_correct', 'parallel_compare'].includes(this.selectedRecognitionStrategy);
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
    return buildCorrectorModels(
      this.capabilities,
      this.configuration,
      this.fieldChoices('generative_corrector_model', []),
      providerId,
    );
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
    return this.activeTab === 'live' ? this.liveResult : this.batchResult;
  }

  private applyEffectiveConfiguration(configuration: VoiceConfiguration): void {
    const effective = configuration.effective || {};
    this.selectedRecognitionStrategy = String(valueAtPath(effective, 'recognition_strategy') || 'single');
    this.selectedBackend = String(valueAtPath(effective, 'primary_backend') || 'vosk');
    const secondary = valueAtPath(effective, 'secondary_backends');
    this.selectedSecondaryBackend = Array.isArray(secondary) ? String(secondary[0] || '') : 'whisper_cpp';
    this.generativeCorrection = String(valueAtPath(effective, 'correction_policy') || '') === 'generative_rewrite'
      || valueAtPath(effective, 'feature_flags.generative_corrector') === true;
    this.selectedCorrectorProvider = String(
      valueAtPath(effective, 'generative_corrector_provider') || 'embedded',
    ).trim().toLowerCase() || 'embedded';
    this.selectedCorrectorModel = String(valueAtPath(effective, 'generative_corrector_model') || '');
    this.manualCorrectorModel = false;
    this.manualCorrectorModelId = '';
    if (this.selectedCorrectorProvider === 'inherit') {
      this.selectedCorrectorModel = '';
    } else if (
      this.selectedCorrectorModel
      && !isReportedCorrectorModel(
        this.capabilities,
        this.selectedCorrectorProvider,
        this.selectedCorrectorModel,
      )
      && this.correctorProviderSupportsManual(this.selectedCorrectorProvider)
    ) {
      this.manualCorrectorModel = true;
      this.manualCorrectorModelId = this.selectedCorrectorModel;
    } else if (!this.selectedCorrectorModel) {
      this.selectedCorrectorModel = this.correctorModels(this.selectedCorrectorProvider)
        .find((choice) => choice.available)?.id || '';
    }
  }

  private async persistSelectedConfiguration(
    transportMode: 'batch' | 'streaming',
    operationScope = false,
  ): Promise<void> {
    const scope: VoiceConfigurationTarget = operationScope && this.sessionId.trim()
      ? 'session'
      : this.configurationTarget;
    const scopeId = scope === 'session' ? this.sessionId.trim() : this.profileId.trim();
    const existingDelta = this.scopeDelta(scope, scopeId);
    const existingFlags = valueAtPath(existingDelta, 'feature_flags');
    const delta: Record<string, unknown> = {
      ...existingDelta,
      transport_mode: transportMode,
      recognition_strategy: this.selectedRecognitionStrategy,
      primary_backend: this.selectedBackend,
      secondary_backends: this.requiresSecondaryBackend() && this.selectedSecondaryBackend
        ? [this.selectedSecondaryBackend]
        : [],
      correction_policy: this.generativeCorrection ? 'generative_rewrite' : 'deterministic',
      review_policy: this.generativeCorrection ? 'always' : 'on_disagreement',
      feature_flags: {
        ...(existingFlags && typeof existingFlags === 'object' ? existingFlags as Record<string, unknown> : {}),
        generative_corrector: this.generativeCorrection,
        voice_fusion: this.selectedRecognitionStrategy === 'parallel_compare',
      },
    };
    if (this.generativeCorrection) {
      delta['generative_corrector_provider'] = this.selectedCorrectorProvider;
      delta['generative_corrector_model'] = this.selectedCorrectorProvider === 'inherit'
        ? ''
        : this.manualCorrectorModel
          ? this.manualCorrectorModelId.trim()
          : this.selectedCorrectorModel;
    }
    await firstValueFrom(this.api.saveConfiguration(this.hubUrl, {
      scope,
      scope_id: scopeId,
      delta,
      expected_version: this.scopeVersion(scope, scopeId),
    }, voiceMutationKey(`console-configuration:${scope}`)));
    const refreshed = await firstValueFrom(this.api.getConfiguration(this.hubUrl, {
      profileId: this.profileId.trim(),
      sessionId: this.sessionId.trim() || undefined,
    }));
    this.configuration = refreshed;
    this.applyEffectiveConfiguration(refreshed);
  }

  private scopeDelta(scope: VoiceConfigurationTarget, scopeId: string): Record<string, unknown> {
    const sources = this.configuration?.sources;
    if (!sources) return {};
    const entries = Array.isArray(sources) ? sources : Object.values(sources);
    const matching = entries.filter((source) => (
      source.scope === scope && String(source.scope_id || '') === scopeId && source.delta
    ));
    return matching.reduce<Record<string, unknown>>((combined, source) => ({
      ...combined,
      ...structuredClone(source.delta || {}),
      feature_flags: {
        ...(valueAtPath(combined, 'feature_flags') as Record<string, unknown> || {}),
        ...(valueAtPath(source.delta, 'feature_flags') as Record<string, unknown> || {}),
      },
    }), {});
  }

  private scopeVersion(scope: VoiceConfigurationTarget, scopeId: string): number | undefined {
    const sources = this.configuration?.sources;
    if (!sources) return undefined;
    const entries = Array.isArray(sources) ? sources : Object.values(sources);
    const source = [...entries].reverse().find((candidate) => (
      candidate.scope === scope && String(candidate.scope_id || '') === scopeId
    ));
    const version = Number(source?.version);
    return Number.isInteger(version) && version > 0 ? version : undefined;
  }

  private fieldChoices(key: string, fallback: string[]): VoiceChoice[] {
    const field = configurationFields(this.schema).find((candidate) => candidate.key === key);
    if (!field) return fallback.map((id) => ({ id, label: id, available: true, reason: '' }));
    const values = field.options?.map((option) => ({
      id: String(option.value),
      label: option.label || String(option.value),
      available: option.enabled !== false,
      reason: String(option.reason_code || ''),
    })) || (field.enum || []).map((value) => ({
      id: String(value), label: String(value), available: true, reason: '',
    }));
    return uniqueChoices(values);
  }

  private onBatchCaptureEnded(generation: number, reason?: string): void {
    const operation = this.batchOperation;
    if (!this.isBatchOperationCurrent(generation) || !operation || operation.ending) return;
    operation.ending = true;
    void this.finishBatchRecording(generation, true, reason);
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

  private onBatchCaptureError(generation: number, _error: unknown): void {
    const operation = this.batchOperation;
    if (!this.isBatchOperationCurrent(generation) || !operation || operation.ending) return;
    operation.ending = true;
    void this.finishBatchRecording(generation, true, 'capture_error');
  }

  private async finishBatchRecording(
    generation: number,
    endedAutomatically: boolean,
    stopReason?: string,
  ): Promise<void> {
    if (!this.isBatchOperationCurrent(generation)) return;
    this.batchBusy = true;
    this.batchRecording = false;
    this.cdr.markForCheck();
    try {
      const audio = await this.batchRecorder.stop();
      if (!this.isBatchOperationCurrent(generation)) return;
      this.batchAudio = audio;
      this.batchFileName = audio.type === 'audio/wav' ? 'voice-recording.wav'
        : audio.type.includes('mp4') ? 'voice-recording.m4a'
        : 'voice-recording.webm';
      this.batchBusy = false;
      this.batchOperation = null;
      this.successMessage = endedAutomatically
        ? this.batchAutomaticStopMessage(stopReason)
        : 'Aufnahme beendet. Sie kann jetzt über den Hub transkribiert werden.';
      this.cdr.markForCheck();
    } catch (error) {
      if (!this.isBatchOperationCurrent(generation)) return;
      this.batchOperation = null;
      this.fail(error, () => { this.batchBusy = false; });
    }
  }

  private batchAutomaticStopMessage(reason?: string): string {
    if (reason === 'safety_limit') {
      return 'Die maximale Aufnahmedauer wurde erreicht. Die lokale Aufnahme kann jetzt über den Hub transkribiert werden.';
    }
    if (reason === 'notification_stop') {
      return 'Die Aufnahme wurde über Android beendet. Sie kann jetzt über den Hub transkribiert werden.';
    }
    if (reason === 'source_ended' || reason === 'projection_revoked') {
      return 'Die Audiofreigabe wurde beendet. Die lokale Aufnahme kann jetzt über den Hub transkribiert werden.';
    }
    return 'Die Aufnahme wurde automatisch beendet. Sie kann jetzt über den Hub transkribiert werden.';
  }

  private ensureLiveOperation(generation: number): void {
    if (!this.isLiveOperationCurrent(generation)) throw new Error('voice.capture.cancelled');
  }

  private isLiveOperationCurrent(generation: number): boolean {
    return !this.destroyed && generation === this.liveOperationGeneration;
  }

  private ensureBatchOperation(generation: number): void {
    if (!this.isBatchOperationCurrent(generation)) throw new Error('voice.capture.cancelled');
  }

  private isBatchOperationCurrent(generation: number): boolean {
    return !this.destroyed && generation === this.batchOperationGeneration;
  }

  private async refreshCaptureCapabilities(): Promise<void> {
    await Promise.allSettled([
      this.liveSession.refreshCaptureCapabilities(),
      this.batchRecorder.refreshCapabilities?.() || Promise.resolve(),
    ]);
    if (!this.destroyed) this.cdr.markForCheck();
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

function modelIsAvailable(model: { available?: boolean; status?: string }): boolean {
  if (typeof model.available === 'boolean') return model.available;
  const status = String(model.status || '').toLowerCase();
  if (!status) return true;
  return ['ready', 'available', 'configured', 'loaded'].includes(status);
}

function asrBackendLabel(backendId: string, reportedLabel?: string): string {
  const labels: Record<string, string> = {
    vosk: 'Vosk',
    whisper_cpp: 'whisper.cpp',
    faster_whisper: 'faster-whisper',
    voxtral: 'Voxtral',
  };
  return labels[backendId] || reportedLabel || backendId;
}

function uniqueChoices(choices: VoiceChoice[]): VoiceChoice[] {
  const values = new Map<string, VoiceChoice>();
  for (const choice of choices) {
    if (!choice.id || values.has(choice.id)) continue;
    values.set(choice.id, choice);
  }
  return [...values.values()];
}
