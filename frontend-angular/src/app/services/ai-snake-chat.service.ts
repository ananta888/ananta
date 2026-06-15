import { Injectable, OnDestroy, inject } from '@angular/core';
import { BehaviorSubject } from 'rxjs';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Router } from '@angular/router';
import { AgentDirectoryService } from './agent-directory.service';
import { UserAuthService } from './user-auth.service';
import { ChatSessionsService } from './chat-sessions.service';
import { UiSnapshotService } from './ui-snapshot.service';

interface SnakeRegistration {
  id: string;
  token: string;
  color: string;
}

export interface SnakeParticipant {
  id: string;
  name: string;
  role: string;
  color: string;
  status: string;
  last_seen?: number;
}

export interface SnakeChatMessage {
  id: string;
  sender_id: string;
  text: string;
  created_at: number;
  channel_type: string;
  session_id?: string;
  visibility?: string;
  ui_snapshot?: string;
}

@Injectable({ providedIn: 'root' })
export class AiSnakeChatService implements OnDestroy {
  private http = inject(HttpClient);
  private directory = inject(AgentDirectoryService);
  private auth = inject(UserAuthService);
  private router = inject(Router);
  private chatSessions = inject(ChatSessionsService);
  private uiSnapshot = inject(UiSnapshotService);

  readonly active$ = new BehaviorSubject<boolean>(false);
  readonly snakeId$ = new BehaviorSubject<string>('');
  readonly participants$ = new BehaviorSubject<SnakeParticipant[]>([]);
  readonly messages$ = new BehaviorSubject<SnakeChatMessage[]>([]);
  readonly error$ = new BehaviorSubject<string>('');
  readonly awaitingReply$ = new BehaviorSubject<boolean>(false);

  private snakeToken = '';
  private messageCursor = '0';
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private pollTimer: ReturnType<typeof setInterval> | null = null;
  private heartbeatFailCount = 0;

  getSnakeToken(): string { return this.snakeToken; }

  private hubUrl(): string {
    return this.directory.list().find((a) => a.role === 'hub')?.url || '';
  }

  private collectVisibleWaypoints(): string[] {
    const result: string[] = [];
    try {
      const els = document.querySelectorAll('[data-waypoint]');
      for (const el of Array.from(els)) {
        const rect = el.getBoundingClientRect();
        if (rect.width > 0 && rect.height > 0 && rect.top < window.innerHeight && rect.bottom > 0) {
          const wp = el.getAttribute('data-waypoint');
          if (wp) result.push(wp);
        }
        if (result.length >= 30) break;
      }
    } catch { /* ignore */ }
    return result;
  }

  private withUserHeaders(headers?: HttpHeaders): HttpHeaders {
    let out = headers || new HttpHeaders();
    if (this.auth.token) out = out.set('Authorization', `Bearer ${this.auth.token}`);
    return out;
  }

  private withSnakeHeaders(): HttpHeaders {
    let out = new HttpHeaders().set('Authorization', `Bearer ${this.snakeToken}`);
    if (this.auth.token) out = out.set('X-Ananta-User-Authorization', `Bearer ${this.auth.token}`);
    return out;
  }

  async connect(name = 'web-ai-snake', role = 'viewer'): Promise<void> {
    const base = this.hubUrl();
    if (!base) {
      this.error$.next('Kein Hub gefunden');
      return;
    }
    this.error$.next('');
    const payload = await this.http.post<SnakeRegistration>(
      `${base}/snakes`,
      { name, role },
      { headers: this.withUserHeaders() },
    ).toPromise();
    if (!payload?.id || !payload?.token) {
      this.error$.next('Snake-Registrierung fehlgeschlagen');
      return;
    }
    this.snakeToken = payload.token;
    this.snakeId$.next(payload.id);
    this.active$.next(true);
    this.messageCursor = '0';
    this.chatSessions.load(); // ensure PUG settings are in sessions$ before first tick
    this.startLoops();
  }

  disconnect(): void {
    const base = this.hubUrl();
    const snakeId = this.snakeId$.value;
    if (base && snakeId) {
      this.http.delete(`${base}/snakes/${encodeURIComponent(snakeId)}`, { headers: this.withUserHeaders() }).subscribe({ error: () => {} });
    }
    this.stopLoops();
    this.snakeToken = '';
    this.messageCursor = '0';
    this.snakeId$.next('');
    this.active$.next(false);
    this.participants$.next([]);
    this.messages$.next([]);
  }

  sendRoomMessage(text: string, snakePanelSessionId = ''): void {
    const base = this.hubUrl();
    const snakeId = this.snakeId$.value;
    const content = String(text || '').trim();
    if (!base || !snakeId || !this.snakeToken || !content) return;
    const id = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
    this.awaitingReply$.next(true);
    const currentRoute = this.router.url;
    const visibleWaypoints = this.collectVisibleWaypoints();
    const uiSnap = this.uiSnapshot.capture();
    const uiContext = { route: currentRoute, visible_waypoints: visibleWaypoints, ui_snapshot: uiSnap };
    const activeSessionId = snakePanelSessionId || this.chatSessions.activeSessionId$.value || '';
    this.http.post(
      `${base}/snakes/${encodeURIComponent(snakeId)}/chat/messages`,
      { id, channel_type: 'room', visibility: 'room', text: content, ui_context: uiContext, session_id: activeSessionId },
      { headers: this.withSnakeHeaders() },
    ).subscribe({
      next: () => {
        const own: SnakeChatMessage = { id, sender_id: snakeId, text: content, created_at: Date.now() / 1000, channel_type: 'room' };
        this.messages$.next([...this.messages$.value, own].slice(-300));
      },
      error: (err) => {
        this.awaitingReply$.next(false);
        if (this.isSessionGoneError(err)) {
          this.resetSessionState('Snake-Session abgelaufen. Bitte neu verbinden.');
          return;
        }
        this.error$.next('Senden fehlgeschlagen');
      },
    });
  }

  /** Silent UI-context tick — sends the compact DOM snapshot to the visual
   *  snake session so the LLM has up-to-date view context without the user
   *  having to type anything. Does not set awaitingReply$. */
  sendUiContextTick(uiSnapshot: string): void {
    const base = this.hubUrl();
    const snakeId = this.snakeId$.value;
    if (!base || !snakeId || !this.snakeToken || !uiSnapshot) return;
    const id = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
    const currentRoute = this.router.url;
    const visibleWaypoints = this.collectVisibleWaypoints();
    this.http.post(
      `${base}/snakes/${encodeURIComponent(snakeId)}/chat/messages`,
      {
        id,
        channel_type: 'room',
        visibility: 'system',
        text: `[ui-tick] ${uiSnapshot}`,
        ui_context: { route: currentRoute, visible_waypoints: visibleWaypoints, ui_snapshot: uiSnapshot },
        session_id: 'ananta-visual',
      },
      { headers: this.withSnakeHeaders() },
    ).subscribe({ error: () => {} });
  }

  cancelChat(): void {
    const base = this.hubUrl();
    const snakeId = this.snakeId$.value;
    if (!base || !snakeId) return;
    this.awaitingReply$.next(false);
    this.http.post(
      `${base}/snakes/${encodeURIComponent(snakeId)}/chat/cancel`,
      {},
      { headers: this.withSnakeHeaders() },
    ).subscribe({ error: () => {} });
  }

  private startLoops(): void {
    this.stopLoops();
    this.tick();
    this.heartbeatTimer = setInterval(() => this.heartbeat(), 10000);
    this.pollTimer = setInterval(() => this.tick(), 2000);
  }

  private stopLoops(): void {
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    if (this.pollTimer) clearInterval(this.pollTimer);
    this.heartbeatTimer = null;
    this.pollTimer = null;
  }

  private heartbeat(): void {
    const base = this.hubUrl();
    const snakeId = this.snakeId$.value;
    if (!base || !snakeId) return;
    this.http.post(
      `${base}/snakes/${encodeURIComponent(snakeId)}/heartbeat`,
      {},
      { headers: this.withUserHeaders() },
    ).subscribe({
      next: () => { this.heartbeatFailCount = 0; },
      error: (err) => {
        if (this.isSessionGoneError(err)) {
          this.heartbeatFailCount++;
          // Require 3 consecutive failures before declaring the session gone.
          // A single 403/404 may be a transient blip (server restart, brief 503).
          if (this.heartbeatFailCount >= 3) {
            this.heartbeatFailCount = 0;
            this.resetSessionState('Snake-Session abgelaufen. Bitte neu verbinden.');
          }
          return;
        }
        this.heartbeatFailCount = 0;
      },
    });
  }

  private tick(): void {
    this.loadParticipants();
    this.loadMessages();
  }

  private loadParticipants(): void {
    const base = this.hubUrl();
    if (!base) return;
    this.http.get<{ participants: SnakeParticipant[] }>(
      `${base}/snakes/participants`,
      { headers: this.withUserHeaders() },
    ).subscribe({
      next: (res) => this.participants$.next(res?.participants || []),
      error: () => this.error$.next('Teilnehmer konnten nicht geladen werden'),
    });
  }

  private loadMessages(): void {
    const base = this.hubUrl();
    const snakeId = this.snakeId$.value;
    if (!base || !snakeId) return;
    this.http.get<{ messages: SnakeChatMessage[]; cursor: string }>(
      `${base}/snakes/${encodeURIComponent(snakeId)}/chat/messages?since=${encodeURIComponent(this.messageCursor)}`,
      { headers: this.withUserHeaders() },
    ).subscribe({
      next: (res) => {
        const incoming = res?.messages || [];
        if (!incoming.length) return;
        const known = new Set(this.messages$.value.map((m) => m.id));
        const fresh = incoming.filter((m) => !known.has(m.id));
        if (!fresh.length) return;
        this.messages$.next([...this.messages$.value, ...fresh].slice(-300));
        if (fresh.some((m) => m.sender_id !== this.snakeId$.value)) {
          this.awaitingReply$.next(false);
        }
        this.messageCursor = String(res.cursor || this.messageCursor);
      },
      error: (err) => {
        if (this.isSessionGoneError(err)) {
          this.resetSessionState('Snake-Session abgelaufen. Bitte neu verbinden.');
          return;
        }
        this.error$.next('Chat konnte nicht geladen werden');
      },
    });
  }

  private isSessionGoneError(err: any): boolean {
    const status = Number(err?.status || 0);
    return status === 404 || status === 401 || status === 403;
  }

  private resetSessionState(message: string): void {
    this.stopLoops();
    this.snakeToken = '';
    this.messageCursor = '0';
    this.heartbeatFailCount = 0;
    this.snakeId$.next('');
    this.active$.next(false);
    this.participants$.next([]);
    this.messages$.next([]);
    this.error$.next(message);
  }

  ngOnDestroy(): void {
    this.stopLoops();
  }
}
