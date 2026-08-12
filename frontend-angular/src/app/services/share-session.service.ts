import { Injectable, inject, OnDestroy } from '@angular/core';
import { BehaviorSubject, Observable, Subscription, combineLatest, firstValueFrom } from 'rxjs';
import { HubApiCoreService } from './hub-api-core.service';
import { AgentDirectoryService } from './agent-directory.service';
import { UserAuthService } from './user-auth.service';
import { WebrtcTransportService } from './webrtc-transport.service';
import { NetworkProfileService } from './network-profile.service';
import { PermissionKey, PermissionSet } from './pair-view-sync.types';
import { hasPermission, normalizePermissions, permissionsFromUiSelection } from './permission-labels';
import { E2eEncryptionService } from './e2e-encryption.service';
import { PAIR_VIEW_CRYPTO, PairViewCryptoPort } from './pair-view-crypto.service';
import { PairSecureSequenceService } from './pair-secure-sequence.service';
import {
  PairSessionControlPlaneService,
  PairSessionMutationOptions,
} from './pair-session-control-plane.service';
import type { PairControlPlaneKind } from './pair-session-binding.store';
import { PairPublicSessionContractPolicy } from './pair-public-session-contract.policy';
import {
  PairSecurityBootstrapState,
  PairViewSecurityBootstrapService,
} from './pair-view-security-bootstrap.service';
import {
  isIdempotentPairSessionRetirementReason,
  isTerminalPairSessionReason,
  terminalPairSessionReason,
} from './pair-session-terminal-error';

export interface ShareSession {
  id: string;
  /** Canonical, server-issued peer identity for this exact control plane. */
  local_peer_id?: string;
  title: string;
  invite_code: string;
  mode: string;
  transport: string;
  permissions: Record<string, boolean>;
  created_at: number;
  expires_at: number | null;
  revoked_at: number | null;
  owner_user_id: string;
  tenant_id?: string;
  permissions_version?: number;
  security_epoch?: number | null;
  security_contract_version?: number;
  security_mode?: string;
  identity_binding_version?: number;
}

export interface ShareParticipant {
  id: string;
  user_id: string;
  device_id: string;
  joined_at: number;
  last_seen_at: number | null;
  revoked_at: number | null;
  permissions: Record<string, boolean>;
}

export interface ShareChatMessage {
  id: string;
  session_id: string;
  sender_id: string;
  text: string;
  created_at: number;
  visibility: string;
}

interface StrictShareChatWireMessage {
  id: string;
  encrypted_payload: string;
}

interface LegacyShareChatWireMessage {
  id?: unknown;
  session_id?: unknown;
  share_session_id?: unknown;
  sender_id?: unknown;
  from_id?: unknown;
  text?: unknown;
  created_at?: unknown;
  visibility?: unknown;
}

interface StrictChatPlaintext {
  version: 1;
  id: string;
  sessionId: string;
  senderUserId: string;
  text: string;
  createdAt: number;
  visibility: 'room';
}

export interface ActiveShareState {
  session: ShareSession | null;
  participants: ShareParticipant[];
  messages: ShareChatMessage[];
  cursor: string;
  role: 'owner' | 'participant' | null;
}

type PendingMembershipAuthority = PairControlPlaneKind | 'unknown';

export type PublicPairRuntimeState =
  | 'idle'
  | 'public_pending'
  | 'hub_pending'
  | 'unknown_pending'
  | 'public'
  | 'hub'
  | 'unknown';

@Injectable({ providedIn: 'root' })
export class ShareSessionService implements OnDestroy {
  private core = inject(HubApiCoreService);
  private dir = inject(AgentDirectoryService);
  private userAuth = inject(UserAuthService);
  private transport = inject(WebrtcTransportService);
  private profiles = inject(NetworkProfileService);
  private e2ee = inject(E2eEncryptionService);
  private cryptoPort: PairViewCryptoPort = inject(PAIR_VIEW_CRYPTO);
  private secureSequences = inject(PairSecureSequenceService);
  private securityBootstrap = inject(PairViewSecurityBootstrapService);
  private controlPlane = inject(PairSessionControlPlaneService);
  private publicContract = inject(PairPublicSessionContractPolicy);

  readonly state$ = new BehaviorSubject<ActiveShareState>({
    session: null, participants: [], messages: [], cursor: '0', role: null,
  });
  readonly publicPairRuntimeState$ = new BehaviorSubject<PublicPairRuntimeState>('idle');
  readonly securityState$ = this.securityBootstrap.state$;

  private pollHandle: ReturnType<typeof setInterval> | null = null;
  private readonly lifetimeSubscriptions = new Subscription();
  private sessionRequests = new Subscription();
  private messagePollInFlight = false;
  private sessionGeneration = 0;
  private transportTerminalSessionId = '';
  private retirementOperation: Readonly<{
    sessionId: string;
    promise: Promise<void>;
  }> | null = null;
  private membershipMutationSerial = 0;
  private activeMembershipMutation: Readonly<{
    serial: number;
    kind: 'create' | 'join';
    authority: PendingMembershipAuthority;
  }> | null = null;

  constructor() {
    this.lifetimeSubscriptions.add(this.state$.subscribe(() => this.publishPublicPairRuntimeState()));
    this.lifetimeSubscriptions.add(combineLatest([
      this.userAuth.oidcToken$,
      this.profiles.profile$,
    ]).subscribe(() => this.reconcileActivePublicAuthority()));
    this.lifetimeSubscriptions.add(this.transport.message$.subscribe((msg) => {
      if (msg.type !== 'chat') return;
      const session = this.state$.value.session;
      if (!session || msg.session_id !== session.id) return;
      if (this.controlPlane.isPublicSession(session.id)) {
        try { this.publicContract.assertValid(session); } catch {
          this.closeUnverifiedStrictTransport();
          return;
        }
      }
      if (session && this.isStrictSession(session)) {
        void this.acceptStrictChatWire(
          msg.payload,
          session.id,
          this.sessionGeneration,
        ).catch(() => undefined);
        return;
      }
      const item = this.parseLegacyChat(msg.payload);
      if (item) this.appendMessage(item);
    }));
    const terminalFailures = this.transport.terminalFailure$;
    if (terminalFailures) {
      this.lifetimeSubscriptions.add(terminalFailures.subscribe(failure => {
        if (!failure) return;
        const active = this.state$.value.session;
        if (active?.id !== failure.sessionId) return;
        if (failure.kind === 'server_terminal') {
          this.terminateRemoteSession(active.id, this.sessionGeneration);
        } else {
          this.quiesceActiveTransport(active.id, this.sessionGeneration);
        }
      }));
    }
  }

  get isActive(): boolean { return !!this.state$.value.session; }
  /** Fail-closed owner projection: an unbound active session may still own Public Pair resources. */
  get hasPublicPairRuntime(): boolean {
    const state = this.publicPairRuntimeState$.value;
    return state === 'public_pending'
      || state === 'unknown_pending'
      || state === 'public'
      || state === 'unknown';
  }
  get sessionMutationPending(): boolean { return this.activeMembershipMutation !== null; }

  /**
   * Permissions of the currently active session, normalised
   * against the PermissionKey union. Returns null when no
   * session is active. Backend may carry unknown keys (forward
   * compat); they are filtered out here.
   */
  currentPermissions(): PermissionSet | null {
    const session = this.state$.value.session;
    if (!session) return null;
    const raw = session.permissions || {};
    try {
      return normalizePermissions(raw);
    } catch {
      return null;
    }
  }

  hasPermission(key: PermissionKey): boolean {
    return hasPermission(this.currentPermissions(), key);
  }

  isStrictSession(session: ShareSession | null = this.state$.value.session): boolean {
    return session?.security_contract_version === 1 && session.security_mode === 'strict_e2ee';
  }

  canSendChat(): boolean {
    const session = this.state$.value.session;
    if (!session || !this.hasPermission('chat')) return false;
    if (!this.isStrictSession(session)) {
      return !this.controlPlane.isPublicSession(session.id);
    }
    return this.securityState$.value.status === 'ready'
      && !!session.security_epoch
      && this.cryptoPort.ready(session.id, session.security_epoch);
  }

  approveFingerprintChange(): void {
    this.securityBootstrap.approveFingerprintChange();
    void this.refreshSecurity();
  }

  get currentUserId(): string {
    const sessionId = this.state$.value.session?.id;
    return sessionId ? this.controlPlane.peerIdForSession(sessionId) : this.controlPlane.currentPeerId;
  }

  private get hubUrl(): string {
    return this.dir.list().find((a) => a.role === 'hub')?.url ?? '';
  }

  private preferredTransport(): 'webrtc' | 'hub_relay' {
    const first = this.profiles.current.transport_order?.[0];
    return first === 'webrtc' ? 'webrtc' : 'hub_relay';
  }

  async createSession(
    title: string,
    permissions: Partial<Record<PermissionKey, boolean>>,
    expiresInSeconds: number | null,
    options: PairSessionMutationOptions = {},
  ): Promise<ShareSession> {
    const authority = this.pinMembershipAuthority(options.expectedAuthority);
    return this.runMembershipMutation('create', authority, async () => {
      const expectedAuthority = this.requirePinnedMembershipAuthority(authority);
      const deviceKey = await this.e2ee.ensureLocalKeyPair();
      const transport = this.preferredTransport();
      const body = {
        title,
        permissions: permissionsFromUiSelection(permissions),
        permissions_version: 1,
        security_contract_version: 1,
        security_mode: 'strict_e2ee',
        public_key_spki_b64: deviceKey.publicKeySpkiB64,
        public_key_fingerprint: deviceKey.fingerprint,
        mode: transport === 'webrtc' ? 'p2p' : 'relay',
        transport,
        expires_at: expiresInSeconds ? Date.now() / 1000 + expiresInSeconds : null,
      };
      const session = await firstValueFrom(this.controlPlane.create<ShareSession>(body, {
        expectedAuthority,
      }));
      if (!session?.id) throw new Error('no session in response');
      this.activateValidatedSession(session, 'owner');
      return session;
    });
  }

  async joinSession(
    inviteCode: string,
    options: { allowLegacy?: boolean } & PairSessionMutationOptions = {},
  ): Promise<ShareSession> {
    const authority = this.pinMembershipAuthority(options.expectedAuthority);
    return this.runMembershipMutation('join', authority, async () => {
      const expectedAuthority = this.requirePinnedMembershipAuthority(authority);
      const deviceKey = await this.e2ee.ensureLocalKeyPair();
      const session = await firstValueFrom(this.controlPlane.join<ShareSession>({
        invite_code: inviteCode,
        minimum_security_mode: options.allowLegacy === true ? 'legacy' : 'strict_e2ee',
        public_key_spki_b64: deviceKey.publicKeySpkiB64,
        public_key_fingerprint: deviceKey.fingerprint,
      }, { expectedAuthority }));
      if (!session?.id) throw new Error('join failed');
      this.activateValidatedSession(session, 'participant', options.allowLegacy === true);
      return session;
    });
  }

  /**
   * Abandons only the unresolved public join mutation. This deliberately does
   * not expose pending request data or the membership capability to the UI.
   */
  discardPendingJoinAttempt(): void {
    this.controlPlane.discardPendingPublicMutation('join');
  }

  async sendMessage(text: string): Promise<void> {
    const { session } = this.state$.value;
    const normalized = text.trim();
    if (!session || !normalized) return;
    if (!this.hasPermission('chat')) throw new Error('chat_permission_required');
    const publicSession = this.controlPlane.isPublicSession(session.id);
    if (publicSession) {
      this.controlPlane.assertSessionAvailable(session.id);
      this.publicContract.assertValid(session);
    }

    if (this.isStrictSession(session)) {
      if (!this.canSendChat() || !session.security_epoch) {
        throw new Error('confirmed_pair_binding_required');
      }
      const senderUserId = this.currentUserId;
      const id = crypto.randomUUID ? crypto.randomUUID() : String(Date.now());
      const createdAt = Date.now() / 1000;
      const plaintext: StrictChatPlaintext = {
        version: 1,
        id,
        sessionId: session.id,
        senderUserId,
        text: normalized,
        createdAt,
        visibility: 'room',
      };
      const encryptedPayload = await this.cryptoPort.seal(JSON.stringify(plaintext), {
        scopeId: session.id,
        epoch: session.security_epoch,
        sequence: await this.secureSequences.next(
          session.id,
          session.security_epoch,
          senderUserId,
          'semantic',
        ),
        payloadType: 'pair.chat_message',
        trafficClass: 'semantic',
      });
      const wire: StrictShareChatWireMessage = { id, encrypted_payload: encryptedPayload };
      if (this.transport.mode$.value === 'webrtc') {
        this.transport.send('chat', wire);
      } else {
        if (publicSession) {
          throw new Error('public_pair_datachannel_required');
        }
        this.assertHubPayloadRelayAllowed(session.id);
        const url = this.hubUrl;
        if (!url) throw new Error('hub_unavailable');
        await firstValueFrom(this.core.post(
          `${url}/share-sessions/${session.id}/chat/messages`, wire, url,
        ));
      }
      this.appendMessage({
        id,
        session_id: session.id,
        sender_id: senderUserId,
        text: normalized,
        created_at: createdAt,
        visibility: 'room',
      });
      return;
    }

    if (this.transport.mode$.value === 'webrtc') {
      this.transport.send('chat', {
        id: crypto.randomUUID ? crypto.randomUUID() : String(Date.now()),
        session_id: session.id,
        text: normalized,
        sender_id: this.currentUserId,
        created_at: Date.now() / 1000,
      });
      return;
    }

    this.assertHubPayloadRelayAllowed(session.id);
    const url = this.hubUrl;
    if (!url) throw new Error('hub_unavailable');
    await firstValueFrom(this.core.post(`${url}/share-sessions/${session.id}/chat/messages`, {
      text: normalized, visibility: 'room', channel_type: 'room',
      id: crypto.randomUUID ? crypto.randomUUID() : String(Date.now()),
    }, url));
  }

  revokeParticipant(participantId: string): void {
    const { session } = this.state$.value;
    if (!session) return;
    const generation = this.sessionGeneration;
    const requestScope = this.sessionRequests;
    try {
      const request = this.controlPlane.revokeParticipant(session.id, participantId).subscribe({
        next: () => this.fetchParticipants(),
        error: error => this.handleSessionRequestError(error, session.id, generation),
      });
      this.trackSessionRequest(requestScope, request);
    } catch { /* public authority loss remains local and never falls back */ }
  }

  endSession(): Promise<void> {
    const { session } = this.state$.value;
    if (!session) return Promise.resolve();
    return this.runRetirementOnce(session.id, () => this.endActiveSession(session));
  }

  leaveSession(): Promise<void> {
    const { session, role } = this.state$.value;
    if (!session) return Promise.resolve();
    if (role === 'owner') return this.endSession();
    if (role !== 'participant' || !this.controlPlane.isPublicSession(session.id)) {
      this.clearActiveSession();
      return Promise.resolve();
    }
    return this.runRetirementOnce(session.id, () => this.leaveActiveMembership(session));
  }

  private async endActiveSession(session: ShareSession): Promise<void> {
    let request: Observable<unknown>;
    try {
      // Construct the authenticated request while the immutable binding is
      // still present, then quiesce every background continuation before the
      // bounded idempotent mutation leaves the browser.
      request = this.controlPlane.end(session.id);
    } catch (error) {
      this.quiesceActiveTransport(session.id, this.sessionGeneration);
      throw error;
    }
    const retirementGeneration = this.quiesceActiveTransport(
      session.id,
      this.sessionGeneration,
    );
    try {
      await firstValueFrom(request);
      this.completeSessionRetirement(session.id, retirementGeneration);
    } catch (error) {
      const terminalReason = terminalPairSessionReason(error);
      if (terminalReason) {
        this.completeSessionRetirement(session.id, retirementGeneration);
      }
      if (isIdempotentPairSessionRetirementReason(terminalReason)) {
        return;
      }
      throw error;
    }
  }

  private async leaveActiveMembership(session: ShareSession): Promise<void> {
    let request: Observable<unknown>;
    try {
      request = this.controlPlane.leave(session.id);
    } catch (error) {
      this.quiesceActiveTransport(session.id, this.sessionGeneration);
      throw error;
    }
    const retirementGeneration = this.quiesceActiveTransport(
      session.id,
      this.sessionGeneration,
    );
    try {
      await firstValueFrom(request);
      this.completeSessionRetirement(session.id, retirementGeneration);
    } catch (error) {
      const terminalReason = terminalPairSessionReason(error);
      if (terminalReason) {
        this.completeSessionRetirement(session.id, retirementGeneration);
      }
      if (isIdempotentPairSessionRetirementReason(terminalReason)) {
        return;
      }
      throw error;
    }
  }

  private runRetirementOnce(sessionId: string, operation: () => Promise<void>): Promise<void> {
    if (this.retirementOperation?.sessionId === sessionId) {
      return this.retirementOperation.promise;
    }
    const pending = operation();
    let tracked: Promise<void>;
    tracked = pending.then(
      () => {
        if (this.retirementOperation?.promise === tracked) this.retirementOperation = null;
      },
      error => {
        if (this.retirementOperation?.promise === tracked) this.retirementOperation = null;
        throw error;
      },
    );
    this.retirementOperation = Object.freeze({ sessionId, promise: tracked });
    return tracked;
  }

  private startPolling(): void {
    this.stopPolling();
    this.pollHandle = setInterval(() => this.tick(), 2000);
    this.tick();
  }

  private stopPolling(): void {
    if (this.pollHandle !== null) { clearInterval(this.pollHandle); this.pollHandle = null; }
  }

  private tick(): void {
    this.fetchParticipants();
    this.fetchMessages();
    this.sendHeartbeat();
    if (this.state$.value.session?.id !== this.transportTerminalSessionId) {
      void this.refreshSecurity();
    }
  }

  private sendHeartbeat(): void {
    const { session } = this.state$.value;
    if (!session) return;
    const generation = this.sessionGeneration;
    const requestScope = this.sessionRequests;
    try {
      const request = this.controlPlane.heartbeat(session.id).subscribe({
        error: error => this.handleSessionRequestError(error, session.id, generation),
      });
      this.trackSessionRequest(requestScope, request);
    } catch {
      this.closeUnverifiedStrictTransport();
    }
  }

  private fetchParticipants(): void {
    const { session } = this.state$.value;
    if (!session) return;
    const generation = this.sessionGeneration;
    const requestScope = this.sessionRequests;
    try {
      const request = this.controlPlane.participants<{ ok: boolean; participants: ShareParticipant[]; data?: { participants: ShareParticipant[] } }>(session.id).subscribe({
        next: (r) => {
          if (!this.isCurrentSession(session.id, generation)) return;
          const participants = r?.participants ?? r?.data?.participants;
          if (participants) this.state$.next({ ...this.state$.value, participants });
        },
        error: error => this.handleSessionRequestError(error, session.id, generation),
      });
      this.trackSessionRequest(requestScope, request);
    } catch {
      this.closeUnverifiedStrictTransport();
    }
  }

  private fetchMessages(): void {
    const { session, cursor } = this.state$.value;
    if (!session || this.transport.mode$.value === 'webrtc' || this.messagePollInFlight) return;
    // Public rendezvous sessions have no application-payload relay. An idle
    // or failed DataChannel must stay closed instead of leaking chat to Hub.
    if (this.controlPlane.isPublicSession(session.id)) {
      try {
        this.controlPlane.assertSessionAvailable(session.id);
        this.publicContract.assertValid(session);
      } catch {
        this.closeUnverifiedStrictTransport();
      }
      return;
    }
    try { this.assertHubPayloadRelayAllowed(session.id); } catch { return; }
    const url = this.hubUrl;
    if (!url) return;
    const generation = this.sessionGeneration;
    const requestScope = this.sessionRequests;
    this.messagePollInFlight = true;
    const request = this.core.get<{ ok: boolean; messages: unknown[]; cursor: string }>(
      `${url}/share-sessions/${session.id}/chat/messages?since=${cursor}`, url,
    ).subscribe({
      next: (r) => {
        if (!this.isCurrentSession(session.id, generation)) return;
        void this.acceptChatPage(session, r?.messages ?? [], r?.cursor ?? cursor, generation)
          .finally(() => {
            if (this.isCurrentSession(session.id, generation)) this.messagePollInFlight = false;
          });
      },
      error: error => {
        if (this.isCurrentSession(session.id, generation)) this.messagePollInFlight = false;
        this.handleSessionRequestError(error, session.id, generation);
      },
    });
    this.trackSessionRequest(requestScope, request);
  }

  participantStatus(p: ShareParticipant): string {
    if (p.revoked_at) return 'gesperrt';
    if (!p.last_seen_at) return 'offline';
    const secs = Math.floor(Date.now() / 1000 - p.last_seen_at);
    return secs < 12 ? 'online' : `offline ${secs}s`;
  }

  ngOnDestroy(): void {
    this.sessionGeneration += 1;
    this.stopPolling();
    this.sessionRequests.unsubscribe();
    this.lifetimeSubscriptions.unsubscribe();
    this.transport.close();
    this.securityBootstrap.clear();
    this.publicPairRuntimeState$.complete();
  }

  private async runMembershipMutation<T>(
    kind: 'create' | 'join',
    authority: PendingMembershipAuthority,
    operation: () => Promise<T>,
  ): Promise<T> {
    if (this.activeMembershipMutation) throw new Error('pair_session_mutation_in_progress');
    const mutation = Object.freeze({
      serial: ++this.membershipMutationSerial,
      kind,
      authority,
    });
    this.activeMembershipMutation = mutation;
    this.publishPublicPairRuntimeState();
    try {
      return await operation();
    } finally {
      if (this.activeMembershipMutation?.serial === mutation.serial) {
        this.activeMembershipMutation = null;
        this.publishPublicPairRuntimeState();
      }
    }
  }

  private publishPublicPairRuntimeState(): void {
    let next: PublicPairRuntimeState;
    if (this.activeMembershipMutation) {
      next = `${this.activeMembershipMutation.authority}_pending`;
    } else {
      const sessionId = this.state$.value.session?.id ?? '';
      if (!sessionId) {
        next = 'idle';
      } else {
        try {
          next = this.controlPlane.authorityKindForSession(sessionId);
        } catch {
          next = 'unknown';
        }
      }
    }
    if (next !== this.publicPairRuntimeState$.value) this.publicPairRuntimeState$.next(next);
  }

  private pinMembershipAuthority(
    expectedAuthority?: PairControlPlaneKind,
  ): PendingMembershipAuthority {
    if (expectedAuthority) return expectedAuthority;
    try {
      return this.controlPlane.isPublic ? 'public' : 'hub';
    } catch {
      return 'unknown';
    }
  }

  private requirePinnedMembershipAuthority(
    authority: PendingMembershipAuthority,
  ): PairControlPlaneKind {
    if (authority === 'unknown') throw new Error('pair_session_authority_unavailable');
    return authority;
  }

  private activateValidatedSession(
    session: ShareSession,
    role: 'owner' | 'participant',
    allowLegacy = false,
  ): void {
    try {
      this.controlPlane.assertSessionAvailable(session.id);
      if (role === 'participant' && !this.isStrictSession(session) && !allowLegacy) {
        throw new Error('legacy_session_requires_explicit_approval');
      }
      if (this.controlPlane.isPublicSession(session.id)) this.publicContract.assertValid(session);
    } catch (error) {
      this.controlPlane.abandonSessionActivation(session.id);
      throw error;
    }
    this.activateSession(session, role);
  }

  private activateSession(session: ShareSession, role: 'owner' | 'participant'): void {
    if (this.controlPlane.isPublicSession(session.id)) this.publicContract.assertValid(session);
    const priorSessionId = this.state$.value.session?.id ?? '';
    this.sessionGeneration += 1;
    this.transportTerminalSessionId = '';
    this.stopPolling();
    this.resetSessionRequestScope();
    // A strict session may need several polling cycles before its remote peer
    // is cryptographically bound. Never retain the prior session's transport
    // while that verification is pending.
    this.transport.close();
    this.securityBootstrap.clear();
    if (priorSessionId && priorSessionId !== session.id) {
      this.secureSequences.clearScope(priorSessionId);
      this.controlPlane.forgetSession(priorSessionId);
    }
    this.state$.next({ session, participants: [], messages: [], cursor: '0', role });
    this.startPolling();
    if (this.isStrictSession(session)) {
      void this.refreshSecurity();
    } else {
      this.securityBootstrap.markLegacy();
      void this.transport.open(session.id, role === 'owner', {
        semanticEpoch: session.security_epoch ?? 1,
        // Legacy sessions have no verified peer-binding contract. They may
        // use the authenticated Hub relay, but never unbound Direct-WebRTC.
        unboundPeerFallback: 'hub_relay',
      }).catch(() => undefined);
    }
  }

  private clearActiveSession(retireSession = false): void {
    const sessionId = this.state$.value.session?.id ?? '';
    this.sessionGeneration += 1;
    this.transportTerminalSessionId = '';
    this.stopPolling();
    this.resetSessionRequestScope();
    this.messagePollInFlight = false;
    this.state$.next({ session: null, participants: [], messages: [], cursor: '0', role: null });
    try {
      if (retireSession && sessionId) this.transport.retireSession(sessionId);
      else this.transport.close();
    } catch { /* continue clearing every independent local authority */ }
    if (sessionId) {
      try { this.secureSequences.clearScope(sessionId); } catch { /* best-effort local cleanup */ }
    }
    if (sessionId) {
      try {
        if (retireSession) this.controlPlane.retireSession(sessionId);
        else this.controlPlane.forgetSession(sessionId);
      } catch { /* state and transport teardown remain terminal */ }
    }
    this.securityBootstrap.clear();
  }

  private async refreshSecurity(): Promise<void> {
    const session = this.state$.value.session;
    if (!session) return;
    if (session.id === this.transportTerminalSessionId) return;
    if (this.controlPlane.isPublicSession(session.id)) {
      try { this.publicContract.assertValid(session); } catch {
        this.closeUnverifiedStrictTransport();
        return;
      }
    }
    if (!this.isStrictSession(session)) {
      if (this.securityState$.value.status !== 'legacy') this.securityBootstrap.markLegacy();
      return;
    }
    const generation = this.sessionGeneration;
    let ready: boolean;
    try {
      ready = await this.securityBootstrap.ensure(session, this.currentUserId);
    } catch (error) {
      if (this.handleSessionRequestError(error, session.id, generation)) return;
      this.closeUnverifiedStrictTransport();
      return;
    }
    if (!this.isCurrentSession(session.id, generation)) return;
    const securityState = this.securityState$.value;
    if (
      !ready
      && securityState.status === 'failed'
      && isTerminalPairSessionReason(securityState.reasonCode)
    ) {
      this.terminateRemoteSession(session.id, generation);
      return;
    }
    const epoch = this.securityBootstrap.currentEpoch;
    if (epoch > 0 && epoch !== this.state$.value.session?.security_epoch) {
      const current = this.state$.value;
      if (!current.session) return;
      // The verified binding is scoped to an exact security epoch. An open
      // peer connection from the prior epoch must not survive re-keying or a
      // membership change, even when the canonical user id stays unchanged.
      this.closeUnverifiedStrictTransport();
      this.transport.setSemanticEpoch(epoch);
      this.state$.next({ ...current, session: { ...current.session, security_epoch: epoch } });
    }
    if (!ready) {
      // `ensure()` also returns false for peer removal, stale confirmation,
      // fingerprint changes and verification errors. All of those revoke the
      // authority under which the direct transport (including media) opened.
      this.closeUnverifiedStrictTransport();
      return;
    }
    await this.openVerifiedPairTransport(session.id, generation);
  }

  private closeUnverifiedStrictTransport(): void {
    this.transport.close();
  }

  private async openVerifiedPairTransport(sessionId: string, generation: number): Promise<void> {
    const active = this.state$.value;
    if (
      generation !== this.sessionGeneration
      || active.session?.id !== sessionId
      || !this.isStrictSession(active.session)
      || !active.role
      || this.securityState$.value.status !== 'ready'
      || this.transport.mode$.value !== 'idle'
    ) return;

    if (this.transport.isSessionRecreationRequired?.(sessionId)) {
      this.quiesceActiveTransport(sessionId, generation);
      return;
    }

    // This value originates exclusively from the verified and confirmed key
    // binding. There is intentionally no participant-list or broadcast
    // fallback when the exact peer identity is unavailable.
    const remotePeerId = this.securityBootstrap.confirmedRemotePeerId;
    if (!remotePeerId) return;

    try {
      await this.transport.open(sessionId, active.role === 'owner', {
        semanticEpoch: active.session.security_epoch ?? 1,
        remotePeerId,
      });
      // The transport owns its own generation fence. A late completion must
      // not touch either a replacement session or a locally quiesced session.
      if (!this.isCurrentSession(sessionId, generation)) return;
    } catch (error) {
      if (this.handleSessionRequestError(error, sessionId, generation)) return;
      // Fail closed and allow a later authenticated bootstrap poll to retry.
      if (this.isCurrentSession(sessionId, generation)) {
        this.transport.close();
      }
    }
  }

  private handleSessionRequestError(
    error: unknown,
    sessionId: string,
    generation: number,
  ): boolean {
    if (!terminalPairSessionReason(error)) return false;
    this.terminateRemoteSession(sessionId, generation);
    return true;
  }

  private terminateRemoteSession(sessionId: string, generation: number): void {
    if (!this.isCurrentSession(sessionId, generation)) return;
    this.clearActiveSession(true);
  }

  private reconcileActivePublicAuthority(): void {
    const sessionId = this.state$.value.session?.id ?? '';
    if (!sessionId || !this.controlPlane.isPublicSession(sessionId)) return;
    try {
      this.controlPlane.assertSessionAvailable(sessionId);
    } catch {
      this.clearActiveSession(true);
    }
  }

  private quiesceActiveTransport(sessionId: string, generation: number): number | null {
    if (!this.isCurrentSession(sessionId, generation)) return null;
    // A poisoned local signaling generation does not prove that the server
    // session or membership ended. Preserve its authority so the owner can
    // still End and a participant can still Leave, while preventing any
    // background transport reopen (and therefore further TURN issuance).
    if (this.transportTerminalSessionId === sessionId) return this.sessionGeneration;
    this.sessionGeneration += 1;
    this.transportTerminalSessionId = sessionId;
    this.stopPolling();
    this.resetSessionRequestScope();
    this.messagePollInFlight = false;
    this.transport.close();
    this.securityBootstrap.clear();
    return this.sessionGeneration;
  }

  private completeSessionRetirement(sessionId: string, generation: number | null): void {
    if (generation !== null && this.isCurrentSession(sessionId, generation)) {
      this.clearActiveSession(true);
      return;
    }
    // The mutation belonged to an older session generation. Its control-plane
    // request already retired the captured authority. Clean only artifacts
    // whose session id cannot alias the replacement now shown in the UI.
    const replacement = this.state$.value.session;
    // A null state means another definitive path already performed the full
    // teardown. Repeating exact-session retirement is unnecessary and could
    // make "exactly once" lifecycle observers fire twice.
    if (!replacement || replacement.id === sessionId) return;
    try { this.transport.retireSession(sessionId); } catch { /* replacement remains authoritative */ }
    try { this.secureSequences.clearScope(sessionId); } catch { /* best-effort stale-scope cleanup */ }
  }

  private isCurrentSession(sessionId: string, generation: number): boolean {
    return generation === this.sessionGeneration
      && this.state$.value.session?.id === sessionId;
  }

  private resetSessionRequestScope(): void {
    this.sessionRequests.unsubscribe();
    this.sessionRequests = new Subscription();
  }

  private trackSessionRequest(scope: Subscription, request: Subscription): void {
    if (scope.closed || scope !== this.sessionRequests) {
      request.unsubscribe();
      return;
    }
    scope.add(request);
  }

  private async acceptChatPage(
    session: ShareSession,
    rawMessages: unknown[],
    cursor: string,
    generation: number,
  ): Promise<void> {
    if (!this.isCurrentSession(session.id, generation)) return;
    if (this.isStrictSession(session)) {
      for (const raw of rawMessages) {
        if (!this.isCurrentSession(session.id, generation)) return;
        try {
          await this.acceptStrictChatWire(raw, session.id, generation);
        } catch { /* reject and advance the opaque relay cursor */ }
      }
    } else {
      for (const raw of rawMessages) {
        if (!this.isCurrentSession(session.id, generation)) return;
        const item = this.parseLegacyChat(raw);
        if (item) this.appendMessage(item);
      }
    }
    if (this.isCurrentSession(session.id, generation)) {
      this.state$.next({ ...this.state$.value, cursor });
    }
  }

  private async acceptStrictChatWire(
    raw: unknown,
    expectedSessionId: string,
    generation: number,
  ): Promise<void> {
    const wire = this.parseStrictChatWire(raw);
    const session = this.state$.value.session;
    if (
      !wire
      || !session
      || session.id !== expectedSessionId
      || !this.isCurrentSession(expectedSessionId, generation)
      || !this.isStrictSession(session)
      || !session.security_epoch
    ) return;
    if (!this.hasPermission('chat') || !this.cryptoPort.ready(session.id, session.security_epoch)) return;
    const opened = await this.cryptoPort.open(wire.encrypted_payload, {
      scopeId: session.id,
      epoch: session.security_epoch,
    });
    if (!this.isCurrentSession(expectedSessionId, generation)) return;
    if (opened.payloadType !== 'pair.chat_message') return;
    let rawPlaintext: unknown;
    try { rawPlaintext = JSON.parse(opened.plaintext); } catch { return; }
    const plaintext = this.parseStrictChatPlaintext(rawPlaintext);
    if (
      !plaintext
      || plaintext.id !== wire.id
      || plaintext.sessionId !== session.id
      || plaintext.senderUserId !== opened.senderId
    ) return;
    this.appendMessage({
      id: plaintext.id,
      session_id: plaintext.sessionId,
      sender_id: plaintext.senderUserId,
      text: plaintext.text,
      created_at: plaintext.createdAt,
      visibility: plaintext.visibility,
    });
  }

  private parseStrictChatWire(raw: unknown): StrictShareChatWireMessage | null {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
    const value = raw as Record<string, unknown>;
    if (Object.keys(value).length !== 2 || !('id' in value) || !('encrypted_payload' in value)) return null;
    if (typeof value['id'] !== 'string' || !value['id'] || value['id'].length > 96) return null;
    if (typeof value['encrypted_payload'] !== 'string' || !value['encrypted_payload']) return null;
    return { id: value['id'], encrypted_payload: value['encrypted_payload'] };
  }

  private parseStrictChatPlaintext(raw: unknown): StrictChatPlaintext | null {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
    const value = raw as Record<string, unknown>;
    const expected = ['version', 'id', 'sessionId', 'senderUserId', 'text', 'createdAt', 'visibility'];
    if (Object.keys(value).length !== expected.length || expected.some((key) => !(key in value))) return null;
    if (value['version'] !== 1 || value['visibility'] !== 'room') return null;
    if (typeof value['id'] !== 'string' || !value['id'] || value['id'].length > 96) return null;
    if (typeof value['sessionId'] !== 'string' || typeof value['senderUserId'] !== 'string') return null;
    if (typeof value['text'] !== 'string' || !value['text'].trim() || value['text'].length > 16_384) return null;
    if (typeof value['createdAt'] !== 'number' || !Number.isFinite(value['createdAt'])) return null;
    return value as unknown as StrictChatPlaintext;
  }

  private parseLegacyChat(raw: unknown): ShareChatMessage | null {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
    const value = raw as LegacyShareChatWireMessage;
    const text = typeof value.text === 'string' ? value.text : '';
    if (!text) return null;
    const sessionId = String(value.session_id ?? value.share_session_id ?? this.state$.value.session?.id ?? '');
    if (!sessionId || sessionId !== this.state$.value.session?.id) return null;
    return {
      id: String(value.id || `legacy-${Date.now()}`),
      session_id: sessionId,
      sender_id: String(value.sender_id ?? value.from_id ?? 'peer'),
      text,
      created_at: Number(value.created_at || Date.now() / 1000),
      visibility: String(value.visibility || 'room'),
    };
  }

  private appendMessage(item: ShareChatMessage): void {
    const current = this.state$.value;
    if (!current.session || item.session_id !== current.session.id) return;
    if (current.messages.some((message) => message.id === item.id)) return;
    this.state$.next({ ...current, messages: [...current.messages, item].slice(-200) });
  }

  private assertHubPayloadRelayAllowed(sessionId: string): void {
    if (this.controlPlane.isPublicSession(sessionId)) {
      throw new Error('public_pair_hub_relay_forbidden');
    }
    this.controlPlane.assertSessionAvailable(sessionId);
  }
}
