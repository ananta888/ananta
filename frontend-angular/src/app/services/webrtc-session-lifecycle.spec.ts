import { TestBed } from '@angular/core/testing';
import { Subject } from 'rxjs';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { NetworkProfileService } from './network-profile.service';
import { OidcAuthService } from './oidc-auth.service';
import { WebrtcChunkReassemblyStore } from './webrtc-chunk-reassembly.store';
import { SignalMessage, WebrtcSignalingService } from './webrtc-signaling.service';
import { WebrtcSessionService } from './webrtc-session.service';

interface Deferred<T> {
  readonly promise: Promise<T>;
  resolve(value: T): void;
}

class FakePeerConnection {
  static instances: FakePeerConnection[] = [];

  connectionState: RTCPeerConnectionState = 'new';
  signalingState: RTCSignalingState = 'stable';
  onicecandidate: ((event: RTCPeerConnectionIceEvent) => void) | null = null;
  onconnectionstatechange: (() => void) | null = null;
  ontrack: ((event: RTCTrackEvent) => void) | null = null;
  ondatachannel: ((event: RTCDataChannelEvent) => void) | null = null;
  remoteDescriptionGate: Deferred<void> | null = null;

  readonly setRemoteDescription = vi.fn(async () => {
    await this.remoteDescriptionGate?.promise;
  });
  readonly createAnswer = vi.fn(async (): Promise<RTCSessionDescriptionInit> => ({
    type: 'answer', sdp: 'answer-sdp',
  }));
  readonly setLocalDescription = vi.fn(async () => undefined);
  readonly addIceCandidate = vi.fn(async () => undefined);
  readonly close = vi.fn(() => { this.connectionState = 'closed'; });

  constructor() {
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
    connect: ReturnType<typeof vi.fn>;
    disconnect: ReturnType<typeof vi.fn>;
    send: ReturnType<typeof vi.fn>;
    fallbackToHubRelay: ReturnType<typeof vi.fn>;
  };
  let service: WebrtcSessionService;

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
    signaling = {
      message$: new Subject<SignalMessage>(),
      connect: vi.fn(),
      disconnect: vi.fn(),
      send: vi.fn(),
      fallbackToHubRelay: vi.fn(),
    };
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ providers: [
      WebrtcSessionService,
      { provide: NetworkProfileService, useValue: { current: {
        ice_servers: [], signaling_url: '', require_e2e_payload_encryption: true,
      } } },
      { provide: WebrtcSignalingService, useValue: signaling },
      { provide: OidcAuthService, useValue: { sessionNonce: 'nonce' } },
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

  it('handles each signal exactly once after closing and starting another session', async () => {
    await service.startSession('session-one', false, 'bob');
    const firstPeer = FakePeerConnection.instances[0];
    service.closeSession();
    await service.startSession('session-two', false, 'carol');
    const secondPeer = FakePeerConnection.instances[1];
    signaling.send.mockClear();

    signaling.message$.next({
      type: 'offer', session_id: 'session-two', sender_id: 'carol',
      recipient_id: 'alice', payload: { type: 'offer', sdp: 'offer-sdp' },
    });
    await settleSignalChain();

    expect(firstPeer.setRemoteDescription).not.toHaveBeenCalled();
    expect(secondPeer.setRemoteDescription).toHaveBeenCalledTimes(1);
    expect(secondPeer.createAnswer).toHaveBeenCalledTimes(1);
    expect(signaling.send).toHaveBeenCalledTimes(1);
    expect(signaling.send).toHaveBeenCalledWith(expect.objectContaining({
      type: 'answer', session_id: 'session-two',
    }));
  });

  it('fences an in-flight signal chain before a replacement session can be touched', async () => {
    await service.startSession('session-one', false, 'bob');
    const firstPeer = FakePeerConnection.instances[0];
    const remoteDescriptionGate = deferred<void>();
    firstPeer.remoteDescriptionGate = remoteDescriptionGate;
    signaling.message$.next({
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
    await settleSignalChain();

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

async function settleSignalChain(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await new Promise<void>(resolve => setTimeout(resolve, 0));
}
