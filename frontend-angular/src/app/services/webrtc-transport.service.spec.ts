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
  const webrtc = {
    state$: new BehaviorSubject('idle'),
    dcMessage$: new Subject(),
    semanticMessage$: new Subject(),
    startSession: vi.fn(),
    closeSession,
    sendDc: vi.fn(),
    sendSemantic: vi.fn(),
  };
  const profile = { current: { transport_order: ['hub_relay'] as string[] } };
  let service: WebrtcTransportService;

  beforeEach(() => {
    vi.useFakeTimers();
    request.mockReset();
    get.mockReset();
    post.mockReset();
    closeSession.mockReset();
    webrtc.startSession.mockReset();
    webrtc.state$.next('idle');
    profile.current.transport_order = ['hub_relay'];
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        WebrtcTransportService,
        { provide: WebrtcSessionService, useValue: webrtc },
        { provide: NetworkProfileService, useValue: profile },
        { provide: HubApiCoreService, useValue: { request, get, post } },
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
