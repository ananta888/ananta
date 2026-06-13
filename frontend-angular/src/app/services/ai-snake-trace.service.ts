import { Injectable, OnDestroy, inject } from '@angular/core';
import { BehaviorSubject, Subscription } from 'rxjs';
import { HttpClient } from '@angular/common/http';
import { AgentDirectoryService } from './agent-directory.service';
import { AiSnakeChatService } from './ai-snake-chat.service';

export interface AiSnakeTraceEvent {
  trace_id: string;
  event_id: string;
  snake_id: string | null;
  session_id: string | null;
  parent_event_id: string | null;
  seq: number;
  phase: string;
  title: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped' | 'cancelled';
  started_at: number;
  finished_at: number | null;
  duration_ms: number | null;
  summary: string;
  details: Record<string, unknown>;
  input_preview: unknown;
  output_preview: unknown;
  raw_available: boolean;
  redaction_applied: boolean;
  error: string | null;
}

export interface AiSnakeTraceMeta {
  trace_id: string;
  snake_id: string | null;
  session_id: string | null;
  status: string;
  created_at: number;
  updated_at: number;
  finished_at: number | null;
  event_count: number;
  latest_seq: number;
}

@Injectable({ providedIn: 'root' })
export class AiSnakeTraceService implements OnDestroy {
  private http = inject(HttpClient);
  private directory = inject(AgentDirectoryService);
  private chatSvc = inject(AiSnakeChatService);

  readonly activeTraceId$ = new BehaviorSubject<string | null>(null);
  readonly traceEvents$ = new BehaviorSubject<AiSnakeTraceEvent[]>([]);
  readonly traceStatus$ = new BehaviorSubject<string>('idle');
  readonly traceMeta$ = new BehaviorSubject<AiSnakeTraceMeta | null>(null);

  private pollTimer: ReturnType<typeof setInterval> | null = null;
  private sinceSeq = 0;
  private awaitingSub?: Subscription;

  constructor() {
    this.awaitingSub = this.chatSvc.awaitingReply$.subscribe((awaiting) => {
      if (awaiting) {
        this.onReplyStarted();
      } else {
        this.onReplyFinished();
      }
    });
  }

  private hubUrl(): string {
    return this.directory.list().find((a) => a.role === 'hub')?.url || '';
  }

  private snakeId(): string {
    return this.chatSvc.snakeId$.value;
  }

  private onReplyStarted(): void {
    this.sinceSeq = 0;
    this.stopPoll();
    // Fast poll while reply is generating
    this.pollTimer = setInterval(() => this.pollLatestTrace(), 1200);
  }

  private onReplyFinished(): void {
    this.stopPoll();
    // One final slow poll to get completed state
    if (this.activeTraceId$.value) {
      this.pollEvents(this.activeTraceId$.value);
    }
  }

  private pollLatestTrace(): void {
    const base = this.hubUrl();
    const sid = this.snakeId();
    if (!base || !sid) return;

    if (this.activeTraceId$.value) {
      this.pollEvents(this.activeTraceId$.value);
      return;
    }

    this.http.get<{ traces: AiSnakeTraceMeta[] }>(
      `${base}/snakes/${encodeURIComponent(sid)}/chat/traces?limit=1`,
    ).subscribe({
      next: (res) => {
        const latest = (res?.traces || [])[0];
        if (!latest) return;
        const now = Date.now() / 1000;
        if (now - (latest.created_at || 0) < 30) {
          this.activeTraceId$.next(latest.trace_id);
          this.traceMeta$.next(latest);
          this.traceStatus$.next(latest.status);
          this.traceEvents$.next([]);
          this.sinceSeq = 0;
          this.pollEvents(latest.trace_id);
        }
      },
      error: () => {},
    });
  }

  private pollEvents(traceId: string): void {
    const base = this.hubUrl();
    const sid = this.snakeId();
    if (!base || !sid) return;

    this.http.get<{
      trace_id: string;
      current_status: string;
      latest_seq: number;
      events: AiSnakeTraceEvent[];
    }>(
      `${base}/snakes/${encodeURIComponent(sid)}/chat/traces/${encodeURIComponent(traceId)}/events?since_seq=${this.sinceSeq}`,
    ).subscribe({
      next: (res) => {
        const incoming = res?.events || [];
        if (incoming.length) {
          const known = new Set(this.traceEvents$.value.map((e) => e.event_id));
          const fresh = incoming.filter((e) => !known.has(e.event_id));
          if (fresh.length) {
            const merged = [...this.traceEvents$.value, ...fresh].sort((a, b) => a.seq - b.seq);
            this.traceEvents$.next(merged);
            this.sinceSeq = Math.max(...merged.map((e) => e.seq)) + 1;
          }
        }
        this.traceStatus$.next(res?.current_status || 'unknown');
      },
      error: () => {},
    });
  }

  loadTrace(traceId: string): void {
    this.activeTraceId$.next(traceId);
    this.traceEvents$.next([]);
    this.sinceSeq = 0;
    this.pollEvents(traceId);
  }

  loadTraceList(snakeId: string): Promise<AiSnakeTraceMeta[]> {
    const base = this.hubUrl();
    if (!base) return Promise.resolve([]);
    return this.http.get<{ traces: AiSnakeTraceMeta[] }>(
      `${base}/snakes/${encodeURIComponent(snakeId)}/chat/traces?limit=20`,
    ).toPromise().then((r) => r?.traces || []).catch(() => []);
  }

  clearTrace(): void {
    this.activeTraceId$.next(null);
    this.traceEvents$.next([]);
    this.traceStatus$.next('idle');
    this.traceMeta$.next(null);
    this.sinceSeq = 0;
    this.stopPoll();
  }

  private stopPoll(): void {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  }

  ngOnDestroy(): void {
    this.stopPoll();
    this.awaitingSub?.unsubscribe();
  }
}
