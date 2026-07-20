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

interface HubSignalPollPayload {
  readonly signals?: readonly SignalMessage[];
  readonly cursor?: string;
}

interface HubSignalPollResponse extends HubSignalPollPayload {
  readonly ok?: boolean;
  readonly data?: HubSignalPollPayload;
}

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
  private recipientId = '';

  private get hubUrl(): string {
    return this.dir.list().find(a => a.role === 'hub')?.url ?? '';
  }

  connect(signalingUrl: string, sessionId: string, recipientId?: string): void {
    this.sessionId = sessionId;
    this.signalingUrl = signalingUrl;
    this.recipientId = normalizePeerId(recipientId);
    this.reconnectAttempts = 0;
    this.openWebSocket();
  }

  disconnect(): void {
    this.stopReconnect();
    this.stopPoll();
    if (this.ws) { this.ws.close(); this.ws = null; }
    this.status$.next('disconnected');
  }

  /**
   * Hard disconnect — irreversible: cancels reconnect, kills Hub-Relay poll,
   * closes WebSocket, clears all peer-connection bindings.
   * Used by Identity-Registry logout: identity went away, so any WebRTC
   * session it was carrying must die now.
   */
  hardDisconnect(): void {
    this.stopReconnect();
    this.stopPoll();
    if (this.ws) {
      try { this.ws.close(1000, 'identity revoked'); } catch { /* ignore */ }
      this.ws = null;
    }
    this.useHubRelay = false;
    this.pollCursor = '';
    this.sessionId = '';
    this.signalingUrl = '';
    this.recipientId = '';
    this.reconnectAttempts = 0;
    this.status$.next('disconnected');
  }

  send(msg: SignalMessage): void {
    const outbound = this.recipientId && !msg.recipient_id
      ? { ...msg, recipient_id: this.recipientId }
      : msg;
    if (this.useHubRelay) {
      this.hubRelaySend(outbound);
      return;
    }
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(outbound));
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
    this.core.get<HubSignalPollResponse>(endpoint, url)
      .subscribe({
        next: r => {
          // The Hub endpoint uses the legacy {ok, data:{signals}} envelope,
          // while HubApiCoreService only unwraps {status, data}. Accept both
          // documented response shapes here and keep malformed rows out of
          // the peer-connection state machine.
          const payload = r?.data ?? r;
          this.pollCursor = payload?.cursor ?? this.pollCursor;
          for (const sig of payload?.signals ?? []) {
            if (isSignalMessage(sig, this.sessionId)) this.message$.next(sig);
          }
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

function normalizePeerId(value: string | undefined): string {
  if (value === undefined || value === '') return '';
  if (!/^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$/.test(value)) {
    throw new Error('webrtc_signaling_recipient_invalid');
  }
  return value;
}

function isSignalMessage(value: unknown, sessionId: string): value is SignalMessage {
  if (!value || typeof value !== 'object') return false;
  const signal = value as Partial<SignalMessage>;
  return (
    signal.session_id === sessionId
    && ['offer', 'answer', 'ice_candidate', 'hangup', 'hello'].includes(String(signal.type || ''))
    && 'payload' in signal
  );
}
