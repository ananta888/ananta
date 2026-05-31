/**
 * T18: WebRTC Signaling Client
 * Primary: WebSocket to wss://webrtc.ananta.de/signaling
 * Fallback: HTTP polling via Hub /api/webrtc/sessions/{id}/signal
 */
import { Injectable, inject } from '@angular/core';
import { Subject, BehaviorSubject } from 'rxjs';
import { HubApiCoreService } from './hub-api-core.service';
import { AgentDirectoryService } from './agent-directory.service';
import { OidcAuthService } from './oidc-auth.service';

export type SignalType = 'offer' | 'answer' | 'ice_candidate' | 'hangup' | 'hello';

export interface SignalMessage {
  type: SignalType;
  session_id: string;
  sender_id?: string;
  recipient_id?: string;
  payload: unknown;
}

export type SignalingStatus = 'disconnected' | 'connecting' | 'connected' | 'failed';

@Injectable({ providedIn: 'root' })
export class WebrtcSignalingService {
  private core = inject(HubApiCoreService);
  private dir = inject(AgentDirectoryService);
  private oidc = inject(OidcAuthService);

  readonly status$ = new BehaviorSubject<SignalingStatus>('disconnected');
  readonly message$ = new Subject<SignalMessage>();

  private ws: WebSocket | null = null;
  private sessionId = '';
  private signalingUrl = '';
  private reconnectHandle: ReturnType<typeof setTimeout> | null = null;
  private reconnectAttempts = 0;
  private pollHandle: ReturnType<typeof setInterval> | null = null;
  private pollCursor = '';
  private useHubRelay = false;

  private get hubUrl(): string {
    return this.dir.list().find(a => a.role === 'hub')?.url ?? '';
  }

  connect(signalingUrl: string, sessionId: string): void {
    this.sessionId = sessionId;
    this.signalingUrl = signalingUrl;
    this.reconnectAttempts = 0;
    this.openWebSocket();
  }

  disconnect(): void {
    this.stopReconnect();
    this.stopPoll();
    if (this.ws) { this.ws.close(); this.ws = null; }
    this.status$.next('disconnected');
  }

  send(msg: SignalMessage): void {
    if (this.useHubRelay) {
      this.hubRelaySend(msg);
      return;
    }
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
    }
  }

  // ── WebSocket ────────────────────────────────────────────────────────

  private openWebSocket(): void {
    this.status$.next('connecting');
    try {
      const url = new URL(this.signalingUrl);
      const nonce = this.oidc.sessionNonce;
      if (nonce) url.searchParams.set('nonce', nonce);
      if (this.sessionId) url.searchParams.set('session_id', this.sessionId);
      this.ws = new WebSocket(url.toString());
    } catch {
      this.fallbackToHubRelay();
      return;
    }

    this.ws.onopen = () => {
      this.reconnectAttempts = 0;
      this.status$.next('connected');
      this.ws?.send(JSON.stringify({ type: 'hello', session_id: this.sessionId }));
    };

    this.ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data as string) as SignalMessage;
        this.message$.next(msg);
      } catch { /* ignore malformed */ }
    };

    this.ws.onerror = () => this.scheduleReconnect();
    this.ws.onclose = () => {
      if (this.status$.value !== 'disconnected') this.scheduleReconnect();
    };
  }

  private scheduleReconnect(): void {
    this.reconnectAttempts++;
    if (this.reconnectAttempts > 5) { this.fallbackToHubRelay(); return; }
    const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts - 1), 16000);
    this.reconnectHandle = setTimeout(() => this.openWebSocket(), delay);
  }

  private stopReconnect(): void {
    if (this.reconnectHandle) { clearTimeout(this.reconnectHandle); this.reconnectHandle = null; }
  }

  // ── Hub-Relay fallback ───────────────────────────────────────────────

  fallbackToHubRelay(): void {
    this.useHubRelay = true;
    this.status$.next('connected');
    this.startPoll();
  }

  private startPoll(): void {
    this.stopPoll();
    this.pollHandle = setInterval(() => this.hubRelayPoll(), 1500);
  }

  private stopPoll(): void {
    if (this.pollHandle) { clearInterval(this.pollHandle); this.pollHandle = null; }
  }

  private hubRelayPoll(): void {
    const url = this.hubUrl;
    if (!url || !this.sessionId) return;
    const endpoint = `${url}/api/webrtc/sessions/${this.sessionId}/signal?since=${encodeURIComponent(this.pollCursor)}`;
    this.core.get<{ ok: boolean; signals: SignalMessage[]; cursor: string }>(endpoint, url)
      .subscribe({
        next: r => {
          this.pollCursor = r?.cursor ?? this.pollCursor;
          for (const sig of r?.signals ?? []) this.message$.next(sig);
        },
        error: () => {},
      });
  }

  private hubRelaySend(msg: SignalMessage): void {
    const url = this.hubUrl;
    if (!url || !this.sessionId) return;
    this.core.post(`${url}/api/webrtc/sessions/${this.sessionId}/signal`, msg, url)
      .subscribe({ error: () => {} });
  }
}
