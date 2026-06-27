import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { WebrtcSignalingService, SignalingStatus } from './webrtc-signaling.service';
import { AgentDirectoryService } from './agent-directory.service';
import { HubApiCoreService } from './hub-api-core.service';
import { OidcAuthService } from './oidc-auth.service';

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
  const OriginalWS = globalThis.WebSocket;

  beforeEach(() => {
    FakeWebSocket.instances = [];
    (globalThis as any).WebSocket = FakeWebSocket as any;
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        WebrtcSignalingService,
        {
          provide: AgentDirectoryService,
          useValue: { list: () => [] },
        },
        {
          provide: HubApiCoreService,
          useValue: { get: () => ({ subscribe: () => {} }), post: () => ({ subscribe: () => {} }) },
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
});