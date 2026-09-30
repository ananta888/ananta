import { Injectable, OnDestroy, inject } from '@angular/core';
import { BehaviorSubject, firstValueFrom, Subscription, take, timeout } from 'rxjs';

import {
  SFU_TRANSPORT_PROJECTION,
  type SfuTransportProjectionPort,
} from '../../services/livekit-sfu-transport.service';
import {
  SfuBroadcastVideoRenderFacade,
  type SfuRemoteVideoView,
} from '../../services/sfu-broadcast-video-render.facade';
import { MobileRuntimeService } from '../../services/mobile-runtime.service';
import { NetworkProfileService } from '../../services/network-profile.service';
import {
  SemanticReceiverPathService,
  SemanticReceiverPathView,
} from '../../services/semantic-receiver-path.service';
import { SemanticSpeechTransportService } from '../../services/semantic-speech-transport.service';
import {
  SemanticSpeechRuntimeCoordinatorService,
} from '../../services/semantic-speech-runtime-coordinator.service';
import { SemanticSpeechCaptureProducerService } from '../../services/semantic-speech-capture-producer.service';
import { DEFAULT_SEMANTIC_SPEECH_SETTINGS } from '../../services/semantic-speech-settings';
import {
  SemanticSpeechQualityControllerService,
  SemanticSpeechQualityState,
} from '../../services/semantic-speech-quality-controller.service';
import { SemanticSfuPathCoordinatorService } from '../../services/semantic-sfu-path-coordinator.service';
import { ShareSession, ShareSessionService } from '../../services/share-session.service';
import { PairSessionControlPlaneService } from '../../services/pair-session-control-plane.service';
import { SpeechEvidenceConsentReadModel } from '../../services/speech-evidence-consent-api.service';
import { SpeechReconciliationApiService } from '../../services/speech-reconciliation-api.service';
import { SpeechAdapterRegistryApiService } from '../../services/speech-adapter-registry-api.service';
import { OrdinaryAudioState, WebrtcMediaSessionService } from '../../services/webrtc-media-session.service';
import {
  MediaPublicationView,
  WebrtcMediaPublicationService,
} from '../../services/webrtc-media-publication.service';
import { VerifiedPeerBinding, WebrtcPeerKeyService } from '../../services/webrtc-peer-key.service';
import {
  PUBLIC_ORDINARY_MEDIA_E2EE_UNAVAILABLE,
  PairOrdinaryMediaPolicy,
} from '../../services/pair-ordinary-media.policy';
import { PairMediaE2eeCoordinatorService } from '../../services/pair-media-e2ee-coordinator.service';
import {
  PublicPairMediaPublicationConsentService,
  type PublicPairMediaPublicationConsentTerm,
} from '../../services/public-pair-media-publication-consent.service';
import { WebrtcTransportService } from '../../services/webrtc-transport.service';
import {
  ComputeContractIntent,
} from '../pair-view/pair-compute-contract-panel.component';
import { SemanticComputeIntentFacade, SemanticComputePanelState } from '../pair-view/semantic-compute-intent.facade';
import { SemanticReceiverPathIntent } from '../pair-view/semantic-receiver-path-panel.component';
import {
  PeerEvidenceOfferView,
  PeerEvidenceProposalIntent,
  PeerEvidenceSyncView,
} from './peer-evidence-sync-panel.component';
import { PeerEvidenceSyncFacade } from './peer-evidence-sync.facade';
import {
  SpeechEvidenceConsentFacade,
  SpeechEvidenceConsentIntent,
  SpeechEvidenceConsentPanelState,
} from './speech-evidence-consent.facade';
import {
  SemanticProgramCapability,
  SemanticProgramCapabilityView,
  SemanticProgramIntent,
  SemanticProgramState,
} from './semantic-media-program-shell.component';
import { SemanticSpeechPanelSettings, SemanticSpeechTransportState } from './semantic-speech-panel.component';
import { OrdinaryMediaCapturePolicy } from './ordinary-media-capture.policy';
import { PublicMediaPublicationController } from './public-media-publication.controller';
import { SemanticMediaAuthorityContext } from './semantic-media-authority-context';
import {
  SemanticMediaSessionSnapshot,
  peerEvidenceSyncContext,
  semanticMediaSessionBindings,
} from './semantic-media-program-bindings';
import {
  activeReconciliationConsent,
  reconciliationConsentClaimKey,
  requireActiveEvidenceConsent,
  speechRuntimeContext,
} from './semantic-media-program-consent.policy';
import {
  EMPTY_COMPUTE,
  EMPTY_CONSENT,
  LABELS,
  SemanticMediaProgramHostView,
  SpeechActivationFence,
  capabilityView,
  mediaPreference,
  reason,
  scopeFor,
  scopeForConsent,
  speechAdapterOptions,
} from './semantic-media-program.models';
import { SpeechAdapterActivationController } from './speech-adapter-activation.controller';
import { SpeechReconciliationAuthorization } from './speech-reconciliation-authorization';

export type { SemanticMediaProgramHostView } from './semantic-media-program.models';

/**
 * Host facade of the semantic media program: routes capability intents,
 * owns the speech session lifecycle and projects every collaborator into one
 * host view. Authority routing, public publication consent, ordinary capture
 * policy, reconciliation authorization, speech adapters, consent checks and
 * context derivation are delegated to the collaborators imported above (SRP).
 */
@Injectable()
export class SemanticMediaProgramFacade implements OnDestroy {
  private readonly profiles = inject(NetworkProfileService);
  private readonly shares = inject(ShareSessionService);
  private readonly pairControlPlane = inject(PairSessionControlPlaneService);
  private readonly transport = inject(WebrtcTransportService);
  private readonly peerKeys = inject(WebrtcPeerKeyService);
  private readonly speech = inject(SemanticSpeechTransportService);
  private readonly speechRuntime = inject(SemanticSpeechRuntimeCoordinatorService);
  private readonly speechProducer = inject(SemanticSpeechCaptureProducerService);
  private readonly speechQualityController = inject(SemanticSpeechQualityControllerService);
  private readonly compute = inject(SemanticComputeIntentFacade);
  private readonly receiverPaths = inject(SemanticReceiverPathService);
  private readonly sfu: SfuTransportProjectionPort = inject(SFU_TRANSPORT_PROJECTION);
  private readonly sfuCoordinator = inject(SemanticSfuPathCoordinatorService);
  private readonly sfuVideo = inject(SfuBroadcastVideoRenderFacade);
  private readonly media = inject(WebrtcMediaSessionService);
  private readonly mediaPublications = inject(WebrtcMediaPublicationService);
  private readonly ordinaryMediaPolicy = inject(PairOrdinaryMediaPolicy);
  private readonly pairMediaE2ee = inject(PairMediaE2eeCoordinatorService);
  private readonly publicationConsent = inject(PublicPairMediaPublicationConsentService);
  private readonly mobileRuntime = inject(MobileRuntimeService);
  private readonly evidenceFlow = inject(PeerEvidenceSyncFacade);
  private readonly consent = inject(SpeechEvidenceConsentFacade);
  private readonly reconciliationApi = inject(SpeechReconciliationApiService);
  private readonly speechAdaptersApi = inject(SpeechAdapterRegistryApiService);
  private readonly subscriptions = new Subscription();
  private readonly authority = new SemanticMediaAuthorityContext(
    this.pairControlPlane,
    this.shares.state$.value,
    this.profiles.current,
    this.mobileRuntime.online$.value,
  );
  private computeState: SemanticComputePanelState = EMPTY_COMPUTE;
  private receiverRows: readonly SemanticReceiverPathView[] = Object.freeze([]);
  private ordinaryAudioState: OrdinaryAudioState = this.media.audioState$.value;
  private ordinaryPublications: readonly MediaPublicationView[] = this.mediaPublications.publications$.value;
  private sfuRemoteVideos: readonly SfuRemoteVideoView[] = Object.freeze([]);
  private ordinaryMediaOperationReason = 'ordinary_media_not_started';
  private readonly publicMedia = new PublicMediaPublicationController(
    {
      publicationConsent: this.publicationConsent,
      pairMediaE2ee: this.pairMediaE2ee,
      ordinaryMediaPolicy: this.ordinaryMediaPolicy,
      media: this.media,
      mediaPublications: this.mediaPublications,
    },
    this.authority,
    {
      emit: () => this.emit(),
      setCapability: (capability, state, requestId, reasonCode) =>
        this.setCapability(capability, state, requestId, reasonCode),
      ordinaryMediaState: () => this.capabilityStates.get('ordinary_media'),
      setOperationReason: reasonCode => { this.ordinaryMediaOperationReason = reasonCode; },
    },
  );
  private speechState: SemanticSpeechTransportState = 'stopped';
  private speechReason = 'semantic_speech_not_started';
  private speechSettings: SemanticSpeechPanelSettings = DEFAULT_SEMANTIC_SPEECH_SETTINGS;
  private speechQuality: SemanticSpeechQualityState = this.speechQualityController.state$.value;
  private evidenceOffer: PeerEvidenceOfferView | null = null;
  private evidenceSync: PeerEvidenceSyncView | null = null;
  private evidenceReason = 'Noch kein Hub-autorisierter Evidence-Offer vorhanden.';
  private consentState: SpeechEvidenceConsentPanelState = EMPTY_CONSENT;
  private readonly capabilityStates = new Map<SemanticProgramCapability, SemanticProgramCapabilityView>();
  private readonly ordinaryCapture = new OrdinaryMediaCapturePolicy(
    { ordinaryMediaPolicy: this.ordinaryMediaPolicy, transport: this.transport, media: this.media, sfu: this.sfu },
    this.authority,
    this.publicMedia,
    {
      ordinaryMediaState: () => this.capabilityStates.get('ordinary_media')?.state,
      operationReason: () => this.ordinaryMediaOperationReason,
    },
  );
  private computeContextKey = '';
  private readonly reconciliation = new SpeechReconciliationAuthorization(() => this.emit());
  private readonly speechAdapters = new SpeechAdapterActivationController(
    this.speechAdaptersApi,
    this.speechRuntime,
    this.authority,
    {
      emit: () => this.emit(),
      setCapability: (capability, state, requestId, reasonCode) =>
        this.setCapability(capability, state, requestId, reasonCode),
    },
  );
  private speechActivationGeneration = 0;

  readonly view$ = new BehaviorSubject<SemanticMediaProgramHostView>(this.buildView());

  constructor() {
    this.subscriptions.add(this.shares.state$.subscribe(state => {
      const previousSessionId = this.authority.shareState.session?.id ?? '';
      const previousEpoch = this.authority.shareState.session?.security_epoch ?? 0;
      const previousAuthority = this.authority.kind();
      const nextSessionId = state.session?.id ?? '';
      this.authority.shareState = state;
      const authorityContextChanged = (
        previousSessionId !== nextSessionId
        || previousEpoch !== (state.session?.security_epoch ?? 0)
      );
      if (authorityContextChanged) {
        this.authority.advanceContextGeneration();
        this.publicMedia.cancelPreparation();
      }
      if (previousSessionId && previousSessionId !== nextSessionId) {
        // Replacing A directly with B parks A. The transport owner has already
        // closed and unbound A; turning that reversible transition into the
        // coordinator's terminal failClosed path would permanently disable A's
        // otherwise reusable media contract. A transition to no session is the
        // terminal End/Leave/remote-retirement projection and keeps the existing
        // terminal coordinator teardown as defense in depth.
        const terminalPublicSessionEnd = !nextSessionId && previousAuthority === 'public';
        this.stopSessionScopedState(previousSessionId, terminalPublicSessionEnd);
      }
      this.syncContext();
      this.emit();
    }));
    this.subscriptions.add(this.profiles.profile$.subscribe(profile => {
      this.authority.profile = profile;
      this.syncContext();
      this.emit();
    }));
    this.subscriptions.add(this.transport.mode$.subscribe(() => this.emit()));
    this.subscriptions.add(this.mobileRuntime.online$.subscribe(online => {
      this.authority.runtimeOnline = online;
      if (!online) this.reconciliation.clear();
      this.emit();
    }));
    this.subscriptions.add(this.media.audioState$.subscribe(state => {
      this.ordinaryAudioState = state;
      if (state.reasonCode) this.ordinaryMediaOperationReason = state.reasonCode;
      this.emit();
    }));
    this.subscriptions.add(this.mediaPublications.publications$.subscribe(publications => {
      this.ordinaryPublications = publications;
      const latestReason = [...publications].reverse().find(value => value.local && value.reasonCode)?.reasonCode;
      if (latestReason) this.ordinaryMediaOperationReason = latestReason;
      this.emit();
    }));
    this.subscriptions.add(this.pairMediaE2ee.status$.subscribe(status => {
      this.publicMedia.syncBinding();
      this.publicMedia.reconcileStatus(status);
    }));
    this.subscriptions.add(this.publicationConsent.state$.subscribe(state => {
      this.publicMedia.acceptConsentState(state);
      this.publicMedia.projectConsent();
      this.emit();
    }));
    this.subscriptions.add(this.speechRuntime.settings$.subscribe(settings => {
      this.speechSettings = settings;
      this.emit();
    }));
    this.subscriptions.add(this.speechQualityController.state$.subscribe(state => {
      this.speechQuality = state;
      if (state.mode === 'ordinary_audio' && this.speechState === 'active') {
        void this.ensureOrdinaryAudio();
      }
      this.emit();
    }));
    this.subscriptions.add(this.speechRuntime.fatalFailure$.subscribe(reasonCode => {
      this.stopSpeech(reasonCode);
      void this.ensureOrdinaryAudio();
    }));
    this.subscriptions.add(this.speechProducer.failure$.subscribe(reasonCode => {
      this.stopSpeech(reasonCode);
      void this.ensureOrdinaryAudio();
    }));
    this.subscriptions.add(this.sfu.state$.subscribe(() => {
      this.syncReceiverPaths();
      this.emit();
    }));
    this.subscriptions.add(this.sfuVideo.videos$.subscribe(rows => {
      this.sfuRemoteVideos = rows;
      this.emit();
    }));
    this.subscriptions.add(this.compute.state$.subscribe(state => {
      this.computeState = state;
      this.emit();
    }));
    this.subscriptions.add(this.receiverPaths.rows$.subscribe(rows => {
      this.receiverRows = rows;
      this.emit();
    }));
    this.subscriptions.add(this.consent.state$.subscribe(state => {
      const previousReconciliationClaim = this.reconciliationConsentClaimKey();
      this.consentState = state;
      if (previousReconciliationClaim !== this.reconciliationConsentClaimKey()) {
        this.reconciliation.clear();
      }
      const consent = state.consent?.consent;
      if (consent && consent.state !== 'active') {
        this.evidenceFlow.clear();
        if (this.capabilityStates.has('evidence_text')) {
          this.setCapability('evidence_text', consent.state === 'expired' ? 'expired' : 'revoked', null);
        }
      }
      this.syncEvidenceContext();
      this.syncSpeechRuntimeBinding();
      this.emit();
    }));
    this.subscriptions.add(this.evidenceFlow.view$.subscribe(view => {
      this.evidenceOffer = view.offer;
      this.evidenceSync = view.sync;
      this.evidenceReason = view.reasonCode;
      this.emit();
    }));
  }

  async start(): Promise<void> {
    await this.profiles.load().catch(() => undefined);
    this.syncContext();
    this.emit();
  }

  async handleProgramIntent(intent: SemanticProgramIntent): Promise<void> {
    const intentSessionId = this.authority.shareState.session?.id ?? '';
    const current = this.capability(intent.capability);
    if (current.requestId && current.requestId !== intent.requestId) return;
    if (intent.capability === 'ordinary_media' && this.authority.kind() === 'public') {
      if (intent.desired === 'activate') {
        await this.grantOrdinaryMediaPublicationConsent({ kind: 'session' });
      } else {
        await this.revokeOrdinaryMediaPublicationConsent();
      }
      return;
    }
    if (intent.capability !== 'ordinary_media' && !this.authority.hasHubAuthority()) {
      this.setCapability(
        intent.capability,
        'failed',
        null,
        'semantic_program_hub_authority_required',
      );
      return;
    }
    const activationAvailable = intent.capability === 'ordinary_media'
      ? this.ordinaryCapture.activationAvailable()
      : this.authority.hubOperationsAvailable();
    if (intent.desired === 'activate' && !activationAvailable) {
      this.setCapability(
        intent.capability,
        'failed',
        null,
        intent.capability === 'ordinary_media'
          ? this.ordinaryCapture.captureReason()
          : this.authority.hubOperationUnavailableReason(),
      );
      return;
    }
    this.setCapability(
      intent.capability,
      intent.desired === 'activate'
        ? intent.capability === 'ordinary_media' && this.authority.kind() === 'public'
          ? 'sent_to_authority'
          : 'sent_to_hub'
        : 'pausing',
      intent.requestId,
    );
    if (intent.desired !== 'activate') {
      this.deactivate(intent.capability, intent.desired === 'revoke' ? 'revoked' : 'pausing');
      this.setCapability(intent.capability, intent.desired === 'revoke' ? 'revoked' : 'revoked', null);
      return;
    }
    try {
      const activatedState = await this.activate(intent);
      if (
        (this.authority.shareState.session?.id ?? '') !== intentSessionId
        || this.capability(intent.capability).requestId !== intent.requestId
      ) return;
      this.setCapability(
        intent.capability,
        activatedState,
        null,
        intent.capability === 'ordinary_media' && activatedState === 'degraded'
          ? this.ordinaryCapture.captureReason()
          : null,
      );
    } catch (error) {
      if (
        (this.authority.shareState.session?.id ?? '') !== intentSessionId
        || this.capability(intent.capability).requestId !== intent.requestId
      ) return;
      const reasonCode = reason(error, 'semantic_program_activation_failed');
      if (intent.capability === 'adapter_activation') {
        this.speechAdapters.clear(reasonCode);
      }
      this.setCapability(intent.capability, 'failed', null, reasonCode);
      if (intent.capability === 'live_speech') {
        this.speechState = 'failed';
        this.speechReason = reason(error, 'semantic_speech_activation_failed');
      }
      this.emit();
    }
  }

  grantOrdinaryMediaPublicationConsent(
    term: PublicPairMediaPublicationConsentTerm,
  ): Promise<void> {
    return this.publicMedia.grant(term);
  }

  revokeOrdinaryMediaPublicationConsent(): Promise<void> {
    return this.publicMedia.revoke();
  }

  async startSpeech(): Promise<void> {
    if (this.speechState === 'starting' || this.speechState === 'active') return;
    if (this.speechSettings.paused || this.speechSettings.ordinaryAudioOverride) return;
    if (!this.authority.hasHubAuthority()) {
      this.speechState = 'failed';
      this.speechReason = 'semantic_program_hub_authority_required';
      this.emit();
      return;
    }
    this.speechState = 'starting';
    this.speechReason = 'semantic_speech_starting';
    const activationGeneration = ++this.speechActivationGeneration;
    this.emit();
    try {
      this.authority.requireHubOperationAuthority();
      const binding = this.peerKeys.requireBinding(true);
      const session = this.authority.requireSession();
      const fence = this.authority.speechActivationFence(session, activationGeneration);
      if (binding.scopeId !== session.id || !this.authority.profile.semantic_media_feature_flags.semantic_speech_runtime) {
        throw new Error('semantic_speech_hub_context_missing');
      }
      await this.ensureOrdinaryAudio(fence);
      this.assertSpeechActivationFence(fence);
      this.speech.start({
        sessionId: binding.scopeId,
        epoch: binding.epoch,
        localPeerId: binding.localPeerId,
        remotePeerId: binding.remotePeerId,
        consentVersion: Math.max(1, session.permissions_version ?? 1),
        contractDigest: binding.contractDigest,
      });
      const runtimeContext = this.speechRuntimeContext(binding, session);
      this.speechRuntime.start(runtimeContext);
      await this.speechProducer.start({ ...runtimeContext, profileId: 'default' });
      this.assertSpeechActivationFence(fence);
      this.speechState = 'active';
      this.speechReason = 'semantic_speech_transport_active';
      this.setCapability('live_speech', 'authoritatively_active', null);
    } catch (error) {
      if (activationGeneration !== this.speechActivationGeneration) return;
      void this.speechProducer.stop('semantic_speech_start_failed');
      this.speech.stop();
      this.speechRuntime.stop('semantic_speech_start_failed');
      this.speechState = 'failed';
      this.speechReason = reason(error, 'semantic_speech_start_failed');
      this.emit();
    }
  }

  stopSpeech(reasonCode = 'semantic_speech_user_stop'): void {
    this.speechActivationGeneration += 1;
    if (this.speechState === 'stopped') {
      void this.speechProducer.stop(reasonCode);
      this.speechRuntime.stop(reasonCode);
      this.speechAdapters.clear(reasonCode, false);
      return;
    }
    this.speechState = 'stopping';
    this.speechReason = reasonCode;
    this.emit();
    void this.speechProducer.stop(reasonCode);
    this.speech.stop();
    this.speechRuntime.stop(reasonCode);
    this.speechAdapters.clear(reasonCode, false);
    this.speechState = 'stopped';
    this.speechReason = reasonCode;
    this.emit();
  }

  handleSpeechSettings(settings: SemanticSpeechPanelSettings): void {
    if (!this.authority.hasHubAuthority()) return;
    const previous = this.speechSettings;
    this.speechRuntime.applySettings(settings);
    this.speechProducer.applySettings(settings);
    if (settings.ordinaryAudioOverride) {
      this.stopSpeech('semantic_speech_ordinary_override');
      void this.ensureOrdinaryAudio();
      return;
    }
    if (settings.paused) {
      this.stopSpeech('semantic_speech_user_paused');
      return;
    }
    if ((previous.paused || previous.ordinaryAudioOverride) && this.capabilityStates.get('live_speech')?.state !== 'revoked') {
      void this.startSpeech();
    }
  }

  handleComputeIntent(intent: ComputeContractIntent): void {
    if (!this.authority.hasHubAuthority()) return;
    if (intent.kind === 'activate' && !this.authority.hubOnline()) return;
    void this.compute.handleIntent(intent);
  }

  requestComputeSuggestion(): void {
    if (!this.authority.hasHubAuthority()) return;
    void this.compute.requestSuggestion();
  }

  async handleReceiverPathIntent(intent: SemanticReceiverPathIntent): Promise<void> {
    if (!this.authority.hasHubAuthority()) {
      this.receiverPaths.setOperationState(
        intent.receiverId,
        false,
        'semantic_media_hub_authority_required',
      );
      this.syncReceiverPaths();
      this.emit();
      return;
    }
    this.receiverPaths.request(intent.receiverId, intent.preference);
    this.receiverPaths.setOperationState(intent.receiverId, true, 'receiver_path_hub_confirmation_required');
    try {
      await this.sfuCoordinator.switchReceiver(intent.receiverId, intent.preference);
      this.receiverPaths.setOperationState(intent.receiverId, false);
    } catch (error) {
      this.receiverPaths.setOperationState(intent.receiverId, false, reason(error, 'sfu_path_activation_failed'));
    }
    this.syncReceiverPaths();
    this.emit();
  }

  async startOrdinaryMicrophone(): Promise<void> {
    try {
      this.ordinaryCapture.requireCaptureAuthorization(false);
      this.ordinaryMediaOperationReason = 'microphone_permission_requested';
      this.emit();
      await this.media.requestMicrophone();
      this.ordinaryMediaOperationReason = 'microphone_active';
      this.setCapability('ordinary_media', 'authoritatively_active', null);
    } catch (error) {
      this.ordinaryMediaOperationReason = reason(error, 'microphone_start_failed');
      this.emit();
    }
  }

  stopOrdinaryMicrophone(): void {
    this.media.stopAudio('microphone_user_stop');
    this.ordinaryMediaOperationReason = 'microphone_user_stop';
    this.emit();
  }

  setOrdinaryMicrophoneMuted(muted: boolean): void {
    this.media.setMuted(muted);
    this.ordinaryMediaOperationReason = muted ? 'microphone_muted' : 'microphone_active';
    this.emit();
  }

  async startOrdinaryVideo(source: 'camera' | 'screen'): Promise<void> {
    try {
      const authorization = this.ordinaryCapture.publicationAuthorization(source);
      this.ordinaryMediaOperationReason = `${source}_permission_requested`;
      this.emit();
      await this.mediaPublications.startLocal(authorization, mediaPreference(source));
      this.ordinaryMediaOperationReason = `${source}_active`;
      this.setCapability('ordinary_media', 'authoritatively_active', null);
    } catch (error) {
      this.ordinaryMediaOperationReason = reason(error, 'publication_start_failed');
      this.emit();
    }
  }

  async replaceOrdinaryVideo(publicationId: string): Promise<void> {
    const publication = this.ordinaryPublications.find(value => value.publicationId === publicationId && value.local);
    if (!publication || (publication.source !== 'camera' && publication.source !== 'screen')) return;
    try {
      this.ordinaryCapture.requireCaptureAuthorization(true);
      await this.mediaPublications.replaceLocal(publicationId, publication.source, mediaPreference(publication.source));
      this.ordinaryMediaOperationReason = `${publication.source}_replaced`;
    } catch (error) {
      this.ordinaryMediaOperationReason = reason(error, 'publication_replace_failed');
      this.emit();
    }
  }

  stopOrdinaryVideo(publicationId: string): void {
    this.mediaPublications.stopPublication(publicationId, 'publication_user_stop');
    this.ordinaryMediaOperationReason = 'publication_user_stop';
    this.emit();
  }

  setOrdinaryVideoMuted(value: Readonly<{ publicationId: string; muted: boolean }>): void {
    this.mediaPublications.setMuted(value.publicationId, value.muted);
    this.ordinaryMediaOperationReason = value.muted ? 'publication_muted' : 'publication_active';
    this.emit();
  }

  handleEvidenceConsentIntent(intent: SpeechEvidenceConsentIntent): void {
    if (!this.authority.hasHubAuthority()) return;
    void this.consent.handle(intent);
  }

  handleEvidencePropose(intent: PeerEvidenceProposalIntent): void {
    if (this.authority.hasHubAuthority()) void this.evidenceFlow.propose(intent);
  }
  handleEvidenceAccept(dataClasses: readonly string[]): void {
    if (this.authority.hasHubAuthority()) void this.evidenceFlow.accept(dataClasses);
  }
  pauseEvidence(): void {
    if (this.authority.hasHubAuthority()) this.evidenceFlow.pause();
  }
  resumeEvidence(): void {
    if (this.authority.hasHubAuthority()) void this.evidenceFlow.resume();
  }
  rejectEvidence(): void {
    if (this.authority.hasHubAuthority()) void this.evidenceFlow.reject();
  }
  revokeEvidence(): void {
    if (this.authority.hasHubAuthority()) void this.evidenceFlow.revoke();
  }
  requestEvidenceCuration(): void {
    if (this.authority.hasHubAuthority()) void this.evidenceFlow.requestHubCuration();
  }
  handleEvidenceLocalOverride(value: { regionId: string; candidateId: string }): void {
    if (this.authority.hasHubAuthority()) this.evidenceFlow.localOverride(value.regionId, value.candidateId);
  }

  ngOnDestroy(): void {
    // Destroying a panel/facade host is a local UI lifecycle event, not proof
    // that the Pair membership ended. Stop capture and revoke runtime consent,
    // but leave terminal E2EE teardown to the session/transport owner.
    this.stopSessionScopedState(this.authority.shareState.session?.id ?? '');
    this.subscriptions.unsubscribe();
    this.view$.complete();
  }

  private async activate(
    intent: SemanticProgramIntent,
  ): Promise<'authoritatively_active' | 'degraded'> {
    const capability = intent.capability;
    const session = this.authority.requireSession();
    const flags = this.authority.profile.semantic_media_feature_flags;
    if (capability === 'ordinary_media') {
      if (this.transport.mode$.value !== 'webrtc') throw new Error('ordinary_media_webrtc_transport_required');
      const authority = this.authority.kind();
      if (authority === 'unbound') throw new Error('ordinary_media_session_binding_missing');
      if (authority === 'hub' && !flags.ordinary_media_publication) {
        throw new Error('ordinary_media_publication_disabled');
      }
      if (authority === 'public' && await this.publicMedia.activate(session) === 'degraded') {
        return 'degraded';
      }
      this.ordinaryMediaPolicy.assertAllowed(session.id);
      return 'authoritatively_active';
    }
    this.authority.requireHubOperationAuthority();
    if (!this.authority.hubUrl()) throw new Error('semantic_program_hub_missing');
    if (capability === 'live_speech') {
      if (!flags.semantic_speech_runtime) throw new Error('semantic_speech_disabled');
      await this.startSpeech();
      if (this.speechState !== 'active') throw new Error(this.speechReason);
      return 'authoritatively_active';
    }
    if (capability === 'evidence_text') {
      if (!flags.peer_evidence_sync) throw new Error('peer_evidence_sync_disabled');
      requireActiveEvidenceConsent(this.consentState.consent, session);
      this.syncEvidenceContext();
      await this.evidenceFlow.activate();
      return 'authoritatively_active';
    }
    if (capability === 'speech_reconciliation') {
      if (!flags.speech_reconciliation) throw new Error('speech_reconciliation_disabled');
      const reconciliationConsent = this.requireActiveReconciliationConsent(session);
      const contextKey = this.reconciliationContextKey();
      const hubUrl = this.authority.hubUrl();
      if (!contextKey || !hubUrl) throw new Error('speech_reconciliation_hub_context_missing');
      await this.reconciliation.authorize(
        contextKey,
        () => firstValueFrom(this.reconciliationApi.list(hubUrl, 0, 1).pipe(take(1), timeout({ first: 10_000 }))),
        () => contextKey === this.reconciliationContextKey()
          && this.authority.hubOnline()
          && this.authority.profile.semantic_media_feature_flags.speech_reconciliation,
        reconciliationConsent.consent.expires_at_ms,
      );
      return 'authoritatively_active';
    }
    if (capability === 'adapter_activation') {
      if (!flags.speech_adapter_routing) throw new Error('speech_adapter_routing_disabled');
      if (this.speechState !== 'active') throw new Error('semantic_speech_runtime_not_started');
      await this.speechAdapters.activate(intent, session.id);
      return 'authoritatively_active';
    }
    throw new Error('semantic_program_capability_endpoint_unavailable');
  }

  private deactivate(capability: SemanticProgramCapability, _state: SemanticProgramState): void {
    if (capability === 'ordinary_media') {
      const sessionId = this.authority.shareState.session?.id ?? '';
      const authority = this.authority.kind();
      if (sessionId && authority === 'public') {
        this.publicMedia.resetReadiness();
        this.pairMediaE2ee.deactivate(sessionId, 'ordinary_media_capability_revoked');
      }
      this.media.stopAudio('ordinary_media_capability_revoked');
      this.mediaPublications.stopAll('ordinary_media_capability_revoked');
      if (authority === 'hub') void this.sfuCoordinator.stop('sfu_ordinary_media_revoked');
    }
    if (capability === 'live_speech') this.stopSpeech('semantic_speech_capability_revoked');
    if (capability === 'evidence_text' || capability === 'raw_audio') {
      this.evidenceFlow.clear();
    }
    if (capability === 'speech_reconciliation') this.reconciliation.clear();
    if (capability === 'adapter_activation') this.speechAdapters.clear('speech_adapter_user_revoked');
  }

  private syncContext(): void {
    this.publicMedia.syncBinding();
    this.syncReceiverPaths();
    const bindings = semanticMediaSessionBindings(this.sessionSnapshot());
    this.consent.bind(bindings.consent);
    this.syncEvidenceContext();
    this.speechAdapters.refresh();
    this.sfuCoordinator.bind(bindings.sfu);
    if (bindings.computeKey === this.computeContextKey) return;
    this.computeContextKey = bindings.computeKey;
    this.compute.bind(bindings.compute);
  }

  private sessionSnapshot(): SemanticMediaSessionSnapshot {
    return {
      session: this.authority.session,
      participants: this.authority.shareState.participants,
      hubAuthority: this.authority.hasHubAuthority(),
      hubUrl: this.authority.hubUrl(),
      localUserId: this.shares.currentUserId,
      binding: this.peerKeys.currentBinding,
      profile: this.authority.profile,
    };
  }

  private syncReceiverPaths(): void {
    const hubAuthority = this.authority.hasHubAuthority();
    const participants = this.authority.shareState.participants
      .filter(participant => !participant.revoked_at && participant.user_id !== this.shares.currentUserId)
      .map(participant => ({ receiverId: participant.user_id, label: participant.user_id }));
    this.receiverPaths.setReceivers(participants);
    this.receiverPaths.setHubState({
      sfuConnected: hubAuthority && this.sfu.currentState().status === 'connected',
      sfuAuthorizedReceiverIds: hubAuthority ? this.sfu.authorizedSubscriberIds() : new Set<string>(),
      sfuFeatureEnabled: hubAuthority && this.authority.profile.semantic_media_feature_flags.semantic_media_sfu,
    });
  }

  private stopSessionScopedState(sessionId = '', terminalPublicMedia = false): void {
    if (sessionId) {
      this.publicMedia.resetReadiness();
      if (terminalPublicMedia) {
        this.pairMediaE2ee.deactivate(sessionId, 'ordinary_media_session_ended');
      }
    }
    this.stopSpeech('semantic_speech_session_ended');
    this.speech.stop();
    this.evidenceFlow.clear();
    this.consent.bind(null);
    this.sfuCoordinator.bind(null);
    this.sfuVideo.clear();
    this.media.stopAudio('ordinary_media_session_ended');
    this.mediaPublications.stopAll('ordinary_media_session_ended', true);
    this.publicationConsent.bind(null);
    this.compute.bind(null);
    this.computeContextKey = '';
    this.reconciliation.clear();
    this.speechAdapters.clear('speech_adapter_session_ended');
    this.speechAdapters.resetSessionScope();
    this.receiverPaths.clear();
    this.capabilityStates.clear();
  }

  private setCapability(
    capability: SemanticProgramCapability,
    state: SemanticProgramState,
    requestId: string | null,
    reasonCode: string | null = null,
  ): void {
    this.capabilityStates.set(capability, capabilityView(capability, state, requestId, reasonCode));
    this.emit();
  }

  private capability(capability: SemanticProgramCapability): SemanticProgramCapabilityView {
    const stored = this.capabilityStates.get(capability);
    let projected = stored ?? this.defaultCapability(capability);
    if (
      stored?.state === 'authoritatively_active'
      && capability === 'ordinary_media'
      && !this.ordinaryCapture.active()
    ) projected = Object.freeze({ ...stored, state: 'degraded' });
    if (
      stored?.state === 'authoritatively_active'
      && capability === 'adapter_activation'
      && !this.speechAdapters.activeAdapter
    ) projected = Object.freeze({ ...stored, state: 'degraded', reasonCode: 'speech_adapter_not_active' });
    if (
      stored?.state === 'authoritatively_active'
      && capability === 'live_speech'
      && this.speechState !== 'active'
    ) projected = Object.freeze({ ...stored, state: 'degraded' });
    if (
      stored?.state === 'authoritatively_active'
      && capability === 'speech_reconciliation'
      && !this.reconciliationHubAuthorized()
    ) projected = Object.freeze({ ...stored, state: 'degraded' });
    if (capability === 'speech_reconciliation') {
      const consent = this.consentState.consent?.consent;
      return Object.freeze({
        ...projected,
        ...(consent ? { scope: scopeForConsent(consent, this.authority.shareState.session) } : {}),
      });
    }
    return projected;
  }

  private defaultCapability(capability: SemanticProgramCapability): SemanticProgramCapabilityView {
    const session = this.authority.shareState.session;
    const state: SemanticProgramState = capability === 'ordinary_media'
      && session
      && this.ordinaryCapture.active() ? 'authoritatively_active' : 'revoked';
    const activationReason = capability === 'ordinary_media'
      && this.authority.kind() === 'public'
      && !this.ordinaryCapture.activationAvailable()
      ? this.ordinaryCapture.activationReason()
      : null;
    return capabilityView(capability, state, null, activationReason);
  }

  private buildView(): SemanticMediaProgramHostView {
    const session = this.authority.shareState.session;
    const authority = this.authority.kind();
    const hubAuthority = authority === 'hub';
    return Object.freeze({
      scope: scopeFor(session),
      capabilities: Object.freeze((Object.keys(LABELS) as SemanticProgramCapability[]).map(value => this.capability(value))),
      online: this.authority.hubOperationsAvailable(),
      hubUrl: hubAuthority ? this.authority.hubUrl() : '',
      ordinaryMediaAuthority: authority,
      ordinaryMediaActivationEnabled: this.ordinaryCapture.activationAvailable(),
      computeVisible: Boolean(
        hubAuthority && session && this.shares.currentUserId && (session.security_epoch ?? 0) > 0
      ),
      compute: this.computeState,
      receiverPaths: this.receiverRows,
      ordinaryMediaCaptureEnabled: this.ordinaryCapture.captureAllowed(false),
      ordinaryMediaVideoCaptureEnabled: this.ordinaryCapture.captureAllowed(true),
      ordinaryMediaE2eeReady: authority === 'public'
        && Boolean(session?.id) && this.ordinaryMediaPolicy.allows(session!.id),
      ordinaryMediaReason: this.ordinaryCapture.captureReason(),
      ordinaryMediaPublicationConsent: this.publicMedia.consentState,
      ordinaryMediaPublicationPreparationPending: this.publicMedia.preparationPending,
      ordinaryAudioState: this.ordinaryAudioState,
      ordinaryMediaPublications: this.ordinaryPublications,
      sfuRemoteVideos: hubAuthority ? this.sfuRemoteVideos : Object.freeze([]),
      speechTransportState: this.speechState,
      speechTransportReason: this.speechReason,
      speechTransportCanStart: this.canStartSpeech(),
      speechSettings: this.speechSettings,
      speechQuality: this.speechQuality,
      speechReconciliationHubAuthorized: this.reconciliationHubAuthorized(),
      speechAdapters: speechAdapterOptions(this.speechAdapters.adapters),
      evidenceOffer: this.evidenceOffer,
      evidenceSync: this.evidenceSync,
      evidenceAvailableReason: this.evidenceReason,
      evidenceConsent: this.consentState,
    });
  }

  private emit(): void { this.view$.next(this.buildView()); }

  private assertSpeechActivationFence(fence: SpeechActivationFence): void {
    this.authority.assertSpeechActivationFence(fence, this.speechActivationGeneration);
  }

  private reconciliationContextKey(): string {
    if (!this.authority.hasHubAuthority()) return '';
    const session = this.authority.shareState.session;
    const localPeerId = this.shares.currentUserId;
    const hubUrl = this.authority.hubUrl();
    const epoch = session?.security_epoch ?? 0;
    const consent = this.activeReconciliationConsent(session);
    if (!session || session.revoked_at !== null || !localPeerId || !hubUrl || epoch <= 0 || !consent) return '';
    return [
      hubUrl, session.id, epoch, localPeerId, this.reconciliationConsentClaimKey(consent),
    ].join('\x1f');
  }

  private reconciliationHubAuthorized(): boolean {
    const contextKey = this.reconciliationContextKey();
    return Boolean(
      contextKey
      && this.authority.hubOnline()
      && this.authority.profile.semantic_media_feature_flags.speech_reconciliation
      && this.reconciliation.authorizedFor(contextKey)
    );
  }

  private activeReconciliationConsent(
    session: ShareSession | null,
  ): SpeechEvidenceConsentReadModel | null {
    return activeReconciliationConsent(
      this.consentState.consent, session, this.peerKeys.currentBinding, this.shares.currentUserId,
    );
  }

  private requireActiveReconciliationConsent(session: ShareSession): SpeechEvidenceConsentReadModel {
    const consent = this.activeReconciliationConsent(session);
    if (!consent) throw new Error('speech_reconciliation_consent_stale_or_narrow');
    return consent;
  }

  private reconciliationConsentClaimKey(
    value: SpeechEvidenceConsentReadModel | null = this.consentState.consent,
  ): string {
    return reconciliationConsentClaimKey(value);
  }

  private canStartSpeech(): boolean {
    if (
      !this.authority.hasHubAuthority()
      || !this.authority.shareState.session || !this.authority.profile.semantic_media_feature_flags.semantic_speech_runtime
      || this.speechSettings.paused || this.speechSettings.ordinaryAudioOverride
    ) return false;
    const binding = this.peerKeys.currentBinding;
    return Boolean(binding?.confirmed && binding.scopeId === this.authority.shareState.session.id);
  }

  private async ensureOrdinaryAudio(fence?: SpeechActivationFence): Promise<void> {
    if (this.transport.mode$.value !== 'webrtc') return;
    const sessionId = this.authority.shareState.session?.id ?? '';
    if (!this.ordinaryMediaPolicy.allows(sessionId)) {
      this.ordinaryMediaOperationReason = PUBLIC_ORDINARY_MEDIA_E2EE_UNAVAILABLE;
      this.emit();
      return;
    }
    if (!['active', 'muted'].includes(this.media.audioState$.value.status)) {
      await this.media.requestMicrophone();
    }
    if (fence) this.assertSpeechActivationFence(fence);
    this.setCapability('ordinary_media', 'authoritatively_active', null);
  }

  private syncEvidenceContext(): void {
    this.evidenceFlow.bind(peerEvidenceSyncContext(this.sessionSnapshot(), this.consentState.consent));
  }

  private syncSpeechRuntimeBinding(): void {
    if (this.speechState !== 'active' || !this.authority.hasHubAuthority()) return;
    const session = this.authority.shareState.session;
    const binding = this.peerKeys.currentBinding;
    if (!session || !binding?.confirmed || binding.scopeId !== session.id) return;
    const runtimeContext = this.speechRuntimeContext(binding, session);
    this.speechRuntime.start(runtimeContext);
    this.speechProducer.rebind({ ...runtimeContext, profileId: 'default' });
  }

  private speechRuntimeContext(binding: Readonly<VerifiedPeerBinding>, session: ShareSession) {
    return speechRuntimeContext(this.authority.hubUrl(), binding, session, this.consentState.consent);
  }
}
