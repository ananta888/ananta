import { TestBed } from '@angular/core/testing';
import { BehaviorSubject, Observable, Subject, of } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AgentDirectoryService } from './agent-directory.service';
import { E2eEncryptionService } from './e2e-encryption.service';
import { HubApiCoreService } from './hub-api-core.service';
import { NetworkProfileService } from './network-profile.service';
import { PairSecureSequenceService } from './pair-secure-sequence.service';
import { PairSessionControlPlaneService } from './pair-session-control-plane.service';
import { PAIR_VIEW_CRYPTO } from './pair-view-crypto.service';
import { PairPublicSessionContractPolicy } from './pair-public-session-contract.policy';
import {
  PairSecurityBootstrapState,
  PairViewSecurityBootstrapService,
} from './pair-view-security-bootstrap.service';
import { ShareParticipant, ShareSession, ShareSessionService } from './share-session.service';
import { UserAuthService } from './user-auth.service';
import { WebrtcTransportService } from './webrtc-transport.service';

const SESSION: ShareSession = {
  id: 'session-a',
  local_peer_id: 'peer:local',
  title: 'Terminal Pair',
  invite_code: 'invite-a',
  mode: 'p2p',
  transport: 'webrtc',
  permissions: { chat: true },
  created_at: 1,
  expires_at: null,
  revoked_at: null,
  owner_user_id: 'owner',
  security_epoch: 3,
  security_contract_version: 1,
  security_mode: 'strict_e2ee',
};

class TerminalControlPlane {
  readonly participantsRequest = new Subject<{
    ok: boolean;
    participants: ShareParticipant[];
  }>();
  readonly heartbeatRequest = new Subject<unknown>();
  readonly endRequests: Subject<unknown>[] = [];
  readonly leaveRequests: Subject<unknown>[] = [];
  readonly participantsCalls = vi.fn();
  readonly forgetSession = vi.fn();
  readonly retireSession = vi.fn();
  readonly abandonSessionActivation = vi.fn();
  readonly end = vi.fn(() => {
    const request = new Subject<unknown>();
    this.endRequests.push(request);
    return request.asObservable();
  });
  readonly leave = vi.fn(() => {
    const request = new Subject<unknown>();
    this.leaveRequests.push(request);
    return request.asObservable();
  });
  publicSession = true;
  sessionResponse: ShareSession = { ...SESSION };

  readonly create = vi.fn(() => of({ ...this.sessionResponse }));
  readonly join = vi.fn(() => of({ ...this.sessionResponse }));
  readonly discardPendingPublicMutation = vi.fn();
  readonly assertSessionAvailable = vi.fn();
  readonly revokeParticipant = vi.fn(() => of({ ok: true }));

  get currentPeerId(): string { return 'peer:local'; }
  peerIdForSession(): string { return 'peer:local'; }
  isPublicSession(): boolean { return this.publicSession; }
  participants(): Observable<{ ok: boolean; participants: ShareParticipant[] }> {
    this.participantsCalls();
    return this.participantsRequest.asObservable();
  }
  heartbeat(): Observable<unknown> { return this.heartbeatRequest.asObservable(); }
}

class TerminalTransport {
  readonly mode$ = new BehaviorSubject<'webrtc' | 'hub_relay' | 'idle'>('idle');
  readonly message$ = new Subject<{ type: string; session_id: string; payload: unknown }>();
  readonly terminalFailure$ = new BehaviorSubject<{
    kind: 'local_recreation_required' | 'server_terminal';
    sessionId: string;
    reasonCode: string;
  } | null>(null);
  readonly open = vi.fn(async () => { this.mode$.next('webrtc'); });
  readonly close = vi.fn(() => { this.mode$.next('idle'); });
  readonly retireSession = vi.fn(() => { this.mode$.next('idle'); });
  readonly isSessionRecreationRequired = vi.fn(() => false);
  readonly setSemanticEpoch = vi.fn();
  readonly send = vi.fn();
}

class TerminalBootstrap {
  readonly state$ = new BehaviorSubject<PairSecurityBootstrapState>({ status: 'waiting_for_peer' });
  currentEpoch = 3;
  confirmedRemotePeerId = '';
  ensureImpl: () => Promise<boolean> = async () => false;
  readonly ensure = vi.fn(() => this.ensureImpl());
  readonly clear = vi.fn(() => {
    this.confirmedRemotePeerId = '';
    this.state$.next({ status: 'idle' });
  });
  readonly markLegacy = vi.fn(() => this.state$.next({ status: 'legacy' }));
  readonly approveFingerprintChange = vi.fn();
}

describe('ShareSessionService terminal session lifecycle', () => {
  let controlPlane: TerminalControlPlane;
  let transport: TerminalTransport;
  let bootstrap: TerminalBootstrap;
  let service: ShareSessionService;
  let oidcToken$: BehaviorSubject<string | null>;
  let profile$: BehaviorSubject<{ profile_id: string; transport_order: string[] }>;

  beforeEach(() => {
    vi.useFakeTimers();
    controlPlane = new TerminalControlPlane();
    transport = new TerminalTransport();
    bootstrap = new TerminalBootstrap();
    oidcToken$ = new BehaviorSubject<string | null>('oidc-token-a');
    profile$ = new BehaviorSubject({ profile_id: 'public-ananta', transport_order: ['webrtc'] });
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ providers: [
      { provide: HubApiCoreService, useValue: { get: vi.fn(), post: vi.fn() } },
      { provide: AgentDirectoryService, useValue: { list: () => [] } },
      {
        provide: UserAuthService,
        useValue: { userPayload: { sub: 'owner' }, oidcToken$: oidcToken$.asObservable() },
      },
      { provide: WebrtcTransportService, useValue: transport },
      {
        provide: NetworkProfileService,
        useValue: { current: profile$.value, profile$: profile$.asObservable() },
      },
      { provide: E2eEncryptionService, useValue: {
        ensureLocalKeyPair: vi.fn(async () => ({
          publicKeySpkiB64: 'public-key', fingerprint: 'fingerprint',
        })),
      } },
      { provide: PAIR_VIEW_CRYPTO, useValue: {
        ready: vi.fn(() => false), seal: vi.fn(), open: vi.fn(), clear: vi.fn(),
      } },
      { provide: PairSecureSequenceService, useValue: {
        next: vi.fn(() => 1), clearScope: vi.fn(),
      } },
      { provide: PairViewSecurityBootstrapService, useValue: bootstrap },
      { provide: PairSessionControlPlaneService, useValue: controlPlane },
      { provide: PairPublicSessionContractPolicy, useValue: { assertValid: vi.fn() } },
    ] });
    service = TestBed.runInInjectionContext(() => new ShareSessionService());
  });

  afterEach(() => {
    service.ngOnDestroy();
    vi.useRealTimers();
    TestBed.resetTestingModule();
  });

  it('exposes a create or join as pending before the first awaited dependency resolves', async () => {
    const response = new Subject<ShareSession>();
    controlPlane.create.mockReturnValueOnce(response);

    const pending = service.createSession('Pending Pair', { chat: true }, null);
    expect(service.sessionMutationPending).toBe(true);
    await Promise.resolve();
    response.next({ ...SESSION });
    response.complete();

    await pending;
    expect(service.sessionMutationPending).toBe(false);
    expect(service.isActive).toBe(true);
  });

  it('abandons a committed response binding that is no longer activatable', async () => {
    controlPlane.assertSessionAvailable.mockImplementationOnce(() => {
      throw new Error('public_session_identity_changed');
    });

    await expect(service.createSession('Stale Pair', { chat: true }, null))
      .rejects.toThrow('public_session_identity_changed');

    expect(controlPlane.abandonSessionActivation).toHaveBeenCalledWith('session-a');
    expect(service.isActive).toBe(false);
    expect(service.sessionMutationPending).toBe(false);
  });

  it('clears a Public session locally when its exact pinned authority is lost', async () => {
    await service.createSession('Authority loss', { chat: true }, null);
    controlPlane.assertSessionAvailable.mockImplementation(() => {
      throw new Error('public_session_identity_binding_mismatch');
    });

    oidcToken$.next('oidc-token-b');

    expect(service.isActive).toBe(false);
    expect(transport.retireSession).toHaveBeenCalledWith('session-a');
    expect(controlPlane.retireSession).toHaveBeenCalledWith('session-a');
  });

  it('does not clear a Hub-bound session for Public authority loss', async () => {
    controlPlane.publicSession = false;
    await service.createSession('Hub session', { chat: true }, null);
    controlPlane.assertSessionAvailable.mockImplementation(() => {
      throw new Error('public_session_binding_required');
    });

    profile$.next({ profile_id: 'local', transport_order: ['webrtc'] });

    expect(service.isActive).toBe(true);
    expect(transport.retireSession).not.toHaveBeenCalled();
    expect(controlPlane.retireSession).not.toHaveBeenCalled();
  });

  it.each([
    [404, 'session_not_found'],
    [409, 'session_inactive'],
    [409, 'session_revoked'],
    [409, 'session_expired'],
    [403, 'local_peer_id_required'],
    [403, 'membership_capability_required'],
    [403, 'membership_capability_invalid'],
    [409, 'membership_capability_retired'],
    [403, 'forbidden'],
  ])('tears down exactly once on terminal participant HTTP %s/%s', async (status, reason) => {
    const security = deferred<boolean>();
    bootstrap.ensureImpl = () => security.promise;
    await service.createSession('Terminal Pair', { chat: true }, null);
    expect(controlPlane.participantsCalls).toHaveBeenCalledTimes(1);

    controlPlane.participantsRequest.error(httpError(status, reason));

    expect(service.state$.value).toEqual({
      session: null, participants: [], messages: [], cursor: '0', role: null,
    });
    expect(transport.retireSession).toHaveBeenCalledTimes(1);
    expect(transport.retireSession).toHaveBeenCalledWith('session-a');
    expect(controlPlane.retireSession).toHaveBeenCalledTimes(1);
    expect(controlPlane.retireSession).toHaveBeenCalledWith('session-a');

    bootstrap.confirmedRemotePeerId = 'peer:remote';
    bootstrap.state$.next({ status: 'ready', fingerprint: 'f'.repeat(64) });
    security.resolve(true);
    await settleAsyncWork();
    vi.advanceTimersByTime(10_000);

    expect(transport.open).not.toHaveBeenCalled();
    expect(controlPlane.participantsCalls).toHaveBeenCalledTimes(1);
    expect(transport.retireSession).toHaveBeenCalledTimes(1);
    expect(controlPlane.retireSession).toHaveBeenCalledTimes(1);
  });

  it('treats a terminal security-bootstrap result as the same terminal teardown', async () => {
    bootstrap.ensureImpl = async () => {
      bootstrap.state$.next({ status: 'failed', reasonCode: 'session_inactive' });
      return false;
    };

    await service.createSession('Terminal Pair', { chat: true }, null);
    await settleAsyncWork();

    expect(service.isActive).toBe(false);
    expect(transport.retireSession).toHaveBeenCalledTimes(1);
    expect(controlPlane.retireSession).toHaveBeenCalledTimes(1);
  });

  it('quiesces a local signaling terminal without retiring live server authority', async () => {
    await service.createSession('Terminal Pair', { chat: true }, null);
    const participantCalls = controlPlane.participantsCalls.mock.calls.length;

    transport.terminalFailure$.next({
      kind: 'local_recreation_required',
      sessionId: 'session-a',
      reasonCode: 'public_signaling_session_recreation_required',
    });
    vi.advanceTimersByTime(10_000);

    expect(service.isActive).toBe(true);
    expect(service.state$.value.role).toBe('owner');
    expect(controlPlane.forgetSession).not.toHaveBeenCalled();
    expect(controlPlane.retireSession).not.toHaveBeenCalled();
    expect(transport.retireSession).not.toHaveBeenCalled();
    expect(controlPlane.participantsCalls).toHaveBeenCalledTimes(participantCalls);

    const completion = service.endSession();
    expect(controlPlane.end).toHaveBeenCalledWith('session-a');
    controlPlane.endRequests[0].next({ ok: true });
    controlPlane.endRequests[0].complete();
    await completion;
    expect(transport.retireSession).toHaveBeenCalledWith('session-a');
  });

  it('retires an exact server-terminal transport failure instead of only quiescing it', async () => {
    await service.createSession('Terminal Pair', { chat: true }, null);

    transport.terminalFailure$.next({
      kind: 'server_terminal',
      sessionId: 'session-a',
      reasonCode: 'session_not_found',
    });

    expect(service.isActive).toBe(false);
    expect(controlPlane.retireSession).toHaveBeenCalledOnce();
    expect(controlPlane.retireSession).toHaveBeenCalledWith('session-a');
    expect(transport.retireSession).toHaveBeenCalledOnce();
    expect(transport.retireSession).toHaveBeenCalledWith('session-a');
  });

  it('quiesces locally before awaiting owner end and clears only after success', async () => {
    await service.createSession('Terminal Pair', { chat: true }, null);

    const completion = service.endSession();

    expect(service.isActive).toBe(true);
    expect(controlPlane.end).toHaveBeenCalledOnce();
    expect(controlPlane.forgetSession).not.toHaveBeenCalled();
    expect(controlPlane.retireSession).not.toHaveBeenCalled();
    expect(transport.retireSession).not.toHaveBeenCalled();

    controlPlane.endRequests[0].next({ ok: true });
    controlPlane.endRequests[0].complete();
    await completion;

    expect(service.isActive).toBe(false);
    expect(transport.retireSession).toHaveBeenCalledOnce();
    expect(transport.retireSession).toHaveBeenCalledWith('session-a');
  });

  it('stops a public guest locally, calls exact membership leave, and accepts inactive as idempotent', async () => {
    await service.joinSession('invite-a');

    const completion = service.leaveSession();

    expect(service.isActive).toBe(true);
    expect(controlPlane.leave).toHaveBeenCalledOnce();
    expect(controlPlane.leave).toHaveBeenCalledWith('session-a');
    controlPlane.leaveRequests[0].error(httpError(409, 'session_inactive'));
    await expect(completion).resolves.toBeUndefined();

    expect(service.isActive).toBe(false);
    expect(transport.retireSession).toHaveBeenCalledOnce();
    expect(transport.retireSession).toHaveBeenCalledWith('session-a');
  });

  it('keeps a transient leave failure observable and preserves authority for a retry', async () => {
    await service.joinSession('invite-a');

    const completion = service.leaveSession();
    const unavailable = httpError(503, 'service_unavailable');
    controlPlane.leaveRequests[0].error(unavailable);

    await expect(completion).rejects.toBe(unavailable);
    expect(service.isActive).toBe(true);
    expect(service.state$.value.role).toBe('participant');
    expect(controlPlane.forgetSession).not.toHaveBeenCalled();
    expect(controlPlane.retireSession).not.toHaveBeenCalled();
    expect(transport.retireSession).not.toHaveBeenCalled();

    const retry = service.leaveSession();
    expect(controlPlane.leave).toHaveBeenCalledTimes(2);
    controlPlane.leaveRequests[1].next({ ok: true, idempotent: true });
    controlPlane.leaveRequests[1].complete();
    await expect(retry).resolves.toBeUndefined();
    expect(service.isActive).toBe(false);
    expect(transport.retireSession).toHaveBeenCalledWith('session-a');
  });

  it('retires a forbidden guest locally but does not report the unproven mutation as success', async () => {
    await service.joinSession('invite-a');

    const completion = service.leaveSession();
    const forbidden = httpError(403, 'forbidden');
    controlPlane.leaveRequests[0].error(forbidden);

    await expect(completion).rejects.toBe(forbidden);
    expect(service.isActive).toBe(false);
    expect(transport.retireSession).toHaveBeenCalledWith('session-a');
  });

  it('keeps Hub participant leave local and sends no public membership mutation', async () => {
    controlPlane.publicSession = false;
    await service.joinSession('invite-a');

    await expect(service.leaveSession()).resolves.toBeUndefined();

    expect(service.isActive).toBe(false);
    expect(controlPlane.leave).not.toHaveBeenCalled();
  });

  it('routes an owner leave request through authoritative session end', async () => {
    await service.createSession('Terminal Pair', { chat: true }, null);

    const completion = service.leaveSession();
    expect(controlPlane.end).toHaveBeenCalledWith('session-a');
    expect(controlPlane.leave).not.toHaveBeenCalled();
    controlPlane.endRequests[0].next({ ok: true });
    controlPlane.endRequests[0].complete();
    await completion;

    expect(service.isActive).toBe(false);
    expect(transport.retireSession).toHaveBeenCalledWith('session-a');
  });

  it('does not clear a replacement when an older owner-end request completes late', async () => {
    await service.createSession('Terminal Pair', { chat: true }, null);
    const oldCompletion = service.endSession();
    controlPlane.sessionResponse = {
      ...SESSION,
      id: 'session-b',
      invite_code: 'invite-b',
      title: 'Replacement Pair',
    };

    await service.createSession('Replacement Pair', { chat: true }, null);
    controlPlane.endRequests[0].next({ ok: true });
    controlPlane.endRequests[0].complete();
    await oldCompletion;

    expect(service.state$.value.session?.id).toBe('session-b');
    expect(service.state$.value.role).toBe('owner');
    expect(transport.retireSession).toHaveBeenCalledWith('session-a');
    expect(transport.retireSession).not.toHaveBeenCalledWith('session-b');
  });

  it('does not clear a replacement when an older guest-leave response arrives late', async () => {
    await service.joinSession('invite-a');
    const oldCompletion = service.leaveSession();
    controlPlane.sessionResponse = {
      ...SESSION,
      id: 'session-b',
      invite_code: 'invite-b',
      title: 'Replacement Pair',
    };

    await service.joinSession('invite-b');
    controlPlane.leaveRequests[0].next({ ok: true });
    controlPlane.leaveRequests[0].complete();
    await oldCompletion;

    expect(service.state$.value.session?.id).toBe('session-b');
    expect(service.state$.value.role).toBe('participant');
    expect(transport.retireSession).toHaveBeenCalledWith('session-a');
    expect(transport.retireSession).not.toHaveBeenCalledWith('session-b');
  });
});

function httpError(status: number, reason: string): unknown {
  return { status, error: { error: reason } };
}

function deferred<T>(): {
  promise: Promise<T>;
  resolve: (value: T) => void;
} {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(next => { resolve = next; });
  return { promise, resolve };
}

async function settleAsyncWork(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}
