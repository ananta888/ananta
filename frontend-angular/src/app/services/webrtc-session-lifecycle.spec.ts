import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, Subject, of } from 'rxjs';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { NetworkProfileService } from './network-profile.service';
import { OidcAuthService } from './oidc-auth.service';
import { WebrtcChunkReassemblyStore } from './webrtc-chunk-reassembly.store';
import { SignalMessage, WebrtcSignalingService } from './webrtc-signaling.service';
import { WebrtcSessionService } from './webrtc-session.service';
import { PairSessionControlPlaneService } from './pair-session-control-plane.service';
import { PUBLIC_WEBRTC_STUN_URL } from './public-ananta-endpoints';

interface Deferred<T> {
  readonly promise: Promise<T>;
  resolve(value: T): void;
}

class FakePeerConnection {
  static instances: FakePeerConnection[] = [];
  static emitIceDuringLocalDescription = false;

  connectionState: RTCPeerConnectionState = 'new';
  signalingState: RTCSignalingState = 'stable';
  onicecandidate: ((event: RTCPeerConnectionIceEvent) => void) | null = null;
  onconnectionstatechange: (() => void) | null = null;
  ontrack: ((event: RTCTrackEvent) => void) | null = null;
  ondatachannel: ((event: RTCDataChannelEvent) => void) | null = null;
  remoteDescriptionGate: Deferred<void> | null = null;
  readonly configuration: RTCConfiguration | undefined;

  readonly setRemoteDescription = vi.fn(async () => {
    await this.remoteDescriptionGate?.promise;
  });
  readonly createAnswer = vi.fn(async (): Promise<RTCSessionDescriptionInit> => ({
    type: 'answer', sdp: 'answer-sdp',
  }));
  readonly setLocalDescription = vi.fn(async () => {
    if (FakePeerConnection.emitIceDuringLocalDescription) {
      this.onicecandidate?.({
        candidate: {
          toJSON: () => ({ candidate: 'candidate:early' }),
        },
      } as unknown as RTCPeerConnectionIceEvent);
    }
  });
  readonly createOffer = vi.fn(async (): Promise<RTCSessionDescriptionInit> => ({
    type: 'offer', sdp: 'offer-sdp',
  }));
  readonly createDataChannel = vi.fn(() => ({
    bufferedAmount: 0,
    bufferedAmountLowThreshold: 0,
    readyState: 'connecting',
    send: vi.fn(),
    close: vi.fn(),
  } as unknown as RTCDataChannel));
  readonly addIceCandidate = vi.fn(async () => undefined);
  readonly close = vi.fn(() => { this.connectionState = 'closed'; });

  constructor(configuration?: RTCConfiguration) {
    this.configuration = configuration;
    FakePeerConnection.instances.push(this);
  }
}

class FakeSessionDescription {
  readonly type: RTCSdpType;
  readonly sdp: string;

  constructor(init: RTCSessionDescriptionInit) {
    this.type = init.type;
    this.sdp = init.sdp ?? '';
  }

  toJSON(): RTCSessionDescriptionInit {
    return { type: this.type, sdp: this.sdp };
  }
}

class FakeIceCandidate {
  constructor(readonly init: RTCIceCandidateInit) {}
}

describe('WebrtcSessionService session-bound signaling lifecycle', () => {
  const runtime = globalThis as typeof globalThis & {
    RTCPeerConnection: typeof RTCPeerConnection;
    RTCSessionDescription: typeof RTCSessionDescription;
    RTCIceCandidate: typeof RTCIceCandidate;
  };
  const originalPeerConnection = runtime.RTCPeerConnection;
  const originalSessionDescription = runtime.RTCSessionDescription;
  const originalIceCandidate = runtime.RTCIceCandidate;

  let signaling: {
    message$: Subject<SignalMessage>;
    status$: BehaviorSubject<'disconnected' | 'connecting' | 'connected' | 'failed'>;
    connect: ReturnType<typeof vi.fn>;
    disconnect: ReturnType<typeof vi.fn>;
    send: ReturnType<typeof vi.fn>;
    fallbackToHubRelay: ReturnType<typeof vi.fn>;
    bindMessageHandler: ReturnType<typeof vi.fn>;
  };
  let signalHandler: ((message: Readonly<SignalMessage>) => Promise<void>) | null;
  let service: WebrtcSessionService;
  const controlPlane = {
    isPublicSession: vi.fn(() => false),
    authorityKindForSession: vi.fn((sessionId: string) => (
      controlPlane.isPublicSession(sessionId) ? 'public' : 'hub'
    )),
    turnCredentials: vi.fn(() => of(null)),
    assertSessionAvailable: vi.fn(),
  };

  beforeAll(() => {
    runtime.RTCPeerConnection = FakePeerConnection as unknown as typeof RTCPeerConnection;
    runtime.RTCSessionDescription = FakeSessionDescription as unknown as typeof RTCSessionDescription;
    runtime.RTCIceCandidate = FakeIceCandidate as unknown as typeof RTCIceCandidate;
  });

  afterAll(() => {
    runtime.RTCPeerConnection = originalPeerConnection;
    runtime.RTCSessionDescription = originalSessionDescription;
    runtime.RTCIceCandidate = originalIceCandidate;
  });

  beforeEach(() => {
    FakePeerConnection.instances = [];
    FakePeerConnection.emitIceDuringLocalDescription = false;
    signalHandler = null;
    signaling = {
      message$: new Subject<SignalMessage>(),
      status$: new BehaviorSubject<'disconnected' | 'connecting' | 'connected' | 'failed'>('disconnected'),
      connect: vi.fn(),
      disconnect: vi.fn(),
      send: vi.fn(),
      fallbackToHubRelay: vi.fn(),
      bindMessageHandler: vi.fn((handler: (message: Readonly<SignalMessage>) => Promise<void>) => {
        signalHandler = handler;
        return () => {
          if (signalHandler === handler) signalHandler = null;
        };
      }),
    };
    controlPlane.isPublicSession.mockReset();
    controlPlane.isPublicSession.mockReturnValue(false);
    controlPlane.turnCredentials.mockReturnValue(of(null));
    controlPlane.assertSessionAvailable.mockReset();
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ providers: [
      WebrtcSessionService,
      { provide: NetworkProfileService, useValue: { current: {
        ice_servers: [{ urls: 'stun:hub-profile.invalid:3478' }],
        signaling_url: '', require_e2e_payload_encryption: true,
      } } },
      { provide: WebrtcSignalingService, useValue: signaling },
      { provide: OidcAuthService, useValue: { sessionNonce: 'nonce' } },
      { provide: PairSessionControlPlaneService, useValue: controlPlane },
      { provide: WebrtcChunkReassemblyStore, useValue: {
        clearContext: vi.fn(), accept: vi.fn(),
      } },
    ] });
    service = TestBed.inject(WebrtcSessionService);
  });

  afterEach(() => {
    service.closeSession();
    signaling.message$.complete();
    vi.useRealTimers();
    TestBed.resetTestingModule();
  });

  it('reports a 15 second direct timeout as failed without restarting Hub signaling', async () => {
    vi.useFakeTimers();
    await service.startSession('session-timeout', false, 'bob');

    await vi.advanceTimersByTimeAsync(15_000);

    expect(service.state$.value).toBe('failed');
    expect(signaling.fallbackToHubRelay).not.toHaveBeenCalled();
    expect(service.auditLog).toContainEqual(expect.objectContaining({
      type: 'ice_failed', session_id: 'session-timeout',
    }));
  });

  it('closes the active peer when authenticated signaling fails', async () => {
    await service.startSession('session-signaling-failed', false, 'bob');
    const peer = FakePeerConnection.instances[0];

    signaling.status$.next('failed');

    expect(peer.close).toHaveBeenCalledTimes(1);
    expect(signaling.disconnect).toHaveBeenCalledTimes(1);
    expect(service.state$.value).toBe('failed');
    expect(service.auditLog).toContainEqual(expect.objectContaining({
      type: 'signaling_failed', session_id: 'session-signaling-failed',
    }));
  });

  it('adds short-lived TURN credentials to STUN for public ICE fallback', async () => {
    controlPlane.isPublicSession.mockImplementation(sessionId => sessionId === 'public-session');
    controlPlane.turnCredentials.mockReturnValue(of({
      username: 'expiry:alice', password: 'credential', ttl: 3600,
      uris: ['turn:webrtc.ananta.de:3478'],
    }));

    await service.startSession('public-session', false, 'bob');

    expect(FakePeerConnection.instances[0].configuration?.iceServers).toContainEqual({
      urls: ['turn:webrtc.ananta.de:3478'], username: 'expiry:alice', credential: 'credential',
    });
    expect(FakePeerConnection.instances[0].configuration?.iceServers).toContainEqual({
      urls: PUBLIC_WEBRTC_STUN_URL,
    });
    expect(FakePeerConnection.instances[0].configuration?.iceServers).not.toContainEqual({
      urls: 'stun:hub-profile.invalid:3478',
    });
    expect(controlPlane.turnCredentials).toHaveBeenCalledWith('public-session');
  });

  it('rejects local and remote ordinary tracks for a public Pair session', async () => {
    controlPlane.isPublicSession.mockImplementation(sessionId => sessionId === 'public-session');
    await service.startSession('public-session', false, 'bob');
    const peer = FakePeerConnection.instances[0];
    const remoteTrack = { stop: vi.fn() } as unknown as MediaStreamTrack;

    expect(() => service.addMediaTrack(
      { kind: 'audio' } as MediaStreamTrack,
      {} as MediaStream,
    )).toThrow('public_media_contract_missing');
    peer.ontrack?.({ track: remoteTrack, streams: [] } as unknown as RTCTrackEvent);

    expect(remoteTrack.stop).toHaveBeenCalledOnce();
    expect(peer.close).toHaveBeenCalledOnce();
    expect(service.state$.value).toBe('failed');
    expect(service.auditLog).toContainEqual(expect.objectContaining({
      type: 'public_media_rejected',
      detail: 'public_media_contract_missing',
    }));
  });

  it('buffers ICE raised by setLocalDescription until the delayed offer POST is acknowledged', async () => {
    const offerAcknowledged = deferred<void>();
    FakePeerConnection.emitIceDuringLocalDescription = true;
    signaling.send.mockImplementation((message: SignalMessage) => (
      message.type === 'offer' ? offerAcknowledged.promise : Promise.resolve()
    ));

    await service.startSession('session-delayed-offer', true, 'bob');
    await settleAsyncWork();

    expect(signaling.send.mock.calls.map(([message]) => message.type)).toEqual(['offer']);
    offerAcknowledged.resolve(undefined);
    await settleAsyncWork();

    expect(signaling.send.mock.calls.map(([message]) => message.type))
      .toEqual(['offer', 'ice_candidate']);
  });

  it('does not create a stale peer after the session closes during TURN issuance', async () => {
    const turnResponse = new Subject<null>();
    controlPlane.isPublicSession.mockImplementation(sessionId => sessionId === 'public-session');
    controlPlane.turnCredentials.mockReturnValue(turnResponse);

    const starting = service.startSession('public-session', false, 'bob');
    service.closeSession();
    turnResponse.next(null);
    turnResponse.complete();
    await starting;

    expect(FakePeerConnection.instances).toEqual([]);
    expect(signaling.connect).not.toHaveBeenCalled();
  });

  it('handles each signal exactly once after closing and starting another session', async () => {
    await service.startSession('session-one', false, 'bob');
    const firstPeer = FakePeerConnection.instances[0];
    service.closeSession();
    await service.startSession('session-two', false, 'carol');
    const secondPeer = FakePeerConnection.instances[1];
    signaling.send.mockClear();

    await signalHandler?.({
      type: 'offer', session_id: 'session-two', sender_id: 'carol',
      recipient_id: 'alice', payload: { type: 'offer', sdp: 'offer-sdp' },
    });

    expect(firstPeer.setRemoteDescription).not.toHaveBeenCalled();
    expect(secondPeer.setRemoteDescription).toHaveBeenCalledTimes(1);
    expect(secondPeer.createAnswer).toHaveBeenCalledTimes(1);
    expect(signaling.send).toHaveBeenCalledTimes(1);
    expect(signaling.send).toHaveBeenCalledWith(expect.objectContaining({
      type: 'answer', session_id: 'session-two',
    }));
  });

  it('terminates the session when the awaitable signal handler rejects', async () => {
    await service.startSession('session-handler-failed', false, 'bob');
    const peer = FakePeerConnection.instances[0];
    peer.setRemoteDescription.mockRejectedValueOnce(new Error('set_remote_description_failed'));

    await expect(signalHandler?.({
      type: 'offer', session_id: 'session-handler-failed', sender_id: 'bob',
      recipient_id: 'alice', payload: { type: 'offer', sdp: 'bad-offer' },
    })).rejects.toThrow('set_remote_description_failed');

    expect(peer.close).toHaveBeenCalledTimes(1);
    expect(service.state$.value).toBe('failed');
    expect(service.auditLog).toContainEqual(expect.objectContaining({
      type: 'signal_error', session_id: 'session-handler-failed',
      detail: 'set_remote_description_failed',
    }));
  });

  it('fences an in-flight signal chain before a replacement session can be touched', async () => {
    await service.startSession('session-one', false, 'bob');
    const firstPeer = FakePeerConnection.instances[0];
    const remoteDescriptionGate = deferred<void>();
    firstPeer.remoteDescriptionGate = remoteDescriptionGate;
    const applyingOldSignal = signalHandler?.({
      type: 'offer', session_id: 'session-one', sender_id: 'bob',
      recipient_id: 'alice', payload: { type: 'offer', sdp: 'old-offer' },
    });
    await Promise.resolve();
    expect(firstPeer.setRemoteDescription).toHaveBeenCalledTimes(1);

    service.closeSession();
    await service.startSession('session-two', false, 'carol');
    const secondPeer = FakePeerConnection.instances[1];
    signaling.send.mockClear();
    remoteDescriptionGate.resolve(undefined);
    await applyingOldSignal;

    expect(firstPeer.createAnswer).not.toHaveBeenCalled();
    expect(secondPeer.setRemoteDescription).not.toHaveBeenCalled();
    expect(signaling.send).not.toHaveBeenCalled();
  });
});

function deferred<T>(): Deferred<T> {
  let resolvePromise!: (value: T) => void;
  const promise = new Promise<T>(resolve => { resolvePromise = resolve; });
  return { promise, resolve: resolvePromise };
}

async function settleAsyncWork(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}
