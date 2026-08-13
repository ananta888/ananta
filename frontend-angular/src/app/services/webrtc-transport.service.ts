/**
 * T21: Unified Transport Abstraction
 * Exposes a single send/receive interface regardless of whether
 * the underlying transport is WebRTC DataChannel or Hub Relay.
 * transport_order from network profile: ["webrtc", "hub_relay"]
 */
import { Injectable, inject } from '@angular/core';
import { BehaviorSubject, Subject, Subscription } from 'rxjs';
import { WebrtcSessionService } from './webrtc-session.service';
import { NetworkProfileService } from './network-profile.service';
import { HubApiCoreService } from './hub-api-core.service';
import { AgentDirectoryService } from './agent-directory.service';
import { RelayEnvelope } from './pair-view-sync.types';
import {
  SemanticDataChannelMessage,
  SemanticTrafficClass,
  semanticDcEncode,
  semanticDcEncodePackets,
  validateSemanticDcMessage,
} from './webrtc-datachannel.service';
import { WebrtcSendOperation } from './webrtc-send-operation';
import { PairSessionControlPlaneService } from './pair-session-control-plane.service';
import { SIGNAL_SESSION_RECREATION_REQUIRED } from './webrtc-signal-session.guard';
import {
  isTerminalPairSessionReason,
  terminalPairSessionReason,
} from './pair-session-terminal-error';
import type { PairSessionTerminalReason } from './pair-session-terminal-error';

export type TransportMode = 'webrtc' | 'hub_relay' | 'idle';

export interface ViewTransportState {
  readonly sessionId: string;
  readonly semanticEpoch: number;
  readonly generation: number;
  readonly ready: boolean;
}

export type TransportTerminalFailure = Readonly<
  | {
    readonly kind: 'local_recreation_required';
    readonly sessionId: string;
    readonly reasonCode: typeof SIGNAL_SESSION_RECREATION_REQUIRED;
  }
  | {
    readonly kind: 'server_terminal';
    readonly sessionId: string;
    readonly reasonCode: PairSessionTerminalReason;
  }
>;

/**
 * Generic transport message used by the existing chat path.
 * The Pair-Dev view-sync path uses a different envelope
 * (RelayEnvelope) on the wire; see `sendView()` below.
 */
export interface TransportMessage {
  type: string;
  session_id: string;
  payload: unknown;
}

export interface SemanticTransportOpenOptions {
  semanticEpoch?: number;
  semanticTrafficClasses?: readonly SemanticTrafficClass[];
  remotePeerId?: string;
  unboundPeerFallback?: 'hub_relay';
}

interface SemanticRelayStoredMessage extends SemanticDataChannelMessage {
  cursor: number;
}

interface SemanticRelayPage {
  ok: boolean;
  messages: SemanticRelayStoredMessage[];
  cursor: number;
}

const SEMANTIC_RELAY_POLL_MS = 1000;
const SEMANTIC_RELAY_CLASSES: readonly SemanticTrafficClass[] = Object.freeze([
  'control', 'transcript', 'audio_recovery', 'visual_semantic', 'evidence_bulk', 'diagnostic',
]);

@Injectable({ providedIn: 'root' })
export class WebrtcTransportService {
  private webrtc = inject(WebrtcSessionService);
  private profiles = inject(NetworkProfileService);
  private core = inject(HubApiCoreService);
  private dir = inject(AgentDirectoryService);
  private controlPlane = inject(PairSessionControlPlaneService);

  readonly mode$ = new BehaviorSubject<TransportMode>('idle');
  readonly terminalFailure$ = new BehaviorSubject<TransportTerminalFailure | null>(null);
  readonly viewTransportState$ = new BehaviorSubject<ViewTransportState>(Object.freeze({
    sessionId: '',
    semanticEpoch: 0,
    generation: 0,
    ready: false,
  }));
  readonly message$ = new Subject<TransportMessage>();
  readonly semanticMessage$ = new Subject<SemanticDataChannelMessage>();

  private sessionId = '';
  private publicSession = false;
  private relayPollHandle: ReturnType<typeof setInterval> | null = null;
  private relayCursor = '';
  private semanticEpoch = 1;
  private readonly semanticRelayClasses = new Set<SemanticTrafficClass>();
  private readonly semanticRelayCursors = new Map<SemanticTrafficClass, number>();
  private readonly semanticRelayPolls = new Set<SemanticTrafficClass>();
  private readonly semanticRelaySeen = new Map<SemanticTrafficClass, Set<string>>();
  private readonly terminalSessions = new Set<string>();
  private subscriptions = new Subscription();
  private viewTransportGeneration = 0;

  private get hubUrl(): string {
    return this.dir.list().find(a => a.role === 'hub')?.url ?? '';
  }

  async open(
    sessionId: string,
    isInitiator: boolean,
    options: SemanticTransportOpenOptions = {},
  ): Promise<void> {
    this.close();
    // A transport may only open from an explicit create/join/list binding.
    // Never infer a Hub authority from an otherwise unknown session id.
    this.controlPlane.assertSessionAvailable(sessionId);
    const order = this.profiles.current.transport_order;
    const useWebrtc = order[0] === 'webrtc';
    const publicSession = this.controlPlane.isPublicSession(sessionId);
    const securityEpoch = this.validEpoch(options.semanticEpoch ?? 1);
    const recreationRequired = publicSession && (
      this.terminalSessions.has(terminalSessionKey(sessionId, securityEpoch))
      || this.webrtc.isSessionRecreationRequired(sessionId, securityEpoch)
    );
    if (recreationRequired) {
      this.latchSessionRecreationRequired(sessionId, securityEpoch);
      throw new Error(SIGNAL_SESSION_RECREATION_REQUIRED);
    }
    const priorTerminal = this.terminalFailure$.value;
    if (
      priorTerminal?.kind === 'local_recreation_required'
      && priorTerminal.sessionId === sessionId
    ) this.terminalFailure$.next(null);

    this.subscriptions = new Subscription();
    this.sessionId = sessionId;
    this.semanticEpoch = securityEpoch;
    const viewTransportGeneration = ++this.viewTransportGeneration;
    this.publishViewTransportState(false, viewTransportGeneration);
    for (const trafficClass of options.semanticTrafficClasses ?? []) {
      this.enableSemanticTraffic(trafficClass);
    }
    this.publicSession = publicSession;

    if (useWebrtc) {
      if (!options.remotePeerId) {
        if (options.unboundPeerFallback === 'hub_relay' && order.includes('hub_relay')) {
          this.switchToHubRelay();
          return;
        }
        this.close();
        throw new Error('webrtc_remote_peer_required');
      }
      this.mode$.next('webrtc');
      let peerStartRequested = false;
      // Monitor for WebRTC failure and reversible public runtime suspension.
      this.subscriptions.add(this.webrtc.state$.subscribe(state => {
        // BehaviorSubject can still project the previous peer generation's
        // terminal level until startSession synchronously publishes connecting.
        if (!peerStartRequested) return;
        if (
          state === 'closed'
          && this.mode$.value === 'webrtc'
          && publicSession
          && this.webrtc.failureReason$.value === 'pair_runtime_not_ready'
        ) {
          this.close();
          return;
        }
        if (state === 'failed' && this.mode$.value === 'webrtc') {
          if (publicSession) {
            const failureReason = this.webrtc.failureReason$.value;
            if (isTerminalPairSessionReason(failureReason)) {
              this.emitServerTerminal(sessionId, failureReason);
            } else if (this.webrtc.isSessionRecreationRequired(sessionId, securityEpoch)) {
              this.latchSessionRecreationRequired(sessionId, securityEpoch);
            }
            this.close();
          }
          else this.fallbackFromDirectToHubRelay();
        }
      }));
      // Relay DataChannel messages
      this.subscriptions.add(this.webrtc.dcMessage$.subscribe(msg => {
        if (publicSession && msg.type === 'cursor') return;
        this.message$.next({ type: msg.type, session_id: sessionId, payload: msg.payload });
      }));
      this.subscriptions.add(this.webrtc.semanticMessage$.subscribe(message => {
        if (message.session_id === this.sessionId && message.epoch === this.semanticEpoch) {
          this.semanticMessage$.next(message);
        }
      }));
      this.subscriptions.add(this.webrtc.dataChannelState$.subscribe(state => {
        if (
          this.sessionId === sessionId
          && this.mode$.value === 'webrtc'
          && this.viewTransportGeneration === viewTransportGeneration
        ) this.publishViewTransportState(state === 'open', viewTransportGeneration);
      }));
      try {
        peerStartRequested = true;
        await this.webrtc.startSession(
          sessionId,
          isInitiator,
          options.remotePeerId,
          securityEpoch,
        );
      } catch (error) {
        const terminalReason = terminalPairSessionReason(error);
        const reasonCode = errorReasonCode(error);
        const serverTerminalReason = terminalReason
          || (isTerminalPairSessionReason(reasonCode) ? reasonCode : null);
        if (
          publicSession
          && serverTerminalReason
        ) this.emitServerTerminal(sessionId, serverTerminalReason);
        else if (
          publicSession
          && (
            this.webrtc.isSessionRecreationRequired(sessionId, securityEpoch)
            || reasonCode === SIGNAL_SESSION_RECREATION_REQUIRED
          )
        ) this.latchSessionRecreationRequired(sessionId, securityEpoch);
        this.close();
        throw error;
      }
    } else {
      if (publicSession) {
        this.close();
        throw new Error('public_pair_requires_webrtc');
      }
      this.switchToHubRelay();
    }
  }

  close(): void {
    const wasOpen = this.mode$.value !== 'idle';
    this.stopRelayPoll();
    this.subscriptions.unsubscribe();
    if (wasOpen && this.webrtc.state$.value !== 'closed') this.webrtc.closeSession();
    this.semanticRelayClasses.clear();
    this.semanticRelayCursors.clear();
    this.semanticRelayPolls.clear();
    this.semanticRelaySeen.clear();
    this.sessionId = '';
    this.publicSession = false;
    this.mode$.next('idle');
    this.viewTransportGeneration += 1;
    this.publishViewTransportState(false, this.viewTransportGeneration);
  }

  /** Final teardown after the control plane confirmed that the session ended. */
  retireSession(sessionId: string): void {
    if (this.sessionId === sessionId) this.close();
    this.webrtc.retireSession(sessionId);
    for (const key of this.terminalSessions) {
      if (key.startsWith(`${sessionId}\u0000`)) this.terminalSessions.delete(key);
    }
    if (this.terminalFailure$.value?.sessionId === sessionId) {
      this.terminalFailure$.next(null);
    }
  }

  isSessionRecreationRequired(sessionId: string, securityEpoch = this.semanticEpoch): boolean {
    const exactEpoch = this.validEpoch(securityEpoch);
    return this.terminalSessions.has(terminalSessionKey(sessionId, exactEpoch))
      || this.webrtc.isSessionRecreationRequired(sessionId, exactEpoch);
  }

  setSemanticEpoch(epoch: number): void {
    const next = this.validEpoch(epoch);
    if (next === this.semanticEpoch) return;
    this.semanticEpoch = next;
    this.semanticRelayCursors.clear();
    this.semanticRelaySeen.clear();
    this.publishViewTransportState(
      this.viewTransportState$.value.ready,
      this.viewTransportGeneration,
    );
  }

  enableSemanticTraffic(trafficClass: SemanticTrafficClass): void {
    if (!SEMANTIC_RELAY_CLASSES.includes(trafficClass)) throw new Error('semantic_traffic_class_invalid');
    this.semanticRelayClasses.add(trafficClass);
    if (!this.semanticRelayCursors.has(trafficClass)) this.semanticRelayCursors.set(trafficClass, 0);
  }

  disableSemanticTraffic(trafficClass: SemanticTrafficClass): void {
    if (!SEMANTIC_RELAY_CLASSES.includes(trafficClass)) throw new Error('semantic_traffic_class_invalid');
    this.semanticRelayClasses.delete(trafficClass);
    this.semanticRelayPolls.delete(trafficClass);
  }

  send(type: string, payload: unknown): void {
    if (this.publicSession && type === 'cursor') {
      throw new Error('public_raw_cursor_transport_disabled');
    }
    if (this.mode$.value === 'webrtc') {
      this.assertPublicAuthorityAvailable();
      // Route through DataChannel
      this.webrtc.sendDc(type as any, payload as Record<string, unknown>);
    } else if (this.mode$.value === 'hub_relay') {
      this.assertHubRelayAllowed();
      this.hubRelaySend({ type, session_id: this.sessionId, payload });
    } else {
      throw new Error('pair_transport_not_open');
    }
  }

  /**
   * T06: Send a Pair-Dev view-sync envelope. Routes through
   * WebRTC DataChannel when in webrtc mode, or through the
   * Hub Relay /view/push endpoint with the backend-compatible
   * RelayEnvelope shape otherwise. The existing chat send()
   * path is unchanged; this is a separate code path.
   */
  sendView(envelope: RelayEnvelope): boolean {
    const strictWireEnvelope: RelayEnvelope = {
      message_id: envelope.message_id,
      encrypted_payload: envelope.encrypted_payload,
    };
    if (this.mode$.value === 'webrtc') {
      this.assertPublicAuthorityAvailable();
      return this.webrtc.sendDc(
        'view_payload',
        strictWireEnvelope as unknown as Record<string, unknown>,
      );
    } else if (this.mode$.value === 'hub_relay') {
      this.assertHubRelayAllowed();
      return this.hubRelayViewPush(strictWireEnvelope);
    } else {
      throw new Error('pair_transport_not_open');
    }
  }

  async sendSemantic(
    message: SemanticDataChannelMessage,
    options: { signal?: AbortSignal; deadlineMs?: number } = {},
  ): Promise<WebrtcSendOperation> {
    if (!this.sessionId || message.session_id !== this.sessionId) throw new Error('semantic_session_mismatch');
    if (message.epoch !== this.semanticEpoch) throw new Error('semantic_epoch_mismatch');
    this.enableSemanticTraffic(message.traffic_class);
    if (this.mode$.value === 'webrtc') {
      this.assertPublicAuthorityAvailable();
      return this.webrtc.sendSemantic(message, options);
    }
    if (this.mode$.value !== 'hub_relay') throw new Error('semantic_transport_not_open');

    this.assertHubRelayAllowed();

    const url = this.hubUrl;
    if (!url) throw new Error('semantic_hub_unavailable');
    const frame = await semanticDcEncode(message);
    const encoded = await semanticDcEncodePackets(message);
    const deadline = Math.min(options.deadlineMs ?? Date.now() + 30_000, message.expires_at_ms);
    const operation = new WebrtcSendOperation(
      message.session_id,
      message.epoch,
      encoded.digest,
      deadline,
      options.signal,
    );
    const request = this.core.request<{ ok: boolean; cursor: number }>(
      'POST',
      `${url}/share-sessions/${this.sessionId}/semantic-relay`,
      url,
      {
        body: frame,
        headers: { 'Content-Type': 'application/vnd.ananta.webrtc.v1' },
        timeoutMs: Math.max(1, deadline - Date.now()),
      },
    ).subscribe({
      next: response => operation.acknowledge(response.cursor),
      error: () => operation.cancel(),
    });
    const cancelRequest = () => request.unsubscribe();
    options.signal?.addEventListener('abort', cancelRequest, { once: true });
    void operation.result.then(() => {
      request.unsubscribe();
      options.signal?.removeEventListener('abort', cancelRequest);
    });
    return operation;
  }

  private switchToHubRelay(): void {
    this.assertHubRelayAllowed();
    this.mode$.next('hub_relay');
    this.publishViewTransportState(true, this.viewTransportGeneration);
    this.startRelayPoll();
  }

  private fallbackFromDirectToHubRelay(): void {
    // The peer connection owns media tracks, its signaling poll and DataChannel
    // queues. Tear that complete direct stack down before exposing relay mode;
    // otherwise both paths can remain live and dispatch duplicate/stale data.
    this.webrtc.closeSession();
    this.switchToHubRelay();
  }

  private startRelayPoll(): void {
    this.stopRelayPoll();
    this.relayPollHandle = setInterval(() => {
      this.relayPoll();
      for (const trafficClass of this.semanticRelayClasses) this.semanticRelayPoll(trafficClass);
    }, SEMANTIC_RELAY_POLL_MS);
  }

  private stopRelayPoll(): void {
    if (this.relayPollHandle) { clearInterval(this.relayPollHandle); this.relayPollHandle = null; }
  }

  private relayPoll(): void {
    try { this.assertHubRelayAllowed(); } catch {
      this.close();
      return;
    }
    const url = this.hubUrl;
    if (!url) return;
    this.core.get<{ ok: boolean; messages: TransportMessage[]; cursor: string; view_messages?: RelayEnvelope[]; view_cursor?: string }>(
      `${url}/share-sessions/${this.sessionId}/view/poll?since=${encodeURIComponent(this.relayCursor)}`, url
    ).subscribe({
      next: r => {
        this.relayCursor = r?.view_cursor ?? r?.cursor ?? this.relayCursor;
        for (const msg of r?.messages ?? []) {
          if (msg && typeof msg.type === 'string') this.message$.next(msg);
        }
        // T06: forward view-sync envelopes through the same message$ bus
        // with type='view_payload' so the PairViewSyncService can subscribe
        // uniformly regardless of transport.
        for (const v of r?.view_messages ?? []) {
          this.message$.next({ type: 'view_payload', session_id: this.sessionId, payload: v });
        }
      },
      error: () => {},
    });
  }

  private semanticRelayPoll(trafficClass: SemanticTrafficClass): void {
    try { this.assertHubRelayAllowed(); } catch {
      this.close();
      return;
    }
    const url = this.hubUrl;
    if (
      !url
      || !this.sessionId
      || !this.semanticRelayClasses.has(trafficClass)
      || this.semanticRelayPolls.has(trafficClass)
    ) return;
    this.semanticRelayPolls.add(trafficClass);
    const cursor = this.semanticRelayCursors.get(trafficClass) ?? 0;
    const query = new URLSearchParams({
      traffic_class: trafficClass,
      epoch: String(this.semanticEpoch),
      cursor: String(cursor),
      limit: '50',
    });
    this.core.get<SemanticRelayPage>(
      `${url}/share-sessions/${this.sessionId}/semantic-relay?${query.toString()}`,
      url,
      undefined,
      false,
    ).subscribe({
      next: page => { void this.dispatchSemanticRelayPage(trafficClass, page).catch(() => {}); },
      error: () => this.semanticRelayPolls.delete(trafficClass),
    });
  }

  private async dispatchSemanticRelayPage(
    trafficClass: SemanticTrafficClass,
    page: SemanticRelayPage,
  ): Promise<void> {
    try {
      if (!this.semanticRelayClasses.has(trafficClass)) return;
      if (!page?.ok || !Number.isSafeInteger(page.cursor) || page.cursor < 0 || !Array.isArray(page.messages)) return;
      const seen = this.semanticRelaySeen.get(trafficClass) ?? new Set<string>();
      this.semanticRelaySeen.set(trafficClass, seen);
      for (const stored of page.messages) {
        const { cursor: _cursor, ...candidate } = stored;
        const message = await validateSemanticDcMessage(candidate);
        if (
          message.session_id !== this.sessionId
          || message.epoch !== this.semanticEpoch
          || message.traffic_class !== trafficClass
        ) throw new Error('semantic_relay_context_mismatch');
        const identity = `${message.epoch}:${message.sender_id}:${message.message_id}`;
        if (!seen.has(identity)) {
          seen.add(identity);
          this.semanticMessage$.next(message);
        }
      }
      while (seen.size > 4096) seen.delete(seen.values().next().value as string);
      await this.acknowledgeSemanticRelay(trafficClass, page.cursor);
    } finally {
      this.semanticRelayPolls.delete(trafficClass);
    }
  }

  private acknowledgeSemanticRelay(trafficClass: SemanticTrafficClass, cursor: number): Promise<void> {
    try { this.assertHubRelayAllowed(); } catch (error) {
      return Promise.reject(error);
    }
    const url = this.hubUrl;
    if (!url) return Promise.reject(new Error('semantic_hub_unavailable'));
    return new Promise((resolve, reject) => {
      this.core.post<{ ok: boolean; acknowledged_cursor: number }>(
        `${url}/share-sessions/${this.sessionId}/semantic-relay/ack`,
        { traffic_class: trafficClass, epoch: this.semanticEpoch, cursor },
        url,
      ).subscribe({
        next: response => {
          const acknowledged = response?.acknowledged_cursor;
          if (!Number.isSafeInteger(acknowledged) || acknowledged < cursor) {
            reject(new Error('semantic_relay_ack_invalid'));
            return;
          }
          this.semanticRelayCursors.set(
            trafficClass,
            Math.max(this.semanticRelayCursors.get(trafficClass) ?? 0, acknowledged),
          );
          resolve();
        },
        error: reject,
      });
    });
  }

  private hubRelaySend(msg: TransportMessage): void {
    this.assertHubRelayAllowed();
    const url = this.hubUrl;
    if (!url) return;
    this.core.post(`${url}/share-sessions/${this.sessionId}/view/push`, msg, url)
      .subscribe({ error: () => {} });
  }

  /**
   * T06: Push a RelayEnvelope to the backend view-sync endpoint.
   * Wraps the envelope in the backend-expected body shape
   * ({ message_id, kind, base_hash, new_hash, width, height,
   * encrypted_payload }) and respects _VIEW_PAYLOAD_MAX_BYTES.
   */
  private hubRelayViewPush(envelope: RelayEnvelope): boolean {
    this.assertHubRelayAllowed();
    const url = this.hubUrl;
    if (!url) return false;
    if (envelope.encrypted_payload.length > 256 * 1024) {
      // The backend rejects payloads over _VIEW_PAYLOAD_MAX_BYTES.
      // We never send a payload that large; this is a safety net.
      return false;
    }
    try {
      this.core.post(`${url}/share-sessions/${this.sessionId}/view/push`, envelope, url)
        .subscribe({ error: () => {} });
      return true;
    } catch {
      return false;
    }
  }

  private publishViewTransportState(ready: boolean, generation: number): void {
    const next: ViewTransportState = Object.freeze({
      sessionId: this.sessionId,
      semanticEpoch: this.sessionId ? this.semanticEpoch : 0,
      generation,
      ready: Boolean(this.sessionId) && ready,
    });
    const current = this.viewTransportState$.value;
    if (
      current.sessionId === next.sessionId
      && current.semanticEpoch === next.semanticEpoch
      && current.generation === next.generation
      && current.ready === next.ready
    ) return;
    this.viewTransportState$.next(next);
  }

  private validEpoch(epoch: number): number {
    if (!Number.isSafeInteger(epoch) || epoch < 1) throw new Error('semantic_epoch_invalid');
    return epoch;
  }

  private assertHubRelayAllowed(): void {
    if (!this.sessionId) throw new Error('pair_transport_not_open');
    if (this.controlPlane.isPublicSession(this.sessionId)) {
      throw new Error('public_pair_hub_relay_forbidden');
    }
    this.controlPlane.assertSessionAvailable(this.sessionId);
  }

  private assertPublicAuthorityAvailable(): void {
    if (this.sessionId && this.controlPlane.isPublicSession(this.sessionId)) {
      this.controlPlane.assertSessionAvailable(this.sessionId);
    }
  }

  private latchSessionRecreationRequired(sessionId: string, securityEpoch: number): void {
    const key = terminalSessionKey(sessionId, securityEpoch);
    if (this.terminalSessions.has(key)) return;
    this.terminalSessions.add(key);
    this.terminalFailure$.next(Object.freeze({
      kind: 'local_recreation_required',
      sessionId,
      reasonCode: SIGNAL_SESSION_RECREATION_REQUIRED,
    }));
  }

  private emitServerTerminal(
    sessionId: string,
    reasonCode: PairSessionTerminalReason,
  ): void {
    const current = this.terminalFailure$.value;
    if (
      current?.kind === 'server_terminal'
      && current.sessionId === sessionId
      && current.reasonCode === reasonCode
    ) return;
    this.terminalFailure$.next(Object.freeze({
      kind: 'server_terminal',
      sessionId,
      reasonCode,
    }));
  }
}

function errorReasonCode(error: unknown): string {
  return error instanceof Error ? error.message : '';
}

function terminalSessionKey(sessionId: string, securityEpoch: number): string {
  return `${sessionId}\u0000${securityEpoch}`;
}
