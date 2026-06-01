import { Injectable, OnDestroy } from '@angular/core';
import { BehaviorSubject } from 'rxjs';

export type StreamState = 'disconnected' | 'connecting' | 'connected' | 'reconnecting' | 'failed';

@Injectable({ providedIn: 'root' })
export class ControlCenterEventStreamService implements OnDestroy {
  readonly state$ = new BehaviorSubject<StreamState>('disconnected');
  readonly lastEvent$ = new BehaviorSubject<string>('');
  readonly lastEventObject$ = new BehaviorSubject<Record<string, unknown> | null>(null);
  readonly lastHeartbeatAt$ = new BehaviorSubject<number>(0);

  private es: EventSource | null = null;
  private reconnectAttempts = 0;
  private reconnectHandle: ReturnType<typeof setTimeout> | null = null;

  connect(url: string, token?: string): void {
    this.disconnect();
    this.state$.next('connecting');
    try {
      const connectUrl = token
        ? `${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}`
        : url;
      this.es = new EventSource(connectUrl);
      this.es.onopen = () => {
        this.reconnectAttempts = 0;
        this.state$.next('connected');
      };
      this.es.onmessage = (evt) => {
        const raw = evt.data || 'event';
        this.lastEvent$.next(raw);
        this.lastHeartbeatAt$.next(Date.now());
        try {
          this.lastEventObject$.next(JSON.parse(raw) as Record<string, unknown>);
        } catch {
          this.lastEventObject$.next(null);
        }
      };
      this.es.onerror = () => this.scheduleReconnect(url, token);
    } catch {
      this.state$.next('failed');
    }
  }

  disconnect(): void {
    if (this.reconnectHandle) { clearTimeout(this.reconnectHandle); this.reconnectHandle = null; }
    if (this.es) { this.es.close(); this.es = null; }
    this.state$.next('disconnected');
  }

  private scheduleReconnect(url: string, token?: string): void {
    if (this.reconnectAttempts >= 5) { this.state$.next('failed'); return; }
    this.reconnectAttempts += 1;
    this.state$.next('reconnecting');
    const delayMs = Math.min(1000 * (2 ** (this.reconnectAttempts - 1)), 10000);
    this.reconnectHandle = setTimeout(() => this.connect(url, token), delayMs);
  }

  ngOnDestroy(): void { this.disconnect(); }
}
