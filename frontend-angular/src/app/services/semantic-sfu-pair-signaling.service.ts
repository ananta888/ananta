import { Injectable, OnDestroy, inject } from '@angular/core';
import { Subject, Subscription } from 'rxjs';

import { E2eEncryptionService } from './e2e-encryption.service';
import {
  SEMANTIC_DC_VERSION,
  SemanticDataChannelMessage,
  validateSemanticDcMessage,
} from './webrtc-datachannel.service';
import { WebrtcPeerKeyService } from './webrtc-peer-key.service';
import { WebrtcTransportService } from './webrtc-transport.service';
import { canonicalSecurityJson, decodeB64, encodeB64 } from './webrtc-secure-envelope';

export interface SemanticSfuPublicationHint {
  readonly schema: 'ananta.semantic-sfu-pair-signal.v1';
  readonly kind: 'publication_hint';
  readonly signal_id: string;
  readonly session_id: string;
  readonly epoch: number;
  readonly sender_id: string;
  readonly audience_id: string;
  readonly publication_id: string;
  readonly room_id: string;
  readonly expires_at_ms: number;
}

/**
 * Authenticated pair signaling carries only a publication identifier. It is
 * not an authorization: the receiving client must present that identifier to
 * the Hub subscription endpoint, which independently checks membership,
 * audience and CAS revision before issuing a LiveKit token.
 */
@Injectable()
export class SemanticSfuPairSignalingService implements OnDestroy {
  private readonly transport = inject(WebrtcTransportService);
  private readonly peerKeys = inject(WebrtcPeerKeyService);
  private readonly encryption = inject(E2eEncryptionService);
  private readonly subscriptions = new Subscription();
  private bound = false;
  private bindingKey = '';
  private sequence = 0;
  readonly publicationHint$ = new Subject<SemanticSfuPublicationHint>();

  constructor() {
    this.subscriptions.add(this.transport.semanticMessage$.subscribe(message => {
      void this.receive(message).catch(() => undefined);
    }));
  }

  bind(): void {
    const pair = this.peerKeys.requireBinding(true);
    if (this.transport.mode$.value === 'idle') throw new Error('sfu_pair_signal_transport_not_open');
    const key = [pair.scopeId, pair.epoch, pair.localPeerId, pair.remotePeerId, pair.keyId].join('\0');
    if (this.bound && this.bindingKey === key) return;
    this.transport.setSemanticEpoch(pair.epoch);
    this.transport.enableSemanticTraffic('control');
    this.bound = true;
    this.bindingKey = key;
    this.sequence = 0;
  }

  clear(): void { this.bound = false; this.bindingKey = ''; this.sequence = 0; }

  async sendPublicationHint(publicationId: string, roomId: string, expiresAtMs: number): Promise<void> {
    if (!this.bound) throw new Error('sfu_pair_signal_not_bound');
    const pair = this.peerKeys.requireBinding(true);
    const sequence = ++this.sequence;
    const hint = parseHint({
      schema: 'ananta.semantic-sfu-pair-signal.v1', kind: 'publication_hint',
      signal_id: `sfu-signal-${sequence}-${crypto.randomUUID()}`,
      session_id: pair.scopeId, epoch: pair.epoch, sender_id: pair.localPeerId,
      audience_id: pair.remotePeerId, publication_id: publicationId, room_id: roomId,
      expires_at_ms: expiresAtMs,
    });
    if (expiresAtMs <= Date.now() || expiresAtMs > Date.now() + 60_000) {
      throw new Error('sfu_pair_signal_expiry_invalid');
    }
    const envelope = await this.encryption.seal(pair, new TextEncoder().encode(canonicalSecurityJson(hint)), {
      sequence, payloadType: 'semantic_sfu_control', trafficClass: 'control', expiresAtMs,
    });
    const ciphertext = new TextEncoder().encode(canonicalSecurityJson(envelope));
    const outer = await validateSemanticDcMessage({
      version: SEMANTIC_DC_VERSION, traffic_class: 'control', message_id: hint.signal_id,
      session_id: pair.scopeId, epoch: pair.epoch, sender_id: pair.localPeerId,
      audience_id: pair.remotePeerId, sequence, expires_at_ms: expiresAtMs, compression: 'none',
      security: { algorithm: 'AES-GCM-256', key_id: pair.keyId },
      payload_bytes: ciphertext.byteLength, payload_digest: await sha256Hex(ciphertext),
      ciphertext: encodeB64(ciphertext),
    });
    await this.transport.sendSemantic(outer, { deadlineMs: Math.min(expiresAtMs, Date.now() + 15_000) });
  }

  ngOnDestroy(): void {
    this.clear();
    this.subscriptions.unsubscribe();
    this.publicationHint$.complete();
  }

  private async receive(raw: SemanticDataChannelMessage): Promise<void> {
    if (!this.bound || raw.traffic_class !== 'control') return;
    const outer = await validateSemanticDcMessage(raw);
    const pair = this.peerKeys.requireBinding(true);
    if (
      outer.session_id !== pair.scopeId || outer.epoch !== pair.epoch
      || outer.sender_id !== pair.remotePeerId || outer.audience_id !== pair.localPeerId
      || outer.security.key_id !== pair.keyId || outer.expires_at_ms <= Date.now()
    ) return;
    let sealed: unknown;
    try {
      sealed = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(decodeB64(outer.ciphertext)));
    } catch { return; }
    const opened = await this.encryption.open(pair, sealed);
    if (
      opened.envelope.payload_type !== 'semantic_sfu_control'
      || opened.envelope.aad.traffic_class !== 'control'
      || opened.envelope.sequence !== outer.sequence
      || opened.envelope.expires_at_ms !== outer.expires_at_ms
    ) return;
    let hint: SemanticSfuPublicationHint;
    try {
      hint = parseHint(JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(opened.plaintext)));
    } catch { return; }
    if (
      hint.signal_id !== outer.message_id || hint.session_id !== pair.scopeId || hint.epoch !== pair.epoch
      || hint.sender_id !== pair.remotePeerId || hint.audience_id !== pair.localPeerId
      || hint.expires_at_ms !== outer.expires_at_ms
    ) return;
    this.publicationHint$.next(hint);
  }
}

function parseHint(raw: unknown): SemanticSfuPublicationHint {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('sfu_pair_signal_invalid');
  const row = raw as Record<string, unknown>;
  const keys = [
    'schema', 'kind', 'signal_id', 'session_id', 'epoch', 'sender_id', 'audience_id',
    'publication_id', 'room_id', 'expires_at_ms',
  ];
  if (Object.keys(row).some(key => !keys.includes(key)) || keys.some(key => !(key in row))) {
    throw new Error('sfu_pair_signal_invalid');
  }
  if (row['schema'] !== 'ananta.semantic-sfu-pair-signal.v1' || row['kind'] !== 'publication_hint') {
    throw new Error('sfu_pair_signal_invalid');
  }
  for (const key of ['signal_id', 'session_id', 'sender_id', 'audience_id', 'publication_id'] as const) {
    if (typeof row[key] !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(row[key])) {
      throw new Error('sfu_pair_signal_invalid');
    }
  }
  if (typeof row['room_id'] !== 'string' || !/^sfu-[a-f0-9]{32}$/.test(row['room_id'])
      || !Number.isSafeInteger(row['epoch']) || (row['epoch'] as number) < 1
      || !Number.isSafeInteger(row['expires_at_ms']) || (row['expires_at_ms'] as number) < 1) {
    throw new Error('sfu_pair_signal_invalid');
  }
  return Object.freeze(row as unknown as SemanticSfuPublicationHint);
}

async function sha256Hex(value: Uint8Array): Promise<string> {
  const copy = Uint8Array.from(value);
  const digest = await crypto.subtle.digest('SHA-256', copy.buffer);
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('');
}
