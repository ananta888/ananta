/**
 * Pins when the WebRTC session reports a negotiated Public media topology.
 *
 * The offer (responder) and answer (initiator) branches used to carry two
 * inline copies of the "active media context + prepared transform" check;
 * both now call WebrtcPublicMediaExtension.markTopologyNegotiated. These
 * specs fix the pre-merge contract of both branches: call order, exact
 * arguments, generation fences and the fallbacks that must not report a
 * negotiated topology.
 */
import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, of } from 'rxjs';

import { NetworkProfileService } from './network-profile.service';
import { OidcAuthService } from './oidc-auth.service';
import { PairMediaE2eeCoordinatorService } from './pair-media-e2ee-coordinator.service';
import { PairMediaE2eeTransformAdapter } from './pair-media-e2ee-transform.adapter';
import { PairOrdinaryMediaPolicy } from './pair-ordinary-media.policy';
import { PairMediaPublicationPolicy } from './pair-media-publication.policy';
import { PairSessionControlPlaneService } from './pair-session-control-plane.service';
import { PairViewSecurityBootstrapService } from './pair-view-security-bootstrap.service';
import { PublicPairMediaSecurityContractV2 } from './public-pair-media-security-contract';
import { WebrtcChunkReassemblyStore } from './webrtc-chunk-reassembly.store';
import { SignalMessage, WebrtcSignalingService } from './webrtc-signaling.service';
import { WebrtcSessionService } from './webrtc-session.service';

const DIGEST = 'a'.repeat(64);
let callOrder: string[] = [];

class TopologyPeerConnection {
  static instances: TopologyPeerConnection[] = [];
  connectionState: RTCPeerConnectionState = 'new';
  signalingState: RTCSignalingState = 'stable';
  onicecandidate: ((event: RTCPeerConnectionIceEvent) => void) | null = null;
  onconnectionstatechange: (() => void) | null = null;
  ontrack: ((event: RTCTrackEvent) => void) | null = null;
  ondatachannel: ((event: RTCDataChannelEvent) => void) | null = null;
  remoteDescriptionGate: ReturnType<typeof deferred<void>> | null = null;
  readonly close = vi.fn(() => { this.connectionState = 'closed'; });
  readonly createAnswer = vi.fn(async () => {
    callOrder.push('createAnswer');
    return { type: 'answer', sdp: 'answer' } as RTCSessionDescriptionInit;
  });
  readonly createOffer = vi.fn(async () => ({ type: 'offer', sdp: 'offer' } as RTCSessionDescriptionInit));
  readonly setLocalDescription = vi.fn(async (description?: RTCSessionDescriptionInit) => {
    if (description) callOrder.push(`setLocalDescription:${description.type}`);
  });
  readonly setRemoteDescription = vi.fn(async () => {
    callOrder.push('setRemoteDescription');
    await this.remoteDescriptionGate?.promise;
  });
  readonly addIceCandidate = vi.fn(async () => { callOrder.push('addIceCandidate'); });
  readonly createDataChannel = vi.fn(() => ({
    readyState: 'connecting', bufferedAmount: 0, bufferedAmountLowThreshold: 0,
    send: vi.fn(), close: vi.fn(),
  }) as unknown as RTCDataChannel);
  readonly getTransceivers = vi.fn(() => [] as RTCRtpTransceiver[]);
  readonly getSenders = vi.fn(() => [] as RTCRtpSender[]);

  constructor(readonly configuration?: RTCConfiguration) {
    TopologyPeerConnection.instances.push(this);
  }
}

class SessionDescription {
  constructor(readonly value: RTCSessionDescriptionInit) {}
  get type(): RTCSdpType { return this.value.type; }
  get sdp(): string { return this.value.sdp ?? ''; }
  toJSON(): RTCSessionDescriptionInit { return this.value; }
}

class IceCandidate {
  constructor(readonly init: RTCIceCandidateInit) {}
}

class TopologyTransforms {
  activeGeneration: number | null = null;
  preparedOverride: boolean | null = null;
  readonly prepareSession = vi.fn(async () => {
    this.activeGeneration = (this.activeGeneration ?? 0) + 1;
    return this.activeGeneration;
  });
  readonly releaseSession = vi.fn((_sessionId?: string, generation?: number) => {
    if (generation === undefined || generation === this.activeGeneration) this.activeGeneration = null;
  });
  readonly isPrepared = vi.fn((_session: string, _epoch?: number, _digest?: string, generation?: number) => {
    if (this.preparedOverride !== null) return this.preparedOverride;
    return this.activeGeneration !== null && (generation === undefined || generation === this.activeGeneration);
  });
  readonly isKeyed = vi.fn(() => false);
  readonly generationForSession = vi.fn(() => this.activeGeneration);
  readonly isAwaitingRemoteTopology = vi.fn((_session: string, generation?: number) => (
    this.activeGeneration !== null && (generation === undefined || generation === this.activeGeneration)
  ));
  readonly bindRemoteOfferTopology = vi.fn(async () => { callOrder.push('bindRemoteOfferTopology'); });
  readonly stageRemoteOfferTrack = vi.fn(() => 'microphone-opus');
  readonly slotForReceiver = vi.fn(() => null);
  readonly slotForSender = vi.fn(() => null);
  readonly senderForSlot = vi.fn(() => { throw new Error('public_media_slot_invalid'); });
  readonly validateFinalTopology = vi.fn();
}

class TopologyCoordinator {
  readonly status$ = new BehaviorSubject<any>({ sessionId: '', state: 'inactive' });
  port: any = null;
  readonly bindTransport = vi.fn((_sessionId: string, port: any) => { this.port = port; });
  readonly unbindTransport = vi.fn(() => { this.port = null; });
  readonly fail = vi.fn((sessionId: string, reasonCode: string) => {
    this.status$.next({ sessionId, state: 'failed', reasonCode });
    this.port?.failClosed(reasonCode);
  });
  readonly failMediaExtension = vi.fn((sessionId: string, reasonCode: string) => {
    callOrder.push(`failMediaExtension:${reasonCode}`);
    this.port?.disableMedia(reasonCode);
    this.status$.next({ sessionId, state: 'failed', reasonCode });
  });
  readonly markDataChannelOpen = vi.fn();
  readonly markTopologyNegotiated = vi.fn((sessionId: string) => {
    callOrder.push(`markTopologyNegotiated:${sessionId}`);
  });
  readonly acceptSemantic = vi.fn(async () => false);
  readonly deactivate = vi.fn((sessionId: string, reasonCode: string) => {
    this.port?.failClosed(reasonCode);
    this.status$.next({ sessionId, state: 'inactive', reasonCode });
  });
  statusFor(sessionId: string): any { return { sessionId, state: 'inactive' }; }
}

describe('WebrtcSessionService Public media topology negotiation', () => {
  const runtime = globalThis as typeof globalThis & {
    RTCPeerConnection: typeof RTCPeerConnection;
    RTCSessionDescription: typeof RTCSessionDescription;
    RTCIceCandidate: typeof RTCIceCandidate;
  };
  const originalPeer = runtime.RTCPeerConnection;
  const originalDescription = runtime.RTCSessionDescription;
  const originalCandidate = runtime.RTCIceCandidate;
  let service: WebrtcSessionService;
  let signalHandler: ((message: SignalMessage) => Promise<void>) | null;
  let signaling: any;
  let transforms: TopologyTransforms;
  let coordinator: TopologyCoordinator;

  beforeAll(() => {
    runtime.RTCPeerConnection = TopologyPeerConnection as unknown as typeof RTCPeerConnection;
    runtime.RTCSessionDescription = SessionDescription as unknown as typeof RTCSessionDescription;
    runtime.RTCIceCandidate = IceCandidate as unknown as typeof RTCIceCandidate;
  });

  afterAll(() => {
    runtime.RTCPeerConnection = originalPeer;
    runtime.RTCSessionDescription = originalDescription;
    runtime.RTCIceCandidate = originalCandidate;
  });

  beforeEach(() => {
    callOrder = [];
    TopologyPeerConnection.instances = [];
    signalHandler = null;
    signaling = {
      status$: new BehaviorSubject('disconnected'),
      failureReason$: new BehaviorSubject(null),
      connect: vi.fn(), disconnect: vi.fn(),
      send: vi.fn(async (message: SignalMessage) => { callOrder.push(`send:${message.type}`); }),
      assertSessionReusable: vi.fn(),
      isSessionRecreationRequired: vi.fn(() => false),
      markSessionRecreationRequired: vi.fn(),
      retireSession: vi.fn(),
      bindMessageHandler: vi.fn((handler: (message: SignalMessage) => Promise<void>) => {
        signalHandler = handler;
        return () => { if (signalHandler === handler) signalHandler = null; };
      }),
    };
    transforms = new TopologyTransforms();
    coordinator = new TopologyCoordinator();
    TestBed.configureTestingModule({ providers: [
      WebrtcSessionService,
      { provide: NetworkProfileService, useValue: { current: {
        ice_servers: [], signaling_url: '', require_e2e_payload_encryption: true,
      } } },
      { provide: WebrtcSignalingService, useValue: signaling },
      { provide: OidcAuthService, useValue: { sessionNonce: 'nonce' } },
      { provide: PairSessionControlPlaneService, useValue: {
        isPublicSession: () => true, authorityKindForSession: () => 'public',
        turnCredentials: () => of(null), assertSessionAvailable: vi.fn(),
      } },
      { provide: PairOrdinaryMediaPolicy, useValue: {
        assertAllowed: vi.fn(() => { throw new Error('raw_public_media_forbidden'); }),
      } },
      { provide: PairMediaPublicationPolicy, useValue: { assertAllowed: vi.fn() } },
      { provide: PairViewSecurityBootstrapService, useValue: { mediaContractFor: vi.fn(() => contract()) } },
      { provide: PairMediaE2eeCoordinatorService, useValue: coordinator },
      { provide: PairMediaE2eeTransformAdapter, useValue: transforms },
      { provide: WebrtcChunkReassemblyStore, useValue: { clearContext: vi.fn(), accept: vi.fn() } },
    ] });
    service = TestBed.inject(WebrtcSessionService);
  });

  afterEach(() => {
    service.closeSession();
    TestBed.resetTestingModule();
  });

  it('responder reports the topology once, only after the answer is published', async () => {
    await service.startSession('session-a', false, 'peer:remote');
    callOrder = [];

    await signalHandler?.(offer());

    expect(callOrder).toEqual([
      'setRemoteDescription',
      'bindRemoteOfferTopology',
      'createAnswer',
      'setLocalDescription:answer',
      'send:answer',
      'markTopologyNegotiated:session-a',
    ]);
    expect(transforms.isPrepared).toHaveBeenLastCalledWith('session-a', undefined, DIGEST, 1);
  });

  // Microtask-order pin: the SDP path must not gain await turns between the
  // settled remote description and createAnswer (a teardown queued in that
  // window would otherwise see createAnswer run on a closed peer). The counts
  // are those of the pre-split inline implementation with these mocks.
  it.each([
    { bindingPending: false, turns: 3 },
    { bindingPending: true, turns: 4 },
  ])('creates the answer $turns microtask turns after the remote description (binding: $bindingPending)', async ({
    bindingPending, turns,
  }) => {
    await service.startSession('session-a', false, 'peer:remote');
    if (!bindingPending) transforms.isAwaitingRemoteTopology.mockReturnValue(false);
    const peer = TopologyPeerConnection.instances.at(-1)!;
    const gate = deferred<void>();
    peer.remoteDescriptionGate = gate;
    const handling = signalHandler?.(offer());
    for (let turn = 0; turn < 5; turn += 1) await Promise.resolve();
    expect(peer.createAnswer).not.toHaveBeenCalled();

    gate.resolve(undefined);
    let observedTurns = -1;
    for (let turn = 0; turn < 40; turn += 1) {
      if (peer.createAnswer.mock.calls.length > 0) { observedTurns = turn; break; }
      await Promise.resolve();
    }
    await handling;

    expect(observedTurns).toBe(turns);
    expect(transforms.bindRemoteOfferTopology).toHaveBeenCalledTimes(bindingPending ? 1 : 0);
  });

  it('initiator reports the topology once after the answer and buffered ICE are applied', async () => {
    await service.startSession('session-a', true, 'peer:remote');
    await signalHandler?.(ice());
    callOrder = [];

    await signalHandler?.(answer());

    expect(callOrder).toEqual([
      'setRemoteDescription',
      'addIceCandidate',
      'markTopologyNegotiated:session-a',
    ]);
    expect(coordinator.markTopologyNegotiated).toHaveBeenCalledOnce();
    expect(transforms.isPrepared).toHaveBeenLastCalledWith('session-a', undefined, DIGEST, 1);
    expect(transforms.bindRemoteOfferTopology).not.toHaveBeenCalled();
  });

  it.each(['offer', 'answer'] as const)(
    'does not report a %s topology whose transform is no longer prepared',
    async type => {
      await service.startSession('session-a', type === 'answer', 'peer:remote');
      transforms.preparedOverride = false;

      await signalHandler?.(type === 'offer' ? offer() : answer());

      expect(coordinator.markTopologyNegotiated).not.toHaveBeenCalled();
      expect(service.auditLog.map(entry => entry.type)).not.toContain('signal_error');
    },
  );

  it.each(['offer', 'answer'] as const)(
    'does not report a %s topology on a data-only fallback peer',
    async type => {
      transforms.prepareSession.mockRejectedValueOnce(new Error('media_e2ee_worker_unavailable'));
      await service.startSession('session-a', type === 'answer', 'peer:remote');

      await signalHandler?.(type === 'offer' ? offer() : answer());

      expect(coordinator.markTopologyNegotiated).not.toHaveBeenCalled();
    },
  );

  it('does not report the responder topology after the offered topology is rejected', async () => {
    transforms.bindRemoteOfferTopology.mockRejectedValueOnce(new Error('public_media_topology_invalid'));
    await service.startSession('session-a', false, 'peer:remote');
    callOrder = [];

    await signalHandler?.(offer());

    expect(callOrder).toEqual([
      'setRemoteDescription',
      'failMediaExtension:public_media_topology_invalid',
      'createAnswer',
      'setLocalDescription:answer',
      'send:answer',
    ]);
    expect(coordinator.markTopologyNegotiated).not.toHaveBeenCalled();
  });

  it.each(['offer', 'answer'] as const)(
    'does not report a %s topology for a peer that was replaced mid-negotiation',
    async type => {
      await service.startSession('session-a', type === 'answer', 'peer:remote');
      const stalePeer = TopologyPeerConnection.instances.at(-1)!;
      const gate = deferred<void>();
      stalePeer.remoteDescriptionGate = gate;
      const staleHandler = signalHandler;
      const applying = staleHandler?.(type === 'offer' ? offer() : answer());
      await Promise.resolve();

      await service.startSession('session-a', type === 'answer', 'peer:remote');
      gate.resolve(undefined);
      await applying;

      expect(coordinator.markTopologyNegotiated).not.toHaveBeenCalled();
      expect(stalePeer.createAnswer).not.toHaveBeenCalled();
    },
  );
});

function contract(): PublicPairMediaSecurityContractV2 {
  return {
    domain: 'ananta.public-pair.media-security-contract.v2', version: 2,
    session_id: 'session-a', epoch: 7, digest: DIGEST,
    expires_at_ms: Date.now() + 60_000, transform: 'RTCRtpScriptTransform',
    frame_format: 'ananta.public-pair.media-frame.v2',
  } as PublicPairMediaSecurityContractV2;
}

function offer(): SignalMessage {
  return {
    type: 'offer', session_id: 'session-a', sender_id: 'peer:remote', recipient_id: 'peer:local',
    payload: { type: 'offer', sdp: 'media-offer' },
  };
}

function answer(): SignalMessage {
  return { ...offer(), type: 'answer', payload: { type: 'answer', sdp: 'media-answer' } };
}

function ice(): SignalMessage {
  return { ...offer(), type: 'ice_candidate', payload: { candidate: 'candidate:1', sdpMid: '0' } };
}

function deferred<T>(): { promise: Promise<T>; resolve(value: T): void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(accept => { resolve = accept; });
  return { promise, resolve };
}
