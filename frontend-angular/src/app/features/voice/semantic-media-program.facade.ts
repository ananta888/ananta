import { Injectable, OnDestroy, inject } from '@angular/core';
import { BehaviorSubject, firstValueFrom, forkJoin, Subscription, take, timeout } from 'rxjs';

import {
  SFU_TRANSPORT_PROJECTION,
  type SfuTransportProjectionPort,
} from '../../services/livekit-sfu-transport.service';
import {
  SfuBroadcastVideoRenderFacade,
  type SfuRemoteVideoView,
} from '../../services/sfu-broadcast-video-render.facade';
import { MobileRuntimeService } from '../../services/mobile-runtime.service';
import { NetworkProfile, NetworkProfileService } from '../../services/network-profile.service';
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
import { ShareSession, ShareSessionService, ActiveShareState } from '../../services/share-session.service';
import { PairSessionControlPlaneService } from '../../services/pair-session-control-plane.service';
import {
  SpeechEvidenceConsentDocument,
  SpeechEvidenceConsentReadModel,
} from '../../services/speech-evidence-consent-api.service';
import { SpeechReconciliationApiService } from '../../services/speech-reconciliation-api.service';
import { SpeechAdapterRegistryApiService } from '../../services/speech-adapter-registry-api.service';
import { OrdinaryAudioState, WebrtcMediaSessionService } from '../../services/webrtc-media-session.service';
import {
  MediaPublicationAuthorization,
  MediaPublicationView,
  UserMediaPreference,
  WebrtcMediaPublicationService,
} from '../../services/webrtc-media-publication.service';
import { WebrtcPeerKeyService } from '../../services/webrtc-peer-key.service';
import {
  PUBLIC_ORDINARY_MEDIA_E2EE_UNAVAILABLE,
  PairOrdinaryMediaPolicy,
} from '../../services/pair-ordinary-media.policy';
import {
  PairMediaE2eeCoordinatorService,
  type PublicPairMediaE2eeState,
  type PublicPairMediaPublicationContext,
} from '../../services/pair-media-e2ee-coordinator.service';
import {
  PublicPairMediaPublicationConsentService,
  type PublicPairMediaPublicationConsentState,
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
  OrdinaryMediaAuthorityKind,
  SemanticProgramScopeView,
  SemanticProgramState,
  SpeechAdapterActivationOption,
} from './semantic-media-program-shell.component';
import {
  SpeechAdapterMetadata,
  SpeechDirection,
} from './reconstruction/personalized-speech-reconstructor.service';
import { SemanticSpeechPanelSettings, SemanticSpeechTransportState } from './semantic-speech-panel.component';

export interface SemanticMediaProgramHostView {
  readonly scope: SemanticProgramScopeView;
  readonly capabilities: readonly SemanticProgramCapabilityView[];
  readonly online: boolean;
  readonly hubUrl: string;
  readonly ordinaryMediaAuthority: OrdinaryMediaAuthorityKind;
  readonly ordinaryMediaActivationEnabled: boolean;
  readonly computeVisible: boolean;
  readonly compute: SemanticComputePanelState;
  readonly receiverPaths: readonly SemanticReceiverPathView[];
  readonly ordinaryMediaCaptureEnabled: boolean;
  readonly ordinaryMediaVideoCaptureEnabled: boolean;
  readonly ordinaryMediaE2eeReady: boolean;
  readonly ordinaryMediaReason: string;
  readonly ordinaryMediaPublicationConsent: PublicPairMediaPublicationConsentState;
  readonly ordinaryAudioState: OrdinaryAudioState;
  readonly ordinaryMediaPublications: readonly MediaPublicationView[];
  readonly sfuRemoteVideos: readonly SfuRemoteVideoView[];
  readonly speechTransportState: SemanticSpeechTransportState;
  readonly speechTransportReason: string;
  readonly speechTransportCanStart: boolean;
  readonly speechSettings: SemanticSpeechPanelSettings;
  readonly speechQuality: SemanticSpeechQualityState;
  readonly speechReconciliationHubAuthorized: boolean;
  readonly speechAdapters: readonly SpeechAdapterActivationOption[];
  readonly evidenceOffer: PeerEvidenceOfferView | null;
  readonly evidenceSync: PeerEvidenceSyncView | null;
  readonly evidenceAvailableReason: string;
  readonly evidenceConsent: SpeechEvidenceConsentPanelState;
}

const LABELS: Readonly<Record<SemanticProgramCapability, string>> = Object.freeze({
  ordinary_media: 'Ordinary Audio/Video',
  semantic_video: 'Semantisches Video',
  live_speech: 'Semantische Live-Sprache',
  evidence_text: 'Peer-Text-Evidence',
  raw_audio: 'Peer-Roh-Audio',
  training: 'Speech-Training',
  speech_reconciliation: 'Offline-Sprachabstimmung',
  adapter_activation: 'Speech-Adapter aktivieren',
  export: 'Speech-Daten exportieren',
});

const SENSITIVE = new Set<SemanticProgramCapability>([
  'raw_audio', 'training', 'speech_reconciliation', 'adapter_activation', 'export',
]);

const EMPTY_COMPUTE: SemanticComputePanelState = Object.freeze({
  contract: Object.freeze({ contractId: '', revision: 0, status: 'absent', profile: 'off', delayMs: 5_000, roles: {} }),
  leases: Object.freeze([]), pending: false, errorCode: 'compute_session_missing',
});

const EMPTY_CONSENT: SpeechEvidenceConsentPanelState = Object.freeze({
  bound: false, signerIds: Object.freeze([]), consent: null, pending: false,
  errorCode: 'speech_consent_context_missing',
});

interface BoundSemanticMediaAuthorityRoute {
  readonly sessionId: string;
  readonly kind: OrdinaryMediaAuthorityKind;
  readonly baseUrl: string;
}

interface SpeechActivationFence {
  readonly sessionId: string;
  readonly securityEpoch: number;
  readonly authorityBaseUrl: string;
  readonly contextGeneration: number;
  readonly activationGeneration: number;
}

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
  private shareState: ActiveShareState = this.shares.state$.value;
  private profile: NetworkProfile = this.profiles.current;
  private computeState: SemanticComputePanelState = EMPTY_COMPUTE;
  private receiverRows: readonly SemanticReceiverPathView[] = Object.freeze([]);
  private ordinaryAudioState: OrdinaryAudioState = this.media.audioState$.value;
  private ordinaryPublications: readonly MediaPublicationView[] = this.mediaPublications.publications$.value;
  private sfuRemoteVideos: readonly SfuRemoteVideoView[] = Object.freeze([]);
  private ordinaryMediaOperationReason = 'ordinary_media_not_started';
  private publicationConsentState = this.publicationConsent.snapshot();
  private speechState: SemanticSpeechTransportState = 'stopped';
  private speechReason = 'semantic_speech_not_started';
  private speechSettings: SemanticSpeechPanelSettings = DEFAULT_SEMANTIC_SPEECH_SETTINGS;
  private speechQuality: SemanticSpeechQualityState = this.speechQualityController.state$.value;
  private evidenceOffer: PeerEvidenceOfferView | null = null;
  private evidenceSync: PeerEvidenceSyncView | null = null;
  private evidenceReason = 'Noch kein Hub-autorisierter Evidence-Offer vorhanden.';
  private consentState: SpeechEvidenceConsentPanelState = EMPTY_CONSENT;
  private runtimeOnline = this.mobileRuntime.online$.value;
  private readonly capabilityStates = new Map<SemanticProgramCapability, SemanticProgramCapabilityView>();
  private computeContextKey = '';
  private reconciliationAuthorizationContextKey = '';
  private reconciliationAuthorizationGeneration = 0;
  private reconciliationAuthorizationExpiryTimer: ReturnType<typeof setTimeout> | null = null;
  private speechAdapterRows: readonly SpeechAdapterMetadata[] = Object.freeze([]);
  private speechAdapterContextKey = '';
  private speechAdapterGeneration = 0;
  private activeSpeechAdapter: SpeechAdapterMetadata | null = null;
  private speechAdapterValidationTimer: ReturnType<typeof setTimeout> | null = null;
  private publicMediaReadySessionId = '';
  private publicMediaFailureCleanupSessionId = '';
  private authorityContextGeneration = 0;
  private speechActivationGeneration = 0;

  readonly view$ = new BehaviorSubject<SemanticMediaProgramHostView>(this.buildView());

  constructor() {
    this.subscriptions.add(this.shares.state$.subscribe(state => {
      const previousSessionId = this.shareState.session?.id ?? '';
      const previousEpoch = this.shareState.session?.security_epoch ?? 0;
      const previousAuthority = this.boundAuthority();
      this.shareState = state;
      if (
        previousSessionId !== (state.session?.id ?? '')
        || previousEpoch !== (state.session?.security_epoch ?? 0)
      ) this.authorityContextGeneration += 1;
      if (previousSessionId && previousSessionId !== (state.session?.id ?? '')) {
        this.stopSessionScopedState(previousSessionId, previousAuthority === 'public');
      }
      this.syncContext();
      this.emit();
    }));
    this.subscriptions.add(this.profiles.profile$.subscribe(profile => {
      this.profile = profile;
      this.syncContext();
      this.emit();
    }));
    this.subscriptions.add(this.transport.mode$.subscribe(() => this.emit()));
    this.subscriptions.add(this.mobileRuntime.online$.subscribe(online => {
      this.runtimeOnline = online;
      if (!online) this.clearReconciliationAuthorization();
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
      this.syncPublicationConsentBinding();
      this.reconcilePublicMediaStatus(status);
    }));
    this.subscriptions.add(this.publicationConsent.state$.subscribe(state => {
      this.publicationConsentState = state;
      this.projectPublicPublicationConsent();
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
        this.clearReconciliationAuthorization();
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
    const intentSessionId = this.shareState.session?.id ?? '';
    const current = this.capability(intent.capability);
    if (current.requestId && current.requestId !== intent.requestId) return;
    if (intent.capability === 'ordinary_media' && this.boundAuthority() === 'public') {
      if (intent.desired === 'activate') {
        await this.grantOrdinaryMediaPublicationConsent({ kind: 'session' });
      } else {
        await this.revokeOrdinaryMediaPublicationConsent();
      }
      return;
    }
    if (intent.capability !== 'ordinary_media' && !this.hasHubAuthority()) {
      this.setCapability(
        intent.capability,
        'failed',
        null,
        'semantic_program_hub_authority_required',
      );
      return;
    }
    const activationAvailable = intent.capability === 'ordinary_media'
      ? this.ordinaryMediaActivationAvailable()
      : this.hubOperationsAvailable();
    if (intent.desired === 'activate' && !activationAvailable) {
      this.setCapability(
        intent.capability,
        'failed',
        null,
        intent.capability === 'ordinary_media'
          ? this.ordinaryCaptureReason()
          : this.hubOperationUnavailableReason(),
      );
      return;
    }
    this.setCapability(
      intent.capability,
      intent.desired === 'activate'
        ? intent.capability === 'ordinary_media' && this.boundAuthority() === 'public'
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
        (this.shareState.session?.id ?? '') !== intentSessionId
        || this.capability(intent.capability).requestId !== intent.requestId
      ) return;
      this.setCapability(
        intent.capability,
        activatedState,
        null,
        intent.capability === 'ordinary_media' && activatedState === 'degraded'
          ? this.ordinaryCaptureReason()
          : null,
      );
    } catch (error) {
      if (
        (this.shareState.session?.id ?? '') !== intentSessionId
        || this.capability(intent.capability).requestId !== intent.requestId
      ) return;
      const reasonCode = reason(error, 'semantic_program_activation_failed');
      if (intent.capability === 'adapter_activation') {
        this.clearSpeechAdapterActivation(reasonCode);
      }
      this.setCapability(intent.capability, 'failed', null, reasonCode);
      if (intent.capability === 'live_speech') {
        this.speechState = 'failed';
        this.speechReason = reason(error, 'semantic_speech_activation_failed');
      }
      this.emit();
    }
  }

  async grantOrdinaryMediaPublicationConsent(
    term: PublicPairMediaPublicationConsentTerm,
  ): Promise<void> {
    if (this.boundAuthority() !== 'public') return;
    this.syncPublicationConsentBinding();
    try {
      this.publicationConsentState = await this.publicationConsent.grant(term);
    } catch (error) {
      this.ordinaryMediaOperationReason = reason(error, 'public_media_publication_consent_grant_failed');
    }
    this.projectPublicPublicationConsent();
    this.emit();
  }

  async revokeOrdinaryMediaPublicationConsent(): Promise<void> {
    if (this.boundAuthority() !== 'public') return;
    try {
      this.publicationConsentState = await this.publicationConsent.revoke(
        'public_media_publication_consent_revoked',
      );
    } catch (error) {
      this.ordinaryMediaOperationReason = reason(error, 'public_media_publication_consent_revoke_failed');
    }
    // Publication consent is intentionally independent of the keyed media
    // transport. The consent service and media owners close only local
    // outbound slots; remote rendering, DataChannel and Pair login survive.
    this.projectPublicPublicationConsent();
    this.emit();
  }

  async startSpeech(): Promise<void> {
    if (this.speechState === 'starting' || this.speechState === 'active') return;
    if (this.speechSettings.paused || this.speechSettings.ordinaryAudioOverride) return;
    if (!this.hasHubAuthority()) {
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
      this.requireHubOperationAuthority();
      const binding = this.peerKeys.requireBinding(true);
      const session = this.requireSession();
      const fence = this.speechActivationFence(session, activationGeneration);
      if (binding.scopeId !== session.id || !this.profile.semantic_media_feature_flags.semantic_speech_runtime) {
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
      this.clearSpeechAdapterActivation(reasonCode, false);
      return;
    }
    this.speechState = 'stopping';
    this.speechReason = reasonCode;
    this.emit();
    void this.speechProducer.stop(reasonCode);
    this.speech.stop();
    this.speechRuntime.stop(reasonCode);
    this.clearSpeechAdapterActivation(reasonCode, false);
    this.speechState = 'stopped';
    this.speechReason = reasonCode;
    this.emit();
  }

  handleSpeechSettings(settings: SemanticSpeechPanelSettings): void {
    if (!this.hasHubAuthority()) return;
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
    if (!this.hasHubAuthority()) return;
    if (intent.kind === 'activate' && !this.hubOnline()) return;
    void this.compute.handleIntent(intent);
  }

  requestComputeSuggestion(): void {
    if (!this.hasHubAuthority()) return;
    void this.compute.requestSuggestion();
  }

  async handleReceiverPathIntent(intent: SemanticReceiverPathIntent): Promise<void> {
    if (!this.hasHubAuthority()) {
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
      this.requireOrdinaryCaptureAuthorization(false);
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
      const authorization = this.mediaPublicationAuthorization(source);
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
      this.requireOrdinaryCaptureAuthorization(true);
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
    if (!this.hasHubAuthority()) return;
    void this.consent.handle(intent);
  }

  handleEvidencePropose(intent: PeerEvidenceProposalIntent): void {
    if (this.hasHubAuthority()) void this.evidenceFlow.propose(intent);
  }
  handleEvidenceAccept(dataClasses: readonly string[]): void {
    if (this.hasHubAuthority()) void this.evidenceFlow.accept(dataClasses);
  }
  pauseEvidence(): void {
    if (this.hasHubAuthority()) this.evidenceFlow.pause();
  }
  resumeEvidence(): void {
    if (this.hasHubAuthority()) void this.evidenceFlow.resume();
  }
  rejectEvidence(): void {
    if (this.hasHubAuthority()) void this.evidenceFlow.reject();
  }
  revokeEvidence(): void {
    if (this.hasHubAuthority()) void this.evidenceFlow.revoke();
  }
  requestEvidenceCuration(): void {
    if (this.hasHubAuthority()) void this.evidenceFlow.requestHubCuration();
  }
  handleEvidenceLocalOverride(value: { regionId: string; candidateId: string }): void {
    if (this.hasHubAuthority()) this.evidenceFlow.localOverride(value.regionId, value.candidateId);
  }

  ngOnDestroy(): void {
    this.stopSessionScopedState(
      this.shareState.session?.id ?? '',
      this.boundAuthority() === 'public',
    );
    this.subscriptions.unsubscribe();
    this.view$.complete();
  }

  private async activate(
    intent: SemanticProgramIntent,
  ): Promise<'authoritatively_active' | 'degraded'> {
    const capability = intent.capability;
    const session = this.requireSession();
    const flags = this.profile.semantic_media_feature_flags;
    if (capability === 'ordinary_media') {
      if (this.transport.mode$.value !== 'webrtc') throw new Error('ordinary_media_webrtc_transport_required');
      const authority = this.boundAuthority();
      if (authority === 'unbound') throw new Error('ordinary_media_session_binding_missing');
      if (authority === 'hub' && !flags.ordinary_media_publication) {
        throw new Error('ordinary_media_publication_disabled');
      }
      if (authority === 'public') {
        this.ordinaryMediaPolicy.assertActivationAllowed(session.id);
        const status = await this.pairMediaE2ee.activate(session.id);
        if (this.shareState.session?.id !== session.id || this.boundAuthority() !== 'public') {
          this.pairMediaE2ee.deactivate(session.id, 'ordinary_media_activation_stale');
          throw new Error('ordinary_media_activation_stale');
        }
        if (status.sessionId !== session.id) {
          this.pairMediaE2ee.deactivate(session.id, 'public_ordinary_media_e2ee_context_mismatch');
          throw new Error('public_ordinary_media_e2ee_context_mismatch');
        }
        if (status.state === 'awaiting-peer' || status.state === 'negotiating') {
          this.ordinaryMediaOperationReason = this.ordinaryMediaPolicyReason(session.id)
            || PUBLIC_ORDINARY_MEDIA_E2EE_UNAVAILABLE;
          return 'degraded';
        }
        if (status.state !== 'ready') {
          throw new Error(status.reasonCode || this.ordinaryMediaPolicyReason(session.id)
            || PUBLIC_ORDINARY_MEDIA_E2EE_UNAVAILABLE);
        }
        this.publicMediaReadySessionId = session.id;
        this.publicMediaFailureCleanupSessionId = '';
      }
      this.ordinaryMediaPolicy.assertAllowed(session.id);
      return 'authoritatively_active';
    }
    this.requireHubOperationAuthority();
    if (!this.hubUrl()) throw new Error('semantic_program_hub_missing');
    if (capability === 'live_speech') {
      if (!flags.semantic_speech_runtime) throw new Error('semantic_speech_disabled');
      await this.startSpeech();
      if (this.speechState !== 'active') throw new Error(this.speechReason);
      return 'authoritatively_active';
    }
    if (capability === 'evidence_text') {
      if (!flags.peer_evidence_sync) throw new Error('peer_evidence_sync_disabled');
      this.requireActiveEvidenceConsent(session);
      this.syncEvidenceContext();
      await this.evidenceFlow.activate();
      return 'authoritatively_active';
    }
    if (capability === 'speech_reconciliation') {
      if (!flags.speech_reconciliation) throw new Error('speech_reconciliation_disabled');
      const reconciliationConsent = this.requireActiveReconciliationConsent(session);
      const contextKey = this.reconciliationContextKey();
      const hubUrl = this.hubUrl();
      if (!contextKey || !hubUrl) throw new Error('speech_reconciliation_hub_context_missing');
      const generation = ++this.reconciliationAuthorizationGeneration;
      this.reconciliationAuthorizationContextKey = '';
      await firstValueFrom(
        this.reconciliationApi.list(hubUrl, 0, 1).pipe(
          take(1),
          timeout({ first: 10_000 }),
        ),
      );
      if (
        generation !== this.reconciliationAuthorizationGeneration
        || contextKey !== this.reconciliationContextKey()
        || !this.hubOnline()
        || !this.profile.semantic_media_feature_flags.speech_reconciliation
      ) throw new Error('speech_reconciliation_authorization_stale');
      this.reconciliationAuthorizationContextKey = contextKey;
      this.armReconciliationAuthorizationExpiry(reconciliationConsent.consent.expires_at_ms);
      return 'authoritatively_active';
    }
    if (capability === 'adapter_activation') {
      if (!flags.speech_adapter_routing) throw new Error('speech_adapter_routing_disabled');
      if (this.speechState !== 'active') throw new Error('semantic_speech_runtime_not_started');
      const adapterId = String(intent.adapterId || '');
      const direction = intent.direction;
      if (!adapterId || !direction) throw new Error('speech_adapter_explicit_selection_required');
      const selected = this.speechAdapterRows.find(
        row => row.adapter_id === adapterId && row.direction === direction,
      );
      if (!selected) throw new Error('speech_adapter_selection_stale');
      const current = await firstValueFrom(
        this.speechAdaptersApi.get(this.hubUrl(), adapterId, session.id, direction).pipe(
          take(1),
          timeout({ first: 10_000 }),
        ),
      );
      this.assertActivatableAdapter(current, selected, session.id);
      await this.speechRuntime.activatePersonalization({
        metadata: current,
        context: {
          pairId: current.pair_id,
          direction: current.direction,
          speakerDigest: current.speaker_digest,
          scopeDigest: current.scope_digest,
          baseModelId: current.base_model_id,
          baseModelDigest: current.base_model_digest,
          consentDigest: current.consent_digest,
        },
      });
      this.activeSpeechAdapter = current;
      this.armSpeechAdapterValidation();
      return 'authoritatively_active';
    }
    throw new Error('semantic_program_capability_endpoint_unavailable');
  }

  private deactivate(capability: SemanticProgramCapability, _state: SemanticProgramState): void {
    if (capability === 'ordinary_media') {
      const sessionId = this.shareState.session?.id ?? '';
      const authority = this.boundAuthority();
      if (sessionId && authority === 'public') {
        this.publicMediaReadySessionId = '';
        this.publicMediaFailureCleanupSessionId = '';
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
    if (capability === 'speech_reconciliation') this.clearReconciliationAuthorization();
    if (capability === 'adapter_activation') this.clearSpeechAdapterActivation('speech_adapter_user_revoked');
  }

  private syncContext(): void {
    const session = this.shareState.session;
    const authority = this.boundAuthority();
    const hubAuthority = authority === 'hub';
    this.syncPublicationConsentBinding();
    this.syncReceiverPaths();
    const senderId = this.shares.currentUserId;
    const hubUrl = this.hubUrl();
    const epoch = session?.security_epoch ?? 0;
    const hubContextAvailable = Boolean(hubAuthority && session && senderId && hubUrl && epoch > 0);
    const key = hubContextAvailable ? `${session!.id}\x1f${epoch}\x1f${senderId}` : '';
    const binding = this.peerKeys.currentBinding;
    const participants = this.shareState.participants
      .filter(participant => !participant.revoked_at && participant.user_id !== senderId)
      .map(participant => participant.user_id);
    const pairContextValid = Boolean(
      hubAuthority && session && senderId && hubUrl && epoch > 0 && binding?.confirmed
      && binding.scopeId === session!.id && binding.epoch === epoch
      && binding.localPeerId === senderId && participants.includes(binding.remotePeerId),
    );
    this.consent.bind(pairContextValid ? {
      hubUrl,
      tenantId: binding!.tenantId,
      sessionId: session!.id,
      epoch,
      localPeerId: senderId,
      remotePeerId: binding!.remotePeerId,
    } : null);
    this.syncEvidenceContext();
    this.refreshSpeechAdapters();
    this.sfuCoordinator.bind(hubContextAvailable ? {
      hubUrl,
      tenantId: String(session!.tenant_id || binding?.tenantId || ''),
      sessionId: session!.id,
      membershipEpoch: epoch,
      localPeerId: senderId,
      remotePeerIds: Object.freeze(participants),
      featureEnabled: this.profile.semantic_media_feature_flags.semantic_media_sfu,
    } : null);
    if (key === this.computeContextKey) return;
    this.computeContextKey = key;
    this.compute.bind(key ? {
      hubUrl,
      sessionId: session!.id,
      epoch,
      senderId,
      consentVersion: Math.max(1, session!.permissions_version ?? 1),
    } : null);
  }

  private syncReceiverPaths(): void {
    const hubAuthority = this.hasHubAuthority();
    const participants = this.shareState.participants
      .filter(participant => !participant.revoked_at && participant.user_id !== this.shares.currentUserId)
      .map(participant => ({ receiverId: participant.user_id, label: participant.user_id }));
    this.receiverPaths.setReceivers(participants);
    this.receiverPaths.setHubState({
      sfuConnected: hubAuthority && this.sfu.currentState().status === 'connected',
      sfuAuthorizedReceiverIds: hubAuthority ? this.sfu.authorizedSubscriberIds() : new Set<string>(),
      sfuFeatureEnabled: hubAuthority && this.profile.semantic_media_feature_flags.semantic_media_sfu,
    });
  }

  private stopSessionScopedState(sessionId = '', deactivatePublicMedia = false): void {
    if (sessionId && deactivatePublicMedia) {
      this.publicMediaReadySessionId = '';
      this.publicMediaFailureCleanupSessionId = '';
      this.pairMediaE2ee.deactivate(sessionId, 'ordinary_media_session_ended');
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
    this.clearReconciliationAuthorization();
    this.clearSpeechAdapterActivation('speech_adapter_session_ended');
    this.speechAdapterRows = Object.freeze([]);
    this.speechAdapterContextKey = '';
    this.speechAdapterGeneration += 1;
    this.receiverPaths.clear();
    this.capabilityStates.clear();
  }

  private setCapability(
    capability: SemanticProgramCapability,
    state: SemanticProgramState,
    requestId: string | null,
    reasonCode: string | null = null,
  ): void {
    this.capabilityStates.set(capability, Object.freeze({
      capability,
      label: LABELS[capability],
      sensitive: SENSITIVE.has(capability),
      state,
      requestId,
      ...(reasonCode ? { reasonCode } : {}),
    }));
    this.emit();
  }

  private capability(capability: SemanticProgramCapability): SemanticProgramCapabilityView {
    const stored = this.capabilityStates.get(capability);
    let projected = stored ?? this.defaultCapability(capability);
    if (
      stored?.state === 'authoritatively_active'
      && capability === 'ordinary_media'
      && !this.ordinaryMediaActive()
    ) projected = Object.freeze({ ...stored, state: 'degraded' });
    if (
      stored?.state === 'authoritatively_active'
      && capability === 'adapter_activation'
      && !this.activeSpeechAdapter
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
        ...(consent ? { scope: scopeForConsent(consent, this.shareState.session) } : {}),
      });
    }
    return projected;
  }

  private defaultCapability(capability: SemanticProgramCapability): SemanticProgramCapabilityView {
    const session = this.shareState.session;
    const state: SemanticProgramState = capability === 'ordinary_media'
      && session
      && this.ordinaryMediaActive() ? 'authoritatively_active' : 'revoked';
    const activationReason = capability === 'ordinary_media'
      && this.boundAuthority() === 'public'
      && !this.ordinaryMediaActivationAvailable()
      ? this.ordinaryMediaActivationReason()
      : null;
    return Object.freeze({
      capability,
      label: LABELS[capability],
      sensitive: SENSITIVE.has(capability),
      state,
      requestId: null,
      ...(activationReason ? { reasonCode: activationReason } : {}),
    });
  }

  private buildView(): SemanticMediaProgramHostView {
    const session = this.shareState.session;
    const authority = this.boundAuthority();
    const hubAuthority = authority === 'hub';
    return Object.freeze({
      scope: scopeFor(session),
      capabilities: Object.freeze((Object.keys(LABELS) as SemanticProgramCapability[]).map(value => this.capability(value))),
      online: this.hubOperationsAvailable(),
      hubUrl: hubAuthority ? this.hubUrl() : '',
      ordinaryMediaAuthority: authority,
      ordinaryMediaActivationEnabled: this.ordinaryMediaActivationAvailable(),
      computeVisible: Boolean(
        hubAuthority && session && this.shares.currentUserId && (session.security_epoch ?? 0) > 0
      ),
      compute: this.computeState,
      receiverPaths: this.receiverRows,
      ordinaryMediaCaptureEnabled: this.ordinaryCaptureAllowed(false),
      ordinaryMediaVideoCaptureEnabled: this.ordinaryCaptureAllowed(true),
      ordinaryMediaE2eeReady: authority === 'public'
        && Boolean(session?.id) && this.ordinaryMediaPolicy.allows(session!.id),
      ordinaryMediaReason: this.ordinaryCaptureReason(),
      ordinaryMediaPublicationConsent: this.publicationConsentState,
      ordinaryAudioState: this.ordinaryAudioState,
      ordinaryMediaPublications: this.ordinaryPublications,
      sfuRemoteVideos: hubAuthority ? this.sfuRemoteVideos : Object.freeze([]),
      speechTransportState: this.speechState,
      speechTransportReason: this.speechReason,
      speechTransportCanStart: this.canStartSpeech(),
      speechSettings: this.speechSettings,
      speechQuality: this.speechQuality,
      speechReconciliationHubAuthorized: this.reconciliationHubAuthorized(),
      speechAdapters: Object.freeze(this.speechAdapterRows.map(row => Object.freeze({
        adapterId: row.adapter_id,
        direction: row.direction,
        label: `${row.adapter_id} · ${row.base_model_id}`,
        expiresAtMs: Math.min(row.expires_at_ms, row.consent_expires_at_ms),
      }))),
      evidenceOffer: this.evidenceOffer,
      evidenceSync: this.evidenceSync,
      evidenceAvailableReason: this.evidenceReason,
      evidenceConsent: this.consentState,
    });
  }

  private emit(): void { this.view$.next(this.buildView()); }

  private hubOnline(): boolean { return Boolean(this.hubUrl()) && this.runtimeOnline; }

  /** Exact session binding is the sole router between Public and Hub media operations. */
  private boundAuthorityRoute(): BoundSemanticMediaAuthorityRoute {
    const sessionId = this.shareState.session?.id ?? '';
    if (!sessionId) return Object.freeze({ sessionId: '', kind: 'unbound', baseUrl: '' });
    try {
      const route = this.pairControlPlane.authorityRouteForSession(sessionId);
      const baseUrl = String(route.baseUrl || '').trim().replace(/\/+$/, '');
      if (route.kind === 'hub' && !baseUrl) throw new Error('semantic_program_hub_missing');
      return Object.freeze({
        sessionId,
        kind: route.kind,
        baseUrl: route.kind === 'hub' ? baseUrl : '',
      });
    } catch {
      return Object.freeze({ sessionId, kind: 'unbound', baseUrl: '' });
    }
  }

  private boundAuthority(): OrdinaryMediaAuthorityKind { return this.boundAuthorityRoute().kind; }

  private hasHubAuthority(): boolean { return this.boundAuthority() === 'hub'; }

  private hubOperationsAvailable(): boolean { return this.hasHubAuthority() && this.hubOnline(); }

  private hubOperationUnavailableReason(): string {
    return this.hasHubAuthority()
      ? 'semantic_program_hub_offline'
      : 'semantic_program_hub_authority_required';
  }

  private requireHubOperationAuthority(): void {
    this.requireSession();
    if (!this.hasHubAuthority()) throw new Error('semantic_program_hub_authority_required');
  }

  private speechActivationFence(
    session: ShareSession,
    activationGeneration: number,
  ): SpeechActivationFence {
    const authority = this.boundAuthorityRoute();
    if (authority.kind !== 'hub' || !authority.baseUrl) {
      throw new Error('semantic_program_hub_authority_required');
    }
    return Object.freeze({
      sessionId: session.id,
      securityEpoch: session.security_epoch ?? 0,
      authorityBaseUrl: authority.baseUrl,
      contextGeneration: this.authorityContextGeneration,
      activationGeneration,
    });
  }

  private assertSpeechActivationFence(fence: SpeechActivationFence): void {
    const session = this.shareState.session;
    const authority = this.boundAuthorityRoute();
    if (
      fence.activationGeneration !== this.speechActivationGeneration
      || fence.contextGeneration !== this.authorityContextGeneration
      || session?.id !== fence.sessionId
      || (session.security_epoch ?? 0) !== fence.securityEpoch
      || authority.kind !== 'hub'
      || authority.baseUrl !== fence.authorityBaseUrl
    ) throw new Error('semantic_speech_activation_stale');
  }

  private ordinaryMediaActivationAvailable(): boolean {
    const session = this.shareState.session;
    if (
      !session
      || session.revoked_at !== null
      || (session.expires_at ?? Number.MAX_SAFE_INTEGER) * 1_000 <= Date.now()
    ) return false;
    const authority = this.boundAuthority();
    return authority === 'public'
      ? this.ordinaryMediaPolicy.canActivate(session.id)
      : authority === 'hub' && this.hubOnline();
  }

  private ordinaryMediaActivationReason(): string {
    const session = this.shareState.session;
    if (
      !session
      || session.revoked_at !== null
      || (session.expires_at ?? Number.MAX_SAFE_INTEGER) * 1_000 <= Date.now()
    ) return 'ordinary_media_session_missing';
    try {
      this.ordinaryMediaPolicy.assertActivationAllowed(session.id);
      return 'ordinary_media_activation_required';
    } catch (error) {
      return reason(error, PUBLIC_ORDINARY_MEDIA_E2EE_UNAVAILABLE);
    }
  }

  private syncPublicationConsentBinding(): void {
    const session = this.shareState.session;
    if (!session || this.boundAuthority() !== 'public') {
      this.publicationConsent.bind(null);
      this.publicationConsentState = this.publicationConsent.snapshot();
      return;
    }
    let context: PublicPairMediaPublicationContext | null = null;
    try {
      context = this.pairMediaE2ee.publicationContextFor(session.id);
    } catch {
      context = null;
    }
    this.publicationConsent.bind(context);
    this.publicationConsentState = this.publicationConsent.snapshot();
  }

  private publicPublicationConsentGranted(sessionId: string): boolean {
    const value = this.publicationConsentState;
    return value.status === 'granted'
      && value.binding?.sessionId === sessionId
      && value.binding.securityEpoch === (this.shareState.session?.security_epoch ?? 0)
      && value.expiresAtMs !== null
      && value.expiresAtMs > Date.now();
  }

  private projectPublicPublicationConsent(): void {
    const sessionId = this.shareState.session?.id ?? '';
    if (!sessionId || this.boundAuthority() !== 'public') return;
    const consent = this.publicationConsentState;
    if (consent.status === 'granted') {
      const ready = this.ordinaryMediaPolicy.allows(sessionId);
      if (ready) {
        this.publicMediaReadySessionId = sessionId;
        this.publicMediaFailureCleanupSessionId = '';
      }
      this.setCapability(
        'ordinary_media',
        ready ? 'authoritatively_active' : 'degraded',
        null,
        ready ? null : 'public_media_technical_preparation_pending',
      );
      return;
    }
    if (consent.status === 'granting') {
      this.setCapability('ordinary_media', 'sent_to_authority', null);
      return;
    }
    if (consent.status === 'revoking') {
      this.setCapability('ordinary_media', 'pausing', null);
      return;
    }
    const state: SemanticProgramState = consent.status === 'expired'
      ? 'expired' : consent.status === 'failed' ? 'failed' : 'revoked';
    const reasonCode = consent.reasonCode
      || (consent.status === 'expired'
        ? 'public_media_publication_consent_expired'
        : 'public_media_publication_consent_required');
    this.setCapability('ordinary_media', state, null, reasonCode);
  }

  private reconcilePublicMediaStatus(status: PublicPairMediaE2eeState): void {
    const sessionId = this.shareState.session?.id ?? '';
    const current = this.capabilityStates.get('ordinary_media');
    if (!sessionId || status.sessionId !== sessionId || this.boundAuthority() !== 'public') {
      this.emit();
      return;
    }
    if (status.state === 'ready') {
      this.publicMediaReadySessionId = sessionId;
      this.publicMediaFailureCleanupSessionId = '';
      if (this.publicationConsentState.status !== 'granted') {
        this.projectPublicPublicationConsent();
        return;
      }
      if (
        !current
        || current.state === 'sent_to_authority'
        || current.state === 'degraded'
        || current.state === 'authoritatively_active'
      ) {
        this.setCapability('ordinary_media', 'authoritatively_active', null);
        return;
      }
    }
    if (
      status.state !== 'ready'
      && this.publicMediaReadySessionId === sessionId
      && current?.state !== 'revoked'
    ) {
      const failureReason = status.reasonCode || this.ordinaryMediaPolicyReason(sessionId)
        || PUBLIC_ORDINARY_MEDIA_E2EE_UNAVAILABLE;
      if (this.publicMediaFailureCleanupSessionId !== sessionId) {
        this.publicMediaFailureCleanupSessionId = sessionId;
        this.media.stopAudio(failureReason);
        this.mediaPublications.stopAll(failureReason, true);
      }
      this.publicMediaReadySessionId = '';
      this.setCapability(
        'ordinary_media',
        status.state === 'failed' || status.state === 'inactive' ? 'failed' : 'degraded',
        null,
        failureReason,
      );
      return;
    }
    if (
      (status.state === 'awaiting-peer' || status.state === 'negotiating' || status.state === 'awaiting-security')
      && (current?.state === 'sent_to_authority' || current?.state === 'authoritatively_active')
    ) {
      this.setCapability(
        'ordinary_media',
        'degraded',
        current?.requestId ?? null,
        status.reasonCode || this.ordinaryMediaPolicyReason(sessionId),
      );
      return;
    }
    this.emit();
  }

  private reconciliationContextKey(): string {
    if (!this.hasHubAuthority()) return '';
    const session = this.shareState.session;
    const localPeerId = this.shares.currentUserId;
    const hubUrl = this.hubUrl();
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
      && this.hubOnline()
      && this.profile.semantic_media_feature_flags.speech_reconciliation
      && contextKey === this.reconciliationAuthorizationContextKey
    );
  }

  private clearReconciliationAuthorization(): void {
    this.reconciliationAuthorizationGeneration += 1;
    this.reconciliationAuthorizationContextKey = '';
    if (this.reconciliationAuthorizationExpiryTimer !== null) {
      globalThis.clearTimeout(this.reconciliationAuthorizationExpiryTimer);
      this.reconciliationAuthorizationExpiryTimer = null;
    }
  }

  private refreshSpeechAdapters(): void {
    const session = this.shareState.session;
    const hubUrl = this.hubUrl();
    const contextKey = this.hasHubAuthority()
      && session && hubUrl && this.profile.semantic_media_feature_flags.speech_adapter_routing
      ? `${hubUrl}\x1f${session.id}\x1f${session.security_epoch ?? 0}`
      : '';
    if (contextKey === this.speechAdapterContextKey) return;
    this.speechAdapterContextKey = contextKey;
    const generation = ++this.speechAdapterGeneration;
    if (!contextKey || !session) {
      this.speechAdapterRows = Object.freeze([]);
      this.clearSpeechAdapterActivation('speech_adapter_context_missing');
      this.emit();
      return;
    }
    void firstValueFrom(forkJoin([
      this.speechAdaptersApi.list(hubUrl, session.id, 'sender_to_receiver'),
      this.speechAdaptersApi.list(hubUrl, session.id, 'receiver_to_sender'),
    ]).pipe(take(1), timeout({ first: 10_000 }))).then(pages => {
      if (generation !== this.speechAdapterGeneration || contextKey !== this.speechAdapterContextKey) return;
      const now = Date.now();
      this.speechAdapterRows = Object.freeze(pages.flatMap(page => page.items).filter(row =>
        row.pair_id === session.id
        && row.status === 'approved'
        && now < Math.min(row.expires_at_ms, row.consent_expires_at_ms)
      ));
      if (this.activeSpeechAdapter && !this.speechAdapterRows.some(row =>
        row.adapter_id === this.activeSpeechAdapter?.adapter_id
        && row.direction === this.activeSpeechAdapter.direction
        && row.registry_version === this.activeSpeechAdapter.registry_version
      )) this.clearSpeechAdapterActivation('speech_adapter_authority_changed');
      this.emit();
    }).catch(error => {
      if (generation !== this.speechAdapterGeneration) return;
      this.speechAdapterRows = Object.freeze([]);
      this.clearSpeechAdapterActivation(reason(error, 'speech_adapter_registry_unavailable'));
      this.emit();
    });
  }

  private assertActivatableAdapter(
    current: SpeechAdapterMetadata,
    selected: SpeechAdapterMetadata,
    pairId: string,
  ): void {
    const now = Date.now();
    if (
      current.adapter_id !== selected.adapter_id
      || current.pair_id !== pairId
      || current.direction !== selected.direction
      || current.registry_version !== selected.registry_version
      || current.artifact_sha256 !== selected.artifact_sha256
      || current.status !== 'approved'
      || now >= Math.min(current.expires_at_ms, current.consent_expires_at_ms)
    ) throw new Error('speech_adapter_selection_stale');
  }

  private armSpeechAdapterValidation(): void {
    if (this.speechAdapterValidationTimer !== null) globalThis.clearTimeout(this.speechAdapterValidationTimer);
    const current = this.activeSpeechAdapter;
    if (!current) return;
    const delay = Math.max(1, Math.min(15_000, current.expires_at_ms - Date.now(), current.consent_expires_at_ms - Date.now()));
    this.speechAdapterValidationTimer = globalThis.setTimeout(() => {
      this.speechAdapterValidationTimer = null;
      void this.validateActiveSpeechAdapter();
    }, delay);
  }

  private async validateActiveSpeechAdapter(): Promise<void> {
    const expected = this.activeSpeechAdapter;
    if (!expected) return;
    if (!this.hasHubAuthority()) {
      this.clearSpeechAdapterActivation('speech_adapter_hub_authority_lost');
      this.setCapability(
        'adapter_activation', 'revoked', null, 'speech_adapter_hub_authority_lost',
      );
      return;
    }
    try {
      const current = await firstValueFrom(
        this.speechAdaptersApi.get(this.hubUrl(), expected.adapter_id, expected.pair_id, expected.direction).pipe(
          take(1), timeout({ first: 10_000 }),
        ),
      );
      this.assertActivatableAdapter(current, expected, expected.pair_id);
      if (this.activeSpeechAdapter !== expected) return;
      this.activeSpeechAdapter = current;
      await this.speechRuntime.cleanupPersonalization();
      this.armSpeechAdapterValidation();
    } catch (error) {
      if (this.activeSpeechAdapter !== expected) return;
      this.clearSpeechAdapterActivation(reason(error, 'speech_adapter_authority_changed'));
      this.setCapability(
        'adapter_activation', 'revoked', null,
        reason(error, 'speech_adapter_authority_changed'),
      );
    }
    this.emit();
  }

  private clearSpeechAdapterActivation(reasonCode: string, notifyRuntime = true): void {
    const current = this.activeSpeechAdapter;
    this.activeSpeechAdapter = null;
    if (this.speechAdapterValidationTimer !== null) {
      globalThis.clearTimeout(this.speechAdapterValidationTimer);
      this.speechAdapterValidationTimer = null;
    }
    if (current && notifyRuntime) void this.speechRuntime.revokePersonalization(current.adapter_id);
    if (reasonCode === 'speech_adapter_browser_engine_not_released') {
      this.setCapability('adapter_activation', 'failed', null, reasonCode);
    }
  }

  private armReconciliationAuthorizationExpiry(expiresAtMs: number): void {
    if (this.reconciliationAuthorizationExpiryTimer !== null) {
      globalThis.clearTimeout(this.reconciliationAuthorizationExpiryTimer);
    }
    const delay = Math.max(1, Math.min(2_147_483_647, expiresAtMs - Date.now()));
    this.reconciliationAuthorizationExpiryTimer = globalThis.setTimeout(() => {
      this.clearReconciliationAuthorization();
      this.emit();
    }, delay);
  }

  private activeReconciliationConsent(
    session: ShareSession | null,
  ): SpeechEvidenceConsentReadModel | null {
    const readModel = this.consentState.consent;
    const consent = readModel?.consent;
    const binding = this.peerKeys.currentBinding;
    const localPeerId = this.shares.currentUserId;
    if (
      !session || !readModel || !consent || !binding?.confirmed || !localPeerId
      || consent.state !== 'active' || consent.expires_at_ms <= Date.now()
      || consent.tenant_id !== binding.tenantId
      || consent.owner_subject !== localPeerId
      || consent.session_id !== session.id || consent.pair_id !== session.id
      || consent.session_epoch !== (session.security_epoch ?? 0)
      || consent.purpose !== 'speech_reconciliation'
      || !consent.data_classes.includes('audio')
      || consent.grants.raw_audio_share !== true
      || consent.grants.dataset_import !== true
      || !consent.required_signers.includes(binding.localPeerId)
      || !consent.required_signers.includes(binding.remotePeerId)
    ) return null;
    return readModel;
  }

  private requireActiveReconciliationConsent(session: ShareSession): SpeechEvidenceConsentReadModel {
    const consent = this.activeReconciliationConsent(session);
    if (!consent) throw new Error('speech_reconciliation_consent_stale_or_narrow');
    return consent;
  }

  private reconciliationConsentClaimKey(
    value: SpeechEvidenceConsentReadModel | null = this.consentState.consent,
  ): string {
    const consent = value?.consent;
    if (!value || !consent) return '';
    return [
      value.consentDigest,
      value.scopeDigest,
      consent.consent_id,
      consent.consent_version,
      consent.revocation_epoch,
      consent.state,
      consent.expires_at_ms,
      consent.purpose,
      [...consent.data_classes].sort().join(','),
      consent.grants.raw_audio_share ? 1 : 0,
      consent.grants.dataset_import ? 1 : 0,
      consent.grants.training ? 1 : 0,
      [...consent.trainer_locations].sort().join(','),
    ].join('\x1f');
  }

  private canStartSpeech(): boolean {
    if (
      !this.hasHubAuthority()
      || !this.shareState.session || !this.profile.semantic_media_feature_flags.semantic_speech_runtime
      || this.speechSettings.paused || this.speechSettings.ordinaryAudioOverride
    ) return false;
    const binding = this.peerKeys.currentBinding;
    return Boolean(binding?.confirmed && binding.scopeId === this.shareState.session.id);
  }

  private ordinaryMediaActive(): boolean {
    const sfuState = typeof this.sfu.currentState === 'function'
      ? this.sfu.currentState()
      : (this.sfu.state$ as unknown as { readonly value?: { readonly status?: string } }).value;
    const sessionId = this.shareState.session?.id ?? '';
    return this.ordinaryMediaPolicy.allows(sessionId)
      && this.transport.mode$.value === 'webrtc'
      && (this.boundAuthority() === 'public'
        || ['active', 'muted'].includes(this.media.audioState$.value.status)
        || sfuState?.status === 'connected');
  }

  private ordinaryCaptureAllowed(video: boolean): boolean {
    const session = this.shareState.session;
    const state = this.capabilityStates.get('ordinary_media')?.state;
    const authority = this.boundAuthority();
    const profileEnabled = authority === 'public'
      || this.profile.semantic_media_feature_flags.ordinary_media_publication;
    return Boolean(
      session && session.revoked_at === null && this.ordinaryMediaActivationAvailable()
      && this.ordinaryMediaPolicy.allows(session.id)
      && this.transport.mode$.value === 'webrtc'
      && profileEnabled
      && (!video || profileEnabled)
      && (authority !== 'public' || this.publicPublicationConsentGranted(session.id))
      && (state === 'authoritatively_active' || state === 'degraded'),
    );
  }

  private requireOrdinaryCaptureAuthorization(video: boolean): void {
    if (!this.ordinaryCaptureAllowed(video)) throw new Error(this.ordinaryCaptureReason());
  }

  private ordinaryCaptureReason(): string {
    const session = this.shareState.session;
    if (
      !session
      || session.revoked_at !== null
      || (session.expires_at ?? Number.MAX_SAFE_INTEGER) * 1_000 <= Date.now()
    ) return 'ordinary_media_session_missing';
    const policyReason = this.ordinaryMediaPolicyReason(session.id);
    if (policyReason) return policyReason;
    const authority = this.boundAuthority();
    if (authority === 'hub' && !this.profile.semantic_media_feature_flags.ordinary_media_publication) {
      return 'ordinary_media_publication_disabled';
    }
    if (this.transport.mode$.value !== 'webrtc') return 'ordinary_media_webrtc_transport_required';
    if (!this.ordinaryMediaActivationAvailable()) {
      return authority === 'public'
        ? this.ordinaryMediaActivationReason()
        : 'ordinary_media_hub_offline';
    }
    if (authority === 'public' && !this.publicPublicationConsentGranted(session.id)) {
      return this.publicationConsentState.reasonCode
        || (this.publicationConsentState.status === 'expired'
          ? 'public_media_publication_consent_expired'
          : 'public_media_publication_consent_required');
    }
    const state = this.capabilityStates.get('ordinary_media')?.state;
    if (state !== 'authoritatively_active' && state !== 'degraded') return 'ordinary_media_activation_required';
    return this.ordinaryMediaOperationReason;
  }

  private ordinaryMediaPolicyReason(sessionId: string): string | null {
    try {
      this.ordinaryMediaPolicy.assertAllowed(sessionId);
      return null;
    } catch (error) {
      return reason(error, PUBLIC_ORDINARY_MEDIA_E2EE_UNAVAILABLE);
    }
  }

  private mediaPublicationAuthorization(source: 'camera' | 'screen'): MediaPublicationAuthorization {
    this.requireOrdinaryCaptureAuthorization(true);
    const session = this.requireSession();
    const nowMs = Date.now();
    const sessionExpiryMs = session.expires_at === null ? nowMs + 8 * 60 * 60 * 1_000 : session.expires_at * 1_000;
    const publicationExpiryMs = this.boundAuthority() === 'public'
      ? this.publicationConsentState.expiresAtMs ?? nowMs
      : nowMs + 8 * 60 * 60 * 1_000;
    const limits = source === 'camera'
      ? { width: 1280, height: 720, fps: 30, bitrate: 1_500_000 }
      : { width: 1920, height: 1080, fps: 20, bitrate: 3_000_000 };
    return Object.freeze({
      publicationId: `ordinary-${source}-${session.security_epoch ?? 1}`,
      sessionId: session.id,
      source,
      permitted: true,
      expiresAtMs: Math.min(sessionExpiryMs, publicationExpiryMs, nowMs + 8 * 60 * 60 * 1_000),
      maxWidth: limits.width,
      maxHeight: limits.height,
      maxFramesPerSecond: limits.fps,
      maxBitrateBps: limits.bitrate,
    });
  }

  private async ensureOrdinaryAudio(fence?: SpeechActivationFence): Promise<void> {
    if (this.transport.mode$.value !== 'webrtc') return;
    const sessionId = this.shareState.session?.id ?? '';
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

  private requireSession(): ShareSession {
    const session = this.shareState.session;
    if (!session || session.revoked_at !== null || (session.expires_at ?? Number.MAX_SAFE_INTEGER) * 1000 <= Date.now()) {
      throw new Error('semantic_program_session_missing');
    }
    return session;
  }

  private requireActiveEvidenceConsent(session: ShareSession) {
    const value = this.consentState.consent?.consent;
    if (!value) throw new Error('speech_evidence_consent_required');
    if (value.state !== 'active' || value.expires_at_ms <= Date.now()) throw new Error('speech_evidence_consent_inactive');
    if (value.session_id !== session.id || value.session_epoch !== (session.security_epoch ?? 0)) {
      throw new Error('speech_evidence_consent_context_mismatch');
    }
    if (!value.grants.transcript_share && !value.grants.feature_share) {
      throw new Error('speech_evidence_share_grant_required');
    }
    return value;
  }

  private syncEvidenceContext(): void {
    const session = this.shareState.session;
    const binding = this.peerKeys.currentBinding;
    const consent = this.consentState.consent;
    const remoteActive = binding && this.shareState.participants.some(participant =>
      participant.user_id === binding.remotePeerId && participant.revoked_at === null);
    if (
      !this.hasHubAuthority()
      || !session
      || !binding?.confirmed
      || !remoteActive
      || binding.scopeId !== session.id
      || binding.epoch !== (session.security_epoch ?? 0)
      || binding.localPeerId !== this.shares.currentUserId
      || !consent
      || consent.consent.state !== 'active'
      || !this.profile.semantic_media_feature_flags.peer_evidence_sync
      || !this.hubUrl()
    ) {
      this.evidenceFlow.bind(null);
      return;
    }
    this.evidenceFlow.bind({
      hubUrl: this.hubUrl(),
      sessionId: session.id,
      pairId: session.id,
      epoch: binding.epoch,
      localPeerId: binding.localPeerId,
      remotePeerId: binding.remotePeerId,
      consent,
    });
  }

  private syncSpeechRuntimeBinding(): void {
    if (this.speechState !== 'active' || !this.hasHubAuthority()) return;
    const session = this.shareState.session;
    const binding = this.peerKeys.currentBinding;
    if (!session || !binding?.confirmed || binding.scopeId !== session.id) return;
    const runtimeContext = this.speechRuntimeContext(binding, session);
    this.speechRuntime.start(runtimeContext);
    this.speechProducer.rebind({ ...runtimeContext, profileId: 'default' });
  }

  private speechRuntimeContext(binding: NonNullable<typeof this.peerKeys.currentBinding>, session: ShareSession) {
    const readModel = this.consentState.consent;
    const consent = readModel?.consent;
    const grants = consent?.grants;
    const classes = new Set(consent?.data_classes ?? []);
    const correctionConsent = consent
      && consent.state === 'active'
      && consent.session_id === session.id
      && consent.session_epoch === binding.epoch
      && consent.expires_at_ms > Date.now()
      && grants?.capture === true
      && grants?.raw_audio_share === true
      && grants?.transcript_share === true
      && classes.has('audio')
      && classes.has('transcript')
      && classes.has('correction')
      && /^[a-f0-9]{64}$/.test(String(readModel?.consentDigest || ''))
      ? Object.freeze({
          consentId: consent.consent_id,
          consentDigest: readModel!.consentDigest,
          consentVersion: consent.consent_version,
          revocationEpoch: consent.revocation_epoch,
          expiresAtMs: consent.expires_at_ms,
        })
      : undefined;
    return Object.freeze({
      hubUrl: this.hubUrl(),
      sessionId: binding.scopeId,
      epoch: binding.epoch,
      localPeerId: binding.localPeerId,
      remotePeerId: binding.remotePeerId,
      consentVersion: Math.max(1, session.permissions_version ?? 1),
      contractDigest: binding.contractDigest,
      ...(correctionConsent ? { correctionConsent } : {}),
    });
  }

  private hubUrl(): string {
    const authority = this.boundAuthorityRoute();
    return authority.kind === 'hub' ? authority.baseUrl : '';
  }
}

function scopeFor(session: ShareSession | null): SemanticProgramScopeView {
  return Object.freeze({
    direction: 'bidirectional',
    dataClass: 'Transcript, semantische Features und separat freigegebene Evidence',
    purpose: 'Live-Kommunikation und explizit freigegebene Sprachverbesserung',
    retentionLabel: 'Vertragsspezifisch; keine implizite Langzeitspeicherung',
    trainerLocation: 'Isolierter lokaler Worker',
    e2eeMode: session?.security_mode || 'strict_e2ee',
    ordinaryFallback: 'Verschlüsseltes Ordinary Media bleibt sicherer Standard',
  });
}

function scopeForConsent(
  consent: SpeechEvidenceConsentDocument,
  session: ShareSession | null,
): SemanticProgramScopeView {
  const trainingEnabled = consent.grants.training === true;
  const granted = [
    consent.grants.raw_audio_share ? 'Roh-Audio' : null,
    consent.grants.dataset_import ? 'Dataset-Import' : null,
    trainingEnabled ? 'Training' : 'Training nicht freigegeben',
  ].filter((value): value is string => Boolean(value));
  return Object.freeze({
    direction: consent.direction,
    dataClass: consent.data_classes.join(', ') || 'Keine Datenklasse',
    purpose: consent.purpose,
    retentionLabel: formatRetention(consent.retention_seconds),
    trainerLocation: trainingEnabled
      ? consent.trainer_locations.join(', ') || 'Kein Trainerstandort gebunden'
      : 'Kein Training freigegeben',
    e2eeMode: session?.security_mode || 'strict_e2ee',
    ordinaryFallback: 'Verschlüsseltes Ordinary Media bleibt sicherer Standard',
    grantLabel: granted.join(', '),
  });
}

function formatRetention(seconds: number): string {
  if (seconds % 86_400 === 0) return `${seconds / 86_400} Tag(e)`;
  if (seconds % 3_600 === 0) return `${seconds / 3_600} Stunde(n)`;
  if (seconds % 60 === 0) return `${seconds / 60} Minute(n)`;
  return `${seconds} Sekunde(n)`;
}

function mediaPreference(source: 'camera' | 'screen'): UserMediaPreference {
  return source === 'camera'
    ? Object.freeze({ maxWidth: 1280, maxHeight: 720, maxFramesPerSecond: 30, maxBitrateBps: 1_500_000 })
    : Object.freeze({ maxWidth: 1920, maxHeight: 1080, maxFramesPerSecond: 20, maxBitrateBps: 3_000_000 });
}

function reason(error: unknown, fallback: string): string {
  return error instanceof Error && /^[a-z][a-z0-9_]{2,119}$/.test(error.message) ? error.message : fallback;
}
