import { Injectable, inject, OnDestroy } from '@angular/core';
import { BehaviorSubject } from 'rxjs';
import { HubApiCoreService } from './hub-api-core.service';
import { AgentDirectoryService } from './agent-directory.service';
import { UserAuthService } from './user-auth.service';
import { WebrtcTransportService } from './webrtc-transport.service';
import { NetworkProfileService } from './network-profile.service';
import { PermissionKey, PermissionSet } from './pair-view-sync.types';
import { hasPermission, permissionsFromUiSelection } from './permission-labels';

export interface ShareSession {
  id: string;
  title: string;
  invite_code: string;
  mode: string;
  transport: string;
  permissions: Record<string, boolean>;
  created_at: number;
  expires_at: number | null;
  revoked_at: number | null;
  owner_user_id: string;
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

  readonly state$ = new BehaviorSubject<ActiveShareState>({
    session: null, participants: [], messages: [], cursor: '0', role: null,
  });

  private pollHandle: ReturnType<typeof setInterval> | null = null;

  constructor() {
    this.transport.message$.subscribe((msg) => {
      if (msg.type !== 'chat') return;
      const payload = (msg.payload || {}) as any;
      const item: ShareChatMessage = {
        id: String(payload.id || `webrtc-${Date.now()}-${Math.random()}`),
        session_id: String(payload.session_id || this.state$.value.session?.id || ''),
        sender_id: String(payload.sender_id || 'peer'),
        text: String(payload.text || ''),
        created_at: Number(payload.created_at || Date.now() / 1000),
        visibility: 'room',
      };
      if (!item.text) return;
      const existing = this.state$.value.messages;
      const known = new Set(existing.map((m) => m.id));
      if (known.has(item.id)) return;
      this.state$.next({ ...this.state$.value, messages: [...existing, item].slice(-200) });
    });
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
    const filtered: Record<PermissionKey, boolean> = {
      chat: false, view_tui: false, cursor: false, control: false, artifact_view: false, annotation: false,
    };
    for (const [k, v] of Object.entries(raw)) {
      if (k in filtered && typeof v === 'boolean') {
        (filtered as Record<string, boolean>)[k] = v;
      }
    }
    return Object.freeze(filtered);
  }

  hasPermission(key: PermissionKey): boolean {
    return hasPermission(this.currentPermissions(), key);
  }

  get currentUserId(): string {
    const p = this.userAuth.userPayload;
    return String(p?.sub || p?.preferred_username || p?.email || '');
  }

  private get hubUrl(): string {
    return this.dir.list().find((a) => a.role === 'hub')?.url ?? '';
  }

  private preferredTransport(): 'webrtc' | 'hub_relay' {
    const first = this.profiles.current.transport_order?.[0];
    return first === 'webrtc' ? 'webrtc' : 'hub_relay';
  }

  createSession(
    title: string,
    permissions: Partial<Record<PermissionKey, boolean>>,
    expiresInSeconds: number | null,
  ): Promise<ShareSession> {
    return new Promise((resolve, reject) => {
      const url = this.hubUrl;
      if (!url) { reject(new Error('no hub')); return; }
      const transport = this.preferredTransport();
      const body = {
        title,
        permissions: permissionsFromUiSelection(permissions),
        mode: transport === 'webrtc' ? 'p2p' : 'relay',
        transport,
        expires_at: expiresInSeconds ? Date.now() / 1000 + expiresInSeconds : null,
      };
      this.core.post<{ ok: boolean; session: ShareSession; data: ShareSession }>(`${url}/share-sessions`, body, url).subscribe({
        next: (r) => {
          const sess = r?.session ?? r?.data;
          if (sess) {
            this.state$.next({ ...this.state$.value, session: sess, role: 'owner' });
            this.startPolling();
            if (sess.transport === 'webrtc') {
              void this.transport.open(sess.id, true);
            }
            resolve(sess);
          } else reject(new Error('no session in response'));
        },
        error: reject,
      });
    });
  }

  joinSession(inviteCode: string): Promise<ShareSession> {
    return new Promise((resolve, reject) => {
      const url = this.hubUrl;
      if (!url) { reject(new Error('no hub')); return; }
      this.core.post<{ ok: boolean; session: ShareSession; data: ShareSession }>(
        `${url}/share-sessions/join-by-code`, { invite_code: inviteCode }, url,
      ).subscribe({
        next: (r) => {
          const sess = r?.session ?? r?.data;
          if (sess) {
            this.state$.next({ ...this.state$.value, session: sess, role: 'participant' });
            this.startPolling();
            if (sess.transport === 'webrtc') {
              void this.transport.open(sess.id, false);
            }
            resolve(sess);
          } else reject(new Error(String((r as any)?.error ?? 'join failed')));
        },
        error: reject,
      });
    });
  }

  sendMessage(text: string): void {
    const { session } = this.state$.value;
    if (!session || !text.trim()) return;

    if (session.transport === 'webrtc' && this.transport.mode$.value !== 'idle') {
      this.transport.send('chat', {
        id: crypto.randomUUID ? crypto.randomUUID() : String(Date.now()),
        session_id: session.id,
        text: text.trim(),
        sender_id: 'self',
        created_at: Date.now() / 1000,
      });
      return;
    }

    const url = this.hubUrl;
    this.core.post(`${url}/share-sessions/${session.id}/chat/messages`, {
      text: text.trim(), visibility: 'room', channel_type: 'room',
      id: crypto.randomUUID ? crypto.randomUUID() : String(Date.now()),
    }, url).subscribe({ error: () => {} });
  }

  revokeParticipant(participantId: string): void {
    const { session } = this.state$.value;
    if (!session) return;
    const url = this.hubUrl;
    this.core.delete(`${url}/share-sessions/${session.id}/participants/${participantId}`, url).subscribe({
      next: () => this.fetchParticipants(),
      error: () => {},
    });
  }

  endSession(): void {
    const { session } = this.state$.value;
    if (!session) return;
    const url = this.hubUrl;
    this.core.delete(`${url}/share-sessions/${session.id}`, url).subscribe({ error: () => {} });
    this.stopPolling();
    this.transport.close();
    this.state$.next({ session: null, participants: [], messages: [], cursor: '0', role: null });
  }

  leaveSession(): void {
    this.stopPolling();
    this.transport.close();
    this.state$.next({ session: null, participants: [], messages: [], cursor: '0', role: null });
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
  }

  private sendHeartbeat(): void {
    const { session } = this.state$.value;
    if (!session) return;
    const url = this.hubUrl;
    this.core.post(`${url}/share-sessions/${session.id}/heartbeat`, {}, url)
      .subscribe({ error: () => {} });
  }

  private fetchParticipants(): void {
    const { session } = this.state$.value;
    if (!session) return;
    const url = this.hubUrl;
    this.core.get<{ ok: boolean; participants: ShareParticipant[] }>(
      `${url}/share-sessions/${session.id}/participants`, url,
    ).subscribe({
      next: (r) => {
        if (r?.participants) this.state$.next({ ...this.state$.value, participants: r.participants });
      },
      error: () => {},
    });
  }

  private fetchMessages(): void {
    const { session, cursor } = this.state$.value;
    if (!session || session.transport === 'webrtc') return;
    const url = this.hubUrl;
    this.core.get<{ ok: boolean; messages: ShareChatMessage[]; cursor: string }>(
      `${url}/share-sessions/${session.id}/chat/messages?since=${cursor}`, url,
    ).subscribe({
      next: (r) => {
        if (!r?.messages?.length) return;
        const existing = this.state$.value.messages;
        const known = new Set(existing.map((m) => m.id));
        const fresh = r.messages.filter((m) => !known.has(m.id));
        if (fresh.length) {
          this.state$.next({
            ...this.state$.value,
            messages: [...existing, ...fresh].slice(-200),
            cursor: r.cursor ?? cursor,
          });
        }
      },
      error: () => {},
    });
  }

  participantStatus(p: ShareParticipant): string {
    if (p.revoked_at) return 'gesperrt';
    if (!p.last_seen_at) return 'offline';
    const secs = Math.floor(Date.now() / 1000 - p.last_seen_at);
    return secs < 12 ? 'online' : `offline ${secs}s`;
  }

  ngOnDestroy(): void { this.stopPolling(); this.transport.close(); }
}
