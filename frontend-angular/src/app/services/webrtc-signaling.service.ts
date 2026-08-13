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
  timer,
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
import { rateLimitRetryAfterMs } from './http-rate-limit';
import { terminalPairSessionReason } from './pair-session-terminal-error';

export type SignalType = 'offer' | 'answer' | 'ice_candidate' | 'hangup' | 'hello';

export interface SignalMessage {
  id?: string;
  type: SignalType;
  session_id: string;
  /** Required and server-validated for public identity-binding v2 sessions. */
  security_epoch?: number;
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
  private pollBackoffUntilMs = 0;
  private pollCursor = '';
  private recipientId = '';
  private localPeerId = '';
  private securityEpoch: number | null = null;
  private signalEpochRequired = false;
  private readonly seenSignalIds = new Set<string>();
  private readonly outbox = new WebrtcSignalOutbox();
  private messageHandler: SignalMessageHandler | null = null;
  private deliveryInFlight = false;
  private connectionGeneration = 0;
  private checkpointContext: WebrtcSignalCheckpointContext | null = null;
  private outboundWriteSerial = 0;
  private activeOutboundWrite: Readonly<{
    sessionId: string;
    securityEpoch: number | null;
    generation: number;
    serial: number;
  }> | null = null;

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

  /** Fails before TURN/peer allocation when a public signaling scope is terminal. */
  assertSessionReusable(sessionId: string, securityEpoch?: number): void {
    if (this.isPublicSession(sessionId)) {
      this.sessionGuard.assertReusable(
        sessionId,
        this.signalGuardEpoch(sessionId, securityEpoch),
      );
    }
  }

  isSessionRecreationRequired(sessionId: string, securityEpoch?: number): boolean {
    if (!this.isPublicSession(sessionId)) return false;
    try {
      return this.sessionGuard.isBlocked(
        sessionId,
        this.signalGuardEpoch(sessionId, securityEpoch),
      );
    } catch {
      return false;
    }
  }

  /** Latches a public replay/apply failure before another owner tears transport down. */
  markSessionRecreationRequired(sessionId: string, securityEpoch?: number): void {
    if (!this.isPublicSession(sessionId)) return;
    const epoch = this.signalGuardEpoch(sessionId, securityEpoch);
    this.sessionGuard.block(sessionId, epoch);
    if (this.sessionId === sessionId) {
      this.failureReason$.next(SIGNAL_SESSION_RECREATION_REQUIRED);
    }
  }

  /** Clears terminal signaling metadata only when the owning session retires. */
  retireSession(sessionId: string): void {
    if (this.sessionId === sessionId) {
      this.connectionGeneration += 1;
      this.stopPoll();
      this.outbox.reset();
      this.pollCursor = '';
      this.pollBackoffUntilMs = 0;
      this.sessionId = '';
      this.recipientId = '';
      this.localPeerId = '';
      this.securityEpoch = null;
      this.signalEpochRequired = false;
      this.seenSignalIds.clear();
      this.messageHandler = null;
      this.checkpointContext = null;
      this.activeOutboundWrite = null;
      this.failureReason$.next(null);
      this.status$.next('disconnected');
    }
    this.checkpoints.clearSession(sessionId);
    this.sessionGuard.clearSession(sessionId);
  }

  connect(
    _signalingUrl: string,
    sessionId: string,
    recipientId?: string,
    securityEpoch?: number,
  ): void {
    this.blockCurrentSessionIfWriteOutcomeIsAmbiguous();
    this.stopPoll();
    this.connectionGeneration += 1;
    this.outbox.reset();
    this.sessionId = sessionId;
    this.recipientId = normalizePeerId(recipientId);
    this.pollCursor = '';
    this.pollBackoffUntilMs = 0;
    this.seenSignalIds.clear();
    this.checkpointContext = null;
    this.securityEpoch = null;
    this.signalEpochRequired = false;
    try {
      this.controlPlane.assertSessionAvailable(sessionId);
      if (this.controlPlane.isPublicSession(sessionId)) {
        this.signalEpochRequired = this.controlPlane.requiresSignalEpoch(sessionId);
        this.securityEpoch = this.signalEpochRequired
          ? validSecurityEpoch(securityEpoch)
          : null;
        this.sessionGuard.assertReusable(sessionId, this.securityEpoch ?? undefined);
      }
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
      ...(this.securityEpoch === null ? {} : { securityEpoch: this.securityEpoch }),
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
    this.securityEpoch = null;
    this.signalEpochRequired = false;
    this.seenSignalIds.clear();
    this.messageHandler = null;
    this.checkpointContext = null;
    this.checkpoints.clearAll();
    this.status$.next('disconnected');
  }

  send(msg: SignalMessage): Promise<void> {
    const sessionId = this.sessionId;
    const securityEpoch = this.securityEpoch;
    const publicSession = sessionId ? this.isPublicSession(sessionId) : false;
    const waitingForPairRuntime = publicSession
      && this.status$.value === 'disconnected'
      && this.failureReason$.value === 'pair_runtime_not_ready';
    if (
      !sessionId
      || msg.session_id !== sessionId
      || !this.recipientId
      || (this.signalEpochRequired && securityEpoch === null)
      || this.status$.value !== 'connected'
    ) {
      const failure = Promise.reject(new Error(
        waitingForPairRuntime ? 'pair_runtime_not_ready' : 'webrtc_signal_context_invalid',
      ));
      void failure.catch(() => undefined);
      if (waitingForPairRuntime) return failure;
      if (sessionId) {
        this.failClosed(
          sessionId,
          this.connectionGeneration,
          publicSession
            && this.sessionGuard.isBlocked(sessionId, securityEpoch ?? undefined)
            ? SIGNAL_SESSION_RECREATION_REQUIRED
            : 'webrtc_signal_context_invalid',
        );
      }
      else this.status$.next('failed');
      return failure;
    }
    // Always replace a caller-provided recipient with the peer selected by
    // connect(). This prevents stale or forged message-level routing.
    const { security_epoch: _callerEpoch, ...callerMessage } = msg;
    const outbound: SignalMessage = this.signalEpochRequired
      ? {
        ...callerMessage,
        recipient_id: this.recipientId,
        security_epoch: securityEpoch as number,
      }
      : { ...callerMessage, recipient_id: this.recipientId };
    const generation = this.connectionGeneration;
    const remotePeerId = this.recipientId;
    const operation = this.outbox.enqueue(() => this.sendAuthenticatedSignal(
      sessionId,
      remotePeerId,
      securityEpoch,
      generation,
      outbound,
    ));
    // Product callers may intentionally fire-and-forget ICE events. Attach the
    // terminal handler here so a rejected HTTP write is never unobserved.
    void operation.catch(error => this.handleSendFailure(
      sessionId, securityEpoch, generation, error,
    ));
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
      || Date.now() < this.pollBackoffUntilMs
    ) return;
    const sessionId = this.sessionId;
    const remotePeerId = this.recipientId;
    const localPeerId = this.localPeerId;
    const generation = this.connectionGeneration;
    const publicSession = this.controlPlane.isPublicSession(sessionId);
    const securityEpoch = this.securityEpoch;
    let request: Observable<HubSignalPollResponse>;
    try {
      request = publicSession && this.signalEpochRequired
        ? this.controlPlane.signalPoll<HubSignalPollResponse>(
          sessionId,
          this.pollCursor,
          securityEpoch as number,
        )
        : this.controlPlane.signalPoll<HubSignalPollResponse>(sessionId, this.pollCursor);
    } catch {
      this.failIfAuthorityLost(sessionId, generation);
      return;
    }
    const subscription = request.pipe(finalize(() => { this.pollRequest = null; })).subscribe({
      next: r => {
        if (!this.isCurrentConnection(sessionId, remotePeerId, generation)) return;
        this.pollBackoffUntilMs = 0;
        this.deliveryInFlight = true;
        void this.applyPollResponse(
          r,
          sessionId,
          remotePeerId,
          localPeerId,
          publicSession,
          securityEpoch,
          generation,
        ).catch(() => {
          if (this.isCurrentConnection(sessionId, remotePeerId, generation)) {
            if (publicSession) {
              this.markSessionRecreationRequired(sessionId, securityEpoch ?? undefined);
            }
            this.failClosed(
              sessionId,
              generation,
              publicSession
                ? SIGNAL_SESSION_RECREATION_REQUIRED
                : 'webrtc_signal_apply_failed',
            );
          }
        }).finally(() => {
          if (generation === this.connectionGeneration) this.deliveryInFlight = false;
        });
      },
      error: error => this.handlePollError(sessionId, securityEpoch, generation, error),
    });
    this.pollRequest = subscription.closed ? null : subscription;
  }

  private async applyPollResponse(
    response: HubSignalPollResponse,
    sessionId: string,
    remotePeerId: string,
    localPeerId: string,
    publicSession: boolean,
    securityEpoch: number | null,
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
      if (!isSignalMessage(
        signal,
        sessionId,
        remotePeerId,
        localPeerId,
        publicSession,
        this.signalEpochRequired,
        securityEpoch,
      )) continue;
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

  private async sendAuthenticatedSignal(
    sessionId: string,
    remotePeerId: string,
    securityEpoch: number | null,
    generation: number,
    msg: SignalMessage,
  ): Promise<void> {
    while (this.isCurrentConnection(sessionId, remotePeerId, generation)) {
      const serial = ++this.outboundWriteSerial;
      this.activeOutboundWrite = Object.freeze({
        sessionId, securityEpoch, generation, serial,
      });
      try {
        const response = await firstValueFrom(
          this.controlPlane.signalSend<SignalSendResponse>(sessionId, msg),
        );
        if (response?.ok !== true) throw new Error('webrtc_signal_send_response_invalid');
        return;
      } catch (error) {
        const retryAfterMs = this.isPublicSession(sessionId)
          ? rateLimitRetryAfterMs(error)
          : null;
        if (retryAfterMs === null) throw error;
        // Public signaling rate limits are evaluated before the server writes
        // the signal. The exact message can therefore be retried without
        // treating the outcome as ambiguous or advancing the serialized
        // outbox. A disconnect/replacement fences the delayed retry below.
        this.clearActiveOutboundWrite(serial);
        await firstValueFrom(timer(retryAfterMs));
      } finally {
        this.clearActiveOutboundWrite(serial);
      }
    }
    throw new Error('webrtc_signal_outbox_stale');
  }

  private handlePollError(
    sessionId: string,
    securityEpoch: number | null,
    generation: number,
    error: unknown,
  ): void {
    const publicSession = this.isPublicSession(sessionId);
    const publicReason = publicSession ? publicSignalRejectionReason(error) : null;
    if (publicReason === 'pair_runtime_not_ready') {
      this.disconnectForRetry(sessionId, securityEpoch, generation, publicReason);
      return;
    }
    if (publicReason === 'epoch_mismatch' || publicReason === 'signal_epoch_required') {
      this.failClosed(sessionId, generation, publicReason, false);
      return;
    }
    const terminalReason = terminalPairSessionReason(error);
    if (terminalReason) {
      this.failClosed(sessionId, generation, terminalReason, false);
      return;
    }
    const status = Number((error as { status?: unknown } | null)?.status);
    const retryAfterMs = rateLimitRetryAfterMs(error);
    if (retryAfterMs !== null) {
      this.pollBackoffUntilMs = Math.max(this.pollBackoffUntilMs, Date.now() + retryAfterMs);
      return;
    }
    // A definitive but unclassified 4xx means the retained public signaling
    // sequence cannot be trusted. It requires a new session, but must not be
    // confused with a server-proven terminal membership above.
    if (status === 400 || status === 401 || status === 403 || status === 404 || status === 409) {
      if (publicSession) {
        this.markSessionRecreationRequired(sessionId, securityEpoch ?? undefined);
        this.failClosed(sessionId, generation, SIGNAL_SESSION_RECREATION_REQUIRED);
      } else {
        this.failClosed(sessionId, generation, 'webrtc_signal_poll_rejected');
      }
      return;
    }
    try {
      this.controlPlane.assertSessionAvailable(sessionId);
    } catch {
      this.failClosed(sessionId, generation, 'webrtc_signal_authority_lost');
      return;
    }
    // Network/5xx failures keep the current cursor and retry unless the local
    // immutable authority vanished while the request was in flight.
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
    blockAmbiguousWrite = true,
  ): void {
    if (this.sessionId !== sessionId || generation !== this.connectionGeneration) return;
    if (blockAmbiguousWrite) {
      this.blockCurrentSessionIfWriteOutcomeIsAmbiguous();
    } else {
      // A server-proven terminal membership supersedes local replay
      // ambiguity. Do not let the teardown disconnect relatch it as a local
      // recreation failure while the terminal event propagates to Share.
      this.activeOutboundWrite = null;
      this.sessionGuard.clear(sessionId, this.securityEpoch ?? undefined);
    }
    this.connectionGeneration += 1;
    this.stopPoll();
    this.outbox.reset();
    this.failureReason$.next(reason);
    this.status$.next('failed');
  }

  private handleSendFailure(
    sessionId: string,
    securityEpoch: number | null,
    generation: number,
    error: unknown,
  ): void {
    const publicSession = this.isPublicSession(sessionId);
    const publicReason = publicSession ? publicSignalRejectionReason(error) : null;
    if (publicReason === 'pair_runtime_not_ready') {
      this.disconnectForRetry(sessionId, securityEpoch, generation, publicReason);
      return;
    }
    if (publicReason === 'epoch_mismatch' || publicReason === 'signal_epoch_required') {
      this.failClosed(sessionId, generation, publicReason, false);
      return;
    }
    const terminalReason = terminalPairSessionReason(error);
    if (terminalReason) {
      if (!this.isCurrentSignalContext(sessionId, securityEpoch, generation)) return;
      this.failClosed(sessionId, generation, terminalReason, false);
      return;
    }
    const staleBeforeStart = errorReason(error) === 'webrtc_signal_outbox_stale'
      && (!publicSession
        || !this.sessionGuard.isBlocked(sessionId, securityEpoch ?? undefined));
    if (staleBeforeStart) return;
    if (publicSession) {
      this.sessionGuard.block(sessionId, securityEpoch ?? undefined);
    }
    // A late result from an older epoch may quarantine only that exact epoch;
    // it must never fail or poison a replacement generation.
    if (
      !this.isCurrentSignalContext(sessionId, securityEpoch, generation)
    ) return;
    this.failClosed(
      sessionId,
      generation,
      publicSession
        && this.sessionGuard.isBlocked(sessionId, securityEpoch ?? undefined)
        ? SIGNAL_SESSION_RECREATION_REQUIRED
        : 'webrtc_signal_send_failed',
    );
  }

  private blockCurrentSessionIfWriteOutcomeIsAmbiguous(): void {
    const write = this.activeOutboundWrite;
    if (
      !this.sessionId
      || !write
      || write.sessionId !== this.sessionId
      || write.generation !== this.connectionGeneration
      || write.securityEpoch !== this.securityEpoch
      || !this.isPublicSession(this.sessionId)
    ) return;
    this.sessionGuard.block(this.sessionId, this.securityEpoch ?? undefined);
    this.failureReason$.next(SIGNAL_SESSION_RECREATION_REQUIRED);
  }

  private disconnectForRetry(
    sessionId: string,
    securityEpoch: number | null,
    generation: number,
    reason: 'pair_runtime_not_ready',
  ): void {
    if (
      this.sessionId !== sessionId
      || this.securityEpoch !== securityEpoch
      || this.connectionGeneration !== generation
    ) return;
    // The rendezvous control plane has proven that it did not accept or expose
    // signaling while one member is parked. Keep membership/security polling
    // eligible and let the transport owner retry after both peers are ready.
    this.activeOutboundWrite = null;
    this.connectionGeneration += 1;
    this.stopPoll();
    this.outbox.reset();
    this.failureReason$.next(reason);
    this.status$.next('disconnected');
  }

  private clearActiveOutboundWrite(serial: number): void {
    if (this.activeOutboundWrite?.serial === serial) this.activeOutboundWrite = null;
  }

  private isPublicSession(sessionId: string): boolean {
    try { return this.controlPlane.isPublicSession(sessionId); } catch { return false; }
  }

  private isCurrentConnection(sessionId: string, remotePeerId: string, generation: number): boolean {
    return generation === this.connectionGeneration
      && this.sessionId === sessionId
      && this.recipientId === remotePeerId;
  }

  private isCurrentSignalContext(
    sessionId: string,
    securityEpoch: number | null,
    generation: number,
  ): boolean {
    return generation === this.connectionGeneration
      && this.sessionId === sessionId
      && this.securityEpoch === securityEpoch;
  }

  private publicSecurityEpoch(sessionId: string, candidate?: number): number {
    if (candidate !== undefined) return validSecurityEpoch(candidate);
    if (this.sessionId === sessionId && this.securityEpoch !== null) return this.securityEpoch;
    throw new Error('signal_epoch_required');
  }

  private signalGuardEpoch(sessionId: string, candidate?: number): number | undefined {
    return this.controlPlane.requiresSignalEpoch(sessionId)
      ? this.publicSecurityEpoch(sessionId, candidate)
      : undefined;
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

type PublicSignalRejectionReason =
  | 'pair_runtime_not_ready'
  | 'epoch_mismatch'
  | 'signal_epoch_required';

function publicSignalRejectionReason(error: unknown): PublicSignalRejectionReason | null {
  if (!error || typeof error !== 'object' || Array.isArray(error)) return null;
  const response = error as { status?: unknown; error?: unknown };
  const status = Number(response.status);
  if (!Number.isInteger(status) || status < 400 || status >= 500) return null;
  if (!response.error || typeof response.error !== 'object' || Array.isArray(response.error)) {
    return null;
  }
  const payload = response.error as Record<string, unknown>;
  const reason = payload['error'] ?? payload['reason_code'];
  return reason === 'pair_runtime_not_ready'
    || reason === 'epoch_mismatch'
    || reason === 'signal_epoch_required'
    ? reason
    : null;
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

function validSecurityEpoch(value: number | undefined): number {
  if (!Number.isSafeInteger(value) || (value as number) < 1) {
    throw new Error('signal_epoch_required');
  }
  return value as number;
}

function isSignalMessage(
  value: unknown,
  sessionId: string,
  remotePeerId: string,
  localPeerId: string,
  publicSession: boolean,
  signalEpochRequired: boolean,
  securityEpoch: number | null,
): value is SignalMessage {
  if (!value || typeof value !== 'object') return false;
  const signal = value as Partial<SignalMessage>;
  const addressedPublicSignal = publicSession
    && signal.session_id === sessionId
    && signal.sender_id === remotePeerId
    && signal.recipient_id === localPeerId;
  if (
    signalEpochRequired
    && addressedPublicSignal
    && signal.security_epoch !== securityEpoch
  ) {
    // The poll endpoint is epoch-partitioned. Receiving an otherwise exact
    // route from another epoch means this generation cannot safely advance its
    // cursor or apply any remaining SDP/ICE.
    throw new Error('epoch_mismatch');
  }
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
