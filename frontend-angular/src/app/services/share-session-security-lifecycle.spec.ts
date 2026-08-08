import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, Subject, of } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AgentDirectoryService } from './agent-directory.service';
import { E2eEncryptionService } from './e2e-encryption.service';
import { HubApiCoreService } from './hub-api-core.service';
import { NetworkProfileService } from './network-profile.service';
import { PairSecureSequenceService } from './pair-secure-sequence.service';
import { PAIR_VIEW_CRYPTO } from './pair-view-crypto.service';
import {
  PairSecurityBootstrapState,
  PairViewSecurityBootstrapService,
} from './pair-view-security-bootstrap.service';
import { ShareSession, ShareSessionService } from './share-session.service';
import { UserAuthService } from './user-auth.service';
import { WebrtcTransportService } from './webrtc-transport.service';

const strictSession: ShareSession = {
  id: 'session-a',
  title: 'Strict Pair',
  invite_code: 'invite-a',
  mode: 'p2p',
  transport: 'webrtc',
  permissions: { chat: true },
  created_at: 1,
  expires_at: null,
  revoked_at: null,
  owner_user_id: 'alice',
  tenant_id: 'tenant-a',
  security_epoch: 3,
  security_contract_version: 1,
  security_mode: 'strict_e2ee',
};

class LifecycleCore {
  readonly posts: Array<{ url: string; body: unknown }> = [];
  session: ShareSession = { ...strictSession };

  post<T>(url: string, body: unknown) {
    this.posts.push({ url, body });
    if (url === 'https://hub.test/share-sessions' || url.endsWith('/join-by-code')) {
      return of({ ok: true, session: { ...this.session } } as T);
    }
    return of({ ok: true } as T);
  }

  get<T>(url: string) {
    if (url.endsWith('/participants')) return of({ ok: true, participants: [] } as T);
    return of({ ok: true, messages: [], cursor: '0' } as T);
  }

  delete<T>() {
    return of({ ok: true } as T);
  }
}

class LifecycleTransport {
  readonly mode$ = new BehaviorSubject<'webrtc' | 'hub_relay' | 'idle'>('idle');
  readonly message$ = new Subject<{ type: string; session_id: string; payload: unknown }>();
  readonly open = vi.fn(async (
    _sessionId: string,
    _isInitiator: boolean,
    options: { unboundPeerFallback?: 'hub_relay' },
  ) => {
    this.mode$.next(options.unboundPeerFallback === 'hub_relay' ? 'hub_relay' : 'webrtc');
  });
  readonly close = vi.fn(() => { this.mode$.next('idle'); });
  readonly setSemanticEpoch = vi.fn();
  readonly send = vi.fn();
}

class LifecycleBootstrap {
  readonly state$ = new BehaviorSubject<PairSecurityBootstrapState>({ status: 'idle' });
  currentEpoch = 3;
  confirmedRemotePeerId = '';
  result: 'waiting_for_peer' | 'fingerprint_changed' | 'failed' | 'ready' = 'waiting_for_peer';

  readonly ensure = vi.fn(async () => {
    if (this.result === 'waiting_for_peer') {
      this.confirmedRemotePeerId = '';
      this.state$.next({ status: 'waiting_for_peer' });
      return false;
    }
    if (this.result === 'fingerprint_changed') {
      this.confirmedRemotePeerId = '';
      this.state$.next({ status: 'fingerprint_changed', fingerprint: 'a'.repeat(64) });
      return false;
    }
    if (this.result === 'failed') {
      this.confirmedRemotePeerId = '';
      this.state$.next({ status: 'failed', reasonCode: 'key_confirmation_failed' });
      return false;
    }
    this.confirmedRemotePeerId = 'bob';
    this.state$.next({ status: 'ready', fingerprint: 'f'.repeat(64) });
    return true;
  });

  readonly clear = vi.fn(() => {
    this.confirmedRemotePeerId = '';
    this.state$.next({ status: 'idle' });
  });
  readonly markLegacy = vi.fn(() => this.state$.next({ status: 'legacy' }));
  readonly approveFingerprintChange = vi.fn();
}

describe('ShareSessionService verified direct-transport lifecycle', () => {
  let core: LifecycleCore;
  let transport: LifecycleTransport;
  let bootstrap: LifecycleBootstrap;
  let auth: { userPayload: Record<string, string> };
  let service: ShareSessionService;

  beforeEach(() => {
    core = new LifecycleCore();
    transport = new LifecycleTransport();
    bootstrap = new LifecycleBootstrap();
    auth = { userPayload: { sub: 'alice' } };
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ providers: [
      { provide: HubApiCoreService, useValue: core },
      { provide: AgentDirectoryService, useValue: {
        list: () => [{ role: 'hub', url: 'https://hub.test' }],
      } },
      { provide: UserAuthService, useValue: auth },
      { provide: WebrtcTransportService, useValue: transport },
      { provide: NetworkProfileService, useValue: {
        current: { transport_order: ['webrtc', 'hub_relay'] },
      } },
      { provide: E2eEncryptionService, useValue: {
        ensureLocalKeyPair: vi.fn(async () => ({
          publicKeySpkiB64: 'public-key', fingerprint: 'device-fingerprint',
        })),
      } },
      { provide: PAIR_VIEW_CRYPTO, useValue: {
        ready: vi.fn(() => false), seal: vi.fn(), open: vi.fn(), clear: vi.fn(),
      } },
      { provide: PairSecureSequenceService, useValue: {
        next: vi.fn(() => 1), clearScope: vi.fn(),
      } },
      { provide: PairViewSecurityBootstrapService, useValue: bootstrap },
    ] });
    service = TestBed.runInInjectionContext(() => new ShareSessionService());
  });

  afterEach(() => {
    service.ngOnDestroy();
    TestBed.resetTestingModule();
  });

  it('creates a strict session but keeps transport idle while waiting_for_peer', async () => {
    bootstrap.result = 'waiting_for_peer';

    await service.createSession('Strict Pair', { chat: true }, null);
    await settleAsyncWork();

    expect(bootstrap.state$.value.status).toBe('waiting_for_peer');
    expect(transport.mode$.value).toBe('idle');
    expect(transport.open).not.toHaveBeenCalled();
  });

  it('opens an owner-created session only when ready and binds the exact remote peer', async () => {
    bootstrap.result = 'ready';

    await service.createSession('Strict Pair', { chat: true }, null);
    await settleAsyncWork();

    expect(bootstrap.state$.value.status).toBe('ready');
    expect(transport.open).toHaveBeenCalledTimes(1);
    expect(transport.open).toHaveBeenCalledWith('session-a', true, {
      semanticEpoch: 3,
      remotePeerId: 'bob',
    });
  });

  it.each([
    ['waiting_for_peer', 'waiting_for_peer'],
    ['fingerprint_changed', 'fingerprint_changed'],
    ['failed', 'failed'],
  ] as const)('closes an established strict transport when binding becomes %s', async (result, status) => {
    bootstrap.result = 'ready';
    await service.createSession('Strict Pair', { chat: true }, null);
    await settleAsyncWork();
    expect(transport.mode$.value).toBe('webrtc');
    transport.close.mockClear();

    bootstrap.result = result;
    await refreshSecurity(service);

    expect(bootstrap.state$.value.status).toBe(status);
    expect(transport.close).toHaveBeenCalledTimes(1);
    expect(transport.mode$.value).toBe('idle');
  });

  it('reopens the strict transport only after binding the new security epoch', async () => {
    bootstrap.result = 'ready';
    await service.createSession('Strict Pair', { chat: true }, null);
    await settleAsyncWork();
    transport.close.mockClear();
    transport.open.mockClear();
    bootstrap.currentEpoch = 4;

    await refreshSecurity(service);

    expect(transport.close).toHaveBeenCalledTimes(1);
    expect(transport.setSemanticEpoch).toHaveBeenCalledWith(4);
    expect(transport.open).toHaveBeenCalledWith('session-a', true, {
      semanticEpoch: 4,
      remotePeerId: 'bob',
    });
  });

  it('joins as non-initiator and routes only to the confirmed owner peer', async () => {
    auth.userPayload = { sub: 'bob' };
    bootstrap.result = 'ready';
    bootstrap.ensure.mockImplementation(async () => {
      bootstrap.confirmedRemotePeerId = 'alice';
      bootstrap.state$.next({ status: 'ready', fingerprint: 'f'.repeat(64) });
      return true;
    });

    await service.joinSession('invite-a');
    await settleAsyncWork();

    expect(transport.open).toHaveBeenCalledTimes(1);
    expect(transport.open).toHaveBeenCalledWith('session-a', false, {
      semanticEpoch: 3,
      remotePeerId: 'alice',
    });
    expect(core.posts).toContainEqual(expect.objectContaining({
      url: 'https://hub.test/share-sessions/join-by-code',
      body: expect.objectContaining({ invite_code: 'invite-a', minimum_security_mode: 'strict_e2ee' }),
    }));
  });

  it('keeps an explicitly approved legacy join on Hub relay without an unbound direct peer', async () => {
    core.session = {
      ...strictSession,
      id: 'legacy-session',
      security_contract_version: 0,
      security_mode: 'legacy',
      security_epoch: null,
    };
    auth.userPayload = { sub: 'bob' };

    await service.joinSession('legacy-invite', { allowLegacy: true });
    await settleAsyncWork();

    expect(bootstrap.ensure).not.toHaveBeenCalled();
    expect(bootstrap.state$.value.status).toBe('legacy');
    expect(transport.open).toHaveBeenCalledTimes(1);
    expect(transport.open).toHaveBeenCalledWith('legacy-session', false, {
      semanticEpoch: 1,
      unboundPeerFallback: 'hub_relay',
    });
    expect(transport.mode$.value).toBe('hub_relay');
  });
});

async function settleAsyncWork(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await new Promise<void>(resolve => setTimeout(resolve, 0));
}

function refreshSecurity(service: ShareSessionService): Promise<void> {
  return (service as unknown as { refreshSecurity(): Promise<void> }).refreshSecurity();
}
