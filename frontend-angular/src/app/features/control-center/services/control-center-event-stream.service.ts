import { Injectable, OnDestroy } from '@angular/core';
import { BehaviorSubject } from 'rxjs';

export type StreamState = 'disconnected' | 'connecting' | 'connected' | 'reconnecting' | 'failed';

@Injectable({ providedIn: 'root' })
export class ControlCenterEventStreamService implements OnDestroy {
  readonly state$ = new BehaviorSubject<StreamState>('disconnected');
  readonly lastEvent$ = new BehaviorSubject<string>('');

  private es: EventSource | null = null;
  private reconnectAttempts = 0;
  private reconnectHandle: ReturnType<typeof setTimeout> | null = null;

  connect(url: string): void {
    this.disconnect();
    this.state$.next('connecting');
    try {
      this.es = new EventSource(url);
      this.es.onopen = () => {
        this.reconnectAttempts = 0;
        this.state$.next('connected');
      };
      this.es.onmessage = (evt) => this.lastEvent$.next(evt.data || 'event');
      this.es.onerror = () => this.scheduleReconnect(url);
    } catch {
      this.state$.next('failed');
    }
  }

  disconnect(): void {
    if (this.reconnectHandle) { clearTimeout(this.reconnectHandle); this.reconnectHandle = null; }
    if (this.es) { this.es.close(); this.es = null; }
    this.state$.next('disconnected');
  }

  private scheduleReconnect(url: string): void {
    if (this.reconnectAttempts >= 5) { this.state$.next('failed'); return; }
    this.reconnectAttempts += 1;
    this.state$.next('reconnecting');
    const delayMs = Math.min(1000 * (2 ** (this.reconnectAttempts - 1)), 10000);
    this.reconnectHandle = setTimeout(() => this.connect(url), delayMs);
  }

  ngOnDestroy(): void { this.disconnect(); }
}
