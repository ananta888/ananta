import { Injectable, OnDestroy, inject } from '@angular/core';
import { BehaviorSubject } from 'rxjs';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { AgentDirectoryService } from './agent-directory.service';
import { UserAuthService } from './user-auth.service';

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
}

@Injectable({ providedIn: 'root' })
export class AiSnakeChatService implements OnDestroy {
  private http = inject(HttpClient);
  private directory = inject(AgentDirectoryService);
  private auth = inject(UserAuthService);

  readonly active$ = new BehaviorSubject<boolean>(false);
  readonly snakeId$ = new BehaviorSubject<string>('');
  readonly participants$ = new BehaviorSubject<SnakeParticipant[]>([]);
  readonly messages$ = new BehaviorSubject<SnakeChatMessage[]>([]);
  readonly error$ = new BehaviorSubject<string>('');

  private snakeToken = '';
  private messageCursor = '0';
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private pollTimer: ReturnType<typeof setInterval> | null = null;

  private hubUrl(): string {
    return this.directory.list().find((a) => a.role === 'hub')?.url || '';
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

  sendRoomMessage(text: string): void {
    const base = this.hubUrl();
    const snakeId = this.snakeId$.value;
    const content = String(text || '').trim();
    if (!base || !snakeId || !this.snakeToken || !content) return;
    const id = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
    this.http.post(
      `${base}/snakes/${encodeURIComponent(snakeId)}/chat/messages`,
      { id, channel_type: 'room', visibility: 'room', text: content },
      { headers: this.withSnakeHeaders() },
    ).subscribe({
      next: () => {
        const own: SnakeChatMessage = { id, sender_id: snakeId, text: content, created_at: Date.now() / 1000, channel_type: 'room' };
        this.messages$.next([...this.messages$.value, own].slice(-300));
      },
      error: (err) => {
        if (this.isSessionGoneError(err)) {
          this.resetSessionState('Snake-Session abgelaufen. Bitte neu verbinden.');
          return;
        }
        this.error$.next('Senden fehlgeschlagen');
      },
    });
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
      error: (err) => {
        if (this.isSessionGoneError(err)) {
          this.resetSessionState('Snake-Session abgelaufen. Bitte neu verbinden.');
          return;
        }
        this.error$.next('Heartbeat fehlgeschlagen');
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
