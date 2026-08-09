import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, Subject, of } from 'rxjs';

import { NetworkProfileService } from './network-profile.service';
import { OidcAuthService } from './oidc-auth.service';
import { PairMediaE2eeCoordinatorService } from './pair-media-e2ee-coordinator.service';
import { PairMediaE2eeTransformAdapter } from './pair-media-e2ee-transform.adapter';
import { PairOrdinaryMediaPolicy } from './pair-ordinary-media.policy';
import { PairSessionControlPlaneService } from './pair-session-control-plane.service';
import { PairViewSecurityBootstrapService } from './pair-view-security-bootstrap.service';
import { PublicPairMediaSecurityContractV2 } from './public-pair-media-security-contract';
import { WebrtcChunkReassemblyStore } from './webrtc-chunk-reassembly.store';
import {
  SEMANTIC_DC_VERSION,
  semanticDcEncode,
  type SemanticDataChannelMessage,
} from './webrtc-datachannel.service';
import { SignalMessage, WebrtcSignalingService } from './webrtc-signaling.service';
import { WebrtcSessionService } from './webrtc-session.service';

class PublicPeerConnection {
  static instances: PublicPeerConnection[] = [];
  connectionState: RTCPeerConnectionState = 'new';
  signalingState: RTCSignalingState = 'stable';
  onicecandidate: ((event: RTCPeerConnectionIceEvent) => void) | null = null;
  onconnectionstatechange: (() => void) | null = null;
  ontrack: ((event: RTCTrackEvent) => void) | null = null;
  ondatachannel: ((event: RTCDataChannelEvent) => void) | null = null;
  remoteTrackDuringDescription: RTCTrackEvent | null = null;
  remoteDescriptionGate: ReturnType<typeof deferred<void>> | null = null;
  remoteOfferTransceivers: Array<{ direction: RTCRtpTransceiverDirection; sender: { replaceTrack: ReturnType<typeof vi.fn> } }> = [];
  readonly close = vi.fn(() => { this.connectionState = 'closed'; });
  readonly createAnswer = vi.fn(async () => ({ type: 'answer', sdp: 'data-only-answer' } as RTCSessionDescriptionInit));
  readonly createOffer = vi.fn(async () => ({ type: 'offer', sdp: 'offer' } as RTCSessionDescriptionInit));
  readonly setLocalDescription = vi.fn(async () => undefined);
  readonly setRemoteDescription = vi.fn(async () => {
    if (this.remoteTrackDuringDescription) this.ontrack?.(this.remoteTrackDuringDescription);
    await this.remoteDescriptionGate?.promise;
  });
  readonly addIceCandidate = vi.fn(async () => undefined);
  readonly createDataChannel = vi.fn(() => dataChannel());
  readonly getTransceivers = vi.fn(() => this.remoteOfferTransceivers as unknown as RTCRtpTransceiver[]);

  constructor(readonly configuration?: RTCConfiguration) {
    PublicPeerConnection.instances.push(this);
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

describe('WebrtcSessionService Public media lifecycle', () => {
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
  let transforms: TransformMock;
  let coordinator: CoordinatorMock;
  let bootstrap: { mediaContractFor: ReturnType<typeof vi.fn> };

  beforeAll(() => {
    runtime.RTCPeerConnection = PublicPeerConnection as unknown as typeof RTCPeerConnection;
    runtime.RTCSessionDescription = SessionDescription as unknown as typeof RTCSessionDescription;
    runtime.RTCIceCandidate = IceCandidate as unknown as typeof RTCIceCandidate;
  });

  afterAll(() => {
    runtime.RTCPeerConnection = originalPeer;
    runtime.RTCSessionDescription = originalDescription;
    runtime.RTCIceCandidate = originalCandidate;
  });

  beforeEach(() => {
    PublicPeerConnection.instances = [];
    signalHandler = null;
    signaling = {
      status$: new BehaviorSubject('disconnected'),
      connect: vi.fn(), disconnect: vi.fn(), send: vi.fn(async () => undefined),
      bindMessageHandler: vi.fn((handler: (message: SignalMessage) => Promise<void>) => {
        signalHandler = handler;
        return () => { if (signalHandler === handler) signalHandler = null; };
      }),
    };
    transforms = new TransformMock();
    coordinator = new CoordinatorMock();
    bootstrap = { mediaContractFor: vi.fn(() => contract()) };
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
      { provide: PairViewSecurityBootstrapService, useValue: bootstrap },
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

  it('recreates a clean data-only peer when transform preparation fails', async () => {
    transforms.prepareSession.mockRejectedValueOnce(new Error('media_e2ee_worker_unavailable'));

    await service.startSession('session-a', false, 'peer:remote');
    expect(PublicPeerConnection.instances).toHaveLength(2);
    expect(PublicPeerConnection.instances[0].close).toHaveBeenCalledOnce();
    expect(PublicPeerConnection.instances[1].close).not.toHaveBeenCalled();
    expect(service.state$.value).toBe('connecting');

    await signalHandler?.(offer());
    expect(PublicPeerConnection.instances[1].createAnswer).toHaveBeenCalledOnce();
    expect(signaling.send).toHaveBeenCalledWith(expect.objectContaining({ type: 'answer' }));
  });

  it('fences an old same-session prepare continuation and fatal callback from its replacement', async () => {
    const firstPrepare = deferred<number>();
    let staleFatal: ((reason: string) => void) | null = null;
    transforms.prepareSession.mockImplementationOnce(async (_pc, _session, _contract, fatal) => {
      staleFatal = fatal;
      transforms.activeGeneration = 1;
      return firstPrepare.promise;
    }).mockImplementationOnce(async (_pc, _session, _contract, fatal) => {
      transforms.activeGeneration = 2;
      transforms.latestFatal = fatal;
      return 2;
    });

    const staleStart = service.startSession('session-a', false, 'peer:remote');
    await settle();
    await service.startSession('session-a', false, 'peer:remote');
    const replacement = PublicPeerConnection.instances[1];
    firstPrepare.resolve(1);
    await staleStart;
    staleFatal?.('media_e2ee_worker_failed');

    expect(replacement.close).not.toHaveBeenCalled();
    expect(coordinator.failMediaExtension).not.toHaveBeenCalled();
    expect(service.state$.value).toBe('connecting');
  });

  it('stages ontrack during answer SRD, then disables asymmetric media without closing data-only Pair', async () => {
    transforms.bindRemoteOfferTopology.mockRejectedValueOnce(new Error('public_media_topology_invalid'));
    await service.startSession('session-a', false, 'peer:remote');
    const peer = PublicPeerConnection.instances[0];
    const track = { stop: vi.fn() } as unknown as MediaStreamTrack;
    peer.remoteTrackDuringDescription = {
      track,
      receiver: transforms.receiver,
      transceiver: { receiver: transforms.receiver },
      streams: [],
    } as unknown as RTCTrackEvent;

    await signalHandler?.(offer());

    expect(track.stop).toHaveBeenCalledOnce();
    expect(coordinator.failMediaExtension).toHaveBeenCalledWith(
      'session-a', 'public_media_topology_invalid',
    );
    expect(peer.close).not.toHaveBeenCalled();
    expect(peer.createAnswer).toHaveBeenCalledOnce();
    expect(service.state$.value).toBe('connecting');
  });

  it('ignores remote media events after local prepare fallback instead of rejecting the Pair', async () => {
    transforms.prepareSession.mockRejectedValueOnce(new Error('media_e2ee_worker_unavailable'));
    await service.startSession('session-a', false, 'peer:remote');
    const dataOnlyPeer = PublicPeerConnection.instances[1];
    dataOnlyPeer.remoteOfferTransceivers = [0, 1, 2].map(() => ({
      direction: 'recvonly', sender: { replaceTrack: vi.fn(async () => undefined) },
    }));
    const track = { stop: vi.fn() } as unknown as MediaStreamTrack;

    dataOnlyPeer.ontrack?.({ track, streams: [] } as unknown as RTCTrackEvent);
    await signalHandler?.(offer());

    expect(track.stop).toHaveBeenCalledOnce();
    expect(dataOnlyPeer.remoteOfferTransceivers.map(value => value.direction))
      .toEqual(['inactive', 'inactive', 'inactive']);
    for (const transceiver of dataOnlyPeer.remoteOfferTransceivers) {
      expect(transceiver.sender.replaceTrack).toHaveBeenCalledWith(null);
    }
    expect(dataOnlyPeer.close).not.toHaveBeenCalled();
    expect(service.state$.value).toBe('connecting');
  });

  it('makes Public ICE restart terminal so salts and worker counters cannot be reused', async () => {
    await service.startSession('session-a', true, 'peer:remote');
    const peer = PublicPeerConnection.instances[0];

    expect(() => service.restartMediaIce()).toThrow('public_media_fresh_connection_required');

    expect(coordinator.deactivate).toHaveBeenCalledWith(
      'session-a', 'public_media_fresh_connection_required',
    );
    expect(peer.close).toHaveBeenCalledOnce();
    expect(service.state$.value).toBe('failed');
  });

  it('fails a data-only Public Pair when its semantic DataChannel closes', async () => {
    transforms.prepareSession.mockRejectedValueOnce(new Error('media_e2ee_worker_unavailable'));
    await service.startSession('session-a', false, 'peer:remote');
    const peer = PublicPeerConnection.instances[1];
    const channel = dataChannel();
    peer.ondatachannel?.({ channel } as RTCDataChannelEvent);

    channel.onclose?.(new Event('close'));

    expect(peer.close).toHaveBeenCalledOnce();
    expect(service.state$.value).toBe('failed');
  });

  it('rejects role reversal and repeated SDP in one Public connection generation', async () => {
    await service.startSession('session-a', false, 'peer:remote');
    const answerer = PublicPeerConnection.instances[0];

    await signalHandler?.({ ...offer(), type: 'answer' });
    expect(answerer.close).toHaveBeenCalledOnce();
    expect(service.state$.value).toBe('failed');

    await service.startSession('session-a', false, 'peer:remote');
    const replacement = PublicPeerConnection.instances[1];
    await signalHandler?.(offer());
    expect(replacement.close).not.toHaveBeenCalled();
    await signalHandler?.(offer());
    expect(replacement.close).toHaveBeenCalledOnce();
    expect(service.auditLog).toContainEqual(expect.objectContaining({
      type: 'public_sdp_rejected', detail: 'unexpected_offer:established',
    }));
  });

  it('fences a delayed old answer before it can key or establish a same-session replacement', async () => {
    await service.startSession('session-a', true, 'peer:remote');
    const stalePeer = PublicPeerConnection.instances[0];
    const staleHandler = signalHandler;
    const remoteDescriptionGate = deferred<void>();
    stalePeer.remoteDescriptionGate = remoteDescriptionGate;
    const applyingStaleAnswer = staleHandler?.(answer());
    await settle();

    await service.startSession('session-a', true, 'peer:remote');
    const replacement = PublicPeerConnection.instances[1];
    const replacementHandler = signalHandler;
    coordinator.markTopologyNegotiated.mockClear();
    remoteDescriptionGate.resolve(undefined);
    await applyingStaleAnswer;

    expect(replacement.close).not.toHaveBeenCalled();
    expect(coordinator.markTopologyNegotiated).not.toHaveBeenCalled();
    await replacementHandler?.(answer());
    expect(coordinator.markTopologyNegotiated).toHaveBeenCalledTimes(1);
    expect(replacement.close).not.toHaveBeenCalled();
  });

  it('fences delayed answer publication before it can establish a responder replacement', async () => {
    const answerPublicationGate = deferred<void>();
    const answerPublicationStarted = deferred<void>();
    signaling.send.mockImplementation(async (message: SignalMessage) => {
      if (message.type !== 'answer') return;
      answerPublicationStarted.resolve(undefined);
      await answerPublicationGate.promise;
    });
    await service.startSession('session-a', false, 'peer:remote');
    const staleHandler = signalHandler;
    const publishingStaleAnswer = staleHandler?.(offer());
    await answerPublicationStarted.promise;

    await service.startSession('session-a', false, 'peer:remote');
    const replacement = PublicPeerConnection.instances[1];
    const replacementHandler = signalHandler;
    coordinator.markTopologyNegotiated.mockClear();
    answerPublicationGate.resolve(undefined);
    await publishingStaleAnswer;

    expect(replacement.close).not.toHaveBeenCalled();
    expect(coordinator.markTopologyNegotiated).not.toHaveBeenCalled();
    await replacementHandler?.(offer());
    expect(coordinator.markTopologyNegotiated).toHaveBeenCalledTimes(1);
    expect(replacement.close).not.toHaveBeenCalled();
  });

  it('processes delayed media hello and following ACK in exact DataChannel wire order', async () => {
    await service.startSession('session-a', false, 'peer:remote');
    const peer = PublicPeerConnection.instances[0];
    const channel = dataChannel();
    peer.ondatachannel?.({ channel } as RTCDataChannelEvent);
    const helloGate = deferred<void>();
    const helloEntered = deferred<void>();
    const ackEntered = deferred<void>();
    const seen: string[] = [];
    coordinator.acceptSemantic.mockImplementationOnce(async message => {
      seen.push(message.message_id);
      helloEntered.resolve(undefined);
      await helloGate.promise;
      return true;
    }).mockImplementationOnce(async message => {
      seen.push(message.message_id);
      ackEntered.resolve(undefined);
      return true;
    });
    const [hello, ack] = await Promise.all([
      semanticFrame('pair-media-hello-test', 1),
      semanticFrame('pair-media-hello_ack-test', 2),
    ]);

    channel.onmessage?.({ data: hello } as MessageEvent);
    channel.onmessage?.({ data: ack } as MessageEvent);
    await helloEntered.promise;
    expect(seen).toEqual(['pair-media-hello-test']);

    helloGate.resolve(undefined);
    await ackEntered.promise;
    expect(seen).toEqual(['pair-media-hello-test', 'pair-media-hello_ack-test']);
  });

  it('bounds the ordered receive queue while the first semantic message is stalled', async () => {
    await service.startSession('session-a', false, 'peer:remote');
    const peer = PublicPeerConnection.instances[0];
    const channel = dataChannel();
    peer.ondatachannel?.({ channel } as RTCDataChannelEvent);
    const firstGate = deferred<void>();
    const firstEntered = deferred<void>();
    coordinator.acceptSemantic.mockImplementationOnce(async () => {
      firstEntered.resolve(undefined);
      await firstGate.promise;
      return true;
    });
    const frame = await semanticFrame('pair-media-hello-queue', 1);
    channel.onmessage?.({ data: frame } as MessageEvent);
    await firstEntered.promise;

    for (let index = 0; index < 128; index += 1) {
      channel.onmessage?.({ data: frame } as MessageEvent);
    }

    expect(coordinator.fail).toHaveBeenCalledWith(
      'session-a', 'public_datachannel_receive_queue_overflow',
    );
    expect(peer.close).toHaveBeenCalledOnce();
    expect(service.state$.value).toBe('failed');
    firstGate.resolve(undefined);
  });

  it('does not emit an old semantic frame after coordinator work crosses a same-session replacement', async () => {
    await service.startSession('session-a', false, 'peer:remote');
    const stalePeer = PublicPeerConnection.instances[0];
    const staleChannel = dataChannel();
    stalePeer.ondatachannel?.({ channel: staleChannel } as RTCDataChannelEvent);
    const coordinatorGate = deferred<void>();
    const coordinatorEntered = deferred<void>();
    coordinator.acceptSemantic.mockImplementationOnce(async () => {
      coordinatorEntered.resolve(undefined);
      await coordinatorGate.promise;
      return false;
    });
    const messages: string[] = [];
    service.semanticMessage$.subscribe(message => messages.push(message.message_id));
    staleChannel.onmessage?.({
      data: await semanticFrame('pair-media-old-generation', 1),
    } as MessageEvent);
    await coordinatorEntered.promise;

    await service.startSession('session-a', false, 'peer:remote');
    const replacement = PublicPeerConnection.instances[1];
    const replacementChannel = dataChannel();
    replacement.ondatachannel?.({ channel: replacementChannel } as RTCDataChannelEvent);
    coordinatorGate.resolve(undefined);
    await settleCrypto(4);

    expect(messages).toEqual([]);
    replacementChannel.onmessage?.({
      data: await semanticFrame('pair-media-new-generation', 2),
    } as MessageEvent);
    await settleCrypto(4);
    expect(messages).toEqual(['pair-media-new-generation']);
    expect(replacement.close).not.toHaveBeenCalled();
  });

  it('drops semantic decoding that finishes after a same-session DataChannel replacement', async () => {
    await service.startSession('session-a', false, 'peer:remote');
    const stalePeer = PublicPeerConnection.instances[0];
    const staleChannel = dataChannel();
    stalePeer.ondatachannel?.({ channel: staleChannel } as RTCDataChannelEvent);
    const frame = await semanticFrame('pair-media-delayed-decode', 3);
    const expectedDigest = await crypto.subtle.digest('SHA-256', Uint8Array.of(3));
    const digestGate = deferred<ArrayBuffer>();
    const digestEntered = deferred<void>();
    const digestSpy = vi.spyOn(crypto.subtle, 'digest').mockImplementationOnce(async () => {
      digestEntered.resolve(undefined);
      return digestGate.promise;
    });

    try {
      staleChannel.onmessage?.({ data: frame } as MessageEvent);
      await digestEntered.promise;
      await service.startSession('session-a', false, 'peer:remote');
      const replacement = PublicPeerConnection.instances[1];
      digestGate.resolve(expectedDigest);
      await settleCrypto(4);

      expect(coordinator.acceptSemantic).not.toHaveBeenCalled();
      expect(replacement.close).not.toHaveBeenCalled();
      expect(service.state$.value).toBe('connecting');
    } finally {
      digestSpy.mockRestore();
    }
  });

  it('does not enqueue encoded media control into a replacement same-session DataChannel', async () => {
    const message = await semanticMessage('pair-media-stale-outbound', 4, 9);
    const expectedPayloadDigest = await crypto.subtle.digest('SHA-256', Uint8Array.of(4));
    await service.startSession('session-a', true, 'peer:remote');
    const stalePort = coordinator.port;
    const digestGate = deferred<ArrayBuffer>();
    const digestEntered = deferred<void>();
    const digestSpy = vi.spyOn(crypto.subtle, 'digest').mockImplementationOnce(async () => {
      digestEntered.resolve(undefined);
      return digestGate.promise;
    });

    try {
      const staleSend = stalePort.send(message) as Promise<void>;
      await digestEntered.promise;
      await service.startSession('session-a', true, 'peer:remote');
      const replacement = PublicPeerConnection.instances[1];
      const replacementChannel = replacement.createDataChannel.mock.results[0].value as RTCDataChannel;
      Object.assign(replacementChannel, { readyState: 'open' });
      replacementChannel.onopen?.(new Event('open'));
      (replacementChannel.send as ReturnType<typeof vi.fn>).mockClear();

      digestGate.resolve(expectedPayloadDigest);
      await expect(staleSend).rejects.toThrow('semantic_send_context_superseded');
      await settleCrypto(3);

      expect(replacementChannel.send).not.toHaveBeenCalled();
      expect((service as unknown as { activeEpoch: number }).activeEpoch).toBe(1);
      expect((service as unknown as { pendingSemanticSends: Map<string, unknown> })
        .pendingSemanticSends.size).toBe(0);
      expect(replacement.close).not.toHaveBeenCalled();
    } finally {
      digestSpy.mockRestore();
    }
  });

  it('does not let an older same-digest completion delete the newer pending operation', async () => {
    await service.startSession('session-a', false, 'peer:remote');
    const message = await semanticMessage('pair-media-same-digest', 5, 7);
    const older = await service.sendSemantic(message);
    const newer = await service.sendSemantic(message);
    const pending = (service as unknown as {
      pendingSemanticSends: Map<string, unknown>;
    }).pendingSemanticSends;

    older.cancel();
    await older.result;
    await settle();

    expect(pending.get(newer.messageDigest)).toBe(newer);
    newer.cancel();
  });
});

class TransformMock {
  activeGeneration: number | null = null;
  latestFatal: ((reason: string) => void) | null = null;
  readonly receiver = {} as RTCRtpReceiver;
  private readonly stagedReceivers = new WeakSet<RTCRtpReceiver>();
  readonly prepareSession = vi.fn(async (
    _peer: RTCPeerConnection,
    _sessionId: string,
    _contract: unknown,
    fatal: (reason: string) => void,
  ) => {
    this.activeGeneration = (this.activeGeneration ?? 0) + 1;
    this.latestFatal = fatal;
    return this.activeGeneration;
  });
  readonly releaseSession = vi.fn((_sessionId?: string, generation?: number) => {
    if (generation === undefined || generation === this.activeGeneration) this.activeGeneration = null;
  });
  readonly isPrepared = vi.fn((_session: string, _epoch?: number, _digest?: string, generation?: number) => (
    this.activeGeneration !== null && (generation === undefined || generation === this.activeGeneration)
  ));
  readonly isKeyed = vi.fn(() => false);
  readonly generationForSession = vi.fn(() => this.activeGeneration);
  readonly isAwaitingRemoteTopology = vi.fn((_session: string, generation?: number) => (
    this.activeGeneration !== null && (generation === undefined || generation === this.activeGeneration)
  ));
  readonly bindRemoteOfferTopology = vi.fn(async () => undefined);
  readonly stageRemoteOfferTrack = vi.fn((
    _session: string, _transceiver: RTCRtpTransceiver, receiver: RTCRtpReceiver,
  ) => {
    this.stagedReceivers.add(receiver);
    return 'microphone-opus';
  });
  readonly slotForReceiver = vi.fn((_session: string, receiver: RTCRtpReceiver) => (
    this.stagedReceivers.has(receiver) ? 'microphone-opus' : null
  ));
  readonly validateFinalTopology = vi.fn();
}

class CoordinatorMock {
  readonly status$ = new BehaviorSubject<any>({ sessionId: '', state: 'inactive' });
  port: any = null;
  readonly bindTransport = vi.fn((_sessionId: string, port: any) => { this.port = port; });
  readonly unbindTransport = vi.fn(() => { this.port = null; });
  readonly fail = vi.fn((sessionId: string, reasonCode: string) => {
    this.status$.next({ sessionId, state: 'failed', reasonCode });
    this.port?.failClosed(reasonCode);
  });
  readonly failMediaExtension = vi.fn((sessionId: string, reasonCode: string) => {
    this.port?.disableMedia(reasonCode);
    this.status$.next({ sessionId, state: 'failed', reasonCode });
  });
  readonly markDataChannelOpen = vi.fn();
  readonly markTopologyNegotiated = vi.fn();
  readonly acceptSemantic = vi.fn(async () => false);
  readonly deactivate = vi.fn((_sessionId: string, reasonCode: string) => {
    this.port?.failClosed(reasonCode);
    this.status$.next({ sessionId: _sessionId, state: 'inactive', reasonCode });
  });
  statusFor(sessionId: string): any { return { sessionId, state: 'inactive' }; }
}

function contract(): PublicPairMediaSecurityContractV2 {
  return {
    domain: 'ananta.public-pair.media-security-contract.v2', version: 2,
    session_id: 'session-a', epoch: 7, digest: 'a'.repeat(64),
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

function dataChannel(): RTCDataChannel {
  return {
    readyState: 'connecting', bufferedAmount: 0, bufferedAmountLowThreshold: 0,
    send: vi.fn(), close: vi.fn(),
  } as unknown as RTCDataChannel;
}

function deferred<T>(): { promise: Promise<T>; resolve(value: T): void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(accept => { resolve = accept; });
  return { promise, resolve };
}

async function settle(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

async function semanticFrame(messageId: string, sequence: number): Promise<string> {
  return semanticDcEncode(await semanticMessage(messageId, sequence));
}

async function semanticMessage(
  messageId: string,
  sequence: number,
  epoch = 7,
): Promise<SemanticDataChannelMessage> {
  const ciphertext = Uint8Array.of(sequence);
  const digest = await crypto.subtle.digest('SHA-256', ciphertext);
  return {
    version: SEMANTIC_DC_VERSION,
    traffic_class: 'control',
    message_id: messageId,
    session_id: 'session-a',
    epoch,
    sender_id: 'peer:remote',
    audience_id: 'peer:local',
    sequence,
    expires_at_ms: 2_000_000_000_000,
    compression: 'none',
    security: { algorithm: 'AES-GCM-256', key_id: 'pair-key-7' },
    payload_bytes: ciphertext.byteLength,
    payload_digest: [...new Uint8Array(digest)]
      .map(byte => byte.toString(16).padStart(2, '0')).join(''),
    ciphertext: btoa(String.fromCharCode(...ciphertext)),
  };
}

async function settleCrypto(turns = 2): Promise<void> {
  for (let index = 0; index < turns; index += 1) {
    await crypto.subtle.digest('SHA-256', new Uint8Array(0));
  }
}
