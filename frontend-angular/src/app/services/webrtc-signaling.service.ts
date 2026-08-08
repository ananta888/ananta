/**
 * T18: Authenticated WebRTC signaling for local and public Share sessions.
 *
 * The configured public `/signaling` endpoint currently exposes authenticated
 * HTTP polling, not an authenticated WebSocket protocol. Browser WebSockets
 * cannot attach the required OIDC bearer header, and a nonce is not a bearer
 * credential. Angular therefore uses authenticated HTTP polling: OIDC bearer
 * auth at the public rendezvous boundary and Hub auth for legacy/local
 * sessions. Media and DataChannels remain peer-to-peer; only SDP/ICE metadata
 * is relayed through the selected control plane.
 */
import { Injectable, inject } from '@angular/core';
import { Subject, BehaviorSubject } from 'rxjs';
import { PairSessionControlPlaneService } from './pair-session-control-plane.service';

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
  private controlPlane = inject(PairSessionControlPlaneService);

  readonly status$ = new BehaviorSubject<SignalingStatus>('disconnected');
  readonly message$ = new Subject<SignalMessage>();

  private sessionId = '';
  private pollHandle: ReturnType<typeof setInterval> | null = null;
  private pollCursor = '';
  private recipientId = '';

  connect(_signalingUrl: string, sessionId: string, recipientId?: string): void {
    this.stopPoll();
    this.sessionId = sessionId;
    this.recipientId = normalizePeerId(recipientId);
    this.pollCursor = '';
    if (!this.recipientId) {
      // Signaling is point-to-point. An unbound session must never turn
      // into a room-wide/broadcast signal path.
      this.status$.next('failed');
      return;
    }
    this.startAuthenticatedPolling();
  }

  disconnect(): void {
    this.stopPoll();
    this.status$.next('disconnected');
  }

  /**
   * Hard disconnect — irreversible: kills the signaling poll and clears
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
    this.sendAuthenticatedSignal(outbound);
  }

  // Kept for the local semantic-media E2E driver.
  // New code starts the selected authenticated polling boundary directly.

  fallbackToHubRelay(): void {
    this.startAuthenticatedPolling();
  }

  private startAuthenticatedPolling(): void {
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
    this.pollHandle = setInterval(() => this.pollSignals(), 1500);
  }

  private stopPoll(): void {
    if (this.pollHandle) { clearInterval(this.pollHandle); this.pollHandle = null; }
  }

  private pollSignals(): void {
    if (!this.sessionId || !this.recipientId) return;
    this.controlPlane.signalPoll<HubSignalPollResponse>(this.sessionId, this.pollCursor)
      .subscribe({
        next: r => {
          // Local Hub and public rendezvous endpoints use compatible legacy
          // {ok, data:{signals}} envelopes,
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

  private sendAuthenticatedSignal(msg: SignalMessage): void {
    if (!this.sessionId) return;
    this.controlPlane.signalSend(this.sessionId, msg)
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
