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

export type TransportMode = 'webrtc' | 'hub_relay' | 'idle';

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
  private subscriptions = new Subscription();

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
    this.subscriptions = new Subscription();
    this.sessionId = sessionId;
    this.semanticEpoch = this.validEpoch(options.semanticEpoch ?? 1);
    for (const trafficClass of options.semanticTrafficClasses ?? []) {
      this.enableSemanticTraffic(trafficClass);
    }
    const order = this.profiles.current.transport_order;
    const useWebrtc = order[0] === 'webrtc';
    const publicSession = this.controlPlane.isPublicSession(sessionId);
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
      // Monitor for WebRTC failure and fall back
      this.subscriptions.add(this.webrtc.state$.subscribe(state => {
        if (state === 'failed' && this.mode$.value === 'webrtc') {
          if (publicSession) this.close();
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
      await this.webrtc.startSession(sessionId, isInitiator, options.remotePeerId);
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
    if (wasOpen) this.webrtc.closeSession();
    this.semanticRelayClasses.clear();
    this.semanticRelayCursors.clear();
    this.semanticRelayPolls.clear();
    this.semanticRelaySeen.clear();
    this.sessionId = '';
    this.publicSession = false;
    this.mode$.next('idle');
  }

  setSemanticEpoch(epoch: number): void {
    const next = this.validEpoch(epoch);
    if (next === this.semanticEpoch) return;
    this.semanticEpoch = next;
    this.semanticRelayCursors.clear();
    this.semanticRelaySeen.clear();
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
  sendView(envelope: RelayEnvelope): void {
    const strictWireEnvelope: RelayEnvelope = {
      message_id: envelope.message_id,
      encrypted_payload: envelope.encrypted_payload,
    };
    if (this.mode$.value === 'webrtc') {
      this.assertPublicAuthorityAvailable();
      this.webrtc.sendDc('view_payload', strictWireEnvelope as unknown as Record<string, unknown>);
    } else if (this.mode$.value === 'hub_relay') {
      this.assertHubRelayAllowed();
      this.hubRelayViewPush(strictWireEnvelope);
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
  private hubRelayViewPush(envelope: RelayEnvelope): void {
    this.assertHubRelayAllowed();
    const url = this.hubUrl;
    if (!url) return;
    if (envelope.encrypted_payload.length > 256 * 1024) {
      // The backend rejects payloads over _VIEW_PAYLOAD_MAX_BYTES.
      // We never send a payload that large; this is a safety net.
      return;
    }
    this.core.post(`${url}/share-sessions/${this.sessionId}/view/push`, envelope, url)
      .subscribe({ error: () => {} });
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
}
