/**
 * T19: RTCPeerConnection Lifecycle Management
 * T22: Policy Gates (allowed message types, rate limiting)
 * T23: Audit Logging
 */
import { Injectable, inject } from '@angular/core';
import { Subject, BehaviorSubject } from 'rxjs';
import { NetworkProfileService } from './network-profile.service';
import { WebrtcSignalingService, SignalMessage } from './webrtc-signaling.service';
import { OidcAuthService } from './oidc-auth.service';
import { dcMake, dcDecode, dcEncode, dcEncodeChunked, dcTryReassembleChunk, DcMessage } from './webrtc-datachannel.service';
import { HubApiCoreService } from './hub-api-core.service';
import { AgentDirectoryService } from './agent-directory.service';

export type PeerState = 'idle' | 'connecting' | 'connected' | 'failed' | 'closed';

const ALLOWED_DC_TYPES = new Set([
  'hello', 'hello_ack', 'ping', 'pong', 'chat', 'view_payload', 'cursor', 'artifact', 'control', 'chunk', 'error',
]);
const RATE_LIMIT_WINDOW_MS = 1000;
const RATE_LIMIT_MAX = 30;

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
  private core = inject(HubApiCoreService);
  private dir = inject(AgentDirectoryService);

  readonly state$ = new BehaviorSubject<PeerState>('idle');
  readonly dcMessage$ = new Subject<DcMessage>();
  readonly auditLog: AuditEvent[] = [];

  private pc: RTCPeerConnection | null = null;
  private dc: RTCDataChannel | null = null;
  private sessionId = '';
  private rateTs: number[] = [];
  private connectionTimeout: ReturnType<typeof setTimeout> | null = null;

  private get hubUrl(): string {
    return this.dir.list().find((a) => a.role === 'hub')?.url ?? '';
  }

  async startSession(sessionId: string, isInitiator: boolean): Promise<void> {
    this.sessionId = sessionId;
    this.state$.next('connecting');
    this.audit('session_start', `initiator=${isInitiator}`);

    const profile = this.profiles.current;
    const config: RTCConfiguration = {
      iceServers: profile.ice_servers,
      iceTransportPolicy: profile.require_e2e_payload_encryption ? 'all' : 'all',
    };

    this.pc = new RTCPeerConnection(config);
    this.wirePeerConnection(isInitiator);

    this.signaling.connect(profile.signaling_url, sessionId);
    this.signaling.message$.subscribe((msg) => { void this.handleSignal(msg); });

    this.connectionTimeout = setTimeout(() => {
      if (this.state$.value === 'connecting') {
        this.audit('ice_failed', 'timeout after 15s');
        this.signaling.fallbackToHubRelay();
        this.state$.next('connected');
      }
    }, 15_000);
  }

  closeSession(): void {
    if (this.connectionTimeout) { clearTimeout(this.connectionTimeout); this.connectionTimeout = null; }
    this.dc?.close();
    this.pc?.close();
    this.dc = null;
    this.pc = null;
    this.signaling.disconnect();
    this.state$.next('closed');
    this.audit('session_closed');
  }

  sendDc(type: string, payload: Record<string, unknown> = {}): void {
    if (!this.dc || this.dc.readyState !== 'open') return;
    const nonce = this.oidc.sessionNonce;
    try {
      const msg = dcMake(type as any, nonce, payload);
      const chunks = dcEncodeChunked(msg);
      for (const part of chunks) this.dc.send(dcEncode(part));
    } catch {
      this.audit('send_error', `type=${type}`);
    }
  }

  private wirePeerConnection(isInitiator: boolean): void {
    const pc = this.pc!;

    pc.onicecandidate = (evt) => {
      if (!evt.candidate) return;
      this.signaling.send({
        type: 'ice_candidate',
        session_id: this.sessionId,
        payload: evt.candidate.toJSON(),
      });
    };

    pc.onconnectionstatechange = () => {
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

    if (isInitiator) {
      this.dc = pc.createDataChannel('ananta', { ordered: true });
      this.wireDc(this.dc);
      void this.createOffer();
    } else {
      pc.ondatachannel = (evt) => {
        this.dc = evt.channel;
        this.wireDc(this.dc);
      };
    }
  }

  private async createOffer(): Promise<void> {
    if (!this.pc) return;
    const offer = await this.pc.createOffer();
    await this.pc.setLocalDescription(offer);
    this.signaling.send({ type: 'offer', session_id: this.sessionId, payload: offer });
  }

  private async handleSignal(msg: SignalMessage): Promise<void> {
    if (!this.pc) return;
    if (msg.type === 'offer') {
      await this.pc.setRemoteDescription(new RTCSessionDescription(msg.payload as RTCSessionDescriptionInit));
      const answer = await this.pc.createAnswer();
      await this.pc.setLocalDescription(answer);
      this.signaling.send({ type: 'answer', session_id: this.sessionId, payload: answer });
    } else if (msg.type === 'answer') {
      await this.pc.setRemoteDescription(new RTCSessionDescription(msg.payload as RTCSessionDescriptionInit));
    } else if (msg.type === 'ice_candidate') {
      await this.pc.addIceCandidate(new RTCIceCandidate(msg.payload as RTCIceCandidateInit));
    }
  }

  private wireDc(dc: RTCDataChannel): void {
    dc.onopen = () => {
      this.audit('datachannel_opened');
      this.sendDc('hello', { version: 1 });
    };
    dc.onclose = () => this.audit('datachannel_closed');
    dc.onmessage = (evt) => this.handleDcMessage(evt.data as string);
  }

  private handleDcMessage(raw: string): void {
    const now = Date.now();
    this.rateTs = this.rateTs.filter((t) => now - t < RATE_LIMIT_WINDOW_MS);
    if (this.rateTs.length >= RATE_LIMIT_MAX) {
      this.audit('policy_violation', 'rate_limit_exceeded');
      return;
    }
    this.rateTs.push(now);

    try {
      const parsed = dcDecode(raw);
      const msg = dcTryReassembleChunk(parsed);
      if (!msg) return;
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

  private audit(type: string, detail?: string): void {
    const event: AuditEvent = { ts: Date.now() / 1000, type, session_id: this.sessionId, detail };
    this.auditLog.push(event);
    if (this.auditLog.length > 200) this.auditLog.shift();

    const url = this.hubUrl;
    if (url) {
      this.core.post(`${url}/api/audit/webrtc`, event, url).subscribe({ error: () => {} });
    }
  }
}
