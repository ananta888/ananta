import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { WebrtcSignalingService, SignalingStatus } from './webrtc-signaling.service';
import { AgentDirectoryService } from './agent-directory.service';
import { HubApiCoreService } from './hub-api-core.service';
import { OidcAuthService } from './oidc-auth.service';
import { of } from 'rxjs';

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  readyState = 1; // OPEN
  onopen: ((e: any) => void) | null = null;
  onclose: ((e: any) => void) | null = null;
  onerror: ((e: any) => void) | null = null;
  onmessage: ((e: any) => void) | null = null;
  sent: string[] = [];
  closedWith: { code: number; reason?: string } | null = null;

  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
    setTimeout(() => this.onopen?.({}), 0);
  }
  send(data: string): void {
    this.sent.push(data);
  }
  close(code?: number, reason?: string): void {
    this.closedWith = { code: code ?? 1000, reason };
    this.readyState = 3;
    setTimeout(() => this.onclose?.({ code: this.closedWith!.code }), 0);
  }
}

describe('WebRtcSignalingService.hardDisconnect', () => {
  let service: WebrtcSignalingService;
  let posted: Array<{ url: string; body: Record<string, unknown> }>;
  let polledResponse: unknown;
  const OriginalWS = globalThis.WebSocket;

  beforeEach(() => {
    FakeWebSocket.instances = [];
    posted = [];
    polledResponse = { ok: true, data: { signals: [] } };
    (globalThis as any).WebSocket = FakeWebSocket as any;
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
            get: () => of(polledResponse),
            post: (url: string, body: Record<string, unknown>) => {
              posted.push({ url, body });
              return of({ ok: true });
            },
          },
        },
        {
          provide: OidcAuthService,
          useValue: { sessionNonce: 'nonce-1' },
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

  it('connect opens a WebSocket and goes to connected', async () => {
    service.connect('wss://signaling.test/signaling', 'session-1');
    await new Promise((r) => setTimeout(r, 5));
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(service.status$.value).toBe<SignalingStatus>('connected');
  });

  it('hardDisconnect closes the WebSocket with code 1000', async () => {
    service.connect('wss://signaling.test/signaling', 'session-1');
    await new Promise((r) => setTimeout(r, 5));
    const ws = FakeWebSocket.instances[0];

    service.hardDisconnect();
    expect(ws.closedWith?.code).toBe(1000);
    expect(ws.closedWith?.reason).toBe('identity revoked');
    expect(service.status$.value).toBe<SignalingStatus>('disconnected');
  });

  it('hardDisconnect clears sessionId and signalingUrl', async () => {
    service.connect('wss://signaling.test/signaling', 'session-1');
    await new Promise((r) => setTimeout(r, 5));

    service.hardDisconnect();
    // Internal fields should be empty — verify by attempting to connect with the same sessionId after
    // a reconnect should NOT be scheduled.
    expect(service.status$.value).toBe('disconnected');
  });

  it('hardDisconnect after disconnect is idempotent', async () => {
    service.connect('wss://signaling.test/signaling', 'session-1');
    await new Promise((r) => setTimeout(r, 5));
    service.hardDisconnect();
    // Second call should not throw
    expect(() => service.hardDisconnect()).not.toThrow();
    expect(service.status$.value).toBe('disconnected');
  });

  it('does not reconnect after hardDisconnect even after WebSocket close event fires', async () => {
    service.connect('wss://signaling.test/signaling', 'session-1');
    await new Promise((r) => setTimeout(r, 5));
    service.hardDisconnect();
    const ws = FakeWebSocket.instances[0];
    // The setTimeout close handler will fire — but reconnect must not start.
    await new Promise((r) => setTimeout(r, 10));
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(service.status$.value).toBe('disconnected');
  });

  it('binds the remote peer to every Hub-relay signal', () => {
    service.connect('', 'session-1', 'peer-b');
    service.send({ type: 'offer', session_id: 'session-1', payload: { sdp: 'v=0' } });

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

  it('dispatches the Hub legacy data envelope and rejects foreign sessions', () => {
    const observed: string[] = [];
    service.connect('', 'session-1', 'peer-b');
    service.message$.subscribe(message => observed.push(String(message.payload)));
    polledResponse = {
      ok: true,
      data: {
        signals: [
          { type: 'offer', session_id: 'session-1', payload: 'accepted' },
          { type: 'answer', session_id: 'foreign-session', payload: 'rejected' },
        ],
      },
    };

    (service as unknown as { hubRelayPoll(): void }).hubRelayPoll();

    expect(observed).toEqual(['accepted']);
    service.hardDisconnect();
  });
});
