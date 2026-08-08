import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { WebrtcSignalingService, SignalingStatus } from './webrtc-signaling.service';
import { AgentDirectoryService } from './agent-directory.service';
import { HubApiCoreService } from './hub-api-core.service';
import { of } from 'rxjs';

describe('WebRtcSignalingService authenticated Hub signaling', () => {
  let service: WebrtcSignalingService;
  let posted: Array<{ url: string; body: Record<string, unknown> }>;
  let polled: string[];
  let polledResponse: unknown;
  const OriginalWS = globalThis.WebSocket;

  beforeEach(() => {
    posted = [];
    polled = [];
    polledResponse = { ok: true, data: { signals: [] } };
    (globalThis as any).WebSocket = class ForbiddenWebSocket {
      constructor() {
        throw new Error('browser_websocket_must_not_be_used');
      }
    };
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        WebrtcSignalingService,
        {
          provide: AgentDirectoryService,
          useValue: { list: () => [{ role: 'hub', url: 'https://hub.test' }] },
        },
        {
          provide: HubApiCoreService,
          useValue: {
            get: (url: string) => {
              polled.push(url);
              return of(polledResponse);
            },
            post: (url: string, body: Record<string, unknown>) => {
              posted.push({ url, body });
              return of({ ok: true });
            },
          },
        },
      ],
    });
    service = TestBed.inject(WebrtcSignalingService);
  });

  afterEach(() => {
    globalThis.WebSocket = OriginalWS;
  });

  it('starts as disconnected', () => {
    expect(service.status$.value).toBe<SignalingStatus>('disconnected');
  });

  it('routes a configured public WSS endpoint through the authenticated Hub without opening WebSocket', () => {
    service.connect('wss://signaling.test/signaling', 'session-1', 'peer-b');
    expect(service.status$.value).toBe<SignalingStatus>('connected');
    (service as unknown as { pollSignals(): void }).pollSignals();
    expect(polled).toEqual([
      'https://hub.test/api/webrtc/sessions/session-1/signal?since=',
    ]);
    service.hardDisconnect();
  });

  it('hardDisconnect is idempotent and leaves no reconnect path', () => {
    service.connect('wss://signaling.test/signaling', 'session-1', 'peer-b');
    service.hardDisconnect();
    expect(() => service.hardDisconnect()).not.toThrow();
    expect(service.status$.value).toBe('disconnected');
  });

  it('binds the remote peer to every Hub-relay signal', () => {
    service.connect('', 'session-1', 'peer-b');
    service.send({
      type: 'offer', session_id: 'session-1', recipient_id: 'stale-peer', payload: { sdp: 'v=0' },
    });

    expect(posted).toEqual([{
      url: 'https://hub.test/api/webrtc/sessions/session-1/signal',
      body: {
        type: 'offer',
        session_id: 'session-1',
        recipient_id: 'peer-b',
        payload: { sdp: 'v=0' },
      },
    }]);
    service.hardDisconnect();
  });

  it('fails closed instead of polling or sending without an exact recipient_id', () => {
    service.connect('', 'session-1');
    service.send({ type: 'offer', session_id: 'session-1', payload: { sdp: 'v=0' } });
    (service as unknown as { pollSignals(): void }).pollSignals();

    expect(service.status$.value).toBe('failed');
    expect(posted).toEqual([]);
    expect(polled).toEqual([]);
    service.hardDisconnect();
  });

  it('dispatches the Hub legacy data envelope and rejects foreign sessions', () => {
    const observed: string[] = [];
    service.connect('', 'session-1', 'peer-b');
    service.message$.subscribe(message => observed.push(String(message.payload)));
    polledResponse = {
      ok: true,
      data: {
        signals: [
          { type: 'offer', session_id: 'session-1', sender_id: 'peer-b', payload: 'accepted' },
          { type: 'answer', session_id: 'foreign-session', sender_id: 'peer-b', payload: 'rejected' },
          { type: 'answer', session_id: 'session-1', sender_id: 'peer-c', payload: 'wrong-peer' },
        ],
      },
    };

    (service as unknown as { pollSignals(): void }).pollSignals();

    expect(observed).toEqual(['accepted']);
    service.hardDisconnect();
  });
});
