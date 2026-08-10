import { Injectable, inject, OnDestroy } from '@angular/core';
import { BehaviorSubject, Subscription, firstValueFrom } from 'rxjs';
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
import { PairSessionControlPlaneService } from './pair-session-control-plane.service';
import { PairPublicSessionContractPolicy } from './pair-public-session-contract.policy';
import {
  PairSecurityBootstrapState,
  PairViewSecurityBootstrapService,
} from './pair-view-security-bootstrap.service';

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
  readonly securityState$ = this.securityBootstrap.state$;

  private pollHandle: ReturnType<typeof setInterval> | null = null;
  private readonly subscriptions = new Subscription();
  private messagePollInFlight = false;
  private securityGeneration = 0;

  constructor() {
    this.subscriptions.add(this.transport.message$.subscribe((msg) => {
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
        void this.acceptStrictChatWire(msg.payload).catch(() => undefined);
        return;
      }
      const item = this.parseLegacyChat(msg.payload);
      if (item) this.appendMessage(item);
    }));
  }

  get isActive(): boolean { return !!this.state$.value.session; }

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
  ): Promise<ShareSession> {
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
    const session = await firstValueFrom(this.controlPlane.create<ShareSession>(body));
    if (!session?.id) throw new Error('no session in response');
    this.activateSession(session, 'owner');
    return session;
  }

  async joinSession(
    inviteCode: string,
    options: { allowLegacy?: boolean } = {},
  ): Promise<ShareSession> {
    const deviceKey = await this.e2ee.ensureLocalKeyPair();
    const session = await firstValueFrom(this.controlPlane.join<ShareSession>({
      invite_code: inviteCode,
      minimum_security_mode: options.allowLegacy === true ? 'legacy' : 'strict_e2ee',
      public_key_spki_b64: deviceKey.publicKeySpkiB64,
      public_key_fingerprint: deviceKey.fingerprint,
    }));
    if (!session?.id) throw new Error('join failed');
    if (!this.isStrictSession(session) && options.allowLegacy !== true) {
      throw new Error('legacy_session_requires_explicit_approval');
    }
    this.activateSession(session, 'participant');
    return session;
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
    try {
      this.controlPlane.revokeParticipant(session.id, participantId).subscribe({
        next: () => this.fetchParticipants(),
        error: () => {},
      });
    } catch { /* public authority loss remains local and never falls back */ }
  }

  endSession(): void {
    const { session } = this.state$.value;
    if (!session) return;
    try {
      this.controlPlane.end(session.id).subscribe({ error: () => {} });
    } finally {
      this.clearActiveSession();
    }
  }

  leaveSession(): void {
    this.clearActiveSession();
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
    void this.refreshSecurity();
  }

  private sendHeartbeat(): void {
    const { session } = this.state$.value;
    if (!session) return;
    try {
      this.controlPlane.heartbeat(session.id)
        .subscribe({ error: () => {} });
    } catch {
      this.closeUnverifiedStrictTransport();
    }
  }

  private fetchParticipants(): void {
    const { session } = this.state$.value;
    if (!session) return;
    try {
      this.controlPlane.participants<{ ok: boolean; participants: ShareParticipant[]; data?: { participants: ShareParticipant[] } }>(session.id).subscribe({
        next: (r) => {
          const participants = r?.participants ?? r?.data?.participants;
          if (participants) this.state$.next({ ...this.state$.value, participants });
        },
        error: () => {},
      });
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
    this.messagePollInFlight = true;
    this.core.get<{ ok: boolean; messages: unknown[]; cursor: string }>(
      `${url}/share-sessions/${session.id}/chat/messages?since=${cursor}`, url,
    ).subscribe({
      next: (r) => {
        void this.acceptChatPage(session, r?.messages ?? [], r?.cursor ?? cursor)
          .finally(() => { this.messagePollInFlight = false; });
      },
      error: () => { this.messagePollInFlight = false; },
    });
  }

  participantStatus(p: ShareParticipant): string {
    if (p.revoked_at) return 'gesperrt';
    if (!p.last_seen_at) return 'offline';
    const secs = Math.floor(Date.now() / 1000 - p.last_seen_at);
    return secs < 12 ? 'online' : `offline ${secs}s`;
  }

  ngOnDestroy(): void {
    this.stopPolling();
    this.subscriptions.unsubscribe();
    this.transport.close();
    this.securityBootstrap.clear();
  }

  private activateSession(session: ShareSession, role: 'owner' | 'participant'): void {
    if (this.controlPlane.isPublicSession(session.id)) this.publicContract.assertValid(session);
    this.securityGeneration += 1;
    // A strict session may need several polling cycles before its remote peer
    // is cryptographically bound. Never retain the prior session's transport
    // while that verification is pending.
    this.transport.close();
    this.securityBootstrap.clear();
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

  private clearActiveSession(): void {
    const sessionId = this.state$.value.session?.id ?? '';
    this.securityGeneration += 1;
    this.stopPolling();
    this.transport.close();
    if (sessionId) this.secureSequences.clearScope(sessionId);
    if (sessionId) this.controlPlane.forgetSession(sessionId);
    this.securityBootstrap.clear();
    this.messagePollInFlight = false;
    this.state$.next({ session: null, participants: [], messages: [], cursor: '0', role: null });
  }

  private async refreshSecurity(): Promise<void> {
    const session = this.state$.value.session;
    if (!session) return;
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
    const generation = this.securityGeneration;
    const ready = await this.securityBootstrap.ensure(session, this.currentUserId);
    if (generation !== this.securityGeneration || this.state$.value.session?.id !== session.id) return;
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
      generation !== this.securityGeneration
      || active.session?.id !== sessionId
      || !this.isStrictSession(active.session)
      || !active.role
      || this.securityState$.value.status !== 'ready'
      || this.transport.mode$.value !== 'idle'
    ) return;

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
      if (generation !== this.securityGeneration || this.state$.value.session?.id !== sessionId) {
        this.transport.close();
      }
    } catch {
      // Fail closed and allow a later authenticated bootstrap poll to retry.
      if (generation === this.securityGeneration && this.state$.value.session?.id === sessionId) {
        this.transport.close();
      }
    }
  }

  private async acceptChatPage(session: ShareSession, rawMessages: unknown[], cursor: string): Promise<void> {
    if (this.state$.value.session?.id !== session.id) return;
    if (this.isStrictSession(session)) {
      for (const raw of rawMessages) {
        try { await this.acceptStrictChatWire(raw); } catch { /* reject and advance the opaque relay cursor */ }
      }
    } else {
      for (const raw of rawMessages) {
        const item = this.parseLegacyChat(raw);
        if (item) this.appendMessage(item);
      }
    }
    if (this.state$.value.session?.id === session.id) {
      this.state$.next({ ...this.state$.value, cursor });
    }
  }

  private async acceptStrictChatWire(raw: unknown): Promise<void> {
    const wire = this.parseStrictChatWire(raw);
    const session = this.state$.value.session;
    if (!wire || !session || !this.isStrictSession(session) || !session.security_epoch) return;
    if (!this.hasPermission('chat') || !this.cryptoPort.ready(session.id, session.security_epoch)) return;
    const opened = await this.cryptoPort.open(wire.encrypted_payload, {
      scopeId: session.id,
      epoch: session.security_epoch,
    });
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
