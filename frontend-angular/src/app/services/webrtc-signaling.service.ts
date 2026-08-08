/**
 * T18: WebRTC signaling for Hub-owned Share sessions.
 *
 * The configured public `/signaling` endpoint currently exposes authenticated
 * HTTP polling, not an authenticated WebSocket protocol. Browser WebSockets
 * cannot attach the required OIDC bearer header, and a nonce is not a bearer
 * credential. Until a ticket-bound native WebSocket adapter exists, Angular
 * therefore uses the authenticated Hub signaling boundary directly. WebRTC
 * media and DataChannels remain peer-to-peer; only SDP/ICE signaling is
 * relayed through the Hub control plane.
 */
import { Injectable, inject } from '@angular/core';
import { Subject, BehaviorSubject } from 'rxjs';
import { HubApiCoreService } from './hub-api-core.service';
import { AgentDirectoryService } from './agent-directory.service';

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

  readonly status$ = new BehaviorSubject<SignalingStatus>('disconnected');
  readonly message$ = new Subject<SignalMessage>();

  private sessionId = '';
  private pollHandle: ReturnType<typeof setInterval> | null = null;
  private pollCursor = '';
  private recipientId = '';

  private get hubUrl(): string {
    return this.dir.list().find(a => a.role === 'hub')?.url ?? '';
  }

  connect(_signalingUrl: string, sessionId: string, recipientId?: string): void {
    this.stopPoll();
    this.sessionId = sessionId;
    this.recipientId = normalizePeerId(recipientId);
    this.pollCursor = '';
    if (!this.recipientId) {
      // Hub signaling is point-to-point. An unbound session must never turn
      // into a room-wide/broadcast signal path.
      this.status$.next('failed');
      return;
    }
    this.fallbackToHubRelay();
  }

  disconnect(): void {
    this.stopPoll();
    this.status$.next('disconnected');
  }

  /**
   * Hard disconnect — irreversible: kills the Hub-signaling poll and clears
   * all peer-connection bindings.
   * Used by Identity-Registry logout: identity went away, so any WebRTC
   * session it was carrying must die now.
   */
  hardDisconnect(): void {
    this.stopPoll();
    this.pollCursor = '';
    this.sessionId = '';
    this.recipientId = '';
    this.status$.next('disconnected');
  }

  send(msg: SignalMessage): void {
    if (!this.recipientId) {
      this.status$.next('failed');
      return;
    }
    // Always replace a caller-provided recipient with the peer selected by
    // connect(). This prevents stale or forged message-level routing.
    const outbound = { ...msg, recipient_id: this.recipientId };
    this.hubRelaySend(outbound);
  }

  // ── Authenticated Hub signaling ─────────────────────────────────────

  fallbackToHubRelay(): void {
    if (!this.sessionId || !this.recipientId) {
      this.stopPoll();
      this.status$.next('failed');
      return;
    }
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
    if (!url || !this.sessionId || !this.recipientId) return;
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
            if (isSignalMessage(sig, this.sessionId, this.recipientId)) this.message$.next(sig);
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

function isSignalMessage(value: unknown, sessionId: string, remotePeerId: string): value is SignalMessage {
  if (!value || typeof value !== 'object') return false;
  const signal = value as Partial<SignalMessage>;
  return (
    signal.session_id === sessionId
    && signal.sender_id === remotePeerId
    && ['offer', 'answer', 'ice_candidate', 'hangup', 'hello'].includes(String(signal.type || ''))
    && 'payload' in signal
  );
}
