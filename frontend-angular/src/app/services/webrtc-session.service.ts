/**
 * T19: RTCPeerConnection Lifecycle Management
 * T22: Policy Gates (allowed message types, rate limiting)
 * T23: Audit Logging
 */
import { Injectable, inject } from '@angular/core';
import { Subject, BehaviorSubject, Subscription, firstValueFrom } from 'rxjs';
import { NetworkProfileService } from './network-profile.service';
import { WebrtcSignalingService, SignalMessage } from './webrtc-signaling.service';
import { OidcAuthService } from './oidc-auth.service';
import {
  DcLegacyChunkReassembler,
  DcMessage,
  SemanticDataChannelError,
  SemanticDataChannelMessage,
  dcDecode,
  dcEncode,
  dcEncodeChunked,
  dcMake,
  dcTryReassembleChunk,
  semanticDcDecode,
  semanticDcDecodeChunk,
  semanticDcEncodePackets,
} from './webrtc-datachannel.service';
import { WebrtcChunkReassemblyStore } from './webrtc-chunk-reassembly.store';
import { WebrtcPrioritySendQueue } from './webrtc-priority-send-queue';
import { WebrtcSendOperation } from './webrtc-send-operation';
import { PairSessionControlPlaneService } from './pair-session-control-plane.service';
import { PairOrdinaryMediaPolicy } from './pair-ordinary-media.policy';
import { PairViewSecurityBootstrapService } from './pair-view-security-bootstrap.service';
import { PairMediaE2eeCoordinatorService } from './pair-media-e2ee-coordinator.service';
import { PairMediaE2eeTransformAdapter } from './pair-media-e2ee-transform.adapter';
import type { PublicPairMediaSlot } from './public-pair-media-security-contract';
import { PUBLIC_WEBRTC_STUN_URL } from './public-ananta-endpoints';

export type PeerState = 'idle' | 'connecting' | 'connected' | 'failed' | 'closed';
type PublicSdpPhase = 'none' | 'awaiting-offer' | 'processing-offer'
  | 'awaiting-answer' | 'processing-answer' | 'established';

export interface OrdinaryMediaStatsSnapshot {
  readonly connection: RTCPeerConnectionState;
  readonly stats: RTCStatsReport;
}

const ALLOWED_DC_TYPES = new Set([
  'hello', 'hello_ack', 'ping', 'pong', 'chat', 'view_payload', 'cursor', 'artifact', 'control', 'chunk', 'error',
]);
const RATE_LIMIT_WINDOW_MS = 1000;
const RATE_LIMIT_MAX = 300;
const RATE_LIMIT_BYTES = 4 * 1024 * 1024;
const DC_RECEIVE_QUEUE_MAX = 128;
const DC_RECEIVE_QUEUE_BYTES = 4 * 1024 * 1024;
const PEER_CONNECTION_DISCONNECT_GRACE_MS = 5_000;

interface AuditEvent {
  ts: number;
  type: string;
  session_id: string;
  detail?: string;
}

interface ActivePublicMediaContext {
  readonly sessionId: string;
  readonly peer: RTCPeerConnection;
  readonly sessionGeneration: number;
  readonly adapterGeneration: number;
  readonly contractDigest: string;
}

interface DataChannelReceiveContext {
  readonly peer: RTCPeerConnection;
  readonly channel: RTCDataChannel;
  readonly sessionId: string;
  readonly sessionGeneration: number;
}

interface SemanticSendContext {
  readonly peer: RTCPeerConnection | null;
  readonly sessionId: string;
  readonly sessionGeneration: number;
  readonly channel?: RTCDataChannel;
}

@Injectable({ providedIn: 'root' })
export class WebrtcSessionService {
  private profiles = inject(NetworkProfileService);
  private signaling = inject(WebrtcSignalingService);
  private oidc = inject(OidcAuthService);
  private controlPlane = inject(PairSessionControlPlaneService);
  private mediaPolicy = inject(PairOrdinaryMediaPolicy);
  private securityBootstrap = inject(PairViewSecurityBootstrapService);
  private pairMediaE2ee = inject(PairMediaE2eeCoordinatorService);
  private pairMediaTransforms = inject(PairMediaE2eeTransformAdapter);

  readonly state$ = new BehaviorSubject<PeerState>('idle');
  readonly dataChannelState$ = new BehaviorSubject<RTCDataChannelState | 'absent'>('absent');
  readonly dcMessage$ = new Subject<DcMessage>();
  readonly semanticMessage$ = new Subject<SemanticDataChannelMessage>();
  readonly remoteTrack$ = new Subject<RTCTrackEvent>();
  readonly sessionStarted$ = new Subject<string>();
  readonly auditLog: AuditEvent[] = [];

  private pc: RTCPeerConnection | null = null;
  private dc: RTCDataChannel | null = null;
  private sessionId = '';
  private rateTs: number[] = [];
  private rateBytes: Array<{ ts: number; bytes: number }> = [];
  private connectionTimeout: ReturnType<typeof setTimeout> | null = null;
  private disconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  private readonly chunkReassembler = new DcLegacyChunkReassembler();
  private readonly semanticReassembler = inject(WebrtcChunkReassemblyStore);
  private readonly sendQueue = new WebrtcPrioritySendQueue();
  private readonly pendingSemanticSends = new Map<string, WebrtcSendOperation>();
  private activeEpoch = 1;
  private isInitiator = false;
  private publicSdpPhase: PublicSdpPhase = 'none';
  private releaseSignalingHandler: (() => void) | null = null;
  private signalingStatusSubscription: Subscription | null = null;
  private sessionGeneration = 0;
  private localDescriptionPublicationPending = false;
  private pendingLocalIce: RTCIceCandidateInit[] = [];
  private readonly pendingPublicMediaTracks = new Map<PublicPairMediaSlot, RTCTrackEvent>();
  private readonly disabledPublicMediaContracts = new Map<string, string>();
  private activePublicMediaContext: ActivePublicMediaContext | null = null;
  private readonly pairMediaStatusSubscription = this.pairMediaE2ee.status$.subscribe(status => {
    if (!this.sessionId || status.sessionId !== this.sessionId) return;
    if (status.state === 'ready') {
      this.releasePendingPublicMediaTracks();
      return;
    }
    if (status.state === 'failed') {
      this.stopPendingPublicMediaTracks();
    }
  });

  async startSession(sessionId: string, isInitiator: boolean, remotePeerId?: string): Promise<void> {
    if (
      this.pc || this.releaseSignalingHandler || this.signalingStatusSubscription
      || this.connectionTimeout || this.disconnectTimeout
    ) {
      this.closeSession();
    }
    this.clearDisconnectTimeout();
    const generation = ++this.sessionGeneration;
    this.sessionId = sessionId;
    this.isInitiator = isInitiator;
    this.activeEpoch = 1;
    this.localDescriptionPublicationPending = false;
    this.pendingLocalIce = [];
    this.state$.next('connecting');
    this.dataChannelState$.next('absent');
    this.sessionStarted$.next(sessionId);
    this.audit('session_start', `initiator=${isInitiator}`);

    const profile = this.profiles.current;
    const publicSession = this.controlPlane.isPublicSession(sessionId);
    this.publicSdpPhase = publicSession
      ? isInitiator ? 'awaiting-answer' : 'awaiting-offer'
      : 'none';
    const iceServers = publicSession
      ? [{ urls: PUBLIC_WEBRTC_STUN_URL }]
      : [...profile.ice_servers];
    if (publicSession) {
      try {
        const credentials = await firstValueFrom(this.controlPlane.turnCredentials(sessionId));
        if (credentials?.uris?.length && credentials.username && credentials.password) {
          iceServers.push({
            urls: credentials.uris,
            username: credentials.username,
            credential: credentials.password,
          });
        }
      } catch (error) {
        if (generation !== this.sessionGeneration || this.sessionId !== sessionId) return;
        // A missing/changed public identity is an authority loss, not a TURN
        // outage. It must stop the session instead of continuing on STUN.
        this.controlPlane.assertSessionAvailable(sessionId);
        const status = Number((error as { status?: unknown } | null)?.status);
        if (status === 401 || status === 403) throw error;
        // STUN/direct connectivity remains available when TURN issuance is
        // temporarily unavailable. No application payload falls back to Hub.
      }
    }
    // TURN issuance is asynchronous. A close/replacement during that await
    // fences this generation before it can create or wire a stale peer.
    if (generation !== this.sessionGeneration || this.sessionId !== sessionId) return;
    const config: RTCConfiguration = {
      iceServers,
      iceTransportPolicy: profile.require_e2e_payload_encryption ? 'all' : 'all',
    };

    let pc = new RTCPeerConnection(config);
    this.pc = pc;
    const publicMediaContract = publicSession
      ? this.securityBootstrap.mediaContractFor(sessionId) : null;
    if (publicMediaContract
        && this.disabledPublicMediaContracts.get(sessionId) !== publicMediaContract.digest) {
      let adapterGeneration: number | null = null;
      try {
        adapterGeneration = await this.pairMediaTransforms.prepareSession(
          pc,
          sessionId,
          publicMediaContract,
          reasonCode => {
            const context = this.activePublicMediaContext;
            if (
              adapterGeneration === null
              || !context
              || context.peer !== pc
              || context.sessionId !== sessionId
              || context.sessionGeneration !== generation
              || context.adapterGeneration !== adapterGeneration
              || !this.isCurrentSession(pc, sessionId, generation)
            ) return;
            this.pairMediaE2ee.failMediaExtension(sessionId, reasonCode);
          },
          isInitiator ? 'offerer' : 'answerer',
        );
        if (!this.isCurrentSession(pc, sessionId, generation)) {
          this.pairMediaTransforms.releaseSession(sessionId, adapterGeneration);
          pc.close();
          return;
        }
        const preparedPeer = pc;
        const preparedAdapterGeneration = adapterGeneration;
        const matchesPreparedContext = (): boolean => {
          const context = this.activePublicMediaContext;
          return !!context
            && context.peer === preparedPeer
            && context.sessionId === sessionId
            && context.sessionGeneration === generation
            && context.adapterGeneration === preparedAdapterGeneration
            && this.isCurrentSession(preparedPeer, sessionId, generation);
        };
        this.pairMediaE2ee.bindTransport(sessionId, {
          isOpen: () => matchesPreparedContext()
            && this.dc?.readyState === 'open',
          send: async message => {
            if (!matchesPreparedContext()) throw new Error('public_media_runtime_superseded');
            const channel = this.dc;
            if (!channel) throw new Error('public_media_consent_channel_unavailable');
            const sendContext: SemanticSendContext = Object.freeze({
              peer: preparedPeer,
              sessionId,
              sessionGeneration: generation,
              channel,
            });
            await this.sendSemanticWithContext(message, {}, sendContext);
            if (!matchesPreparedContext()) throw new Error('public_media_runtime_superseded');
          },
          disableMedia: reasonCode => {
            if (!matchesPreparedContext()) return;
            this.audit('public_media_disabled', reasonCode);
            this.disabledPublicMediaContracts.set(sessionId, publicMediaContract.digest);
            this.activePublicMediaContext = null;
            this.pairMediaTransforms.releaseSession(sessionId, preparedAdapterGeneration);
            this.stopPendingPublicMediaTracks();
          },
          failClosed: reasonCode => {
            if (!matchesPreparedContext()) return;
            this.audit('public_media_fail_closed', reasonCode);
            this.disabledPublicMediaContracts.set(sessionId, publicMediaContract.digest);
            this.terminateSession('failed');
          },
        });
        this.activePublicMediaContext = Object.freeze({
          sessionId,
          peer: preparedPeer,
          sessionGeneration: generation,
          adapterGeneration: preparedAdapterGeneration,
          contractDigest: publicMediaContract.digest,
        });
      } catch (error) {
        if (!this.isCurrentSession(pc, sessionId, generation)) {
          if (adapterGeneration !== null) {
            this.pairMediaTransforms.releaseSession(sessionId, adapterGeneration);
          }
          pc.close();
          return;
        }
        this.audit('public_media_prepare_failed', error instanceof Error ? error.message : String(error));
        this.disabledPublicMediaContracts.set(sessionId, publicMediaContract.digest);
        this.pairMediaTransforms.releaseSession(sessionId, adapterGeneration ?? undefined);
        pc.close();
        // The optional extension failed before any media could leave DROP
        // mode. Recreate a clean data-only PC so base Pair chat/view remains.
        pc = new RTCPeerConnection(config);
        this.pc = pc;
        this.pairMediaE2ee.fail(
          sessionId,
          error instanceof Error ? error.message : 'public_media_transform_prepare_failed',
        );
      }
    }
    this.releaseSignalingHandler = this.signaling.bindMessageHandler(async msg => {
      if (!this.isCurrentSession(pc, sessionId, generation) || msg.session_id !== sessionId) return;
      try {
        await this.handleSignal(msg, pc, sessionId, generation);
      } catch (error) {
        if (this.isCurrentSession(pc, sessionId, generation)) {
          this.audit('signal_error', error instanceof Error ? error.message : String(error));
          this.terminateSession('failed');
        }
        throw error;
      }
    });
    this.signalingStatusSubscription = this.signaling.status$.subscribe(status => {
      if (status !== 'failed' || !this.isCurrentSession(pc, sessionId, generation)) return;
      this.audit('signaling_failed');
      this.terminateSession('failed');
    });
    this.signaling.connect(profile.signaling_url, sessionId, remotePeerId);
    if (!this.isCurrentSession(pc, sessionId, generation)) return;
    this.wirePeerConnection(pc, isInitiator, sessionId, generation);

    this.connectionTimeout = setTimeout(() => {
      if (this.isCurrentSession(pc, sessionId, generation) && this.state$.value === 'connecting') {
        this.audit('ice_failed', 'timeout after 15s');
        // The transport coordinator decides whether a local legacy session
        // may use Hub relay. Public sessions fail closed after direct/TURN ICE.
        if (this.pairMediaTransforms.isPrepared(sessionId)) {
          this.pairMediaE2ee.fail(sessionId, 'public_media_peer_connection_timeout');
        } else {
          this.state$.next('failed');
        }
      }
    }, 15_000);
  }

  closeSession(): void {
    this.terminateSession('closed');
  }

  private terminateSession(finalState: Extract<PeerState, 'failed' | 'closed'>): void {
    const closingSessionId = this.sessionId;
    const mediaContext = this.activePublicMediaContext;
    this.activePublicMediaContext = null;
    this.sessionGeneration += 1;
    this.releaseSignalingHandler?.();
    this.releaseSignalingHandler = null;
    this.signalingStatusSubscription?.unsubscribe();
    this.signalingStatusSubscription = null;
    this.localDescriptionPublicationPending = false;
    this.pendingLocalIce = [];
    this.stopPendingPublicMediaTracks();
    if (this.connectionTimeout) { clearTimeout(this.connectionTimeout); this.connectionTimeout = null; }
    this.clearDisconnectTimeout();
    const closingDataChannel = this.dc;
    this.dc = null;
    closingDataChannel?.close();
    this.dataChannelState$.next('absent');
    this.pc?.close();
    this.pc = null;
    this.signaling.disconnect();
    this.chunkReassembler.clear();
    this.semanticReassembler.clearContext(closingSessionId);
    this.sendQueue.cancelContext(closingSessionId);
    this.sendQueue.unbind();
    this.pendingSemanticSends.clear();
    if (closingSessionId) {
      if (mediaContext?.sessionId === closingSessionId) {
        this.pairMediaTransforms.releaseSession(closingSessionId, mediaContext.adapterGeneration);
      } else {
        // Covers cancellation while prepareSession is still awaiting worker
        // ACKs. This is the current generation and cannot target a replacement.
        this.pairMediaTransforms.releaseSession(closingSessionId);
      }
    }
    if (closingSessionId) this.pairMediaE2ee.unbindTransport(closingSessionId, `public_media_session_${finalState}`);
    this.state$.next(finalState);
    this.audit(finalState === 'failed' ? 'session_failed' : 'session_closed');
    this.sessionId = '';
    this.isInitiator = false;
    this.publicSdpPhase = 'none';
  }

  sendDc(type: string, payload: Record<string, unknown> = {}): void {
    if (!this.dc || this.dc.readyState !== 'open') return;
    if (type === 'cursor' && this.controlPlane.isPublicSession(this.sessionId)) {
      throw new Error('public_raw_cursor_transport_disabled');
    }
    const nonce = this.oidc.sessionNonce;
    try {
      const msg = dcMake(type as any, nonce, payload);
      const chunks = dcEncodeChunked(msg);
      const trafficClass = type === 'artifact' ? 'evidence_bulk'
        : type === 'chat' || type === 'cursor' ? 'transcript'
          : type === 'view_payload' ? 'visual_semantic' : 'control';
      for (const part of chunks) {
        this.sendQueue.enqueue(trafficClass, dcEncode(part), Date.now() + 60_000);
      }
    } catch {
      this.audit('send_error', `type=${type}`);
    }
  }

  async sendSemantic(
    message: SemanticDataChannelMessage,
    options: { signal?: AbortSignal; deadlineMs?: number } = {},
  ): Promise<WebrtcSendOperation> {
    const context: SemanticSendContext = Object.freeze({
      peer: this.pc,
      sessionId: this.sessionId,
      sessionGeneration: this.sessionGeneration,
    });
    return this.sendSemanticWithContext(message, options, context);
  }

  private async sendSemanticWithContext(
    message: SemanticDataChannelMessage,
    options: { signal?: AbortSignal; deadlineMs?: number },
    context: SemanticSendContext,
  ): Promise<WebrtcSendOperation> {
    this.assertSemanticSendContext(message, context);
    const encoded = await semanticDcEncodePackets(message);
    // Encoding hashes payloads asynchronously. A same-session replacement
    // must win before any epoch, pending-operation, or singleton queue state
    // can be changed by the old continuation.
    this.assertSemanticSendContext(message, context);
    this.acceptEpoch(message.epoch, context.sessionId);
    const deadline = Math.min(options.deadlineMs ?? Date.now() + 30_000, message.expires_at_ms);
    const operation = new WebrtcSendOperation(
      message.session_id,
      message.epoch,
      encoded.digest,
      deadline,
      options.signal,
    );
    this.pendingSemanticSends.set(encoded.digest, operation);
    void operation.result.then(() => {
      if (this.pendingSemanticSends.get(encoded.digest) === operation) {
        this.pendingSemanticSends.delete(encoded.digest);
      }
    });
    for (const packet of encoded.packets) {
      if (!this.sendQueue.enqueue(message.traffic_class, packet, message.expires_at_ms, operation)) {
        operation.cancel();
        break;
      }
    }
    return operation;
  }

  acknowledgeSemantic(messageDigest: string, cursor?: number): void {
    this.pendingSemanticSends.get(messageDigest)?.acknowledge(cursor);
  }

  addMediaTrack(track: MediaStreamTrack, stream: MediaStream): RTCRtpSender {
    this.mediaPolicy.assertAllowed(this.sessionId);
    if (!this.pc || this.pc.connectionState === 'closed') throw new Error('webrtc_session_not_open');
    if (this.controlPlane.isPublicSession(this.sessionId)) {
      throw new Error('public_media_slot_required');
    }
    const sender = this.pc.addTrack(track, stream);
    void this.negotiateMedia();
    return sender;
  }

  async attachMediaTrack(
    slot: PublicPairMediaSlot,
    track: MediaStreamTrack,
    stream: MediaStream,
  ): Promise<RTCRtpSender> {
    this.mediaPolicy.assertAllowed(this.sessionId);
    if (!this.pc || this.pc.connectionState === 'closed') throw new Error('webrtc_session_not_open');
    if (!this.controlPlane.isPublicSession(this.sessionId)) {
      const sender = this.pc.addTrack(track, stream);
      void this.negotiateMedia();
      return sender;
    }
    const status = this.pairMediaE2ee.statusFor(this.sessionId);
    if (status.state !== 'ready' || !this.pairMediaTransforms.isKeyed(
      this.sessionId,
      this.securityBootstrap.mediaContractFor(this.sessionId)?.epoch,
      status.contractDigest,
    )) throw new Error(status.reasonCode || 'public_media_e2ee_not_ready');
    const sender = this.pairMediaTransforms.senderForSlot(this.sessionId, slot);
    const expectedKind = slot === 'microphone-opus' ? 'audio' : 'video';
    if (track.kind !== expectedKind) throw new Error('public_media_track_kind_invalid');
    await sender.replaceTrack(track);
    return sender;
  }

  publicMediaSlotForReceiver(receiver: RTCRtpReceiver): PublicPairMediaSlot | null {
    return this.pairMediaTransforms.slotForReceiver(this.sessionId, receiver);
  }

  async replaceMediaTrack(sender: RTCRtpSender, track: MediaStreamTrack | null): Promise<void> {
    this.mediaPolicy.assertAllowed(this.sessionId);
    if (!this.pc || !this.pc.getSenders().includes(sender)) throw new Error('webrtc_media_sender_stale');
    if (this.controlPlane.isPublicSession(this.sessionId)
        && this.pairMediaE2ee.statusFor(this.sessionId).state !== 'ready') {
      throw new Error('public_media_e2ee_not_ready');
    }
    await sender.replaceTrack(track);
  }

  removeMediaSender(sender: RTCRtpSender): void {
    if (!this.pc || !this.pc.getSenders().includes(sender)) return;
    if (this.controlPlane.isPublicSession(this.sessionId)) {
      void sender.replaceTrack(null).catch(() => undefined);
      return;
    }
    this.pc.removeTrack(sender);
    void this.negotiateMedia();
  }

  restartMediaIce(): void {
    if (!this.pc || this.pc.connectionState === 'closed') throw new Error('webrtc_session_not_open');
    if (this.controlPlane.isPublicSession(this.sessionId)) {
      const sessionId = this.sessionId;
      this.pairMediaE2ee.deactivate(sessionId, 'public_media_fresh_connection_required');
      throw new Error('public_media_fresh_connection_required');
    }
    this.pc.restartIce();
    void this.negotiateMedia();
  }

  async ordinaryMediaStats(): Promise<OrdinaryMediaStatsSnapshot> {
    const peer = this.pc;
    if (!peer || peer.connectionState === 'closed') {
      throw new Error('webrtc_session_not_open');
    }
    return Object.freeze({
      connection: peer.connectionState,
      stats: await peer.getStats(),
    });
  }

  private wirePeerConnection(
    pc: RTCPeerConnection,
    isInitiator: boolean,
    sessionId: string,
    generation: number,
  ): void {
    pc.onicecandidate = (evt) => {
      if (!this.isCurrentSession(pc, sessionId, generation) || !evt.candidate) return;
      const candidate = evt.candidate.toJSON();
      if (this.localDescriptionPublicationPending) {
        this.pendingLocalIce.push(candidate);
        return;
      }
      void this.signaling.send({
        type: 'ice_candidate', session_id: sessionId, payload: candidate,
      });
    };

    pc.onconnectionstatechange = () => {
      if (!this.isCurrentSession(pc, sessionId, generation)) return;
      const s = pc.connectionState;
      this.audit('connection_state', s);
      if (s === 'connected') {
        this.clearDisconnectTimeout();
        if (this.connectionTimeout) { clearTimeout(this.connectionTimeout); this.connectionTimeout = null; }
        this.state$.next('connected');
        return;
      }
      if (s === 'failed') {
        this.clearDisconnectTimeout();
        this.audit('connection_failed', s);
        if (this.pairMediaTransforms.isPrepared(sessionId)) {
          this.pairMediaE2ee.fail(sessionId, 'public_media_peer_connection_lost');
          return;
        }
        this.state$.next('failed');
        return;
      }
      if (s === 'disconnected') {
        this.audit('connection_interrupted', s);
        this.armDisconnectTimeout(pc, sessionId, generation);
      }
    };
    pc.ontrack = event => {
      if (!this.isCurrentSession(pc, sessionId, generation)) return;
      const isPublicSession = this.controlPlane.isPublicSession(sessionId);
      const currentMediaContract = isPublicSession
        ? this.securityBootstrap.mediaContractFor(sessionId) : null;
      if (
        isPublicSession
        && currentMediaContract
        && this.disabledPublicMediaContracts.get(sessionId) === currentMediaContract.digest
      ) {
        // The remote SDP may still contain rejected/muted media m-lines after
        // either peer downgraded the optional extension. Never route those
        // browser events through the ordinary-media policy or render them.
        try { event.track.stop(); } catch { /* Browser receiver owns the track. */ }
        this.audit('public_media_track_ignored', 'public_media_extension_disabled');
        return;
      }
      if (isPublicSession && this.pairMediaTransforms.isPrepared(sessionId)) {
        let slot = this.pairMediaTransforms.slotForReceiver(sessionId, event.receiver);
        const adapterGeneration = this.activePublicMediaContext?.adapterGeneration;
        if (!slot && this.pairMediaTransforms.isAwaitingRemoteTopology(sessionId, adapterGeneration)) {
          try {
            if (adapterGeneration === undefined) throw new Error('public_media_topology_invalid');
            slot = this.pairMediaTransforms.stageRemoteOfferTrack(
              sessionId, event.transceiver, event.receiver, adapterGeneration,
            );
          } catch {
            try { event.track.stop(); } catch { /* Browser receiver owns the track. */ }
            this.pairMediaE2ee.failMediaExtension(sessionId, 'public_media_remote_slot_invalid');
            return;
          }
        }
        if (!slot || this.pendingPublicMediaTracks.has(slot)) {
          try { event.track.stop(); } catch { /* Browser receiver owns the track. */ }
          this.audit('public_media_rejected', 'public_media_remote_slot_invalid');
          this.pairMediaE2ee.failMediaExtension(sessionId, 'public_media_remote_slot_invalid');
          return;
        }
        if (this.pairMediaE2ee.statusFor(sessionId).state === 'ready') {
          this.remoteTrack$.next(event);
        } else {
          // The receiver transform is already DROP-first. Hold the browser
          // track event until hello/ack, exact topology, and worker key ACK.
          this.pendingPublicMediaTracks.set(slot, event);
        }
        return;
      }
      try {
        this.mediaPolicy.assertAllowed(sessionId);
      } catch (error) {
        try { event.track.stop(); } catch { /* Browser receiver owns the track. */ }
        this.audit('public_media_rejected', error instanceof Error ? error.message : String(error));
        this.terminateSession('failed');
        return;
      }
      this.remoteTrack$.next(event);
    };

    if (isInitiator) {
      this.dc = pc.createDataChannel('ananta', { ordered: true });
      this.wireDc(this.dc, pc, sessionId, generation);
      void this.createOffer(pc, sessionId, generation).catch(error => {
        if (this.isCurrentSession(pc, sessionId, generation)) {
          this.audit('signal_error', error instanceof Error ? error.message : String(error));
          this.terminateSession('failed');
        }
      });
    } else {
      pc.ondatachannel = (evt) => {
        if (!this.isCurrentSession(pc, sessionId, generation)) {
          evt.channel.close();
          return;
        }
        this.dc = evt.channel;
        this.wireDc(this.dc, pc, sessionId, generation);
      };
    }
  }

  private async createOffer(
    pc: RTCPeerConnection,
    sessionId: string,
    generation: number,
  ): Promise<void> {
    if (!this.isCurrentSession(pc, sessionId, generation)) return;
    const offer = await pc.createOffer();
    if (!this.isCurrentSession(pc, sessionId, generation)) return;
    await this.publishLocalDescription('offer', offer, pc, sessionId, generation);
  }

  private async negotiateMedia(): Promise<void> {
    const pc = this.pc;
    const sessionId = this.sessionId;
    const generation = this.sessionGeneration;
    if (!pc || !this.isInitiator || pc.signalingState !== 'stable'
        || this.controlPlane.isPublicSession(sessionId)) return;
    try {
      await this.createOffer(pc, sessionId, generation);
    } catch (error) {
      this.audit('media_negotiation_failed', error instanceof Error ? error.message : String(error));
    }
  }

  private async handleSignal(
    msg: SignalMessage,
    pc: RTCPeerConnection,
    sessionId: string,
    generation: number,
  ): Promise<void> {
    if (!this.isCurrentSession(pc, sessionId, generation)) return;
    if (this.controlPlane.isPublicSession(sessionId)) {
      const expected = msg.type === 'offer'
        ? !this.isInitiator && this.publicSdpPhase === 'awaiting-offer'
        : msg.type === 'answer'
          ? this.isInitiator && this.publicSdpPhase === 'awaiting-answer'
          : true;
      if (!expected) {
        this.audit('public_sdp_rejected', `unexpected_${msg.type}:${this.publicSdpPhase}`);
        if (this.pairMediaTransforms.isPrepared(sessionId)) {
          this.pairMediaE2ee.fail(sessionId, 'public_media_unexpected_sdp');
        }
        if (this.isCurrentSession(pc, sessionId, generation)) this.terminateSession('failed');
        return;
      }
      if (msg.type === 'offer') this.publicSdpPhase = 'processing-offer';
      if (msg.type === 'answer') this.publicSdpPhase = 'processing-answer';
    }
    if (msg.type === 'offer') {
      await pc.setRemoteDescription(new RTCSessionDescription(msg.payload as RTCSessionDescriptionInit));
      if (!this.isCurrentSession(pc, sessionId, generation)) return;
      const disabledContract = this.securityBootstrap.mediaContractFor(sessionId);
      if (
        this.controlPlane.isPublicSession(sessionId)
        && disabledContract
        && this.disabledPublicMediaContracts.get(sessionId) === disabledContract.digest
      ) {
        this.rejectOfferedPublicMedia(pc);
      }
      const mediaContext = this.activePublicMediaContext;
      if (
        this.isActivePublicMediaContext(mediaContext, pc, sessionId, generation)
        && this.pairMediaTransforms.isAwaitingRemoteTopology(
          sessionId, mediaContext.adapterGeneration,
        )
      ) {
        try {
          await this.pairMediaTransforms.bindRemoteOfferTopology(
            sessionId, mediaContext.adapterGeneration,
          );
          if (!this.isCurrentSession(pc, sessionId, generation)) return;
        } catch (error) {
          this.audit('public_media_topology_disabled', error instanceof Error ? error.message : String(error));
          this.pairMediaE2ee.failMediaExtension(
            sessionId,
            error instanceof Error ? error.message : 'public_media_topology_invalid',
          );
        }
      }
      const answer = await pc.createAnswer();
      if (!this.isCurrentSession(pc, sessionId, generation)) return;
      await this.publishLocalDescription('answer', answer, pc, sessionId, generation);
      if (!this.isCurrentSession(pc, sessionId, generation)) return;
      const currentMediaContext = this.activePublicMediaContext;
      if (
        this.isActivePublicMediaContext(currentMediaContext, pc, sessionId, generation)
        && this.pairMediaTransforms.isPrepared(
          sessionId, undefined, currentMediaContext.contractDigest,
          currentMediaContext.adapterGeneration,
        )
      ) {
        this.pairMediaE2ee.markTopologyNegotiated(sessionId);
      }
      if (this.controlPlane.isPublicSession(sessionId)) this.publicSdpPhase = 'established';
    } else if (msg.type === 'answer') {
      await pc.setRemoteDescription(new RTCSessionDescription(msg.payload as RTCSessionDescriptionInit));
      if (!this.isCurrentSession(pc, sessionId, generation)) return;
      const currentMediaContext = this.activePublicMediaContext;
      if (
        this.isActivePublicMediaContext(currentMediaContext, pc, sessionId, generation)
        && this.pairMediaTransforms.isPrepared(
          sessionId, undefined, currentMediaContext.contractDigest,
          currentMediaContext.adapterGeneration,
        )
      ) {
        this.pairMediaE2ee.markTopologyNegotiated(sessionId);
      }
      if (this.controlPlane.isPublicSession(sessionId)) this.publicSdpPhase = 'established';
    } else if (msg.type === 'ice_candidate') {
      await pc.addIceCandidate(new RTCIceCandidate(msg.payload as RTCIceCandidateInit));
    }
  }

  private async publishLocalDescription(
    type: 'offer' | 'answer',
    description: RTCSessionDescriptionInit,
    pc: RTCPeerConnection,
    sessionId: string,
    generation: number,
  ): Promise<void> {
    if (this.localDescriptionPublicationPending) {
      throw new Error('local_description_publication_in_progress');
    }
    this.localDescriptionPublicationPending = true;
    this.pendingLocalIce = [];
    let published = false;
    try {
      await pc.setLocalDescription(description);
      if (!this.isCurrentSession(pc, sessionId, generation)) return;
      await this.signaling.send({ type, session_id: sessionId, payload: description });
      if (!this.isCurrentSession(pc, sessionId, generation)) return;
      published = true;
    } finally {
      if (this.isCurrentSession(pc, sessionId, generation)) {
        const bufferedIce = this.pendingLocalIce;
        this.pendingLocalIce = [];
        this.localDescriptionPublicationPending = false;
        if (published) {
          for (const candidate of bufferedIce) {
            await this.signaling.send({
              type: 'ice_candidate', session_id: sessionId, payload: candidate,
            });
            if (!this.isCurrentSession(pc, sessionId, generation)) return;
          }
        }
      }
    }
  }

  private wireDc(
    dc: RTCDataChannel,
    pc: RTCPeerConnection,
    sessionId: string,
    generation: number,
  ): void {
    const receiveContext: DataChannelReceiveContext = Object.freeze({
      peer: pc,
      channel: dc,
      sessionId,
      sessionGeneration: generation,
    });
    let receiveChain = Promise.resolve();
    let queuedMessages = 0;
    let queuedBytes = 0;
    let openObserved = false;
    this.sendQueue.bind(dc);
    this.dataChannelState$.next(dc.readyState);
    dc.onbufferedamountlow = () => {
      if (this.isCurrentSession(pc, sessionId, generation) && this.dc === dc) this.sendQueue.flush();
    };
    const handleOpen = () => {
      if (!this.isCurrentSession(pc, sessionId, generation) || this.dc !== dc) return;
      if (openObserved) return;
      openObserved = true;
      this.dataChannelState$.next('open');
      this.audit('datachannel_opened');
      this.sendDc('hello', { version: 1 });
      if (this.pairMediaTransforms.isPrepared(sessionId)) {
        this.pairMediaE2ee.markDataChannelOpen(sessionId);
      }
    };
    dc.onopen = handleOpen;
    dc.onclose = () => {
      if (!this.isCurrentSession(pc, sessionId, generation) || this.dc !== dc) return;
      this.sendQueue.unbind();
      this.dataChannelState$.next('closed');
      this.audit('datachannel_closed');
      if (this.pairMediaTransforms.isPrepared(sessionId)) {
        this.pairMediaE2ee.fail(sessionId, 'public_media_consent_channel_closed');
      } else if (this.controlPlane.isPublicSession(sessionId)) {
        this.terminateSession('failed');
      }
    };
    dc.onerror = () => {
      if (!this.isCurrentSession(pc, sessionId, generation) || this.dc !== dc) return;
      this.audit('datachannel_error');
      this.dataChannelState$.next(dc.readyState);
      if (this.pairMediaTransforms.isPrepared(sessionId)) {
        this.pairMediaE2ee.fail(sessionId, 'public_media_consent_channel_failed');
      } else if (this.controlPlane.isPublicSession(sessionId)) {
        this.terminateSession('failed');
      }
    };
    dc.onmessage = (evt) => {
      if (!this.isCurrentSession(pc, sessionId, generation) || this.dc !== dc) return;
      const raw = evt.data as string;
      const incomingBytes = this.dcMessageBytes(raw);
      if (!this.admitDcMessage(raw, incomingBytes, Date.now())) return;
      if (
        queuedMessages >= DC_RECEIVE_QUEUE_MAX
        || queuedBytes + incomingBytes > DC_RECEIVE_QUEUE_BYTES
      ) {
        this.audit('policy_violation', 'datachannel_receive_queue_overflow');
        if (this.controlPlane.isPublicSession(sessionId)) {
          if (this.pairMediaTransforms.isPrepared(sessionId)) {
            this.pairMediaE2ee.fail(sessionId, 'public_datachannel_receive_queue_overflow');
          }
          if (this.isCurrentSession(pc, sessionId, generation)) this.terminateSession('failed');
        }
        return;
      }
      queuedMessages += 1;
      queuedBytes += incomingBytes;
      receiveChain = receiveChain.then(async () => {
        if (!this.isCurrentDataChannel(receiveContext)) return;
        await this.handleDcMessage(raw, receiveContext);
      }).catch(error => {
        // Keep the ordered queue usable after one decoder failure. Security
        // failures close the current generation through the coordinator.
        if (this.isCurrentSession(pc, sessionId, generation) && this.dc === dc) {
          this.audit('decode_error', String(error));
        }
      }).finally(() => {
        queuedMessages -= 1;
        queuedBytes -= incomingBytes;
      });
    };
    // Some implementations may dispatch `open` before a late answerer has
    // finished wiring every callback. Reconcile the level state after all
    // handlers are installed; `openObserved` keeps the edge path idempotent.
    if (dc.readyState === 'open') queueMicrotask(handleOpen);
  }

  private isCurrentSession(
    pc: RTCPeerConnection,
    sessionId: string,
    generation: number,
  ): boolean {
    return generation === this.sessionGeneration && this.pc === pc && this.sessionId === sessionId;
  }

  private isCurrentDataChannel(context: DataChannelReceiveContext): boolean {
    return this.isCurrentSession(
      context.peer, context.sessionId, context.sessionGeneration,
    ) && this.dc === context.channel;
  }

  private assertSemanticSendContext(
    message: SemanticDataChannelMessage,
    context: SemanticSendContext,
  ): void {
    const current = context.sessionGeneration === this.sessionGeneration
      && context.peer === this.pc
      && context.sessionId === this.sessionId
      && message.session_id === context.sessionId
      && (context.channel === undefined || context.channel === this.dc);
    if (!current) throw new Error('semantic_send_context_superseded');
  }

  private isActivePublicMediaContext(
    context: ActivePublicMediaContext | null,
    pc: RTCPeerConnection,
    sessionId: string,
    generation: number,
  ): context is ActivePublicMediaContext {
    return !!context
      && context.peer === pc
      && context.sessionId === sessionId
      && context.sessionGeneration === generation
      && this.isCurrentSession(pc, sessionId, generation);
  }

  private rejectOfferedPublicMedia(pc: RTCPeerConnection): void {
    for (const transceiver of pc.getTransceivers()) {
      try { transceiver.direction = 'inactive'; } catch { /* Rejected/closed m-line is already safe. */ }
      void transceiver.sender.replaceTrack(null).catch(() => undefined);
    }
  }

  private async handleDcMessage(
    raw: string,
    context: DataChannelReceiveContext,
  ): Promise<void> {
    try {
      if (raw.startsWith('ANANTA-DC1 ')) {
        const semantic = await semanticDcDecode(raw);
        if (!this.isCurrentDataChannel(context)) return;
        this.assertSemanticSession(semantic.session_id, context);
        this.acceptEpoch(semantic.epoch, context.sessionId);
        if (semantic.expires_at_ms <= Date.now()) throw new SemanticDataChannelError('expired');
        if (!this.isCurrentDataChannel(context)) return;
        const accepted = await this.pairMediaE2ee.acceptSemantic(semantic);
        if (!this.isCurrentDataChannel(context)) return;
        if (accepted) return;
        this.semanticMessage$.next(semantic);
        return;
      }
      if (raw.startsWith('ANANTA-DCCHUNK1 ')) {
        const chunk = semanticDcDecodeChunk(raw);
        if (!this.isCurrentDataChannel(context)) return;
        this.assertSemanticSession(chunk.session_id, context);
        this.acceptEpoch(chunk.epoch, context.sessionId);
        const result = await this.semanticReassembler.accept(chunk, Date.now());
        if (!this.isCurrentDataChannel(context)) return;
        if (result.status === 'rejected') throw new SemanticDataChannelError(result.reason);
        if (result.status !== 'complete') return;
        const frame = new TextDecoder('utf-8', { fatal: true }).decode(result.value);
        const semantic = await semanticDcDecode(frame);
        if (!this.isCurrentDataChannel(context)) return;
        this.assertSemanticSession(semantic.session_id, context);
        if (semantic.epoch !== chunk.epoch) throw new SemanticDataChannelError('chunk_context_mismatch');
        if (semantic.expires_at_ms <= Date.now()) throw new SemanticDataChannelError('expired');
        const accepted = await this.pairMediaE2ee.acceptSemantic(semantic);
        if (!this.isCurrentDataChannel(context)) return;
        if (accepted) return;
        this.semanticMessage$.next(semantic);
        return;
      }
      const parsed = dcDecode(raw);
      const msg = dcTryReassembleChunk(parsed, this.chunkReassembler);
      if (!msg) return;
      if (!this.isCurrentDataChannel(context)) return;
      if (msg.type === 'cursor' && this.controlPlane.isPublicSession(context.sessionId)) {
        this.audit('policy_violation', 'public_raw_cursor_transport_disabled');
        return;
      }
      if (!ALLOWED_DC_TYPES.has(msg.type)) {
        this.audit('policy_violation', `disallowed_type:${msg.type}`);
        return;
      }
      if (msg.type === 'ping') {
        if (this.isCurrentDataChannel(context)) this.sendDc('pong');
        return;
      }
      if (!this.isCurrentDataChannel(context)) return;
      this.dcMessage$.next(msg);
    } catch (e) {
      if (this.isCurrentDataChannel(context)) this.audit('decode_error', String(e));
    }
  }

  private assertSemanticSession(
    semanticSessionId: string,
    context: DataChannelReceiveContext,
  ): void {
    if (semanticSessionId !== context.sessionId) {
      throw new SemanticDataChannelError('semantic_session_mismatch');
    }
  }

  private admitDcMessage(raw: unknown, incomingBytes: number, now: number): raw is string {
    this.rateTs = this.rateTs.filter(timestamp => now - timestamp < RATE_LIMIT_WINDOW_MS);
    this.rateBytes = this.rateBytes.filter(row => now - row.ts < RATE_LIMIT_WINDOW_MS);
    const windowBytes = this.rateBytes.reduce((sum, row) => sum + row.bytes, 0);
    if (this.rateTs.length >= RATE_LIMIT_MAX || windowBytes + incomingBytes > RATE_LIMIT_BYTES) {
      this.audit('policy_violation', 'rate_limit_exceeded');
      return false;
    }
    this.rateTs.push(now);
    this.rateBytes.push({ ts: now, bytes: incomingBytes });
    return typeof raw === 'string';
  }

  private dcMessageBytes(raw: unknown): number {
    return typeof raw === 'string'
      ? new TextEncoder().encode(raw).byteLength
      : RATE_LIMIT_BYTES + 1;
  }

  private acceptEpoch(epoch: number, sessionId = this.sessionId): void {
    if (!Number.isSafeInteger(epoch) || epoch < this.activeEpoch) throw new Error('stale_semantic_epoch');
    if (epoch === this.activeEpoch) return;
    const prior = this.activeEpoch;
    this.activeEpoch = epoch;
    this.semanticReassembler.clearContext(sessionId, prior);
    this.sendQueue.cancelContext(sessionId, prior);
  }

  private releasePendingPublicMediaTracks(): void {
    if (!this.sessionId || this.pairMediaE2ee.statusFor(this.sessionId).state !== 'ready') return;
    for (const definition of ['microphone-opus', 'camera-vp8', 'screen-vp8'] as const) {
      const event = this.pendingPublicMediaTracks.get(definition);
      if (!event) continue;
      this.pendingPublicMediaTracks.delete(definition);
      this.remoteTrack$.next(event);
    }
  }

  private stopPendingPublicMediaTracks(): void {
    for (const event of this.pendingPublicMediaTracks.values()) {
      try { event.track.stop(); } catch { /* Deterministic local cleanup. */ }
    }
    this.pendingPublicMediaTracks.clear();
  }

  private armDisconnectTimeout(
    pc: RTCPeerConnection,
    sessionId: string,
    generation: number,
  ): void {
    if (this.disconnectTimeout !== null) return;
    const timeout = setTimeout(() => {
      if (this.disconnectTimeout !== timeout) return;
      this.disconnectTimeout = null;
      if (!this.isCurrentSession(pc, sessionId, generation) || pc.connectionState !== 'disconnected') return;
      this.audit('connection_failed', pc.connectionState);
      if (this.pairMediaTransforms.isPrepared(sessionId)) {
        this.pairMediaE2ee.fail(sessionId, 'public_media_peer_connection_lost');
        return;
      }
      this.state$.next('failed');
    }, PEER_CONNECTION_DISCONNECT_GRACE_MS);
    this.disconnectTimeout = timeout;
  }

  private clearDisconnectTimeout(): void {
    if (this.disconnectTimeout !== null) clearTimeout(this.disconnectTimeout);
    this.disconnectTimeout = null;
  }

  private audit(type: string, detail?: string): void {
    const event: AuditEvent = { ts: Date.now() / 1000, type, session_id: this.sessionId, detail };
    this.auditLog.push(event);
    if (this.auditLog.length > 200) this.auditLog.shift();
    // Audit events stay in-memory only (this.auditLog). The Hub has no
    // /api/audit/webrtc endpoint — sending the POST would surface a 404
    // in the console and trip the auth interceptor's refresh logic.
    // If a future Hub version exposes such an endpoint, wire it up here.
  }
}
