import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, of, Subject, throwError } from 'rxjs';

import { AgentDirectoryService } from '../../services/agent-directory.service';
import { SFU_TRANSPORT_PROJECTION } from '../../services/livekit-sfu-transport.service';
import { MobileRuntimeService } from '../../services/mobile-runtime.service';
import { NetworkProfileService } from '../../services/network-profile.service';
import { SemanticReceiverPathService } from '../../services/semantic-receiver-path.service';
import { SemanticSpeechTransportService } from '../../services/semantic-speech-transport.service';
import {
  SemanticSpeechRuntimeCoordinatorService,
} from '../../services/semantic-speech-runtime-coordinator.service';
import { SemanticSpeechCaptureProducerService } from '../../services/semantic-speech-capture-producer.service';
import { DEFAULT_SEMANTIC_SPEECH_SETTINGS } from '../../services/semantic-speech-settings';
import { SemanticSpeechQualityControllerService } from '../../services/semantic-speech-quality-controller.service';
import { SemanticSfuPathCoordinatorService } from '../../services/semantic-sfu-path-coordinator.service';
import { ShareSessionService } from '../../services/share-session.service';
import { SpeechReconciliationApiService } from '../../services/speech-reconciliation-api.service';
import { SpeechAdapterRegistryApiService } from '../../services/speech-adapter-registry-api.service';
import { SpeechEvidenceDatachannelTransportService } from '../../services/speech-evidence-datachannel-transport.service';
import { SpeechEvidenceSyncApiService } from '../../services/speech-evidence-sync-api.service';
import { SpeechEvidenceSyncService } from '../../services/speech-evidence-sync.service';
import { SpeechEvidenceSyncCryptoContext } from '../../services/speech-evidence-sync.providers';
import { WebrtcPeerKeyService } from '../../services/webrtc-peer-key.service';
import { WebrtcMediaSessionService } from '../../services/webrtc-media-session.service';
import { WebrtcMediaPublicationService } from '../../services/webrtc-media-publication.service';
import { WebrtcTransportService } from '../../services/webrtc-transport.service';
import { PairOrdinaryMediaPolicy } from '../../services/pair-ordinary-media.policy';
import { PairMediaE2eeCoordinatorService } from '../../services/pair-media-e2ee-coordinator.service';
import { PairSessionControlPlaneService } from '../../services/pair-session-control-plane.service';
import { SemanticComputeIntentFacade } from '../pair-view/semantic-compute-intent.facade';
import { SemanticMediaProgramFacade } from './semantic-media-program.facade';
import { PeerEvidenceSyncFacade } from './peer-evidence-sync.facade';
import { SpeechEvidenceConsentFacade } from './speech-evidence-consent.facade';

const flags = {
  ordinary_media_publication: true, semantic_visual_capture: true, semantic_speech_runtime: true, semantic_media_sfu: true,
  semantic_media_background_operations: true, peer_evidence_sync: true, speech_reconciliation: true,
  speech_adaptation_training: true, speech_adapter_routing: true,
};
const profile = {
  profile_id: 'test', label: 'Test', oidc: { issuer: '', client_id: '', audience: '', pkce_required: true },
  rendezvous: { base_url: '', signaling_url: '', transport_order: ['webrtc'] }, ice_servers: [],
  require_e2e_payload_encryption: true, signaling_url: '', transport_order: ['webrtc'],
  semantic_media_feature_flags: flags, warning: '',
};
const session = {
  id: 'session-a', title: 'Pair', invite_code: 'ABC', mode: 'p2p', transport: 'webrtc', permissions: {},
  created_at: 1, expires_at: null, revoked_at: null, owner_user_id: 'alice', permissions_version: 2,
  security_epoch: 3, security_mode: 'strict_e2ee',
};
const binding = {
  scopeId: 'session-a', epoch: 3, localPeerId: 'alice', remotePeerId: 'bob', contractDigest: 'a'.repeat(64),
  keyId: 'pair-key', tenantId: 'tenant-a', confirmed: true,
};
const computeState = {
  contract: { contractId: '', revision: 0, status: 'absent', profile: 'off', delayMs: 5_000, roles: {} },
  leases: [], pending: false, errorCode: null,
};

describe('SemanticMediaProgramFacade', () => {
  const shareState$ = new BehaviorSubject<any>({
    session, participants: [{ id: 'participant-b', user_id: 'bob', device_id: 'device-b', joined_at: 1,
      last_seen_at: 1, revoked_at: null, permissions: {} }], messages: [], cursor: '0', role: 'owner',
  });
  const profile$ = new BehaviorSubject<any>(profile);
  const mode$ = new BehaviorSubject<any>('webrtc');
  const sfuState$ = new BehaviorSubject<any>({ status: 'connected' });
  const speech = { start: vi.fn(), stop: vi.fn() };
  const speechRuntime = {
    settings$: new BehaviorSubject<any>(DEFAULT_SEMANTIC_SPEECH_SETTINGS),
    fatalFailure$: new Subject<string>(),
    start: vi.fn(), stop: vi.fn(), applySettings: vi.fn((value: any) => speechRuntime.settings$.next(value)),
    activatePersonalization: vi.fn(async () => undefined),
    revokePersonalization: vi.fn(async () => undefined),
    cleanupPersonalization: vi.fn(async () => false),
  };
  const speechProducer = {
    failure$: new Subject<string>(),
    start: vi.fn(async () => undefined),
    stop: vi.fn(async () => undefined),
    applySettings: vi.fn(),
    rebind: vi.fn(),
  };
  const qualityState$ = new BehaviorSubject<any>({
    mode: 'semantic_reconstruction', reasonCode: 'quality_healthy', transitioned: false,
    ordinaryAudioAvailable: true, liveTranscriptEnabled: true,
    delayedSourceEnabled: true, semanticFeaturesEnabled: true,
  });
  const compute = {
    state$: new BehaviorSubject<any>(computeState),
    bind: vi.fn(),
    handleIntent: vi.fn(),
    requestSuggestion: vi.fn(),
  };
  const runtimeOnline$ = new BehaviorSubject(true);
  const evidence = { pause: vi.fn(), revoke: vi.fn() };
  const evidenceCrypto = { configure: vi.fn(), exportPublicSigningKey: vi.fn(async () => ({ keyId: 'key', rawKeyB64: 'raw' })), clear: vi.fn() };
  const evidenceTransport = {
    verifiedInbound$: new Subject<any>(), verificationRejected$: new Subject<any>(), bind: vi.fn(), clear: vi.fn(),
  };
  const evidenceApi = { registerKey: vi.fn(() => of({})) };
  const evidenceFlow = {
    view$: new BehaviorSubject<any>({
      offer: null,
      sync: {
        state: 'inactive', pending: false, acknowledgedChunks: 0, chunkCount: 0, firstMissingIndex: 0,
        inFlightBytes: 0, retries: 0, quarantineCount: 0, receiptId: null, receiptVerification: 'none',
        revocationState: null, reasonCode: null, localGroups: [], quarantine: [], lineage: [], candidates: [],
        regions: [], resolutionHash: '', resolutionPolicyVersion: '',
      },
      reasonCode: 'peer_evidence_sync_ready_for_activation',
    }),
    bind: vi.fn(), activate: vi.fn(async () => undefined), clear: vi.fn(), propose: vi.fn(), accept: vi.fn(),
    pause: vi.fn(), resume: vi.fn(), reject: vi.fn(), revoke: vi.fn(), localOverride: vi.fn(),
  };
  const initialConsentState = {
    bound: true, signerIds: ['alice', 'bob'], pending: false, errorCode: null,
    consent: { consent: {
      consent_id: 'consent-a', tenant_id: 'tenant-a', owner_subject: 'alice', speaker_id: 'alice',
      recipient_id: 'bob', direction: 'sender_to_receiver', pair_id: 'session-a', session_id: 'session-a',
      session_epoch: 3, consent_version: 4, expires_at_ms: Date.now() + 60_000, state: 'active',
      revocation_epoch: 1, issued_at_ms: Date.now() - 1_000, purpose: 'live_correction',
      data_classes: ['transcript'], retention_seconds: 3_600, trainer_locations: [],
      required_signers: ['alice', 'bob'], signatures: {},
      grants: {
        capture: false, transcript_share: true, feature_share: false, raw_audio_share: false,
        dataset_import: false, training: false, inference: false, export: false,
      },
    }, consentDigest: 'e'.repeat(64), scopeDigest: 'f'.repeat(64) },
  };
  const consentState$ = new BehaviorSubject<any>(initialConsentState);
  const consent = { state$: consentState$, bind: vi.fn(), handle: vi.fn() };
  const sfuCoordinator = { bind: vi.fn(), switchReceiver: vi.fn(async () => ({ effectivePath: 'sfu' })), stop: vi.fn() };
  const audioState$ = new BehaviorSubject<any>({ status: 'active' });
  const media = {
    audioState$, requestMicrophone: vi.fn(async () => audioState$.next({ status: 'active', trackId: 'mic' })),
    stopAudio: vi.fn(), setMuted: vi.fn(),
  };
  const ordinaryMediaPolicy = {
    canActivate: vi.fn(() => true),
    assertActivationAllowed: vi.fn(),
    allows: vi.fn(() => true),
    assertAllowed: vi.fn(),
  };
  const pairMediaE2eeStatus$ = new BehaviorSubject<any>({
    sessionId: 'session-a', state: 'ready', contractDigest: 'a'.repeat(64),
  });
  const pairMediaE2ee = {
    status$: pairMediaE2eeStatus$,
    statusFor: vi.fn(() => pairMediaE2eeStatus$.value),
    canActivate: vi.fn(() => true),
    activate: vi.fn(async () => pairMediaE2eeStatus$.value),
    deactivate: vi.fn(),
  };
  const pairControlPlane = {
    authorityKindForSession: vi.fn<(sessionId: string) => 'hub' | 'public'>(() => 'hub'),
  };
  const directory = {
    list: vi.fn<() => Array<{ role: string; name: string; url: string }>>(
      () => [{ role: 'hub', name: 'hub', url: 'http://hub.test' }],
    ),
  };
  const mediaPublications$ = new BehaviorSubject<readonly any[]>([]);
  const mediaPublications = {
    publications$: mediaPublications$,
    startLocal: vi.fn(async (authorization: any) => mediaPublications$.next([{
      publicationId: authorization.publicationId, source: authorization.source, status: 'active', local: true,
      trackId: `${authorization.source}-track`, captureLabel: authorization.source, reasonCode: null,
    }])),
    replaceLocal: vi.fn(async () => undefined), stopPublication: vi.fn(), setMuted: vi.fn(), stopAll: vi.fn(),
  };
  const reconciliationApi = {
    list: vi.fn(() => of({ jobs: Object.freeze([]), next_offset: null })),
  };
  const adapterApi = {
    list: vi.fn(() => of({ items: Object.freeze([]), count: 0 })),
    get: vi.fn(),
  };
  let facade: SemanticMediaProgramFacade;

  beforeEach(() => {
    vi.clearAllMocks();
    speechRuntime.activatePersonalization.mockResolvedValue(undefined);
    speechRuntime.revokePersonalization.mockResolvedValue(undefined);
    speechRuntime.cleanupPersonalization.mockResolvedValue(false);
    adapterApi.list.mockImplementation(() => of({ items: Object.freeze([]), count: 0 }));
    adapterApi.get.mockReset();
    shareState$.next({ ...shareState$.value, session });
    profile$.next(profile);
    mode$.next('webrtc');
    sfuState$.next({ status: 'connected' });
    runtimeOnline$.next(true);
    audioState$.next({ status: 'active' });
    mediaPublications$.next([]);
    ordinaryMediaPolicy.allows.mockReset();
    ordinaryMediaPolicy.allows.mockReturnValue(true);
    ordinaryMediaPolicy.canActivate.mockReset();
    ordinaryMediaPolicy.canActivate.mockReturnValue(true);
    ordinaryMediaPolicy.assertActivationAllowed.mockReset();
    ordinaryMediaPolicy.assertAllowed.mockReset();
    pairControlPlane.authorityKindForSession.mockReset();
    pairControlPlane.authorityKindForSession.mockReturnValue('hub');
    directory.list.mockReset();
    directory.list.mockReturnValue([{ role: 'hub', name: 'hub', url: 'http://hub.test' }]);
    pairMediaE2eeStatus$.next({
      sessionId: 'session-a', state: 'ready', contractDigest: 'a'.repeat(64),
    });
    pairMediaE2ee.activate.mockReset();
    pairMediaE2ee.activate.mockImplementation(async () => pairMediaE2eeStatus$.value);
    pairMediaE2ee.deactivate.mockReset();
    speechRuntime.settings$.next(DEFAULT_SEMANTIC_SPEECH_SETTINGS);
    qualityState$.next({
      mode: 'semantic_reconstruction', reasonCode: 'quality_healthy', transitioned: false,
      ordinaryAudioAvailable: true, liveTranscriptEnabled: true,
      delayedSourceEnabled: true, semanticFeaturesEnabled: true,
    });
    consentState$.next(initialConsentState);
    TestBed.configureTestingModule({ providers: [
      SemanticMediaProgramFacade,
      SemanticReceiverPathService,
      { provide: AgentDirectoryService, useValue: directory },
      { provide: NetworkProfileService, useValue: { current: profile, profile$, load: vi.fn(async () => undefined) } },
      { provide: ShareSessionService, useValue: { state$: shareState$, currentUserId: 'alice' } },
      { provide: WebrtcTransportService, useValue: { mode$ } },
      { provide: WebrtcPeerKeyService, useValue: { currentBinding: binding, requireBinding: () => binding } },
      { provide: SemanticSpeechTransportService, useValue: speech },
      { provide: SemanticSpeechRuntimeCoordinatorService, useValue: speechRuntime },
      { provide: SemanticSpeechCaptureProducerService, useValue: speechProducer },
      { provide: SemanticSpeechQualityControllerService, useValue: { state$: qualityState$ } },
      { provide: SemanticComputeIntentFacade, useValue: compute },
      { provide: SFU_TRANSPORT_PROJECTION, useValue: {
        state$: sfuState$.asObservable(),
        currentState: () => sfuState$.value,
        authorizedSubscriberIds: () => new Set(['bob']),
      } },
      { provide: MobileRuntimeService, useValue: { online$: runtimeOnline$ } },
      { provide: SpeechEvidenceSyncService, useValue: evidence },
      { provide: SpeechEvidenceSyncCryptoContext, useValue: evidenceCrypto },
      { provide: SpeechEvidenceDatachannelTransportService, useValue: evidenceTransport },
      { provide: SpeechEvidenceSyncApiService, useValue: evidenceApi },
      { provide: PeerEvidenceSyncFacade, useValue: evidenceFlow },
      { provide: SpeechEvidenceConsentFacade, useValue: consent },
      { provide: SemanticSfuPathCoordinatorService, useValue: sfuCoordinator },
      { provide: WebrtcMediaSessionService, useValue: media },
      { provide: WebrtcMediaPublicationService, useValue: mediaPublications },
      { provide: PairOrdinaryMediaPolicy, useValue: ordinaryMediaPolicy },
      { provide: PairMediaE2eeCoordinatorService, useValue: pairMediaE2ee },
      { provide: PairSessionControlPlaneService, useValue: pairControlPlane },
      { provide: SpeechReconciliationApiService, useValue: reconciliationApi },
      { provide: SpeechAdapterRegistryApiService, useValue: adapterApi },
    ] });
    facade = TestBed.inject(SemanticMediaProgramFacade);
  });

  afterEach(() => facade.ngOnDestroy());

  it('projects the active Pair session into real compute and receiver inputs', () => {
    expect(compute.bind).toHaveBeenCalledWith(expect.objectContaining({
      hubUrl: 'http://hub.test', sessionId: 'session-a', epoch: 3, senderId: 'alice', consentVersion: 2,
    }));
    expect(facade.view$.value.computeVisible).toBe(true);
    expect(facade.view$.value.receiverPaths).toEqual([
      expect.objectContaining({ receiverId: 'bob', effectivePath: 'sfu' }),
    ]);
    expect(facade.view$.value.capabilities.find(row => row.capability === 'ordinary_media')?.state)
      .toBe('authoritatively_active');
  });

  it('starts and stops semantic speech only with the confirmed Hub-bound peer context', async () => {
    await facade.handleProgramIntent({ capability: 'live_speech', desired: 'activate', requestId: 'request-live-1' });
    expect(speech.start).toHaveBeenCalledWith({
      sessionId: 'session-a', epoch: 3, localPeerId: 'alice', remotePeerId: 'bob',
      consentVersion: 2, contractDigest: 'a'.repeat(64),
    });
    expect(speechRuntime.start).toHaveBeenCalledWith(expect.objectContaining({
      hubUrl: 'http://hub.test', sessionId: 'session-a', epoch: 3,
      localPeerId: 'alice', remotePeerId: 'bob',
    }));
    expect(speechProducer.start).toHaveBeenCalledWith(expect.objectContaining({
      hubUrl: 'http://hub.test', sessionId: 'session-a', epoch: 3,
      localPeerId: 'alice', remotePeerId: 'bob', profileId: 'default',
    }));
    expect(facade.view$.value.speechTransportState).toBe('active');
    await facade.handleProgramIntent({ capability: 'live_speech', desired: 'revoke', requestId: 'request-live-2' });
    expect(speech.stop).toHaveBeenCalled();
    expect(speechProducer.stop).toHaveBeenCalled();
    expect(facade.view$.value.speechTransportState).toBe('stopped');
  });

  it('executes explicit microphone, camera, screen, replace and stop intents through production ports', async () => {
    await facade.handleProgramIntent({
      capability: 'ordinary_media', desired: 'activate', requestId: 'ordinary-activate',
    });
    expect(facade.view$.value.ordinaryMediaCaptureEnabled).toBe(true);

    await facade.startOrdinaryMicrophone();
    await facade.startOrdinaryVideo('camera');
    await facade.startOrdinaryVideo('screen');
    expect(media.requestMicrophone).toHaveBeenCalled();
    expect(mediaPublications.startLocal).toHaveBeenCalledWith(
      expect.objectContaining({ sessionId: 'session-a', source: 'camera', permitted: true }),
      expect.objectContaining({ maxWidth: 1280 }),
    );
    expect(mediaPublications.startLocal).toHaveBeenCalledWith(
      expect.objectContaining({ sessionId: 'session-a', source: 'screen', permitted: true }),
      expect.objectContaining({ maxWidth: 1920 }),
    );

    mediaPublications$.next([{
      publicationId: 'ordinary-camera-3', source: 'camera', status: 'active', local: true,
      trackId: 'camera-track', captureLabel: 'Kamera', reasonCode: null,
    }]);
    await facade.replaceOrdinaryVideo('ordinary-camera-3');
    facade.setOrdinaryVideoMuted({ publicationId: 'ordinary-camera-3', muted: true });
    facade.stopOrdinaryVideo('ordinary-camera-3');
    facade.setOrdinaryMicrophoneMuted(true);
    facade.stopOrdinaryMicrophone();
    expect(mediaPublications.replaceLocal).toHaveBeenCalledWith(
      'ordinary-camera-3', 'camera', expect.anything(),
    );
    expect(mediaPublications.setMuted).toHaveBeenCalledWith('ordinary-camera-3', true);
    expect(mediaPublications.stopPublication).toHaveBeenCalledWith('ordinary-camera-3', 'publication_user_stop');
    expect(media.setMuted).toHaveBeenCalledWith(true);
    expect(media.stopAudio).toHaveBeenCalledWith('microphone_user_stop');
  });

  it('activates key-bound Public Pair media without a Hub URL or Hub feature flag', async () => {
    directory.list.mockReturnValue([]);
    pairControlPlane.authorityKindForSession.mockReturnValue('public');
    runtimeOnline$.next(false);
    audioState$.next({ status: 'idle', trackId: null, deviceLabelVisible: false, reasonCode: null });
    sfuState$.next({ status: 'disconnected' });
    profile$.next({
      ...profile,
      profile_id: 'public-ananta',
      public_rendezvous: true,
      semantic_media_feature_flags: { ...flags, ordinary_media_publication: false },
    });

    await facade.handleProgramIntent({
      capability: 'ordinary_media', desired: 'activate', requestId: 'public-media-ready',
    });

    expect(pairMediaE2ee.activate).toHaveBeenCalledWith('session-a');
    expect(facade.view$.value.online).toBe(false);
    expect(facade.view$.value.ordinaryMediaAuthority).toBe('public');
    expect(facade.view$.value.ordinaryMediaActivationEnabled).toBe(true);
    expect(facade.view$.value.ordinaryMediaCaptureEnabled).toBe(true);
    expect(facade.view$.value.ordinaryMediaVideoCaptureEnabled).toBe(true);
    expect(facade.view$.value.capabilities.find(row => row.capability === 'ordinary_media')?.state)
      .toBe('authoritatively_active');

    await facade.startOrdinaryVideo('camera');
    expect(media.requestMicrophone).not.toHaveBeenCalled();
    expect(mediaPublications.startLocal).toHaveBeenCalledWith(
      expect.objectContaining({ sessionId: 'session-a', source: 'camera', permitted: true }),
      expect.anything(),
    );
    expect(facade.view$.value.capabilities.find(row => row.capability === 'ordinary_media')?.state)
      .toBe('authoritatively_active');

    await facade.startOrdinaryMicrophone();
    expect(media.requestMicrophone).toHaveBeenCalled();

    await facade.handleProgramIntent({
      capability: 'ordinary_media', desired: 'revoke', requestId: 'public-media-revoke',
    });
    expect(pairMediaE2ee.deactivate)
      .toHaveBeenCalledWith('session-a', 'ordinary_media_capability_revoked');
  });

  it('keeps bilateral Public media activation pending until the peer consents', async () => {
    directory.list.mockReturnValue([]);
    pairControlPlane.authorityKindForSession.mockReturnValue('public');
    profile$.next({
      ...profile,
      profile_id: 'public-ananta',
      public_rendezvous: true,
      semantic_media_feature_flags: { ...flags, ordinary_media_publication: false },
    });
    pairMediaE2eeStatus$.next({ sessionId: 'session-a', state: 'inactive' });
    ordinaryMediaPolicy.allows.mockReturnValue(false);
    ordinaryMediaPolicy.assertAllowed.mockImplementation(() => {
      throw new Error('public_ordinary_media_e2ee_awaiting_peer');
    });
    pairMediaE2ee.activate.mockImplementation(async () => {
      const awaitingPeer = { sessionId: 'session-a', state: 'awaiting-peer' };
      pairMediaE2eeStatus$.next(awaitingPeer);
      return awaitingPeer;
    });

    await facade.handleProgramIntent({
      capability: 'ordinary_media', desired: 'activate', requestId: 'public-media-await-peer',
    });

    expect(facade.view$.value.capabilities.find(row => row.capability === 'ordinary_media'))
      .toMatchObject({ state: 'degraded', reasonCode: 'public_ordinary_media_e2ee_awaiting_peer' });
    expect(facade.view$.value.ordinaryMediaCaptureEnabled).toBe(false);

    ordinaryMediaPolicy.allows.mockReturnValue(true);
    ordinaryMediaPolicy.assertAllowed.mockReset();
    pairMediaE2eeStatus$.next({
      sessionId: 'session-a', state: 'ready', contractDigest: 'a'.repeat(64),
    });

    expect(facade.view$.value.capabilities.find(row => row.capability === 'ordinary_media')?.state)
      .toBe('authoritatively_active');
    expect(facade.view$.value.ordinaryMediaCaptureEnabled).toBe(true);
  });

  it('keeps the active Public media request fenced while transient status updates arrive', async () => {
    directory.list.mockReturnValue([]);
    pairControlPlane.authorityKindForSession.mockReturnValue('public');
    profile$.next({
      ...profile,
      profile_id: 'public-ananta',
      public_rendezvous: true,
      semantic_media_feature_flags: { ...flags, ordinary_media_publication: false },
    });
    let resolveActivation!: (state: any) => void;
    pairMediaE2ee.activate.mockReturnValueOnce(new Promise(resolve => { resolveActivation = resolve; }));

    const activation = facade.handleProgramIntent({
      capability: 'ordinary_media', desired: 'activate', requestId: 'public-media-pending',
    });
    await Promise.resolve();
    pairMediaE2eeStatus$.next({
      sessionId: 'session-a', state: 'awaiting-peer', reasonCode: 'public_media_local_activation_pending',
    });

    expect(facade.view$.value.capabilities.find(row => row.capability === 'ordinary_media'))
      .toMatchObject({ state: 'degraded', requestId: 'public-media-pending' });

    resolveActivation({ sessionId: 'session-a', state: 'awaiting-peer' });
    await activation;
    expect(facade.view$.value.capabilities.find(row => row.capability === 'ordinary_media'))
      .toMatchObject({ state: 'degraded', requestId: null });
  });

  it('does not let a superseded Public activation overwrite an explicit revoke', async () => {
    directory.list.mockReturnValue([]);
    pairControlPlane.authorityKindForSession.mockReturnValue('public');
    profile$.next({
      ...profile,
      profile_id: 'public-ananta',
      public_rendezvous: true,
      semantic_media_feature_flags: { ...flags, ordinary_media_publication: false },
    });
    let resolveActivation!: (state: any) => void;
    pairMediaE2ee.activate.mockReturnValueOnce(new Promise(resolve => { resolveActivation = resolve; }));
    const activation = facade.handleProgramIntent({
      capability: 'ordinary_media', desired: 'activate', requestId: 'public-media-cancelled',
    });
    await Promise.resolve();

    await facade.handleProgramIntent({
      capability: 'ordinary_media', desired: 'revoke', requestId: 'public-media-cancelled',
    });
    resolveActivation({ sessionId: 'session-a', state: 'ready', contractDigest: 'a'.repeat(64) });
    await activation;

    expect(facade.view$.value.capabilities.find(row => row.capability === 'ordinary_media')?.state)
      .toBe('revoked');
    expect(pairMediaE2ee.deactivate)
      .toHaveBeenCalledWith('session-a', 'ordinary_media_capability_revoked');
  });

  it.each([
    ['awaiting-security', 'public_ordinary_media_e2ee_awaiting_security', 'degraded'],
    ['awaiting-peer', 'public_ordinary_media_e2ee_awaiting_peer', 'degraded'],
    ['negotiating', 'public_ordinary_media_e2ee_negotiating', 'degraded'],
    ['failed', 'media_e2ee_worker_failed', 'failed'],
    ['inactive', 'public_ordinary_media_e2ee_not_ready', 'failed'],
  ])('cleans Public captures exactly once when ready becomes %s', async (state, reasonCode, expectedState) => {
    directory.list.mockReturnValue([]);
    pairControlPlane.authorityKindForSession.mockReturnValue('public');
    profile$.next({
      ...profile,
      profile_id: 'public-ananta',
      public_rendezvous: true,
      semantic_media_feature_flags: { ...flags, ordinary_media_publication: false },
    });
    await facade.handleProgramIntent({
      capability: 'ordinary_media', desired: 'activate', requestId: 'public-media-before-failure',
    });
    media.stopAudio.mockClear();
    mediaPublications.stopAll.mockClear();
    ordinaryMediaPolicy.allows.mockReturnValue(false);
    ordinaryMediaPolicy.assertAllowed.mockImplementation(() => {
      throw new Error(reasonCode);
    });

    const unavailable = { sessionId: 'session-a', state, reasonCode };
    pairMediaE2eeStatus$.next(unavailable);
    pairMediaE2eeStatus$.next(unavailable);

    expect(media.stopAudio).toHaveBeenCalledTimes(1);
    expect(media.stopAudio).toHaveBeenCalledWith(reasonCode);
    expect(mediaPublications.stopAll).toHaveBeenCalledTimes(1);
    expect(mediaPublications.stopAll).toHaveBeenCalledWith(reasonCode, true);
    expect(facade.view$.value.capabilities.find(row => row.capability === 'ordinary_media'))
      .toMatchObject({ state: expectedState, reasonCode });
  });

  it('fails Public Pair capture closed when the E2EE coordinator is unavailable', async () => {
    pairControlPlane.authorityKindForSession.mockReturnValue('public');
    ordinaryMediaPolicy.allows.mockReturnValue(false);
    ordinaryMediaPolicy.assertActivationAllowed.mockImplementation(() => {
      throw new Error('media_e2ee_transform_unsupported');
    });
    ordinaryMediaPolicy.assertAllowed.mockImplementation(() => {
      throw new Error('media_e2ee_transform_unsupported');
    });

    await facade.handleProgramIntent({
      capability: 'ordinary_media', desired: 'activate', requestId: 'public-media-activate',
    });
    await facade.startOrdinaryMicrophone();
    await facade.startOrdinaryVideo('camera');

    expect(facade.view$.value.ordinaryMediaCaptureEnabled).toBe(false);
    expect(facade.view$.value.ordinaryMediaVideoCaptureEnabled).toBe(false);
    expect(facade.view$.value.ordinaryMediaReason)
      .toBe('media_e2ee_transform_unsupported');
    expect(pairMediaE2ee.activate).not.toHaveBeenCalled();
    expect(media.requestMicrophone).not.toHaveBeenCalled();
    expect(mediaPublications.startLocal).not.toHaveBeenCalled();
  });

  it('cleans ordinary publications on revoke and session replacement', async () => {
    await facade.handleProgramIntent({
      capability: 'ordinary_media', desired: 'activate', requestId: 'ordinary-start',
    });
    await facade.handleProgramIntent({
      capability: 'ordinary_media', desired: 'revoke', requestId: 'ordinary-revoke',
    });
    expect(mediaPublications.stopAll).toHaveBeenCalledWith('ordinary_media_capability_revoked');
    expect(media.stopAudio).toHaveBeenCalledWith('ordinary_media_capability_revoked');
    expect(pairMediaE2ee.deactivate).not.toHaveBeenCalled();

    shareState$.next({ ...shareState$.value, session: { ...session, id: 'session-b' } });
    expect(mediaPublications.stopAll).toHaveBeenCalledWith('ordinary_media_session_ended', true);
    expect(pairMediaE2ee.deactivate).not.toHaveBeenCalled();
  });

  it('applies pause and ordinary override to transport and ordinary media, not only the panel label', async () => {
    await facade.handleProgramIntent({ capability: 'live_speech', desired: 'activate', requestId: 'request-live-settings' });
    facade.handleSpeechSettings({
      displayMode: 'segment', segmentDurationSeconds: 30, correctEachSegment: true,
      paused: true, ordinaryAudioOverride: false,
    });
    expect(speechRuntime.applySettings).toHaveBeenCalledWith(expect.objectContaining({
      displayMode: 'segment', segmentDurationSeconds: 30, paused: true,
    }));
    expect(speechProducer.applySettings).toHaveBeenCalledWith(expect.objectContaining({
      displayMode: 'segment', segmentDurationSeconds: 30, paused: true,
    }));
    expect(speech.stop).toHaveBeenCalled();
    expect(speechRuntime.stop).toHaveBeenCalledWith('semantic_speech_user_paused');

    audioState$.next({ status: 'stopped' });
    facade.handleSpeechSettings({
      displayMode: 'segment', segmentDurationSeconds: 30, correctEachSegment: true,
      paused: false, ordinaryAudioOverride: true,
    });
    await vi.waitFor(() => expect(media.requestMicrophone).toHaveBeenCalled());
    expect(facade.view$.value.capabilities.find(row => row.capability === 'ordinary_media')?.state)
      .toBe('authoritatively_active');
  });

  it('keeps ordinary media active on quality fallback and fatal semantic-session failure', async () => {
    audioState$.next({ status: 'stopped' });
    await facade.handleProgramIntent({ capability: 'live_speech', desired: 'activate', requestId: 'quality-live' });
    expect(media.requestMicrophone).toHaveBeenCalled();

    qualityState$.next({
      ...qualityState$.value, mode: 'ordinary_audio', reasonCode: 'speech_queue_high',
      semanticFeaturesEnabled: false, delayedSourceEnabled: false,
    });
    await vi.waitFor(() => expect(
      facade.view$.value.capabilities.find(row => row.capability === 'ordinary_media')?.state,
    ).toBe('authoritatively_active'));
    expect(facade.view$.value.speechQuality.reasonCode).toBe('speech_queue_high');

    speechRuntime.fatalFailure$.next('speech_session_gone');
    expect(facade.view$.value.speechTransportState).toBe('stopped');
    expect(speechRuntime.stop).toHaveBeenCalledWith('speech_session_gone');
  });

  it('contains a productive capture/transport failure and keeps Ordinary Audio active', async () => {
    audioState$.next({ status: 'stopped' });
    await facade.handleProgramIntent({ capability: 'live_speech', desired: 'activate', requestId: 'capture-live' });

    speechProducer.failure$.next('semantic_speech_send_failed');

    await vi.waitFor(() => expect(facade.view$.value.speechTransportState).toBe('stopped'));
    expect(speechProducer.stop).toHaveBeenCalledWith('semantic_speech_send_failed');
    expect(speechRuntime.stop).toHaveBeenCalledWith('semantic_speech_send_failed');
    expect(media.requestMicrophone).toHaveBeenCalled();
  });

  it('binds source correction only when active bilateral audio/transcript/correction consent is current', async () => {
    consentState$.next({
      ...initialConsentState,
      consent: {
        consentDigest: 'b'.repeat(64),
        consent: {
          ...initialConsentState.consent.consent,
          data_classes: ['audio', 'transcript', 'correction'],
          revocation_epoch: 1,
          grants: { capture: true, raw_audio_share: true, transcript_share: true },
        },
      },
    });
    await facade.handleProgramIntent({ capability: 'live_speech', desired: 'activate', requestId: 'consented-live' });

    expect(speechRuntime.start).toHaveBeenCalledWith(expect.objectContaining({
      correctionConsent: {
        consentId: 'consent-a', consentDigest: 'b'.repeat(64), consentVersion: 4,
        revocationEpoch: 1, expiresAtMs: expect.any(Number),
      },
    }));
  });

  it('requires both a Hub URL and the mobile runtime online signal for activations', async () => {
    vi.clearAllMocks();
    runtimeOnline$.next(false);
    expect(facade.view$.value.online).toBe(false);
    await facade.handleProgramIntent({ capability: 'live_speech', desired: 'activate', requestId: 'offline-live' });
    facade.handleComputeIntent({ kind: 'activate', expectedRevision: 1 });
    expect(speech.start).not.toHaveBeenCalled();
    expect(compute.handleIntent).not.toHaveBeenCalled();
  });

  it('marks reconciliation authoritative only after an authenticated Hub read', async () => {
    consentState$.next(reconciliationConsentState(initialConsentState));
    await facade.handleProgramIntent({
      capability: 'speech_reconciliation', desired: 'activate', requestId: 'request-reconciliation-1',
    });

    expect(reconciliationApi.list).toHaveBeenCalledWith('http://hub.test', 0, 1);
    expect(facade.view$.value.capabilities.find(row => row.capability === 'speech_reconciliation')?.state)
      .toBe('authoritatively_active');
    expect(facade.view$.value.speechReconciliationHubAuthorized).toBe(true);
    expect(facade.view$.value.capabilities.find(row => row.capability === 'speech_reconciliation')?.scope)
      .toMatchObject({
        purpose: 'speech_reconciliation', dataClass: 'audio, transcript',
        retentionLabel: '2 Stunde(n)', trainerLocation: 'Kein Training freigegeben',
        grantLabel: 'Roh-Audio, Dataset-Import, Training nicht freigegeben',
      });

    consentState$.next(initialConsentState);
    expect(facade.view$.value.speechReconciliationHubAuthorized).toBe(false);
    expect(facade.view$.value.capabilities.find(row => row.capability === 'speech_reconciliation')?.state)
      .toBe('degraded');
  });

  it('fails reconciliation activation when the Hub does not authorize the read', async () => {
    consentState$.next(reconciliationConsentState(initialConsentState));
    reconciliationApi.list.mockReturnValueOnce(throwError(() => Object.assign(new Error('forbidden'), { status: 403 })));

    await facade.handleProgramIntent({
      capability: 'speech_reconciliation', desired: 'activate', requestId: 'request-reconciliation-denied',
    });

    expect(facade.view$.value.capabilities.find(row => row.capability === 'speech_reconciliation')?.state)
      .toBe('failed');
    expect(facade.view$.value.speechReconciliationHubAuthorized).toBe(false);
  });

  it('rejects a stale reconciliation read after the Hub session epoch changes', async () => {
    consentState$.next(reconciliationConsentState(initialConsentState));
    const pendingRead = new Subject<any>();
    reconciliationApi.list.mockReturnValueOnce(pendingRead);
    const activation = facade.handleProgramIntent({
      capability: 'speech_reconciliation', desired: 'activate', requestId: 'request-reconciliation-stale',
    });

    shareState$.next({ ...shareState$.value, session: { ...session, security_epoch: 4 } });
    pendingRead.next({ jobs: Object.freeze([]), next_offset: null });
    pendingRead.complete();
    await activation;

    expect(facade.view$.value.speechReconciliationHubAuthorized).toBe(false);
    expect(facade.view$.value.capabilities.find(row => row.capability === 'speech_reconciliation')?.state)
      .toBe('failed');
  });

  it('rejects reconciliation before the Hub read when consent scope is stale or too narrow', async () => {
    await facade.handleProgramIntent({
      capability: 'speech_reconciliation', desired: 'activate', requestId: 'request-reconciliation-narrow',
    });

    expect(reconciliationApi.list).not.toHaveBeenCalled();
    expect(facade.view$.value.capabilities.find(row => row.capability === 'speech_reconciliation')?.state)
      .toBe('failed');
    expect(facade.view$.value.speechReconciliationHubAuthorized).toBe(false);
  });

  it('binds the scoped evidence flow to active Hub consent but never fabricates an offer or raw-audio grant', async () => {
    await facade.handleProgramIntent({ capability: 'evidence_text', desired: 'activate', requestId: 'request-evidence-1' });
    expect(evidenceFlow.bind).toHaveBeenCalledWith(expect.objectContaining({
      hubUrl: 'http://hub.test', sessionId: 'session-a', pairId: 'session-a', epoch: 3,
      localPeerId: 'alice', remotePeerId: 'bob',
    }));
    expect(evidenceFlow.activate).toHaveBeenCalled();
    expect(facade.view$.value.evidenceOffer).toBeNull();
    expect(facade.view$.value.evidenceAvailableReason).toBe('peer_evidence_sync_ready_for_activation');

    await facade.handleProgramIntent({ capability: 'raw_audio', desired: 'activate', requestId: 'request-audio-1' });
    expect(facade.view$.value.capabilities.find(row => row.capability === 'raw_audio')?.state).toBe('failed');
  });

  it('reaches explicit approved Pair adapter activation without auto-selecting metadata', async () => {
    const approved = {
      adapter_id: 'speech-adapter-approved', pair_id: 'session-a', direction: 'sender_to_receiver' as const,
      speaker_digest: 'a'.repeat(64), scope_digest: 'b'.repeat(64), base_model_id: 'base-test',
      base_model_digest: 'c'.repeat(64), consent_digest: 'd'.repeat(64),
      artifact_ref: 'artifact://speech-adapters/test/speech-adapter-approved', artifact_sha256: 'e'.repeat(64),
      expires_at_ms: Date.now() + 60_000, consent_expires_at_ms: Date.now() + 60_000,
      registry_version: 2, status: 'approved' as const,
    };
    adapterApi.list.mockImplementation((_hubUrl: string, _pairId: string, direction: string) => of({
      items: Object.freeze(direction === 'sender_to_receiver' ? [approved] : []),
      count: direction === 'sender_to_receiver' ? 1 : 0,
    }));
    adapterApi.get.mockReturnValue(of(approved));
    profile$.next({ ...profile, semantic_media_feature_flags: { ...flags, speech_adapter_routing: false } });
    profile$.next(profile);
    await vi.waitFor(() => expect(facade.view$.value.speechAdapters).toHaveLength(1));
    expect(speechRuntime.activatePersonalization).not.toHaveBeenCalled();

    await facade.handleProgramIntent({ capability: 'live_speech', desired: 'activate', requestId: 'live-adapter' });
    await facade.handleProgramIntent({
      capability: 'adapter_activation', desired: 'activate', requestId: 'adapter-explicit',
      adapterId: approved.adapter_id, direction: approved.direction,
    });

    expect(adapterApi.get).toHaveBeenCalledWith(
      'http://hub.test', approved.adapter_id, 'session-a', 'sender_to_receiver',
    );
    expect(speechRuntime.activatePersonalization).toHaveBeenCalledWith({
      metadata: approved,
      context: {
        pairId: 'session-a', direction: 'sender_to_receiver', speakerDigest: 'a'.repeat(64),
        scopeDigest: 'b'.repeat(64), baseModelId: 'base-test', baseModelDigest: 'c'.repeat(64),
        consentDigest: 'd'.repeat(64),
      },
    });
    expect(facade.view$.value.capabilities.find(row => row.capability === 'adapter_activation'))
      .toMatchObject({ state: 'authoritatively_active' });
  });

  it('reports the default browser engine as unavailable instead of claiming adapter activation', async () => {
    const approved = {
      adapter_id: 'speech-adapter-unavailable', pair_id: 'session-a', direction: 'sender_to_receiver' as const,
      speaker_digest: 'a'.repeat(64), scope_digest: 'b'.repeat(64), base_model_id: 'base-test',
      base_model_digest: 'c'.repeat(64), consent_digest: 'd'.repeat(64),
      artifact_ref: 'artifact://speech-adapters/test/speech-adapter-unavailable', artifact_sha256: 'e'.repeat(64),
      expires_at_ms: Date.now() + 60_000, consent_expires_at_ms: Date.now() + 60_000,
      registry_version: 2, status: 'approved' as const,
    };
    adapterApi.list.mockImplementation((_hubUrl: string, _pairId: string, direction: string) => of({
      items: Object.freeze(direction === 'sender_to_receiver' ? [approved] : []),
      count: direction === 'sender_to_receiver' ? 1 : 0,
    }));
    adapterApi.get.mockReturnValue(of(approved));
    speechRuntime.activatePersonalization.mockRejectedValue(new Error('speech_adapter_browser_engine_not_released'));
    profile$.next({ ...profile, semantic_media_feature_flags: { ...flags, speech_adapter_routing: false } });
    profile$.next(profile);
    await vi.waitFor(() => expect(facade.view$.value.speechAdapters).toHaveLength(1));
    await facade.handleProgramIntent({ capability: 'live_speech', desired: 'activate', requestId: 'live-unavailable' });

    await facade.handleProgramIntent({
      capability: 'adapter_activation', desired: 'activate', requestId: 'adapter-unavailable',
      adapterId: approved.adapter_id, direction: approved.direction,
    });

    expect(facade.view$.value.capabilities.find(row => row.capability === 'adapter_activation'))
      .toMatchObject({ state: 'failed', reasonCode: 'speech_adapter_browser_engine_not_released' });
  });
});

function reconciliationConsentState(initialConsentState: any) {
  return {
    ...initialConsentState,
    consent: {
      consentDigest: '1'.repeat(64),
      scopeDigest: '2'.repeat(64),
      consent: {
        ...initialConsentState.consent.consent,
        purpose: 'speech_reconciliation',
        data_classes: ['audio', 'transcript'],
        retention_seconds: 7_200,
        trainer_locations: [],
        grants: {
          capture: true, transcript_share: true, feature_share: false, raw_audio_share: true,
          dataset_import: true, training: false, inference: false, export: false,
        },
      },
    },
  };
}
