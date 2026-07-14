import { Injectable, OnDestroy, inject } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Subject, Subscription } from 'rxjs';
import { AiSnakeChatService } from './ai-snake-chat.service';
import { AgentDirectoryService } from './agent-directory.service';
import { UserAuthService } from './user-auth.service';

export interface SnakeEvent {
  type: string;
  ts: number;
  payload: unknown;
}

interface GuideEventPayload {
  request_id?: string;
  trigger_type?: string;
  steps: Array<{
    waypoint: string;
    bubble: string;
    delay_ms?: number;
    x?: number;
    y?: number;
  }>;
}

export interface Candidate {
  label: string;
  bubble: string;
  steps: Array<{
    waypoint: string;
    bubble: string;
    delay_ms?: number;
    x?: number;
    y?: number;
  }>;
}

interface CandidatesEventPayload {
  request_id?: string;
  candidates: Candidate[];
}

@Injectable({ providedIn: 'root' })
export class SnakeEventsService implements OnDestroy {
  private http = inject(HttpClient);
  private snake = inject(AiSnakeChatService);
  private directory = inject(AgentDirectoryService);
  private auth = inject(UserAuthService);

  readonly events$ = new Subject<SnakeEvent>();
  readonly guide$ = new Subject<GuideEventPayload>();
  readonly candidates$ = new Subject<CandidatesEventPayload>();
  readonly connected$ = new Subject<boolean>();

  private eventSource: EventSource | null = null;
  private streamTokenRequest: Subscription | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectDelay = 1000;
  private maxReconnectDelay = 30000;
  private activeSnakeId = '';
  private activeToken = '';

  constructor() {
    this.snake.active$.subscribe(active => {
      if (active) {
        const snakeId = this.snake.snakeId$.value;
        const token = this.snake.getSnakeToken();
        this.connect(snakeId, token);
      } else {
        this.disconnect();
      }
    });
  }

  ngOnDestroy(): void {
    this.disconnect();
  }

  connect(snakeId: string, token: string): void {
    if (!snakeId || !token) return;
    this.disconnect();
    this.activeSnakeId = snakeId;
    this.activeToken = token;
    this.openEventSource();
  }

  disconnect(): void {
    this.clearReconnect();
    this.streamTokenRequest?.unsubscribe();
    this.streamTokenRequest = null;
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
    this.activeSnakeId = '';
    this.activeToken = '';
    this.reconnectDelay = 1000;
    this.connected$.next(false);
  }

  private openEventSource(): void {
    const base = this.directory.list().find(a => a.role === 'hub')?.url || '';
    if (!base) return;
    const snakeId = this.activeSnakeId;
    let headers = new HttpHeaders();
    if (this.auth.token) headers = headers.set('Authorization', `Bearer ${this.auth.token}`);
    this.streamTokenRequest?.unsubscribe();
    this.streamTokenRequest = this.http.post<{ stream_token: string }>(
      `${base}/snakes/${encodeURIComponent(snakeId)}/events/stream-token`,
      {},
      { headers },
    ).subscribe({
      next: (credential) => {
        if (!credential?.stream_token || this.activeSnakeId !== snakeId || !this.activeToken) return;
        const url = `${base}/snakes/${encodeURIComponent(snakeId)}/events/stream?stream_token=${encodeURIComponent(credential.stream_token)}`;
        const es = new EventSource(url);
        this.eventSource = es;

        es.onopen = () => {
          this.reconnectDelay = 1000;
          this.connected$.next(true);
        };

        es.onmessage = (ev: MessageEvent<string>) => {
          try {
            const evt = JSON.parse(ev.data) as SnakeEvent;
            this.events$.next(evt);
            if (evt.type === 'guide') {
              this.guide$.next(evt.payload as GuideEventPayload);
            } else if (evt.type === 'candidates') {
              this.candidates$.next(evt.payload as CandidatesEventPayload);
            }
          } catch (err) {
            // ignore malformed events
          }
        };

        es.onerror = () => {
          this.connected$.next(false);
          // Close current connection and schedule reconnect if still active.
          es.close();
          if (this.eventSource === es) {
            this.eventSource = null;
          }
          if (this.activeSnakeId && this.activeToken) {
            this.scheduleReconnect();
          }
        };
      },
      error: () => {
        this.connected$.next(false);
        if (this.activeSnakeId && this.activeToken) this.scheduleReconnect();
      },
    });
  }

  private scheduleReconnect(): void {
    this.clearReconnect();
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (this.activeSnakeId && this.activeToken) {
        this.openEventSource();
      }
    }, this.reconnectDelay);
    this.reconnectDelay = Math.min(this.maxReconnectDelay, this.reconnectDelay * 2);
  }

  private clearReconnect(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }
}
