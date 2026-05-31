import { Injectable, inject, OnDestroy } from '@angular/core';
import { BehaviorSubject } from 'rxjs';
import { HubApiCoreService } from './hub-api-core.service';
import { AgentDirectoryService } from './agent-directory.service';
import { WebrtcTransportService } from './webrtc-transport.service';
import { NetworkProfileService } from './network-profile.service';

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

  readonly state$ = new BehaviorSubject<ActiveShareState>({
    session: null, participants: [], messages: [], cursor: '0', role: null,
  });

  private pollHandle: ReturnType<typeof setInterval> | null = null;

  get isActive(): boolean { return !!this.state$.value.session; }

  private get hubUrl(): string {
    return this.dir.list().find(a => a.role === 'hub')?.url ?? '';
  }

  createSession(title: string, permissions: Record<string, boolean>, expiresInSeconds: number | null): Promise<ShareSession> {
    return new Promise((resolve, reject) => {
      const url = this.hubUrl;
      if (!url) { reject(new Error('no hub')); return; }
      const body = {
        title, permissions,
        mode: 'relay', transport: 'hub_relay',
        expires_at: expiresInSeconds ? Date.now() / 1000 + expiresInSeconds : null,
      };
      this.core.post<{ ok: boolean; session: ShareSession }>(`${url}/share-sessions`, body, url).subscribe({
        next: r => {
          if (r?.session) {
            this.state$.next({ ...this.state$.value, session: r.session, role: 'owner' });
            this.startPolling();
            resolve(r.session);
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
      this.core.post<{ ok: boolean; session: ShareSession }>(
        `${url}/share-sessions/join`, { invite_code: inviteCode }, url
      ).subscribe({
        next: r => {
          if (r?.session) {
            this.state$.next({ ...this.state$.value, session: r.session, role: 'participant' });
            this.startPolling();
            resolve(r.session);
          } else reject(new Error(String((r as any)?.error ?? 'join failed')));
        },
        error: reject,
      });
    });
  }

  sendMessage(text: string): void {
    const { session } = this.state$.value;
    if (!session || !text.trim()) return;
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
    this.state$.next({ session: null, participants: [], messages: [], cursor: '0', role: null });
  }

  leaveSession(): void {
    this.stopPolling();
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
  }

  private fetchParticipants(): void {
    const { session } = this.state$.value;
    if (!session) return;
    const url = this.hubUrl;
    this.core.get<{ ok: boolean; participants: ShareParticipant[] }>(
      `${url}/share-sessions/${session.id}/participants`, url
    ).subscribe({
      next: r => {
        if (r?.participants) this.state$.next({ ...this.state$.value, participants: r.participants });
      },
      error: () => {},
    });
  }

  private fetchMessages(): void {
    const { session, cursor } = this.state$.value;
    if (!session) return;
    const url = this.hubUrl;
    this.core.get<{ ok: boolean; messages: ShareChatMessage[]; cursor: string }>(
      `${url}/share-sessions/${session.id}/chat/messages?since=${cursor}`, url
    ).subscribe({
      next: r => {
        if (!r?.messages?.length) return;
        const existing = this.state$.value.messages;
        const known = new Set(existing.map(m => m.id));
        const fresh = r.messages.filter(m => !known.has(m.id));
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

  ngOnDestroy(): void { this.stopPolling(); }
}
