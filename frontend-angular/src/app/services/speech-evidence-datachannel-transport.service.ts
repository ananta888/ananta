import { Injectable, OnDestroy, inject } from '@angular/core';
import { firstValueFrom, Subject, Subscription } from 'rxjs';

import { E2eEncryptionService } from './e2e-encryption.service';
import type { SpeechEvidenceSendPort } from './speech-evidence-sync.service';
import { SpeechEvidenceSyncApiService } from './speech-evidence-sync-api.service';
import {
  SpeechEvidenceMessage,
  SpeechEvidenceReplayWindow,
  validateSpeechEvidenceMessage,
  verifySpeechEvidenceMessage,
} from './speech-evidence-sync.validators';
import {
  SEMANTIC_DC_VERSION,
  SEMANTIC_TRAFFIC_CLASS_LIMITS,
  SemanticDataChannelMessage,
  validateSemanticDcMessage,
} from './webrtc-datachannel.service';
import { WebrtcPeerKeyService } from './webrtc-peer-key.service';
import { WebrtcTransportService } from './webrtc-transport.service';
import { canonicalSecurityJson, decodeB64, encodeB64 } from './webrtc-secure-envelope';

interface EvidenceTransportBinding {
  readonly hubUrl: string;
  readonly sessionId: string;
  readonly epoch: number;
  readonly localPeerId: string;
  readonly remotePeerId: string;
  readonly consentVersion: number;
  readonly keyId: string;
}

export interface RejectedSpeechEvidenceInbound {
  readonly messageId: string;
  readonly reasonCode: string;
}

/**
 * Pair-bound outer transport for signed evidence protocol messages.
 *
 * It authenticates and encrypts the DataChannel envelope with the confirmed
 * Hub-bound ECDH pair key. Inner messages are promoted only after resolving a
 * current membership/consent-bound Ed25519 key from the Hub and verifying the
 * signature, audience, epoch, TTL, payload digest and replay window locally.
 */
@Injectable()
export class SpeechEvidenceDatachannelTransportService implements SpeechEvidenceSendPort, OnDestroy {
  private readonly transport = inject(WebrtcTransportService);
  private readonly peerKeys = inject(WebrtcPeerKeyService);
  private readonly encryption = inject(E2eEncryptionService);
  private readonly api = inject(SpeechEvidenceSyncApiService);
  private readonly subscriptions = new Subscription();
  private readonly replay = new SpeechEvidenceReplayWindow();
  private binding: EvidenceTransportBinding | null = null;

  readonly verifiedInbound$ = new Subject<SpeechEvidenceMessage>();
  readonly verificationRejected$ = new Subject<RejectedSpeechEvidenceInbound>();

  constructor() {
    this.subscriptions.add(this.transport.semanticMessage$.subscribe(message => {
      void this.receive(message).catch(error => {
        this.reject(safeMessageId(message.message_id), reason(error, 'speech_evidence_transport_invalid'));
      });
    }));
  }

  bind(hubUrl: string, consentVersion: number): void {
    const normalizedHubUrl = String(hubUrl || '').trim().replace(/\/+$/, '');
    if (!/^https?:\/\/[^\s]+$/.test(normalizedHubUrl)) throw new Error('speech_evidence_hub_url_invalid');
    if (!Number.isSafeInteger(consentVersion) || consentVersion < 1) {
      throw new Error('speech_evidence_consent_version_invalid');
    }
    const pair = this.peerKeys.requireBinding(true);
    if (this.transport.mode$.value === 'idle') throw new Error('speech_evidence_transport_not_open');
    this.transport.setSemanticEpoch(pair.epoch);
    this.transport.enableSemanticTraffic('control');
    this.transport.enableSemanticTraffic('evidence_bulk');
    this.binding = Object.freeze({
      hubUrl: normalizedHubUrl,
      sessionId: pair.scopeId,
      epoch: pair.epoch,
      localPeerId: pair.localPeerId,
      remotePeerId: pair.remotePeerId,
      consentVersion,
      keyId: pair.keyId,
    });
  }

  clear(): void {
    this.binding = null;
    this.transport.disableSemanticTraffic('evidence_bulk');
  }

  async send(
    trafficClass: 'control' | 'evidence_bulk',
    payload: string,
    expiresAtMs: number,
  ): Promise<boolean> {
    const context = this.binding;
    if (!context || this.transport.mode$.value === 'idle') return false;
    let parsed: SpeechEvidenceMessage;
    try { parsed = validateSpeechEvidenceMessage(JSON.parse(payload)); } catch { return false; }
    const expectedTraffic = parsed.message_type === 'chunk' ? 'evidence_bulk' : 'control';
    if (
      trafficClass !== expectedTraffic
      || parsed.session_id !== context.sessionId
      || parsed.pair_id !== context.sessionId
      || parsed.sender_id !== context.localPeerId
      || parsed.audience_id !== context.remotePeerId
      || parsed.epoch !== context.epoch
      || parsed.consent_version !== context.consentVersion
      || parsed.expires_at_ms !== expiresAtMs
      || expiresAtMs <= Date.now()
      || expiresAtMs > Date.now() + 600_000
    ) return false;

    try {
      const pair = this.peerKeys.requireBinding(true);
      if (!matchesPair(context, pair)) return false;
      const plaintext = new TextEncoder().encode(payload);
      const envelope = await this.encryption.seal(pair, plaintext, {
        sequence: parsed.sequence,
        payloadType: 'speech_evidence',
        trafficClass: trafficClass === 'control' ? 'control' : 'bulk',
        expiresAtMs,
      });
      const ciphertext = new TextEncoder().encode(canonicalSecurityJson(envelope));
      if (ciphertext.byteLength > SEMANTIC_TRAFFIC_CLASS_LIMITS[trafficClass]) return false;
      const outer = await validateSemanticDcMessage({
        version: SEMANTIC_DC_VERSION,
        traffic_class: trafficClass,
        message_id: `evidence-${parsed.sequence}-${crypto.randomUUID()}`,
        session_id: context.sessionId,
        epoch: context.epoch,
        sender_id: context.localPeerId,
        audience_id: context.remotePeerId,
        sequence: parsed.sequence,
        expires_at_ms: expiresAtMs,
        compression: 'none',
        security: { algorithm: 'AES-GCM-256', key_id: context.keyId },
        payload_bytes: ciphertext.byteLength,
        payload_digest: await sha256Hex(ciphertext),
        ciphertext: encodeB64(ciphertext),
      });
      const relayDisposition = await this.authorizeAndRelay(context.hubUrl, parsed, outer);
      if (!relayDisposition) return false;
      // In relay mode the authenticated speech-evidence endpoint above has
      // already persisted this exact opaque envelope. Posting it a second time
      // would create a message-id conflict. The shared transport poll delivers
      // it to the recipient. WebRTC mode still sends the direct low-latency copy.
      if (this.transport.mode$.value === 'hub_relay' && relayDisposition === 'evidence_api_relayed') return true;
      await this.transport.sendSemantic(outer, { deadlineMs: Math.min(expiresAtMs, Date.now() + 30_000) });
      return true;
    } catch {
      return false;
    }
  }

  ngOnDestroy(): void {
    this.clear();
    this.subscriptions.unsubscribe();
    this.verifiedInbound$.complete();
    this.verificationRejected$.complete();
  }

  private async receive(raw: SemanticDataChannelMessage): Promise<void> {
    const context = this.binding;
    if (!context || !['control', 'evidence_bulk'].includes(raw.traffic_class)) return;
    const outer = await validateSemanticDcMessage(raw);
    if (
      outer.session_id !== context.sessionId
      || outer.epoch !== context.epoch
      || outer.sender_id !== context.remotePeerId
      || outer.audience_id !== context.localPeerId
      || outer.security.key_id !== context.keyId
      || outer.expires_at_ms <= Date.now()
    ) {
      this.reject(outer.message_id, 'speech_evidence_outer_binding_mismatch');
      return;
    }
    const pair = this.peerKeys.requireBinding(true);
    if (!matchesPair(context, pair)) {
      this.reject(outer.message_id, 'speech_evidence_pair_binding_mismatch');
      return;
    }
    let sealed: unknown;
    try {
      sealed = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(decodeB64(outer.ciphertext)));
    } catch {
      this.reject(outer.message_id, 'speech_evidence_outer_ciphertext_invalid');
      return;
    }
    const opened = await this.encryption.open(pair, sealed);
    if (
      opened.envelope.payload_type !== 'speech_evidence'
      || opened.envelope.sequence !== outer.sequence
      || opened.envelope.expires_at_ms !== outer.expires_at_ms
      || opened.envelope.aad.traffic_class !== (outer.traffic_class === 'control' ? 'control' : 'bulk')
    ) {
      this.reject(outer.message_id, 'speech_evidence_inner_outer_mismatch');
      return;
    }
    let inner: SpeechEvidenceMessage;
    try {
      inner = validateSpeechEvidenceMessage(JSON.parse(
        new TextDecoder('utf-8', { fatal: true }).decode(opened.plaintext),
      ));
    } catch {
      this.reject(outer.message_id, 'speech_evidence_inner_message_invalid');
      return;
    }
    const expectedTraffic = inner.message_type === 'chunk' ? 'evidence_bulk' : 'control';
    if (
      outer.traffic_class !== expectedTraffic
      || inner.session_id !== context.sessionId
      || inner.pair_id !== context.sessionId
      || inner.sender_id !== context.remotePeerId
      || inner.audience_id !== context.localPeerId
      || inner.epoch !== context.epoch
      || inner.consent_version !== context.consentVersion
      || inner.sequence !== outer.sequence
      || inner.expires_at_ms !== outer.expires_at_ms
    ) {
      this.reject(inner.message_id, 'speech_evidence_inner_outer_mismatch');
      return;
    }
    try {
      const verified = await verifySpeechEvidenceMessage(inner, {
        sessionId: context.sessionId,
        pairId: context.sessionId,
        audienceId: context.localPeerId,
        epoch: context.epoch,
        consentVersion: context.consentVersion,
        nowMs: Date.now(),
      }, {
        resolve: async message => this.resolveHubSigningKey(context, message),
      }, this.replay);
      this.verifiedInbound$.next(verified);
    } catch (error) {
      this.reject(inner.message_id, reason(error, 'speech_evidence_verification_failed'));
    }
  }

  private reject(messageId: string, reasonCode: string): void {
    this.verificationRejected$.next(Object.freeze({ messageId, reasonCode }));
  }

  private async authorizeAndRelay(
    hubUrl: string,
    message: SpeechEvidenceMessage,
    outer: SemanticDataChannelMessage,
  ): Promise<'evidence_api_relayed' | 'transport_required' | null> {
    try {
      if (message.message_type === 'chunk') {
        await firstValueFrom(this.api.appendChunk(hubUrl, message, outer));
      } else if (message.message_type === 'chunk_ack') {
        await firstValueFrom(this.api.acknowledgeChunk(hubUrl, message, outer));
      } else if (message.message_type === 'offer') {
        const stage = message.payload['stage'];
        if (stage === 'proposal') await firstValueFrom(this.api.propose(hubUrl, message, outer));
        else if (stage === 'acceptance') await firstValueFrom(this.api.accept(hubUrl, message, outer));
        else return null;
      } else {
        // Resolution, receipt and revocation controls have no state-mutating
        // evidence endpoint. They still travel through the authenticated,
        // bounded generic semantic transport and are verified by the peer.
        return 'transport_required';
      }
      return 'evidence_api_relayed';
    } catch {
      return null;
    }
  }

  private async resolveHubSigningKey(
    context: EvidenceTransportBinding,
    message: SpeechEvidenceMessage,
  ): Promise<CryptoKey | null> {
    try {
      const record = await firstValueFrom(this.api.discoverKey(context.hubUrl, {
        sessionId: message.session_id,
        pairId: message.pair_id,
        senderId: message.sender_id,
        epoch: message.epoch,
        keyId: message.key_id,
      }));
      if (
        record.sessionId !== context.sessionId
        || record.pairId !== context.sessionId
        || record.senderId !== context.remotePeerId
        || record.audienceId !== context.localPeerId
        || record.epoch !== context.epoch
        || record.keyId !== message.key_id
        || record.consentVersion !== context.consentVersion
        || record.expiresAtMs <= Date.now()
      ) return null;
      const raw = decodeB64(record.publicKeyB64);
      if (raw.byteLength !== 32) return null;
      return await crypto.subtle.importKey('raw', Uint8Array.from(raw), 'Ed25519', false, ['verify']);
    } catch {
      return null;
    }
  }
}

function matchesPair(
  context: EvidenceTransportBinding,
  pair: Readonly<{
    scopeId: string; epoch: number; localPeerId: string; remotePeerId: string; keyId: string; confirmed: boolean;
  }>,
): boolean {
  return pair.confirmed
    && pair.scopeId === context.sessionId
    && pair.epoch === context.epoch
    && pair.localPeerId === context.localPeerId
    && pair.remotePeerId === context.remotePeerId
    && pair.keyId === context.keyId;
}

function safeMessageId(value: unknown): string {
  return typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$/.test(value)
    ? value
    : 'speech-evidence-message-redacted';
}

function reason(error: unknown, fallback: string): string {
  return error instanceof Error && /^[a-z][a-z0-9_]{2,159}$/.test(error.message)
    ? error.message
    : fallback;
}

async function sha256Hex(value: Uint8Array): Promise<string> {
  const copy = Uint8Array.from(value);
  const digest = await crypto.subtle.digest('SHA-256', copy.buffer);
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('');
}
