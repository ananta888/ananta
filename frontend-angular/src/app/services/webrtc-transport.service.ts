/**
 * T21: Unified Transport Abstraction
 * Exposes a single send/receive interface regardless of whether
 * the underlying transport is WebRTC DataChannel or Hub Relay.
 * transport_order from network profile: ["webrtc", "hub_relay"]
 */
import { Injectable, inject } from '@angular/core';
import { Subject, BehaviorSubject } from 'rxjs';
import { WebrtcSessionService } from './webrtc-session.service';
import { NetworkProfileService } from './network-profile.service';
import { HubApiCoreService } from './hub-api-core.service';
import { AgentDirectoryService } from './agent-directory.service';
import { RelayEnvelope } from './pair-view-sync.types';

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

@Injectable({ providedIn: 'root' })
export class WebrtcTransportService {
  private webrtc = inject(WebrtcSessionService);
  private profiles = inject(NetworkProfileService);
  private core = inject(HubApiCoreService);
  private dir = inject(AgentDirectoryService);

  readonly mode$ = new BehaviorSubject<TransportMode>('idle');
  readonly message$ = new Subject<TransportMessage>();

  private sessionId = '';
  private relayPollHandle: ReturnType<typeof setInterval> | null = null;
  private relayCursor = '';

  private get hubUrl(): string {
    return this.dir.list().find(a => a.role === 'hub')?.url ?? '';
  }

  async open(sessionId: string, isInitiator: boolean): Promise<void> {
    this.sessionId = sessionId;
    const order = this.profiles.current.transport_order;
    const useWebrtc = order[0] === 'webrtc';

    if (useWebrtc) {
      this.mode$.next('webrtc');
      // Monitor for WebRTC failure and fall back
      this.webrtc.state$.subscribe(state => {
        if (state === 'failed' && this.mode$.value === 'webrtc') {
          this.switchToHubRelay();
        }
      });
      // Relay DataChannel messages
      this.webrtc.dcMessage$.subscribe(msg => {
        this.message$.next({ type: msg.type, session_id: sessionId, payload: msg.payload });
      });
      await this.webrtc.startSession(sessionId, isInitiator);
    } else {
      this.switchToHubRelay();
    }
  }

  close(): void {
    this.stopRelayPoll();
    this.webrtc.closeSession();
    this.mode$.next('idle');
  }

  send(type: string, payload: unknown): void {
    if (this.mode$.value === 'webrtc') {
      // Route through DataChannel
      this.webrtc.sendDc(type as any, payload as Record<string, unknown>);
    } else {
      this.hubRelaySend({ type, session_id: this.sessionId, payload });
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
    if (this.mode$.value === 'webrtc') {
      this.webrtc.sendDc('view_payload', envelope as unknown as Record<string, unknown>);
    } else {
      this.hubRelayViewPush(envelope);
    }
  }

  private switchToHubRelay(): void {
    this.mode$.next('hub_relay');
    this.startRelayPoll();
  }

  private startRelayPoll(): void {
    this.stopRelayPoll();
    this.relayPollHandle = setInterval(() => this.relayPoll(), 1000);
  }

  private stopRelayPoll(): void {
    if (this.relayPollHandle) { clearInterval(this.relayPollHandle); this.relayPollHandle = null; }
  }

  private relayPoll(): void {
    const url = this.hubUrl;
    if (!url) return;
    this.core.get<{ ok: boolean; messages: TransportMessage[]; cursor: string; view_messages?: RelayEnvelope[]; view_cursor?: string }>(
      `${url}/share-sessions/${this.sessionId}/view/poll?cursor=${encodeURIComponent(this.relayCursor)}`, url
    ).subscribe({
      next: r => {
        this.relayCursor = r?.cursor ?? this.relayCursor;
        for (const msg of r?.messages ?? []) this.message$.next(msg);
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

  private hubRelaySend(msg: TransportMessage): void {
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
}
