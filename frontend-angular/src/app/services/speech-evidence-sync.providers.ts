import { Injectable, Provider, inject } from '@angular/core';

import { E2eEncryptionService } from './e2e-encryption.service';
import {
  SPEECH_EVIDENCE_AEAD_KEYS,
  SPEECH_EVIDENCE_SEND_QUEUE,
  SPEECH_EVIDENCE_SIGNER,
  SpeechEvidenceAeadKeyPort,
  SpeechEvidenceMessageSignerPort,
  SpeechEvidenceSyncService,
} from './speech-evidence-sync.service';
import { SpeechEvidenceDatachannelTransportService } from './speech-evidence-datachannel-transport.service';
import {
  IndexedDbSpeechEvidenceQuarantineStore,
  SPEECH_EVIDENCE_QUARANTINE_STORE,
  SpeechEvidenceQuarantineStore,
} from './speech-evidence-quarantine.store';
import {
  SPEECH_EVIDENCE_OFFER_PROTOCOL_VERSION,
  SPEECH_EVIDENCE_PROTOCOL_VERSION,
  SpeechEvidenceMessage,
  SpeechEvidenceMessageType,
  canonicalSigningJson,
  sha256Canonical,
} from './speech-evidence-sync.validators';
import { WebrtcPeerKeyService } from './webrtc-peer-key.service';

interface SpeechEvidenceSigningContext {
  readonly pairId: string;
  readonly consentVersion: number;
}

/**
 * Browser-side crypto adapter for the evidence protocol.  It remains
 * deliberately unusable until a confirmed Hub-bound peer context and an
 * explicit evidence consent version have both been supplied.
 */
@Injectable()
export class SpeechEvidenceSyncCryptoContext implements SpeechEvidenceMessageSignerPort, SpeechEvidenceAeadKeyPort {
  private readonly peerKeys = inject(WebrtcPeerKeyService);
  private readonly encryption = inject(E2eEncryptionService);
  private signingContext: SpeechEvidenceSigningContext | null = null;
  private signingKeys: CryptoKeyPair | null = null;
  private signingKeyId = '';
  private sequence = 0;
  private readonly contentKeys = new Map<string, CryptoKey>();

  configure(pairId: string, consentVersion: number): void {
    if (
      !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(pairId)
      || !Number.isSafeInteger(consentVersion)
      || consentVersion < 1
    ) throw new Error('speech_evidence_signing_context_invalid');
    const binding = this.peerKeys.requireBinding(true);
    if (binding.scopeId !== pairId) throw new Error('speech_evidence_pair_binding_mismatch');
    this.signingContext = Object.freeze({ pairId, consentVersion });
    this.sequence = 0;
    this.contentKeys.clear();
  }

  clear(): void {
    this.signingContext = null;
    this.signingKeys = null;
    this.signingKeyId = '';
    this.sequence = 0;
    this.contentKeys.clear();
  }

  async resolve(offerId: string, epoch: number, keyId: string): Promise<CryptoKey | null> {
    const context = this.requireContext();
    const binding = this.peerKeys.requireBinding(true);
    if (
      binding.scopeId !== context.pairId
      || binding.epoch !== epoch
      || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(offerId)
      || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(keyId)
    ) return null;
    const cacheKey = `${offerId}\x1f${epoch}\x1f${keyId}`;
    let key = this.contentKeys.get(cacheKey);
    if (!key) {
      const purposeBinding = await sha256Text(`${offerId}\x1f${keyId}`);
      key = await this.encryption.derivePurposeAesKey(
        binding,
        'speech-evidence-chunk',
        `evidence-${purposeBinding}`,
      );
      this.contentKeys.set(cacheKey, key);
      while (this.contentKeys.size > 32) this.contentKeys.delete(this.contentKeys.keys().next().value!);
    }
    return key;
  }

  async sign(
    type: SpeechEvidenceMessageType,
    payload: Record<string, unknown>,
    expiresAtMs: number,
  ): Promise<SpeechEvidenceMessage> {
    const context = this.requireContext();
    const binding = this.peerKeys.requireBinding(true);
    if (binding.scopeId !== context.pairId) throw new Error('speech_evidence_pair_binding_mismatch');
    if (!Number.isSafeInteger(expiresAtMs) || expiresAtMs <= Date.now() || expiresAtMs > Date.now() + 600_000) {
      throw new Error('speech_evidence_expiry_invalid');
    }
    await this.ensureSigningKey();
    const sequence = ++this.sequence;
    if (!Number.isSafeInteger(sequence)) throw new Error('speech_evidence_sequence_exhausted');
    const issuedAtMs = Date.now();
    const unsigned: SpeechEvidenceMessage = {
      protocol_version: type === 'offer'
        ? SPEECH_EVIDENCE_OFFER_PROTOCOL_VERSION
        : SPEECH_EVIDENCE_PROTOCOL_VERSION,
      message_type: type,
      message_id: `speech-evidence-${binding.epoch}-${sequence}-${crypto.randomUUID()}`,
      session_id: binding.scopeId,
      pair_id: context.pairId,
      sender_id: binding.localPeerId,
      audience_id: binding.remotePeerId,
      epoch: binding.epoch,
      sequence,
      consent_version: context.consentVersion,
      key_id: this.signingKeyId,
      issued_at_ms: issuedAtMs,
      expires_at_ms: expiresAtMs,
      payload_digest: await sha256Canonical(payload),
      payload: Object.freeze({ ...payload }),
      signature_algorithm: 'Ed25519',
      signature_b64: bytesToB64(new Uint8Array(64)),
    };
    const signature = await crypto.subtle.sign(
      'Ed25519',
      this.signingKeys!.privateKey,
      new TextEncoder().encode(canonicalSigningJson(unsigned)),
    );
    return Object.freeze({ ...unsigned, signature_b64: bytesToB64(new Uint8Array(signature)) });
  }

  async exportPublicSigningKey(): Promise<Readonly<{ keyId: string; rawKeyB64: string }>> {
    this.requireContext();
    await this.ensureSigningKey();
    const raw = await crypto.subtle.exportKey('raw', this.signingKeys!.publicKey);
    return Object.freeze({ keyId: this.signingKeyId, rawKeyB64: bytesToB64(new Uint8Array(raw)) });
  }

  private requireContext(): SpeechEvidenceSigningContext {
    if (!this.signingContext) throw new Error('speech_evidence_signing_context_unavailable');
    return this.signingContext;
  }

  private async ensureSigningKey(): Promise<void> {
    if (this.signingKeys) return;
    this.signingKeys = await crypto.subtle.generateKey('Ed25519', false, ['sign', 'verify']) as CryptoKeyPair;
    const raw = new Uint8Array(await crypto.subtle.exportKey('raw', this.signingKeys.publicKey));
    const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', raw));
    this.signingKeyId = `speech-sign-${hex(digest).slice(0, 32)}`;
  }
}

export function provideSpeechEvidenceSync(): Provider[] {
  return [
    SpeechEvidenceSyncCryptoContext,
    SpeechEvidenceDatachannelTransportService,
    IndexedDbSpeechEvidenceQuarantineStore,
    SpeechEvidenceQuarantineStore,
    SpeechEvidenceSyncService,
    { provide: SPEECH_EVIDENCE_SIGNER, useExisting: SpeechEvidenceSyncCryptoContext },
    { provide: SPEECH_EVIDENCE_AEAD_KEYS, useExisting: SpeechEvidenceSyncCryptoContext },
    { provide: SPEECH_EVIDENCE_SEND_QUEUE, useExisting: SpeechEvidenceDatachannelTransportService },
    { provide: SPEECH_EVIDENCE_QUARANTINE_STORE, useExisting: IndexedDbSpeechEvidenceQuarantineStore },
  ];
}

function bytesToB64(value: Uint8Array): string {
  let binary = '';
  for (const byte of value) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function hex(value: Uint8Array): string {
  return [...value].map(byte => byte.toString(16).padStart(2, '0')).join('');
}

async function sha256Text(value: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
  return hex(new Uint8Array(digest));
}
