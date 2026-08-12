import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, Subject, of } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AgentDirectoryService } from './agent-directory.service';
import { E2eEncryptionService } from './e2e-encryption.service';
import { HubApiCoreService } from './hub-api-core.service';
import { NetworkProfileService } from './network-profile.service';
import { PAIR_VIEW_CRYPTO, PairViewCryptoPort } from './pair-view-crypto.service';
import { PairViewSecurityBootstrapService } from './pair-view-security-bootstrap.service';
import { PairSessionControlPlaneService } from './pair-session-control-plane.service';
import { ShareSession, ShareSessionService } from './share-session.service';
import { UserAuthService } from './user-auth.service';
import { WebrtcTransportService } from './webrtc-transport.service';

const CANARY = 'PAIR_CHAT_CANARY_BROWSER_ONLY_0123456789';

class FakeCore {
  posts: Array<{ url: string; body: unknown }> = [];
  gets: string[] = [];
  post<T>(_url: string, body: unknown) {
    this.posts.push({ url: _url, body });
    return of({ ok: true } as T);
  }
  get<T>(url: string) {
    this.gets.push(url);
    return of({ ok: true, messages: [], cursor: '0' } as T);
  }
  delete<T>() { return of({ ok: true } as T); }
}

class FakeTransport {
  mode$ = new BehaviorSubject<'webrtc' | 'hub_relay' | 'idle'>('webrtc');
  message$ = new Subject<{ type: string; session_id: string; payload: unknown }>();
  sent: Array<{ type: string; payload: unknown }> = [];
  open = vi.fn(async () => undefined);
  close = vi.fn();
  setSemanticEpoch = vi.fn();
  send(type: string, payload: unknown): void { this.sent.push({ type, payload }); }
}

class FakeCrypto implements PairViewCryptoPort {
  readyValue = true;
  sealedPlaintexts: string[] = [];
  opened: { plaintext: string; payloadType: string; senderId: string; sequence: number } | null = null;
  ready(): boolean { return this.readyValue; }
  async seal(plaintext: string): Promise<string> {
    this.sealedPlaintexts.push(plaintext);
    return '{"ciphertext_b64":"opaque","payload_type":"pair.chat_message"}';
  }
  async open(): Promise<any> {
    if (!this.opened) throw new Error('test_open_missing');
    return this.opened;
  }
  clear(): void {}
}

const strictSession: ShareSession = {
  id: 'session-a', title: 'Strict Pair', invite_code: 'invite', mode: 'p2p', transport: 'webrtc',
  permissions: { chat: true, view_tui: true, remote_cursor: false, artifact_share: false, remote_control: false },
  created_at: 1, expires_at: null, revoked_at: null, owner_user_id: 'alice', tenant_id: 'tenant-a',
  security_epoch: 3, security_contract_version: 1, security_mode: 'strict_e2ee',
  identity_binding_version: 1, permissions_version: 1,
};

describe('ShareSessionService strict Pair chat', () => {
  let core: FakeCore;
  let transport: FakeTransport;
  let cryptoPort: FakeCrypto;
  let securityState: BehaviorSubject<any>;
  let auth: {
    userPayload: Record<string, string> | null;
    oidcToken$: BehaviorSubject<string | null>;
  };
  let publicSession: boolean;
  let newSessionPublic: boolean;
  let controlPlane: {
    readonly isPublic: boolean;
    readonly currentPeerId: string;
    peerIdForSession: () => string;
    isPublicSession: () => boolean;
    authorityKindForSession: () => 'public' | 'hub';
    create: ReturnType<typeof vi.fn>;
    join: ReturnType<typeof vi.fn>;
    assertSessionAvailable: ReturnType<typeof vi.fn>;
    discardPendingPublicMutation: ReturnType<typeof vi.fn>;
  };
  let service: ShareSessionService;

  beforeEach(() => {
    core = new FakeCore();
    transport = new FakeTransport();
    cryptoPort = new FakeCrypto();
    auth = {
      userPayload: { sub: 'alice' },
      oidcToken$: new BehaviorSubject<string | null>('oidc-token'),
    };
    publicSession = false;
    newSessionPublic = false;
    securityState = new BehaviorSubject({ status: 'ready', fingerprint: 'f'.repeat(64) });
    controlPlane = {
      get isPublic() { return newSessionPublic; },
      get currentPeerId() {
        return String(auth.userPayload?.['sub'] || auth.userPayload?.['username'] || '');
      },
      peerIdForSession: () => String(auth.userPayload?.['sub'] || auth.userPayload?.['username'] || ''),
      isPublicSession: () => publicSession,
      authorityKindForSession: () => publicSession ? 'public' : 'hub',
      create: vi.fn(),
      join: vi.fn(),
      assertSessionAvailable: vi.fn(),
      discardPendingPublicMutation: vi.fn(),
    };
    TestBed.configureTestingModule({ providers: [
      { provide: HubApiCoreService, useValue: core },
      { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: 'http://hub' }] } },
      { provide: UserAuthService, useValue: auth },
      { provide: WebrtcTransportService, useValue: transport },
      { provide: NetworkProfileService, useValue: {
        current: { transport_order: ['webrtc', 'hub_relay'] },
        profile$: of({ transport_order: ['webrtc', 'hub_relay'] }),
      } },
      { provide: E2eEncryptionService, useValue: {
        ensureLocalKeyPair: vi.fn(async () => ({
          publicKeySpkiB64: 'public-key', fingerprint: 'device-fingerprint',
        })),
      } },
      { provide: PAIR_VIEW_CRYPTO, useValue: cryptoPort },
      { provide: PairViewSecurityBootstrapService, useValue: {
        state$: securityState, currentEpoch: 3, clear: vi.fn(), markLegacy: vi.fn(),
        ensure: vi.fn(async () => true), approveFingerprintChange: vi.fn(),
      } },
      { provide: PairSessionControlPlaneService, useValue: controlPlane },
    ] });
    service = TestBed.runInInjectionContext(() => new ShareSessionService());
    service.state$.next({ session: strictSession, participants: [], messages: [], cursor: '0', role: 'owner' });
  });

  it('projects the same subject-or-username peer identity as the Hub', () => {
    auth.userPayload = { username: 'admin' };
    expect(service.currentUserId).toBe('admin');

    auth.userPayload = { sub: 'stable-subject', username: 'admin' };
    expect(service.currentUserId).toBe('stable-subject');

    auth.userPayload = { preferred_username: 'display-only', email: 'display@example.test' };
    expect(service.currentUserId).toBe('');
  });

  it('projects active Public Pair authority without treating Hub sharing as Public Pair', () => {
    expect(service.publicPairRuntimeState$.value).toBe('hub');
    expect(service.hasPublicPairRuntime).toBe(false);

    publicSession = true;
    service.state$.next({ ...service.state$.value });
    expect(service.publicPairRuntimeState$.value).toBe('public');
    expect(service.hasPublicPairRuntime).toBe(true);

    service.state$.next({ session: null, participants: [], messages: [], cursor: '0', role: null });
    expect(service.publicPairRuntimeState$.value).toBe('idle');
    expect(service.hasPublicPairRuntime).toBe(false);
  });

  it('fails closed when an active session has no provable authority binding', () => {
    controlPlane.authorityKindForSession = () => { throw new Error('binding_missing'); };
    service.state$.next({ ...service.state$.value });

    expect(service.publicPairRuntimeState$.value).toBe('unknown');
    expect(service.hasPublicPairRuntime).toBe(true);
  });

  it('publishes pending ownership before membership activation can race another media owner', async () => {
    service.state$.next({ session: null, participants: [], messages: [], cursor: '0', role: null });
    let complete!: () => void;
    const operation = new Promise<void>(resolve => { complete = resolve; });

    const result = (service as unknown as {
      runMembershipMutation<T>(
        kind: 'create' | 'join',
        authority: 'public' | 'hub' | 'unknown',
        operation: () => Promise<T>,
      ): Promise<T>;
    }).runMembershipMutation('create', 'public', () => operation);

    expect(service.publicPairRuntimeState$.value).toBe('public_pending');
    expect(service.hasPublicPairRuntime).toBe(true);

    complete();
    await result;
    expect(service.publicPairRuntimeState$.value).toBe('idle');
    expect(service.hasPublicPairRuntime).toBe(false);
  });

  it('pins an implicit Hub create before its first await and never exposes Public ownership', async () => {
    service.state$.next({ session: null, participants: [], messages: [], cursor: '0', role: null });
    const response = new Subject<ShareSession>();
    controlPlane.create.mockReturnValueOnce(response);
    newSessionPublic = false;

    const pending = service.createSession('Hub Share', { chat: true }, null);
    const rejection = expect(pending).rejects.toThrow('test_stop');
    expect(service.publicPairRuntimeState$.value).toBe('hub_pending');
    expect(service.hasPublicPairRuntime).toBe(false);

    newSessionPublic = true;
    await Promise.resolve();
    await Promise.resolve();
    expect(controlPlane.create).toHaveBeenCalledWith(expect.any(Object), {
      expectedAuthority: 'hub',
    });
    response.error(new Error('test_stop'));
    await rejection;
    expect(service.publicPairRuntimeState$.value).toBe('idle');
  });

  it('pins an explicit Public join even when the ambient authority says Hub', async () => {
    service.state$.next({ session: null, participants: [], messages: [], cursor: '0', role: null });
    const response = new Subject<ShareSession>();
    controlPlane.join.mockReturnValueOnce(response);
    newSessionPublic = false;

    const pending = service.joinSession('invite-public', { expectedAuthority: 'public' });
    const rejection = expect(pending).rejects.toThrow('test_stop');
    expect(service.publicPairRuntimeState$.value).toBe('public_pending');
    expect(service.hasPublicPairRuntime).toBe(true);

    await Promise.resolve();
    await Promise.resolve();
    expect(controlPlane.join).toHaveBeenCalledWith(expect.objectContaining({
      invite_code: 'invite-public',
    }), { expectedAuthority: 'public' });
    response.error(new Error('test_stop'));
    await rejection;
    expect(service.publicPairRuntimeState$.value).toBe('idle');
  });

  it('exposes only a narrow pending-join discard operation to UI consumers', () => {
    service.discardPendingJoinAttempt();

    expect(controlPlane.discardPendingPublicMutation).toHaveBeenCalledOnce();
    expect(controlPlane.discardPendingPublicMutation).toHaveBeenCalledWith('join');
  });

  it('sends only an opaque closed envelope over the direct DataChannel', async () => {
    await service.sendMessage(CANARY);
    expect(cryptoPort.sealedPlaintexts[0]).toContain(CANARY);
    expect(transport.sent).toHaveLength(1);
    expect(transport.sent[0].type).toBe('chat');
    expect(Object.keys(transport.sent[0].payload as object).sort()).toEqual(['encrypted_payload', 'id']);
    expect(JSON.stringify(transport.sent[0].payload)).not.toContain(CANARY);
    expect(service.state$.value.messages[0].text).toBe(CANARY);
  });

  it('fails closed before bidirectional key confirmation', async () => {
    securityState.next({ status: 'confirming' });
    await expect(service.sendMessage(CANARY)).rejects.toThrow('confirmed_pair_binding_required');
    expect(transport.sent).toEqual([]);
    expect(core.posts).toEqual([]);
  });

  it('uses the same opaque body for Hub relay fallback', async () => {
    transport.mode$.next('hub_relay');
    await service.sendMessage(CANARY);
    const body = core.posts[0].body as Record<string, unknown>;
    expect(Object.keys(body).sort()).toEqual(['encrypted_payload', 'id']);
    expect(JSON.stringify(body)).not.toContain(CANARY);
  });

  it('never falls back to Hub chat for a public strict session', async () => {
    publicSession = true;
    transport.mode$.next('idle');

    await expect(service.sendMessage(CANARY)).rejects.toThrow('public_pair_datachannel_required');
    expect(core.posts).toEqual([]);
  });

  it('rejects a legacy mutation of a bound public session before chat or fetch reaches Hub', async () => {
    publicSession = true;
    transport.mode$.next('hub_relay');
    const downgraded = {
      ...strictSession,
      security_mode: 'legacy',
      security_contract_version: 0,
      security_epoch: null,
    };
    service.state$.next({
      session: downgraded, participants: [], messages: [], cursor: '0', role: 'owner',
    });

    await expect(service.sendMessage(CANARY)).rejects
      .toThrow('public_pair_security_contract_invalid');
    (service as unknown as { fetchMessages(): void }).fetchMessages();

    expect(core.posts).toEqual([]);
    expect(core.gets).toEqual([]);
    expect(transport.sent).toEqual([]);
  });

  it('decrypts and re-authorizes an inbound direct message in the browser', async () => {
    cryptoPort.opened = {
      plaintext: JSON.stringify({
        version: 1, id: 'peer-message', sessionId: 'session-a', senderUserId: 'bob',
        text: CANARY, createdAt: 2, visibility: 'room',
      }),
      payloadType: 'pair.chat_message', senderId: 'bob', sequence: 1,
    };
    transport.message$.next({
      type: 'chat', session_id: 'session-a',
      payload: { id: 'peer-message', encrypted_payload: '{"ciphertext_b64":"opaque"}' },
    });
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(service.state$.value.messages).toEqual([
      expect.objectContaining({ id: 'peer-message', sender_id: 'bob', text: CANARY }),
    ]);
  });
});
