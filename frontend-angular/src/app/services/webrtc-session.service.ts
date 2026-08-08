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
import { PUBLIC_WEBRTC_STUN_URL } from './public-ananta-endpoints';

export type PeerState = 'idle' | 'connecting' | 'connected' | 'failed' | 'closed';

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

interface AuditEvent {
  ts: number;
  type: string;
  session_id: string;
  detail?: string;
}

@Injectable({ providedIn: 'root' })
export class WebrtcSessionService {
  private profiles = inject(NetworkProfileService);
  private signaling = inject(WebrtcSignalingService);
  private oidc = inject(OidcAuthService);
  private controlPlane = inject(PairSessionControlPlaneService);
  private mediaPolicy = inject(PairOrdinaryMediaPolicy);

  readonly state$ = new BehaviorSubject<PeerState>('idle');
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
  private readonly chunkReassembler = new DcLegacyChunkReassembler();
  private readonly semanticReassembler = inject(WebrtcChunkReassemblyStore);
  private readonly sendQueue = new WebrtcPrioritySendQueue();
  private readonly pendingSemanticSends = new Map<string, WebrtcSendOperation>();
  private activeEpoch = 1;
  private isInitiator = false;
  private releaseSignalingHandler: (() => void) | null = null;
  private signalingStatusSubscription: Subscription | null = null;
  private sessionGeneration = 0;
  private localDescriptionPublicationPending = false;
  private pendingLocalIce: RTCIceCandidateInit[] = [];

  async startSession(sessionId: string, isInitiator: boolean, remotePeerId?: string): Promise<void> {
    if (this.pc || this.releaseSignalingHandler || this.signalingStatusSubscription || this.connectionTimeout) {
      this.closeSession();
    }
    const generation = ++this.sessionGeneration;
    this.sessionId = sessionId;
    this.isInitiator = isInitiator;
    this.activeEpoch = 1;
    this.localDescriptionPublicationPending = false;
    this.pendingLocalIce = [];
    this.state$.next('connecting');
    this.sessionStarted$.next(sessionId);
    this.audit('session_start', `initiator=${isInitiator}`);

    const profile = this.profiles.current;
    const publicSession = this.controlPlane.isPublicSession(sessionId);
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

    const pc = new RTCPeerConnection(config);
    this.pc = pc;
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
        this.state$.next('failed');
      }
    }, 15_000);
  }

  closeSession(): void {
    this.terminateSession('closed');
  }

  private terminateSession(finalState: Extract<PeerState, 'failed' | 'closed'>): void {
    const closingSessionId = this.sessionId;
    this.sessionGeneration += 1;
    this.releaseSignalingHandler?.();
    this.releaseSignalingHandler = null;
    this.signalingStatusSubscription?.unsubscribe();
    this.signalingStatusSubscription = null;
    this.localDescriptionPublicationPending = false;
    this.pendingLocalIce = [];
    if (this.connectionTimeout) { clearTimeout(this.connectionTimeout); this.connectionTimeout = null; }
    this.dc?.close();
    this.pc?.close();
    this.dc = null;
    this.pc = null;
    this.signaling.disconnect();
    this.chunkReassembler.clear();
    this.semanticReassembler.clearContext(closingSessionId);
    this.sendQueue.cancelContext(closingSessionId);
    this.sendQueue.unbind();
    this.pendingSemanticSends.clear();
    this.state$.next(finalState);
    this.audit(finalState === 'failed' ? 'session_failed' : 'session_closed');
    this.sessionId = '';
    this.isInitiator = false;
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
    if (message.session_id !== this.sessionId) throw new Error('semantic_session_mismatch');
    this.acceptEpoch(message.epoch);
    const encoded = await semanticDcEncodePackets(message);
    const deadline = Math.min(options.deadlineMs ?? Date.now() + 30_000, message.expires_at_ms);
    const operation = new WebrtcSendOperation(
      message.session_id,
      message.epoch,
      encoded.digest,
      deadline,
      options.signal,
    );
    this.pendingSemanticSends.set(encoded.digest, operation);
    void operation.result.then(() => this.pendingSemanticSends.delete(encoded.digest));
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
    const sender = this.pc.addTrack(track, stream);
    void this.negotiateMedia();
    return sender;
  }

  async replaceMediaTrack(sender: RTCRtpSender, track: MediaStreamTrack | null): Promise<void> {
    this.mediaPolicy.assertAllowed(this.sessionId);
    if (!this.pc || !this.pc.getSenders().includes(sender)) throw new Error('webrtc_media_sender_stale');
    await sender.replaceTrack(track);
  }

  removeMediaSender(sender: RTCRtpSender): void {
    if (!this.pc || !this.pc.getSenders().includes(sender)) return;
    this.pc.removeTrack(sender);
    void this.negotiateMedia();
  }

  restartMediaIce(): void {
    if (!this.pc || this.pc.connectionState === 'closed') throw new Error('webrtc_session_not_open');
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
        if (this.connectionTimeout) { clearTimeout(this.connectionTimeout); this.connectionTimeout = null; }
        this.state$.next('connected');
      }
      if (s === 'failed' || s === 'disconnected') {
        this.state$.next('failed');
        this.audit('connection_failed', s);
      }
    };
    pc.ontrack = event => {
      if (!this.isCurrentSession(pc, sessionId, generation)) return;
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
    if (!pc || !this.isInitiator || pc.signalingState !== 'stable') return;
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
    if (msg.type === 'offer') {
      await pc.setRemoteDescription(new RTCSessionDescription(msg.payload as RTCSessionDescriptionInit));
      if (!this.isCurrentSession(pc, sessionId, generation)) return;
      const answer = await pc.createAnswer();
      if (!this.isCurrentSession(pc, sessionId, generation)) return;
      await this.publishLocalDescription('answer', answer, pc, sessionId, generation);
    } else if (msg.type === 'answer') {
      await pc.setRemoteDescription(new RTCSessionDescription(msg.payload as RTCSessionDescriptionInit));
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
      const bufferedIce = this.pendingLocalIce;
      this.pendingLocalIce = [];
      this.localDescriptionPublicationPending = false;
      if (published && this.isCurrentSession(pc, sessionId, generation)) {
        for (const candidate of bufferedIce) {
          await this.signaling.send({
            type: 'ice_candidate', session_id: sessionId, payload: candidate,
          });
          if (!this.isCurrentSession(pc, sessionId, generation)) return;
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
    this.sendQueue.bind(dc);
    dc.onbufferedamountlow = () => {
      if (this.isCurrentSession(pc, sessionId, generation) && this.dc === dc) this.sendQueue.flush();
    };
    dc.onopen = () => {
      if (!this.isCurrentSession(pc, sessionId, generation) || this.dc !== dc) return;
      this.audit('datachannel_opened');
      this.sendDc('hello', { version: 1 });
    };
    dc.onclose = () => {
      if (!this.isCurrentSession(pc, sessionId, generation) || this.dc !== dc) return;
      this.sendQueue.unbind();
      this.audit('datachannel_closed');
    };
    dc.onmessage = (evt) => {
      if (this.isCurrentSession(pc, sessionId, generation) && this.dc === dc) {
        void this.handleDcMessage(evt.data as string);
      }
    };
  }

  private isCurrentSession(
    pc: RTCPeerConnection,
    sessionId: string,
    generation: number,
  ): boolean {
    return generation === this.sessionGeneration && this.pc === pc && this.sessionId === sessionId;
  }

  private async handleDcMessage(raw: string): Promise<void> {
    const now = Date.now();
    this.rateTs = this.rateTs.filter((t) => now - t < RATE_LIMIT_WINDOW_MS);
    this.rateBytes = this.rateBytes.filter(row => now - row.ts < RATE_LIMIT_WINDOW_MS);
    const incomingBytes = typeof raw === 'string' ? new TextEncoder().encode(raw).byteLength : RATE_LIMIT_BYTES + 1;
    const windowBytes = this.rateBytes.reduce((sum, row) => sum + row.bytes, 0);
    if (this.rateTs.length >= RATE_LIMIT_MAX || windowBytes + incomingBytes > RATE_LIMIT_BYTES) {
      this.audit('policy_violation', 'rate_limit_exceeded');
      return;
    }
    this.rateTs.push(now);
    this.rateBytes.push({ ts: now, bytes: incomingBytes });

    try {
      if (raw.startsWith('ANANTA-DC1 ')) {
        const semantic = await semanticDcDecode(raw);
        this.acceptEpoch(semantic.epoch);
        if (semantic.expires_at_ms <= now) throw new SemanticDataChannelError('expired');
        this.semanticMessage$.next(semantic);
        return;
      }
      if (raw.startsWith('ANANTA-DCCHUNK1 ')) {
        const chunk = semanticDcDecodeChunk(raw);
        this.acceptEpoch(chunk.epoch);
        const result = await this.semanticReassembler.accept(chunk, now);
        if (result.status === 'rejected') throw new SemanticDataChannelError(result.reason);
        if (result.status !== 'complete') return;
        const frame = new TextDecoder('utf-8', { fatal: true }).decode(result.value);
        const semantic = await semanticDcDecode(frame);
        if (semantic.expires_at_ms <= now) throw new SemanticDataChannelError('expired');
        this.semanticMessage$.next(semantic);
        return;
      }
      const parsed = dcDecode(raw);
      const msg = dcTryReassembleChunk(parsed, this.chunkReassembler);
      if (!msg) return;
      if (msg.type === 'cursor' && this.controlPlane.isPublicSession(this.sessionId)) {
        this.audit('policy_violation', 'public_raw_cursor_transport_disabled');
        return;
      }
      if (!ALLOWED_DC_TYPES.has(msg.type)) {
        this.audit('policy_violation', `disallowed_type:${msg.type}`);
        return;
      }
      if (msg.type === 'ping') { this.sendDc('pong'); return; }
      this.dcMessage$.next(msg);
    } catch (e) {
      this.audit('decode_error', String(e));
    }
  }

  private acceptEpoch(epoch: number): void {
    if (!Number.isSafeInteger(epoch) || epoch < this.activeEpoch) throw new Error('stale_semantic_epoch');
    if (epoch === this.activeEpoch) return;
    const prior = this.activeEpoch;
    this.activeEpoch = epoch;
    this.semanticReassembler.clearContext(this.sessionId, prior);
    this.sendQueue.cancelContext(this.sessionId, prior);
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
