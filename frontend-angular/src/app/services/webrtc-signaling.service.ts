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
import {
  BehaviorSubject,
  Observable,
  Subject,
  Subscription,
  finalize,
  firstValueFrom,
} from 'rxjs';
import { PairSessionControlPlaneService } from './pair-session-control-plane.service';
import { WebrtcSignalOutbox } from './webrtc-signal-outbox';
import {
  WebrtcSignalCheckpointContext,
  WebrtcSignalCheckpointStore,
} from './webrtc-signal-checkpoint.store';
import {
  SIGNAL_SESSION_RECREATION_REQUIRED,
  WebrtcSignalSessionGuard,
} from './webrtc-signal-session.guard';

export type SignalType = 'offer' | 'answer' | 'ice_candidate' | 'hangup' | 'hello';

export interface SignalMessage {
  id?: string;
  type: SignalType;
  session_id: string;
  sender_id?: string;
  recipient_id?: string;
  payload: unknown;
}

export type SignalingStatus = 'disconnected' | 'connecting' | 'connected' | 'failed';
export type SignalMessageHandler = (message: Readonly<SignalMessage>) => Promise<void>;

interface HubSignalPollPayload {
  readonly signals?: readonly SignalMessage[];
  readonly cursor?: string | number;
  readonly cursor_floor?: string | number;
  readonly truncated?: boolean;
}

interface HubSignalPollResponse extends HubSignalPollPayload {
  readonly ok?: boolean;
  readonly data?: HubSignalPollPayload;
}

interface SignalSendResponse {
  readonly ok?: boolean;
}

@Injectable({ providedIn: 'root' })
export class WebrtcSignalingService {
  private controlPlane = inject(PairSessionControlPlaneService);
  private checkpoints = inject(WebrtcSignalCheckpointStore);
  private sessionGuard = inject(WebrtcSignalSessionGuard);

  readonly status$ = new BehaviorSubject<SignalingStatus>('disconnected');
  readonly failureReason$ = new BehaviorSubject<string | null>(null);
  readonly message$ = new Subject<SignalMessage>();

  private sessionId = '';
  private pollHandle: ReturnType<typeof setInterval> | null = null;
  private pollRequest: Subscription | null = null;
  private pollCursor = '';
  private recipientId = '';
  private localPeerId = '';
  private readonly seenSignalIds = new Set<string>();
  private readonly outbox = new WebrtcSignalOutbox();
  private messageHandler: SignalMessageHandler | null = null;
  private deliveryInFlight = false;
  private connectionGeneration = 0;
  private checkpointContext: WebrtcSignalCheckpointContext | null = null;

  /** Installs the single awaitable SDP/ICE consumer for the active peer. */
  bindMessageHandler(handler: SignalMessageHandler): () => void {
    if (this.messageHandler) throw new Error('webrtc_signal_handler_already_bound');
    this.messageHandler = handler;
    let active = true;
    return () => {
      if (!active) return;
      active = false;
      if (this.messageHandler === handler) this.messageHandler = null;
    };
  }

  connect(_signalingUrl: string, sessionId: string, recipientId?: string): void {
    this.blockCurrentSessionIfWriteOutcomeIsAmbiguous();
    this.stopPoll();
    this.connectionGeneration += 1;
    this.outbox.reset();
    this.sessionId = sessionId;
    this.recipientId = normalizePeerId(recipientId);
    this.pollCursor = '';
    this.seenSignalIds.clear();
    this.checkpointContext = null;
    try {
      this.controlPlane.assertSessionAvailable(sessionId);
      if (this.controlPlane.isPublicSession(sessionId)) this.sessionGuard.assertReusable(sessionId);
      this.localPeerId = normalizePeerId(this.controlPlane.peerIdForSession(sessionId));
    } catch (error) {
      this.localPeerId = '';
      this.failureReason$.next(errorReason(error));
      this.status$.next('failed');
      return;
    }
    if (this.recipientId && this.localPeerId && this.recipientId === this.localPeerId) {
      this.failureReason$.next('webrtc_peer_identity_must_be_distinct');
      this.status$.next('failed');
      return;
    }
    if (!this.recipientId || !this.localPeerId || !this.messageHandler) {
      // Signaling is point-to-point. An unbound session must never turn
      // into a room-wide/broadcast signal path.
      this.failureReason$.next('webrtc_signal_context_invalid');
      this.status$.next('failed');
      return;
    }
    this.checkpointContext = Object.freeze({
      sessionId,
      localPeerId: this.localPeerId,
      remotePeerId: this.recipientId,
    });
    const checkpoint = this.checkpoints.load(this.checkpointContext);
    this.pollCursor = checkpoint.cursor;
    for (const id of checkpoint.seenSignalIds) this.seenSignalIds.add(id);
    this.startAuthenticatedPolling();
  }

  disconnect(): void {
    this.blockCurrentSessionIfWriteOutcomeIsAmbiguous();
    this.connectionGeneration += 1;
    this.stopPoll();
    this.outbox.reset();
    this.status$.next('disconnected');
  }

  /**
   * Hard disconnect — irreversible: kills the signaling poll and clears
   * all peer-connection bindings.
   * Used by Identity-Registry logout: identity went away, so any WebRTC
   * session it was carrying must die now.
   */
  hardDisconnect(): void {
    this.blockCurrentSessionIfWriteOutcomeIsAmbiguous();
    this.connectionGeneration += 1;
    this.stopPoll();
    this.outbox.reset();
    this.pollCursor = '';
    this.sessionId = '';
    this.recipientId = '';
    this.localPeerId = '';
    this.seenSignalIds.clear();
    this.messageHandler = null;
    this.checkpointContext = null;
    this.checkpoints.clearAll();
    this.status$.next('disconnected');
  }

  send(msg: SignalMessage): Promise<void> {
    const sessionId = this.sessionId;
    if (
      !sessionId
      || msg.session_id !== sessionId
      || !this.recipientId
      || this.status$.value !== 'connected'
    ) {
      const failure = Promise.reject(new Error('webrtc_signal_context_invalid'));
      void failure.catch(() => undefined);
      if (sessionId) {
        this.failClosed(
          sessionId,
          this.connectionGeneration,
          this.sessionGuard.isBlocked(sessionId)
            ? SIGNAL_SESSION_RECREATION_REQUIRED
            : 'webrtc_signal_context_invalid',
        );
      }
      else this.status$.next('failed');
      return failure;
    }
    // Always replace a caller-provided recipient with the peer selected by
    // connect(). This prevents stale or forged message-level routing.
    const outbound = { ...msg, recipient_id: this.recipientId };
    const generation = this.connectionGeneration;
    const operation = this.outbox.enqueue(() => this.sendAuthenticatedSignal(sessionId, outbound));
    // Product callers may intentionally fire-and-forget ICE events. Attach the
    // terminal handler here so a rejected HTTP write is never unobserved.
    void operation.catch(error => this.handleSendFailure(sessionId, generation, error));
    return operation;
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
    this.failureReason$.next(null);
    this.status$.next('connected');
    this.startPoll();
  }

  private startPoll(): void {
    this.stopPoll();
    this.pollHandle = setInterval(() => this.pollSignals(), 1500);
    this.pollSignals();
  }

  private stopPoll(): void {
    if (this.pollHandle) { clearInterval(this.pollHandle); this.pollHandle = null; }
    this.pollRequest?.unsubscribe();
    this.pollRequest = null;
    this.deliveryInFlight = false;
  }

  private pollSignals(): void {
    if (
      !this.sessionId
      || !this.recipientId
      || !this.localPeerId
      || this.pollRequest
      || this.deliveryInFlight
    ) return;
    const sessionId = this.sessionId;
    const remotePeerId = this.recipientId;
    const localPeerId = this.localPeerId;
    const generation = this.connectionGeneration;
    const publicSession = this.controlPlane.isPublicSession(sessionId);
    let request: Observable<HubSignalPollResponse>;
    try {
      request = this.controlPlane.signalPoll<HubSignalPollResponse>(sessionId, this.pollCursor);
    } catch {
      this.failIfAuthorityLost(sessionId, generation);
      return;
    }
    const subscription = request.pipe(finalize(() => { this.pollRequest = null; })).subscribe({
      next: r => {
        if (!this.isCurrentConnection(sessionId, remotePeerId, generation)) return;
        this.deliveryInFlight = true;
        void this.applyPollResponse(
          r,
          sessionId,
          remotePeerId,
          localPeerId,
          publicSession,
          generation,
        ).catch(() => {
          if (this.isCurrentConnection(sessionId, remotePeerId, generation)) {
            this.failClosed(sessionId, generation, 'webrtc_signal_apply_failed');
          }
        }).finally(() => {
          if (generation === this.connectionGeneration) this.deliveryInFlight = false;
        });
      },
      error: error => this.handlePollError(sessionId, generation, error),
    });
    this.pollRequest = subscription.closed ? null : subscription;
  }

  private async applyPollResponse(
    response: HubSignalPollResponse,
    sessionId: string,
    remotePeerId: string,
    localPeerId: string,
    publicSession: boolean,
    generation: number,
  ): Promise<void> {
    // Local Hub and public rendezvous endpoints use compatible envelopes.
    const payload = response?.data ?? response;
    if (
      payload?.truncated === true
      || (publicSession && cursorFallsBelowFloor(this.pollCursor, payload?.cursor_floor))
    ) {
      throw new Error('webrtc_signal_cursor_gap');
    }
    const handler = this.messageHandler;
    if (!handler) throw new Error('webrtc_signal_handler_missing');
    for (const signal of payload?.signals ?? []) {
      if (!isSignalMessage(signal, sessionId, remotePeerId, localPeerId, publicSession)) continue;
      if (signal.id && this.seenSignalIds.has(signal.id)) continue;
      await handler(signal);
      if (!this.isCurrentConnection(sessionId, remotePeerId, generation)) return;
      if (signal.id) {
        this.rememberSignalId(signal.id);
        // Applying one signal can succeed before a later signal in the same
        // page fails. Persist that local application fact without moving the
        // server ACK cursor so a same-session reconnect neither re-applies it
        // nor prunes the still-unapplied remainder.
        this.saveCheckpoint();
      }
      // message$ remains an observation API. It is emitted only after the
      // authoritative async handler applied the signal successfully.
      this.message$.next(signal);
    }
    if (!this.isCurrentConnection(sessionId, remotePeerId, generation)) return;
    // The public backend treats the *next* `since` as the ACK/pruning fence.
    // Store it only after the complete ordered page was applied successfully.
    this.pollCursor = nextCursor(this.pollCursor, payload?.cursor, publicSession);
    this.saveCheckpoint();
  }

  private async sendAuthenticatedSignal(sessionId: string, msg: SignalMessage): Promise<void> {
    const response = await firstValueFrom(
      this.controlPlane.signalSend<SignalSendResponse>(sessionId, msg),
    );
    if (response?.ok !== true) throw new Error('webrtc_signal_send_response_invalid');
  }

  private handlePollError(sessionId: string, generation: number, error: unknown): void {
    try {
      this.controlPlane.assertSessionAvailable(sessionId);
    } catch {
      this.failClosed(sessionId, generation, 'webrtc_signal_authority_lost');
      return;
    }
    const status = Number((error as { status?: unknown } | null)?.status);
    // A bad/ahead cursor means the retained SDP/ICE sequence cannot be
    // reconstructed. Network/5xx failures keep the current cursor and retry.
    if (status === 400 || status === 401 || status === 403 || status === 409) {
      this.failClosed(sessionId, generation, 'webrtc_signal_poll_rejected');
    }
  }

  private failIfAuthorityLost(sessionId: string, generation = this.connectionGeneration): void {
    try { this.controlPlane.assertSessionAvailable(sessionId); } catch {
      this.failClosed(sessionId, generation, 'webrtc_signal_authority_lost');
    }
  }

  private failClosed(
    sessionId: string,
    generation = this.connectionGeneration,
    reason = 'webrtc_signaling_failed',
  ): void {
    if (this.sessionId !== sessionId || generation !== this.connectionGeneration) return;
    this.blockCurrentSessionIfWriteOutcomeIsAmbiguous();
    this.connectionGeneration += 1;
    this.stopPoll();
    this.outbox.reset();
    this.failureReason$.next(reason);
    this.status$.next('failed');
  }

  private handleSendFailure(sessionId: string, generation: number, error: unknown): void {
    const staleBeforeStart = errorReason(error) === 'webrtc_signal_outbox_stale'
      && !this.sessionGuard.isBlocked(sessionId);
    if (staleBeforeStart) return;
    if (this.isPublicSession(sessionId)) this.sessionGuard.block(sessionId);
    const currentGeneration = this.sessionId === sessionId
      ? this.connectionGeneration
      : generation;
    this.failClosed(
      sessionId,
      currentGeneration,
      this.sessionGuard.isBlocked(sessionId)
        ? SIGNAL_SESSION_RECREATION_REQUIRED
        : 'webrtc_signal_send_failed',
    );
  }

  private blockCurrentSessionIfWriteOutcomeIsAmbiguous(): void {
    if (!this.sessionId || !this.outbox.hasInFlightWrite || !this.isPublicSession(this.sessionId)) return;
    this.sessionGuard.block(this.sessionId);
    this.failureReason$.next(SIGNAL_SESSION_RECREATION_REQUIRED);
  }

  private isPublicSession(sessionId: string): boolean {
    try { return this.controlPlane.isPublicSession(sessionId); } catch { return false; }
  }

  private isCurrentConnection(sessionId: string, remotePeerId: string, generation: number): boolean {
    return generation === this.connectionGeneration
      && this.sessionId === sessionId
      && this.recipientId === remotePeerId;
  }

  private rememberSignalId(id: string): void {
    this.seenSignalIds.add(id);
    while (this.seenSignalIds.size > 256) {
      this.seenSignalIds.delete(this.seenSignalIds.values().next().value as string);
    }
  }

  private saveCheckpoint(): void {
    if (!this.checkpointContext) return;
    this.checkpoints.save(this.checkpointContext, {
      cursor: this.pollCursor,
      seenSignalIds: [...this.seenSignalIds],
    });
  }
}

function errorReason(error: unknown): string {
  return error instanceof Error && error.message
    ? error.message
    : 'webrtc_signaling_failed';
}

function cursorFallsBelowFloor(current: string, candidate: string | number | undefined): boolean {
  if (candidate === undefined) return false;
  const floor = typeof candidate === 'number'
    ? Number.isSafeInteger(candidate) && candidate >= 0 ? String(candidate) : ''
    : candidate;
  if (!/^\d+$/.test(floor) || (current && !/^\d+$/.test(current))) return true;
  try { return BigInt(current || '0') < BigInt(floor); } catch { return true; }
}

function nextCursor(current: string, candidate: string | number | undefined, publicSession: boolean): string {
  if (candidate === undefined) return current;
  const normalized = typeof candidate === 'number'
    ? Number.isSafeInteger(candidate) && candidate >= 0 ? String(candidate) : ''
    : candidate;
  if (!normalized || normalized.length > 128) return current;
  if (!publicSession) return normalized;
  if (!/^\d+$/.test(normalized) || (current && !/^\d+$/.test(current))) return current;
  try {
    return !current || BigInt(normalized) >= BigInt(current) ? normalized : current;
  } catch {
    return current;
  }
}

function normalizePeerId(value: string | undefined): string {
  if (value === undefined || value === '') return '';
  if (!/^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$/.test(value)) {
    throw new Error('webrtc_signaling_recipient_invalid');
  }
  return value;
}

function isSignalMessage(
  value: unknown,
  sessionId: string,
  remotePeerId: string,
  localPeerId: string,
  publicSession: boolean,
): value is SignalMessage {
  if (!value || typeof value !== 'object') return false;
  const signal = value as Partial<SignalMessage>;
  return (
    signal.session_id === sessionId
    && signal.sender_id === remotePeerId
    && (!publicSession || (
      typeof signal.id === 'string'
      && /^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$/.test(signal.id)
      && signal.recipient_id === localPeerId
    ))
    && ['offer', 'answer', 'ice_candidate', 'hangup', 'hello'].includes(String(signal.type || ''))
    && 'payload' in signal
  );
}
