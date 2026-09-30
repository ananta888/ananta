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
import { PairMediaPublicationPolicy } from './pair-media-publication.policy';
import { PairViewSecurityBootstrapService } from './pair-view-security-bootstrap.service';
import { PairMediaE2eeCoordinatorService } from './pair-media-e2ee-coordinator.service';
import { PairMediaE2eeTransformAdapter } from './pair-media-e2ee-transform.adapter';
import type { PublicPairMediaSlot } from './public-pair-media-security-contract';
import { PUBLIC_WEBRTC_STUN_URL } from './public-ananta-endpoints';
import { SIGNAL_SESSION_RECREATION_REQUIRED } from './webrtc-signal-session.guard';
import {
  isTerminalPairSessionReason,
  terminalPairSessionReason,
} from './pair-session-terminal-error';
import {
  AuditEvent,
  DataChannelReceiveContext,
  OrdinaryMediaStatsSnapshot,
  PeerState,
  PublicSdpPhase,
  SemanticSendContext,
  WebrtcSessionContextPort,
  errorReasonCode,
  validSecurityEpoch,
} from './webrtc-session.types';
import {
  ALLOWED_DC_TYPES,
  DC_RECEIVE_QUEUE_BYTES,
  DC_RECEIVE_QUEUE_MAX,
  DcReceiveRateLimiter,
  dcMessageBytes,
  legacyDcTrafficClass,
} from './webrtc-dc-receive-policy';
import { WebrtcMediaSenderController } from './webrtc-media-sender-controller';
import { WebrtcPublicMediaExtension, WebrtcPublicMediaSessionPort } from './webrtc-public-media-extension';

export type { OrdinaryMediaStatsSnapshot, PeerState } from './webrtc-session.types';

const PEER_CONNECTION_DISCONNECT_GRACE_MS = 5_000;
const REMOTE_ICE_BUFFER_MAX = 256;

@Injectable({ providedIn: 'root' })
export class WebrtcSessionService {
  private profiles = inject(NetworkProfileService);
  private signaling = inject(WebrtcSignalingService);
  private oidc = inject(OidcAuthService);
  private controlPlane = inject(PairSessionControlPlaneService);
  private mediaPolicy = inject(PairOrdinaryMediaPolicy);
  private publicationPolicy = inject(PairMediaPublicationPolicy);
  private securityBootstrap = inject(PairViewSecurityBootstrapService);
  private pairMediaE2ee = inject(PairMediaE2eeCoordinatorService);
  private pairMediaTransforms = inject(PairMediaE2eeTransformAdapter);
  private readonly sessionContext: WebrtcSessionContextPort = this.createSessionContextPort();
  private readonly publicMedia = new WebrtcPublicMediaExtension(this.createPublicMediaSessionPort(), {
    controlPlane: this.controlPlane,
    securityBootstrap: this.securityBootstrap,
    pairMediaE2ee: this.pairMediaE2ee,
    pairMediaTransforms: this.pairMediaTransforms,
  });
  private readonly mediaSenders = new WebrtcMediaSenderController(this.sessionContext, {
    controlPlane: this.controlPlane,
    mediaPolicy: this.mediaPolicy,
    publicationPolicy: this.publicationPolicy,
    securityBootstrap: this.securityBootstrap,
    pairMediaE2ee: this.pairMediaE2ee,
    pairMediaTransforms: this.pairMediaTransforms,
  });

  readonly state$ = new BehaviorSubject<PeerState>('idle');
  readonly failureReason$ = new BehaviorSubject<string | null>(null);
  readonly dataChannelState$ = new BehaviorSubject<RTCDataChannelState | 'absent'>('absent');
  readonly dcMessage$ = new Subject<DcMessage>();
  readonly semanticMessage$ = new Subject<SemanticDataChannelMessage>();
  readonly remoteTrack$ = new Subject<RTCTrackEvent>();
  readonly sessionStarted$ = new Subject<string>();
  readonly auditLog: AuditEvent[] = [];

  private pc: RTCPeerConnection | null = null;
  private dc: RTCDataChannel | null = null;
  private sessionId = '';
  private readonly receiveRateLimiter = new DcReceiveRateLimiter();
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
  private remoteDescriptionApplied = false;
  private pendingRemoteIce: RTCIceCandidateInit[] = [];
  async startSession(
    sessionId: string,
    isInitiator: boolean,
    remotePeerId?: string,
    securityEpoch = 1,
  ): Promise<void> {
    const exactSecurityEpoch = validSecurityEpoch(securityEpoch);
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
    this.activeEpoch = exactSecurityEpoch;
    this.localDescriptionPublicationPending = false;
    this.pendingLocalIce = [];
    this.remoteDescriptionApplied = false;
    this.pendingRemoteIce = [];
    this.failureReason$.next(null);
    this.state$.next('connecting');
    this.dataChannelState$.next('absent');
    this.sessionStarted$.next(sessionId);
    this.audit('session_start', `initiator=${isInitiator}`);

    const profile = this.profiles.current;
    const publicSession = this.controlPlane.isPublicSession(sessionId);
    if (publicSession) {
      try {
        // This check must precede TURN issuance: a signaling generation whose
        // retained sequence cannot safely be reused must not consume another
        // short-lived credential on every UI security refresh.
        this.controlPlane.assertSessionAvailable(sessionId);
        this.signaling.assertSessionReusable(sessionId, exactSecurityEpoch);
      } catch (error) {
        const reasonCode = errorReasonCode(error, 'public_session_unavailable');
        this.terminateSession('failed', reasonCode);
        throw error;
      }
    }
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
            urls: [...credentials.uris],
            username: credentials.username,
            credential: credentials.password,
          });
        }
      } catch (error) {
        if (generation !== this.sessionGeneration || this.sessionId !== sessionId) return;
        const terminalReason = terminalPairSessionReason(error);
        if (terminalReason) {
          this.terminateSession('failed', terminalReason);
          throw error;
        }
        // A missing/changed public identity is an authority loss, not a TURN
        // outage. It must stop the session instead of continuing on STUN.
        this.controlPlane.assertSessionAvailable(sessionId);
        const status = Number((error as { status?: unknown } | null)?.status);
        if (status === 401 || status === 403) {
          // A server-rejected authority must not become a two-second
          // TURN/reopen loop. Preserve the control-plane binding for an
          // explicit End/Leave retry, but quarantine this signaling session.
          this.signaling.markSessionRecreationRequired(sessionId, exactSecurityEpoch);
          this.terminateSession('failed', SIGNAL_SESSION_RECREATION_REQUIRED);
          throw error;
        }
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
    const preparation = await this.publicMedia.prepare(pc, sessionId, generation, isInitiator, publicSession);
    if (preparation === 'superseded') return;
    if (preparation !== 'continue') {
      pc = new RTCPeerConnection(config);
      this.pc = pc;
      this.pairMediaE2ee.fail(sessionId, preparation.recreateReason);
    }
    this.releaseSignalingHandler = this.signaling.bindMessageHandler(async msg => {
      if (!this.isCurrentSession(pc, sessionId, generation) || msg.session_id !== sessionId) return;
      try {
        await this.handleSignal(msg, pc, sessionId, generation);
      } catch (error) {
        if (this.isCurrentSession(pc, sessionId, generation)) {
          this.audit('signal_error', error instanceof Error ? error.message : String(error));
          const publicFailure = this.controlPlane.isPublicSession(sessionId);
          if (publicFailure) {
            this.signaling.markSessionRecreationRequired(sessionId, exactSecurityEpoch);
          }
          // Public apply failures are latched by WebrtcSignalingService while
          // its generation is still current. Keep that connection alive until
          // the rejected handler reaches the signaling owner; its failed state
          // then drives the single teardown below.
          if (!publicFailure) {
            this.terminateSession(
              'failed', errorReasonCode(error, 'webrtc_signal_apply_failed'),
            );
          }
        }
        throw error;
      }
    });
    this.signalingStatusSubscription = this.signaling.status$.subscribe(status => {
      if (!this.isCurrentSession(pc, sessionId, generation)) return;
      const signalingReason = this.signaling.failureReason$.value;
      if (status === 'disconnected' && signalingReason === 'pair_runtime_not_ready') {
        this.audit('signaling_waiting_for_pair_runtime');
        this.failureReason$.next(signalingReason);
        this.terminateSession('closed');
        return;
      }
      if (status !== 'failed') return;
      this.audit('signaling_failed');
      const reasonCode = isTerminalPairSessionReason(signalingReason)
        ? signalingReason
        : this.signaling.isSessionRecreationRequired(sessionId, exactSecurityEpoch)
          ? SIGNAL_SESSION_RECREATION_REQUIRED
          : signalingReason || 'webrtc_signaling_failed';
      this.terminateSession('failed', reasonCode);
    });
    this.signaling.connect(
      profile.signaling_url,
      sessionId,
      remotePeerId,
      exactSecurityEpoch,
    );
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
          this.failureReason$.next('webrtc_peer_connection_timeout');
          this.state$.next('failed');
        }
      }
    }, 15_000);
  }

  closeSession(): void {
    this.terminateSession('closed');
  }

  /** Clears same-session replay state only after confirmed server retirement. */
  retireSession(sessionId: string): void {
    if (this.sessionId === sessionId) this.terminateSession('closed');
    this.signaling.retireSession(sessionId);
    if (this.failureReason$.value === SIGNAL_SESSION_RECREATION_REQUIRED) {
      this.failureReason$.next(null);
    }
  }

  isSessionRecreationRequired(sessionId: string, securityEpoch = this.activeEpoch): boolean {
    return this.signaling.isSessionRecreationRequired(sessionId, securityEpoch);
  }

  private terminateSession(
    finalState: Extract<PeerState, 'failed' | 'closed'>,
    reasonCode?: string,
  ): void {
    const closingSessionId = this.sessionId;
    const mediaContext = this.publicMedia.takeActiveContext();
    this.sessionGeneration += 1;
    this.releaseSignalingHandler?.();
    this.releaseSignalingHandler = null;
    this.signalingStatusSubscription?.unsubscribe();
    this.signalingStatusSubscription = null;
    this.localDescriptionPublicationPending = false;
    this.pendingLocalIce = [];
    this.remoteDescriptionApplied = false;
    this.pendingRemoteIce = [];
    this.publicMedia.stopPendingTracks();
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
    if (closingSessionId) this.publicMedia.releaseSession(closingSessionId, mediaContext);
    if (closingSessionId) this.pairMediaE2ee.unbindTransport(closingSessionId, `public_media_session_${finalState}`);
    if (finalState === 'failed') {
      this.failureReason$.next(reasonCode || this.failureReason$.value || 'webrtc_session_failed');
    }
    this.state$.next(finalState);
    this.audit(
      finalState === 'failed' ? 'session_failed' : 'session_closed',
      finalState === 'failed' ? this.failureReason$.value || undefined : undefined,
    );
    this.sessionId = '';
    this.isInitiator = false;
    this.publicSdpPhase = 'none';
  }

  sendDc(type: string, payload: Record<string, unknown> = {}): boolean {
    if (!this.dc || this.dc.readyState !== 'open') return false;
    if (type === 'cursor' && this.controlPlane.isPublicSession(this.sessionId)) {
      throw new Error('public_raw_cursor_transport_disabled');
    }
    const nonce = this.oidc.sessionNonce;
    try {
      const msg = dcMake(type as any, nonce, payload);
      const chunks = dcEncodeChunked(msg);
      const trafficClass = legacyDcTrafficClass(type);
      let accepted = true;
      for (const part of chunks) {
        accepted = this.sendQueue.enqueue(
          trafficClass,
          dcEncode(part),
          Date.now() + 60_000,
        ) && accepted;
      }
      return accepted;
    } catch {
      this.audit('send_error', `type=${type}`);
      return false;
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
    return this.mediaSenders.addMediaTrack(track, stream);
  }

  attachMediaTrack(
    slot: PublicPairMediaSlot,
    track: MediaStreamTrack,
    stream: MediaStream,
  ): Promise<RTCRtpSender> {
    return this.mediaSenders.attachMediaTrack(slot, track, stream);
  }

  publicMediaSlotForReceiver(receiver: RTCRtpReceiver): PublicPairMediaSlot | null {
    return this.mediaSenders.publicMediaSlotForReceiver(receiver);
  }

  replaceMediaTrack(sender: RTCRtpSender, track: MediaStreamTrack | null): Promise<void> {
    return this.mediaSenders.replaceMediaTrack(sender, track);
  }

  removeMediaSender(sender: RTCRtpSender): void {
    this.mediaSenders.removeMediaSender(sender);
  }

  restartMediaIce(): void {
    this.mediaSenders.restartMediaIce();
  }

  ordinaryMediaStats(): Promise<OrdinaryMediaStatsSnapshot> {
    return this.mediaSenders.ordinaryMediaStats();
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
        this.failureReason$.next('webrtc_peer_connection_failed');
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
      if (this.publicMedia.routeRemoteTrack(event, sessionId)) return;
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
          const publicFailure = this.controlPlane.isPublicSession(sessionId);
          this.terminateSession(
            'failed',
            publicFailure && this.signaling.isSessionRecreationRequired(
              sessionId, this.activeEpoch,
            )
              ? SIGNAL_SESSION_RECREATION_REQUIRED
              : errorReasonCode(error, 'webrtc_signal_send_failed'),
          );
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
        // Latch before media fail callbacks: the E2EE coordinator is allowed
        // to synchronously close signaling as part of fail-closed teardown.
        this.signaling.markSessionRecreationRequired(sessionId, this.activeEpoch);
        if (this.pairMediaTransforms.isPrepared(sessionId)) {
          this.pairMediaE2ee.fail(sessionId, 'public_media_unexpected_sdp');
        }
        throw new Error('public_media_unexpected_sdp');
      }
      if (msg.type === 'offer') this.publicSdpPhase = 'processing-offer';
      if (msg.type === 'answer') this.publicSdpPhase = 'processing-answer';
    }
    if (msg.type === 'offer') {
      await pc.setRemoteDescription(new RTCSessionDescription(msg.payload as RTCSessionDescriptionInit));
      if (!this.isCurrentSession(pc, sessionId, generation)) return;
      this.remoteDescriptionApplied = true;
      await this.flushRemoteIce(pc, sessionId, generation);
      if (!this.isCurrentSession(pc, sessionId, generation)) return;
      if (!(await this.publicMedia.prepareAnswerTopology(pc, sessionId, generation))) return;
      const answer = await pc.createAnswer();
      if (!this.isCurrentSession(pc, sessionId, generation)) return;
      await this.publishLocalDescription('answer', answer, pc, sessionId, generation);
      if (!this.isCurrentSession(pc, sessionId, generation)) return;
      this.publicMedia.markTopologyNegotiated(pc, sessionId, generation);
      if (this.controlPlane.isPublicSession(sessionId)) this.publicSdpPhase = 'established';
    } else if (msg.type === 'answer') {
      await pc.setRemoteDescription(new RTCSessionDescription(msg.payload as RTCSessionDescriptionInit));
      if (!this.isCurrentSession(pc, sessionId, generation)) return;
      this.remoteDescriptionApplied = true;
      await this.flushRemoteIce(pc, sessionId, generation);
      if (!this.isCurrentSession(pc, sessionId, generation)) return;
      this.publicMedia.markTopologyNegotiated(pc, sessionId, generation);
      if (this.controlPlane.isPublicSession(sessionId)) this.publicSdpPhase = 'established';
    } else if (msg.type === 'ice_candidate') {
      const candidate = msg.payload as RTCIceCandidateInit;
      if (!this.remoteDescriptionApplied) {
        if (this.pendingRemoteIce.length >= REMOTE_ICE_BUFFER_MAX) {
          throw new Error('webrtc_remote_ice_buffer_overflow');
        }
        this.pendingRemoteIce.push(candidate);
        this.audit('remote_ice_buffered', `count=${this.pendingRemoteIce.length}`);
        return;
      }
      await pc.addIceCandidate(new RTCIceCandidate(candidate));
    }
  }

  private async flushRemoteIce(
    pc: RTCPeerConnection,
    sessionId: string,
    generation: number,
  ): Promise<void> {
    const buffered = this.pendingRemoteIce;
    this.pendingRemoteIce = [];
    for (const candidate of buffered) {
      if (!this.isCurrentSession(pc, sessionId, generation)) return;
      await pc.addIceCandidate(new RTCIceCandidate(candidate));
    }
    if (buffered.length > 0) this.audit('remote_ice_flushed', `count=${buffered.length}`);
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
      // `RTCDataChannel.open` is a level signal that its underlying SCTP/
      // DTLS/ICE data transport is established and usable. Some browsers can
      // leave the aggregate RTCPeerConnection state in `connecting` while
      // that application transport is already live. Do not let the initial
      // connection deadline tear down that exact, generation-bound session.
      if (this.state$.value === 'connecting') {
        if (this.connectionTimeout) {
          clearTimeout(this.connectionTimeout);
          this.connectionTimeout = null;
        }
        this.audit('connection_ready', 'datachannel');
        this.state$.next('connected');
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
      const incomingBytes = dcMessageBytes(raw);
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
    if (!this.receiveRateLimiter.admit(incomingBytes, now)) {
      this.audit('policy_violation', 'rate_limit_exceeded');
      return false;
    }
    return typeof raw === 'string';
  }

  private acceptEpoch(epoch: number, sessionId = this.sessionId): void {
    if (!Number.isSafeInteger(epoch) || epoch < this.activeEpoch) throw new Error('stale_semantic_epoch');
    if (epoch === this.activeEpoch) return;
    const prior = this.activeEpoch;
    this.activeEpoch = epoch;
    this.semanticReassembler.clearContext(sessionId, prior);
    this.sendQueue.cancelContext(sessionId, prior);
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
      this.failureReason$.next('webrtc_peer_connection_lost');
      this.state$.next('failed');
    }, PEER_CONNECTION_DISCONNECT_GRACE_MS);
    this.disconnectTimeout = timeout;
  }

  private clearDisconnectTimeout(): void {
    if (this.disconnectTimeout !== null) clearTimeout(this.disconnectTimeout);
    this.disconnectTimeout = null;
  }

  /** Narrow, live view of this service for its media collaborators. */
  private createSessionContextPort(): WebrtcSessionContextPort {
    const current = () => ({ pc: this.pc, sessionId: this.sessionId, generation: this.sessionGeneration });
    return {
      get peer() { return current().pc; },
      get sessionId() { return current().sessionId; },
      get sessionGeneration() { return current().generation; },
      isCurrentSession: (peer, sessionId, generation) => this.isCurrentSession(peer, sessionId, generation),
      negotiateMedia: () => this.negotiateMedia(),
      audit: (type, detail) => this.audit(type, detail),
    };
  }

  private createPublicMediaSessionPort(): WebrtcPublicMediaSessionPort {
    const base = this.createSessionContextPort();
    const channel = () => this.dc;
    return Object.defineProperties({
      isCurrentSession: base.isCurrentSession,
      negotiateMedia: base.negotiateMedia,
      audit: base.audit,
      sendSemantic: (message: SemanticDataChannelMessage, context: SemanticSendContext) =>
        this.sendSemanticWithContext(message, {}, context),
      failSession: () => this.terminateSession('failed'),
      emitRemoteTrack: (event: RTCTrackEvent) => this.remoteTrack$.next(event),
    }, {
      peer: { get: () => base.peer },
      sessionId: { get: () => base.sessionId },
      sessionGeneration: { get: () => base.sessionGeneration },
      dataChannel: { get: channel },
    }) as WebrtcPublicMediaSessionPort;
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
