import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, Subject, of } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AgentDirectoryService } from './agent-directory.service';
import { HubApiCoreService } from './hub-api-core.service';
import { NetworkProfileService } from './network-profile.service';
import {
  SEMANTIC_DC_VERSION,
  SemanticDataChannelMessage,
} from './webrtc-datachannel.service';
import { WebrtcSessionService } from './webrtc-session.service';
import { WebrtcTransportService } from './webrtc-transport.service';
import { PairSessionControlPlaneService } from './pair-session-control-plane.service';

const CIPHERTEXT_DIGEST = '2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881';

function message(): SemanticDataChannelMessage {
  return {
    version: SEMANTIC_DC_VERSION,
    traffic_class: 'control',
    message_id: 'message-1',
    session_id: 'session-1',
    epoch: 2,
    sender_id: 'alice',
    audience_id: 'bob',
    sequence: 1,
    expires_at_ms: Date.now() + 60_000,
    compression: 'none',
    security: { algorithm: 'AES-GCM-256', key_id: 'pair-key-1' },
    payload_bytes: 1,
    payload_digest: CIPHERTEXT_DIGEST,
    ciphertext: btoa('x'),
  };
}

describe('WebrtcTransportService semantic relay', () => {
  const request = vi.fn();
  const get = vi.fn();
  const post = vi.fn();
  const closeSession = vi.fn();
  const recreationRequired = new Set<string>();
  const webrtc = {
    state$: new BehaviorSubject('idle'),
    dataChannelState$: new BehaviorSubject<RTCDataChannelState | 'absent'>('absent'),
    failureReason$: new BehaviorSubject<string | null>(null),
    dcMessage$: new Subject(),
    semanticMessage$: new Subject(),
    startSession: vi.fn(),
    closeSession,
    retireSession: vi.fn((sessionId: string) => recreationRequired.delete(sessionId)),
    isSessionRecreationRequired: vi.fn((sessionId: string) => recreationRequired.has(sessionId)),
    sendDc: vi.fn(),
    sendSemantic: vi.fn(),
  };
  const profile = { current: { transport_order: ['hub_relay'] as string[] } };
  const controlPlane = {
    isPublicSession: vi.fn(() => false),
    assertSessionAvailable: vi.fn(),
  };
  let service: WebrtcTransportService;

  beforeEach(() => {
    vi.useFakeTimers();
    request.mockReset();
    get.mockReset();
    post.mockReset();
    closeSession.mockReset();
    closeSession.mockImplementation(() => webrtc.state$.next('closed'));
    recreationRequired.clear();
    webrtc.startSession.mockReset();
    webrtc.retireSession.mockClear();
    webrtc.isSessionRecreationRequired.mockClear();
    webrtc.sendDc.mockReset();
    webrtc.sendDc.mockReturnValue(false);
    webrtc.state$.next('idle');
    webrtc.dataChannelState$.next('absent');
    profile.current.transport_order = ['hub_relay'];
    controlPlane.isPublicSession.mockReset();
    controlPlane.isPublicSession.mockReturnValue(false);
    controlPlane.assertSessionAvailable.mockReset();
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        WebrtcTransportService,
        { provide: WebrtcSessionService, useValue: webrtc },
        { provide: NetworkProfileService, useValue: profile },
        { provide: HubApiCoreService, useValue: { request, get, post } },
        { provide: PairSessionControlPlaneService, useValue: controlPlane },
        {
          provide: AgentDirectoryService,
          useValue: { list: () => [{ name: 'hub', role: 'hub', url: 'https://hub.test' }] },
        },
      ],
    });
    service = TestBed.inject(WebrtcTransportService);
  });

  afterEach(() => {
    service.close();
    vi.useRealTimers();
  });

  it('fails closed before direct WebRTC when no exact remote peer is bound', async () => {
    profile.current.transport_order = ['webrtc', 'hub_relay'];

    await expect(service.open('session-1', true, { semanticEpoch: 2 }))
      .rejects.toThrow('webrtc_remote_peer_required');

    expect(service.mode$.value).toBe('idle');
    expect(webrtc.startSession).not.toHaveBeenCalled();
  });

  it('uses an available Hub relay for an explicitly admitted unbound legacy peer', async () => {
    profile.current.transport_order = ['webrtc', 'hub_relay'];

    await service.open('legacy-session', false, {
      semanticEpoch: 1,
      unboundPeerFallback: 'hub_relay',
    });

    expect(service.mode$.value).toBe('hub_relay');
    expect(webrtc.startSession).not.toHaveBeenCalled();
  });

  it('does not invent a legacy relay when the selected profile has none', async () => {
    profile.current.transport_order = ['webrtc'];

    await expect(service.open('legacy-session', false, {
      semanticEpoch: 1,
      unboundPeerFallback: 'hub_relay',
    })).rejects.toThrow('webrtc_remote_peer_required');

    expect(service.mode$.value).toBe('idle');
    expect(webrtc.startSession).not.toHaveBeenCalled();
  });

  it('closes the failed direct peer before exposing Hub-relay mode', async () => {
    profile.current.transport_order = ['webrtc', 'hub_relay'];
    const lifecycle: string[] = [];
    closeSession.mockImplementation(() => {
      lifecycle.push('peer_closed');
      webrtc.state$.next('closed');
    });
    const modeSubscription = service.mode$.subscribe(mode => {
      if (mode === 'hub_relay') lifecycle.push('relay_open');
    });

    await service.open('session-1', true, { semanticEpoch: 2, remotePeerId: 'bob' });
    webrtc.state$.next('failed');

    expect(lifecycle).toEqual(['peer_closed', 'relay_open']);
    expect(closeSession).toHaveBeenCalledTimes(1);
    expect(service.mode$.value).toBe('hub_relay');
    modeSubscription.unsubscribe();
  });

  it('never sends a public Pair session through the local Hub relay', async () => {
    profile.current.transport_order = ['webrtc', 'hub_relay'];
    controlPlane.isPublicSession.mockImplementation(sessionId => sessionId === 'public-session');

    await service.open('public-session', true, { semanticEpoch: 2, remotePeerId: 'bob' });
    webrtc.state$.next('failed');

    expect(closeSession).toHaveBeenCalledTimes(1);
    expect(service.mode$.value).toBe('idle');
    expect(vi.getTimerCount()).toBe(0);
    expect(() => service.send('chat', { text: 'legacy-canary' }))
      .toThrow('pair_transport_not_open');
    expect(() => service.sendView({ message_id: 'm1', encrypted_payload: 'legacy-canary' }))
      .toThrow('pair_transport_not_open');
    vi.advanceTimersByTime(5_000);
    expect(post).not.toHaveBeenCalled();
    expect(get).not.toHaveBeenCalled();
  });

  it('latches terminal public signaling failure and blocks every same-session reopen', async () => {
    profile.current.transport_order = ['webrtc'];
    controlPlane.isPublicSession.mockImplementation(sessionId => sessionId === 'public-session');
    await service.open('public-session', true, { semanticEpoch: 2, remotePeerId: 'bob' });
    recreationRequired.add('public-session');

    webrtc.state$.next('failed');

    expect(service.mode$.value).toBe('idle');
    expect(service.terminalFailure$.value).toEqual({
      kind: 'local_recreation_required',
      sessionId: 'public-session',
      reasonCode: 'public_signaling_session_recreation_required',
    });
    await expect(service.open(
      'public-session', true, { semanticEpoch: 2, remotePeerId: 'bob' },
    )).rejects.toThrow('public_signaling_session_recreation_required');
    expect(webrtc.startSession).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('emits a distinct server-terminal failure without latching local recreation', async () => {
    profile.current.transport_order = ['webrtc'];
    controlPlane.isPublicSession.mockImplementation(sessionId => sessionId === 'public-session');
    await service.open('public-session', true, { semanticEpoch: 2, remotePeerId: 'bob' });
    webrtc.failureReason$.next('session_not_found');

    webrtc.state$.next('failed');

    expect(service.terminalFailure$.value).toEqual({
      kind: 'server_terminal',
      sessionId: 'public-session',
      reasonCode: 'session_not_found',
    });
    expect(service.isSessionRecreationRequired('public-session')).toBe(false);
    expect(service.mode$.value).toBe('idle');
  });

  it('classifies a raw terminal start rejection before its HttpError reaches Error', async () => {
    profile.current.transport_order = ['webrtc'];
    controlPlane.isPublicSession.mockImplementation(sessionId => sessionId === 'public-session');
    const terminalError = {
      status: 409,
      error: { reason_code: 'session_inactive' },
    };
    webrtc.startSession.mockRejectedValueOnce(terminalError);

    await expect(service.open(
      'public-session', true, { semanticEpoch: 2, remotePeerId: 'bob' },
    )).rejects.toBe(terminalError);

    expect(service.terminalFailure$.value).toEqual({
      kind: 'server_terminal',
      sessionId: 'public-session',
      reasonCode: 'session_inactive',
    });
    expect(service.isSessionRecreationRequired('public-session')).toBe(false);
    expect(service.mode$.value).toBe('idle');
  });

  it('clears a terminal reopen latch only through exact-session retirement', async () => {
    profile.current.transport_order = ['webrtc'];
    controlPlane.isPublicSession.mockImplementation(sessionId => sessionId === 'public-session');
    recreationRequired.add('public-session');
    await expect(service.open(
      'public-session', true, { semanticEpoch: 2, remotePeerId: 'bob' },
    )).rejects.toThrow('public_signaling_session_recreation_required');

    service.close();
    expect(service.isSessionRecreationRequired('public-session')).toBe(true);
    service.retireSession('public-session');

    expect(webrtc.retireSession).toHaveBeenCalledWith('public-session');
    expect(service.isSessionRecreationRequired('public-session')).toBe(false);
    expect(service.terminalFailure$.value).toBeNull();
    await expect(service.open(
      'public-session', true, { semanticEpoch: 2, remotePeerId: 'bob' },
    )).resolves.toBeUndefined();
  });

  it('rejects raw cursor traffic for public Pair while keeping secure sync separate', async () => {
    profile.current.transport_order = ['webrtc'];
    controlPlane.isPublicSession.mockImplementation(sessionId => sessionId === 'public-session');
    const received: unknown[] = [];
    service.message$.subscribe(message => received.push(message));
    await service.open('public-session', true, { semanticEpoch: 2, remotePeerId: 'bob' });

    expect(() => service.send('cursor', { x: 0.5, y: 0.5 }))
      .toThrow('public_raw_cursor_transport_disabled');
    webrtc.dcMessage$.next({ type: 'cursor', payload: { sender_id: 'bob', x: 0.2, y: 0.3 } });

    expect(webrtc.sendDc).not.toHaveBeenCalled();
    expect(received).toEqual([]);
  });

  it('reports exact DataChannel readiness and only accepts actually queued view payloads', async () => {
    profile.current.transport_order = ['webrtc'];
    controlPlane.isPublicSession.mockImplementation(sessionId => sessionId === 'public-session');

    await service.open('public-session', true, {
      semanticEpoch: 2,
      remotePeerId: 'bob',
    });

    expect(service.viewTransportState$.value).toMatchObject({
      sessionId: 'public-session',
      semanticEpoch: 2,
      ready: false,
    });
    expect(service.sendView({ message_id: 'before-open', encrypted_payload: 'sealed' })).toBe(false);

    webrtc.sendDc.mockReturnValue(true);
    webrtc.dataChannelState$.next('open');

    expect(service.viewTransportState$.value).toMatchObject({
      sessionId: 'public-session',
      semanticEpoch: 2,
      ready: true,
    });
    expect(service.sendView({ message_id: 'after-open', encrypted_payload: 'sealed' })).toBe(true);

    const openGeneration = service.viewTransportState$.value.generation;
    service.setSemanticEpoch(3);
    expect(service.viewTransportState$.value).toEqual({
      sessionId: 'public-session',
      semanticEpoch: 3,
      generation: openGeneration,
      ready: true,
    });

    service.close();
    expect(service.viewTransportState$.value).toMatchObject({
      sessionId: '',
      semanticEpoch: 0,
      ready: false,
    });
    expect(service.viewTransportState$.value.generation).toBeGreaterThan(openGeneration);
  });

  it('does not treat idle chat or view sends as Hub relay traffic', () => {
    expect(() => service.send('chat', { encrypted_payload: 'secret' }))
      .toThrow('pair_transport_not_open');
    expect(() => service.sendView({ message_id: 'm1', encrypted_payload: 'secret' }))
      .toThrow('pair_transport_not_open');
    expect(post).not.toHaveBeenCalled();
  });

  it('checks the pinned public authority before every DataChannel send', async () => {
    profile.current.transport_order = ['webrtc'];
    controlPlane.isPublicSession.mockImplementation(sessionId => sessionId === 'public-session');
    await service.open('public-session', true, { semanticEpoch: 2, remotePeerId: 'bob' });
    controlPlane.assertSessionAvailable.mockImplementation(() => {
      throw new Error('public_session_authentication_lost');
    });

    expect(() => service.send('chat', { encrypted_payload: 'secret' }))
      .toThrow('public_session_authentication_lost');
    expect(() => service.sendView({ message_id: 'm1', encrypted_payload: 'secret' }))
      .toThrow('public_session_authentication_lost');
    expect(webrtc.sendDc).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
  });

  it('posts the exact framed message and settles the send receipt once', async () => {
    request.mockReturnValue(of({ ok: true, cursor: 7 }));
    await service.open('session-1', true, { semanticEpoch: 2 });

    const operation = await service.sendSemantic(message());
    const receipt = await operation.result;

    expect(receipt.state).toBe('acknowledged');
    expect(receipt.ackCursor).toBe(7);
    expect(request).toHaveBeenCalledOnce();
    const [, url, baseUrl, options] = request.mock.calls[0];
    expect(url).toBe('https://hub.test/share-sessions/session-1/semantic-relay');
    expect(baseUrl).toBe('https://hub.test');
    expect(options.headers['Content-Type']).toBe('application/vnd.ananta.webrtc.v1');
    expect(options.body).toMatch(/^ANANTA-DC1 control 1 [0-9]+\n/);
  });

  it('dispatches repeated relay pages idempotently and acknowledges their cursor', async () => {
    const stored = { ...message(), cursor: 1 };
    get.mockImplementation((url: string) => url.includes('/semantic-relay?')
      ? of({ ok: true, messages: [stored], cursor: 1 })
      : of({ ok: true, messages: [], cursor: '' }));
    post.mockReturnValue(of({ ok: true, acknowledged_cursor: 1 }));
    const received: SemanticDataChannelMessage[] = [];
    service.semanticMessage$.subscribe(value => received.push(value));
    await service.open('session-1', true, {
      semanticEpoch: 2,
      semanticTrafficClasses: ['control'],
    });

    const relay = service as unknown as {
      dispatchSemanticRelayPage(trafficClass: 'control', page: unknown): Promise<void>;
    };
    const page = { ok: true, messages: [stored], cursor: 1 };
    await relay.dispatchSemanticRelayPage('control', page);
    await relay.dispatchSemanticRelayPage('control', page);

    expect(received.map(value => value.message_id)).toEqual(['message-1']);
    expect(post).toHaveBeenCalledWith(
      'https://hub.test/share-sessions/session-1/semantic-relay/ack',
      { traffic_class: 'control', epoch: 2, cursor: 1 },
      'https://hub.test',
    );
    service.close();
    expect(vi.getTimerCount()).toBe(0);
  });
});
