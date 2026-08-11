import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, Subject, of, throwError } from 'rxjs';
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
    failureReason$: BehaviorSubject<string | null>;
    connect: ReturnType<typeof vi.fn>;
    disconnect: ReturnType<typeof vi.fn>;
    assertSessionReusable: ReturnType<typeof vi.fn>;
    isSessionRecreationRequired: ReturnType<typeof vi.fn>;
    markSessionRecreationRequired: ReturnType<typeof vi.fn>;
    retireSession: ReturnType<typeof vi.fn>;
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
      failureReason$: new BehaviorSubject<string | null>(null),
      connect: vi.fn(),
      disconnect: vi.fn(),
      assertSessionReusable: vi.fn(),
      isSessionRecreationRequired: vi.fn(() => false),
      markSessionRecreationRequired: vi.fn(),
      retireSession: vi.fn(),
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
    controlPlane.turnCredentials.mockReset();
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

  it('preserves an explicit server-terminal signaling reason over a local latch', async () => {
    controlPlane.isPublicSession.mockImplementation(sessionId => sessionId === 'public-session');
    await service.startSession('public-session', false, 'bob');
    signaling.failureReason$.next('session_expired');
    signaling.isSessionRecreationRequired.mockReturnValue(true);

    signaling.status$.next('failed');

    expect(service.state$.value).toBe('failed');
    expect(service.failureReason$.value).toBe('session_expired');
    expect(FakePeerConnection.instances[0].close).toHaveBeenCalledOnce();
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

  it('rejects a terminal public signaling generation before requesting TURN', async () => {
    controlPlane.isPublicSession.mockImplementation(sessionId => sessionId === 'public-session');
    signaling.assertSessionReusable.mockImplementation(() => {
      throw new Error('public_signaling_session_recreation_required');
    });

    await expect(service.startSession('public-session', false, 'bob'))
      .rejects.toThrow('public_signaling_session_recreation_required');

    expect(controlPlane.turnCredentials).not.toHaveBeenCalled();
    expect(FakePeerConnection.instances).toEqual([]);
    expect(service.failureReason$.value).toBe('public_signaling_session_recreation_required');
  });

  it.each([
    [404, 'session_not_found'],
    [409, 'session_inactive'],
  ])('does not allocate a peer after terminal TURN HTTP %s (%s)', async (status, reasonCode) => {
    controlPlane.isPublicSession.mockImplementation(sessionId => sessionId === 'public-session');
    const terminalError = { status, error: { error: reasonCode } };
    controlPlane.turnCredentials.mockReturnValue(throwError(() => terminalError));

    await expect(service.startSession('public-session', false, 'bob'))
      .rejects.toBe(terminalError);

    expect(FakePeerConnection.instances).toEqual([]);
    expect(signaling.bindMessageHandler).not.toHaveBeenCalled();
    expect(signaling.connect).not.toHaveBeenCalled();
    expect(service.failureReason$.value).toBe(reasonCode);
  });

  it.each([401, 403])(
    'quarantines a generic TURN authority rejection (HTTP %s) before peer allocation',
    async status => {
      controlPlane.isPublicSession.mockImplementation(sessionId => sessionId === 'public-session');
      const authorityError = { status, error: { error: 'oidc_token_rejected' } };
      controlPlane.turnCredentials.mockReturnValue(throwError(() => authorityError));

      await expect(service.startSession('public-session', false, 'bob'))
        .rejects.toBe(authorityError);

      expect(signaling.markSessionRecreationRequired).toHaveBeenCalledOnce();
      expect(signaling.markSessionRecreationRequired).toHaveBeenCalledWith('public-session');
      expect(service.failureReason$.value).toBe('public_signaling_session_recreation_required');
      expect(FakePeerConnection.instances).toEqual([]);
      expect(signaling.bindMessageHandler).not.toHaveBeenCalled();
      expect(signaling.connect).not.toHaveBeenCalled();
      expect(controlPlane.turnCredentials).toHaveBeenCalledOnce();
    },
  );

  it.each([429, 503])('retains STUN fallback for transient TURN HTTP %s', async status => {
    controlPlane.isPublicSession.mockImplementation(sessionId => sessionId === 'public-session');
    controlPlane.turnCredentials.mockReturnValue(throwError(() => ({
      status,
      error: { error: status === 429 ? 'rate_limited' : 'turn_unavailable' },
      headers: { get: () => status === 429 ? '5' : null },
    })));

    await expect(service.startSession('public-session', false, 'bob')).resolves.toBeUndefined();

    expect(FakePeerConnection.instances).toHaveLength(1);
    expect(FakePeerConnection.instances[0].configuration?.iceServers).toEqual([
      { urls: PUBLIC_WEBRTC_STUN_URL },
    ]);
    expect(signaling.connect).toHaveBeenCalledWith('', 'public-session', 'bob');
  });

  it('buffers retained remote ICE until the matching remote description is applied', async () => {
    controlPlane.isPublicSession.mockImplementation(sessionId => sessionId === 'public-session');
    await service.startSession('public-session', false, 'bob');
    const peer = FakePeerConnection.instances[0];

    await signalHandler?.({
      id: 'signal-1', type: 'ice_candidate', session_id: 'public-session',
      sender_id: 'bob', recipient_id: 'alice', payload: { candidate: 'candidate:retained' },
    });
    expect(peer.addIceCandidate).not.toHaveBeenCalled();

    await signalHandler?.({
      id: 'signal-2', type: 'offer', session_id: 'public-session',
      sender_id: 'bob', recipient_id: 'alice', payload: { type: 'offer', sdp: 'offer-sdp' },
    });

    expect(peer.setRemoteDescription).toHaveBeenCalledOnce();
    expect(peer.addIceCandidate).toHaveBeenCalledOnce();
    expect(peer.addIceCandidate.mock.calls[0][0]).toMatchObject({
      init: { candidate: 'candidate:retained' },
    });
    expect(service.auditLog).toContainEqual(expect.objectContaining({
      type: 'remote_ice_flushed', session_id: 'public-session', detail: 'count=1',
    }));
  });

  it('does not carry buffered remote ICE into a replacement generation', async () => {
    controlPlane.isPublicSession.mockImplementation(sessionId => sessionId.startsWith('public-'));
    await service.startSession('public-one', false, 'bob');
    await signalHandler?.({
      id: 'signal-1', type: 'ice_candidate', session_id: 'public-one',
      sender_id: 'bob', recipient_id: 'alice', payload: { candidate: 'candidate:old' },
    });

    service.closeSession();
    await service.startSession('public-two', false, 'carol');
    const replacement = FakePeerConnection.instances[1];
    await signalHandler?.({
      id: 'signal-2', type: 'offer', session_id: 'public-two',
      sender_id: 'carol', recipient_id: 'alice', payload: { type: 'offer', sdp: 'new' },
    });

    expect(replacement.addIceCandidate).not.toHaveBeenCalled();
  });

  it('bounds remote ICE retained before SDP', async () => {
    controlPlane.isPublicSession.mockImplementation(sessionId => sessionId === 'public-session');
    await service.startSession('public-session', false, 'bob');
    const candidate = (index: number): SignalMessage => ({
      id: `signal-${index}`, type: 'ice_candidate', session_id: 'public-session',
      sender_id: 'bob', recipient_id: 'alice', payload: { candidate: `candidate:${index}` },
    });

    for (let index = 0; index < 256; index += 1) await signalHandler?.(candidate(index));
    await expect(signalHandler?.(candidate(256)))
      .rejects.toThrow('webrtc_remote_ice_buffer_overflow');
    expect(FakePeerConnection.instances[0].addIceCandidate).not.toHaveBeenCalled();
    expect(signaling.markSessionRecreationRequired).toHaveBeenCalledWith('public-session');
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

  it('retires exact-session signaling metadata after confirmed teardown', async () => {
    await service.startSession('session-retired', false, 'bob');

    service.retireSession('session-retired');

    expect(signaling.retireSession).toHaveBeenCalledWith('session-retired');
    expect(service.state$.value).toBe('closed');
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
