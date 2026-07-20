import { Injectable, inject } from '@angular/core';

import { E2eEncryptionService } from './e2e-encryption.service';
import type { SemanticSpeechCryptoPort } from './semantic-speech-crypto.port';
import {
  SEMANTIC_DC_VERSION,
  SEMANTIC_TRAFFIC_CLASS_LIMITS,
  SemanticDataChannelMessage,
  SemanticTrafficClass,
  validateSemanticDcMessage,
} from './webrtc-datachannel.service';
import { WebrtcPeerKeyService } from './webrtc-peer-key.service';
import { canonicalSecurityJson, decodeB64, encodeB64 } from './webrtc-secure-envelope';

@Injectable({ providedIn: 'root' })
export class SemanticSpeechCryptoService implements SemanticSpeechCryptoPort {
  private readonly encryption = inject(E2eEncryptionService);
  private readonly peerKeys = inject(WebrtcPeerKeyService);
  private readonly sequences = new Map<string, number>();

  async seal(payload: Uint8Array, trafficClass: SemanticTrafficClass): Promise<SemanticDataChannelMessage> {
    if (Object.prototype.toString.call(payload) !== '[object Uint8Array]' || payload.byteLength === 0) {
      throw new Error('semantic_speech_plaintext_invalid');
    }
    if (!['control', 'transcript', 'audio_recovery'].includes(trafficClass)) {
      throw new Error('semantic_speech_traffic_class_invalid');
    }
    const binding = this.peerKeys.requireBinding(true);
    const sequenceKey = `${binding.scopeId}:${binding.epoch}:${binding.keyId}`;
    const sequence = (this.sequences.get(sequenceKey) ?? 0) + 1;
    if (!Number.isSafeInteger(sequence)) throw new Error('semantic_speech_sequence_exhausted');
    const expiresAtMs = Date.now() + 120_000;
    const envelope = await this.encryption.seal(binding, Uint8Array.from(payload), {
      sequence,
      payloadType: 'semantic_speech',
      trafficClass: trafficClass === 'control' ? 'control' : 'semantic',
      expiresAtMs,
    });
    const envelopeBytes = new TextEncoder().encode(canonicalSecurityJson(envelope));
    if (envelopeBytes.byteLength > SEMANTIC_TRAFFIC_CLASS_LIMITS[trafficClass]) {
      throw new Error('semantic_speech_ciphertext_too_large');
    }
    this.sequences.set(sequenceKey, sequence);
    while (this.sequences.size > 64) {
      const oldest = this.sequences.keys().next().value as string | undefined;
      if (oldest === undefined) break;
      this.sequences.delete(oldest);
    }
    return validateSemanticDcMessage({
      version: SEMANTIC_DC_VERSION,
      traffic_class: trafficClass,
      message_id: `speech-${binding.epoch}-${sequence}-${crypto.randomUUID()}`,
      session_id: binding.scopeId,
      epoch: binding.epoch,
      sender_id: binding.localPeerId,
      audience_id: binding.remotePeerId,
      sequence,
      expires_at_ms: expiresAtMs,
      compression: 'none',
      security: { algorithm: 'AES-GCM-256', key_id: binding.keyId },
      payload_bytes: envelopeBytes.byteLength,
      payload_digest: await sha256Hex(envelopeBytes),
      ciphertext: encodeB64(envelopeBytes),
    });
  }

  async open(raw: SemanticDataChannelMessage): Promise<Uint8Array> {
    const message = await validateSemanticDcMessage(raw);
    const binding = this.peerKeys.requireBinding(true);
    if (
      message.session_id !== binding.scopeId
      || message.epoch !== binding.epoch
      || message.sender_id !== binding.remotePeerId
      || message.audience_id !== binding.localPeerId
      || message.security.key_id !== binding.keyId
    ) throw new Error('semantic_speech_cipher_context_mismatch');
    let envelope: unknown;
    try {
      envelope = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(decodeB64(message.ciphertext)));
    } catch {
      throw new Error('semantic_speech_ciphertext_invalid');
    }
    const opened = await this.encryption.open(binding, envelope);
    if (
      opened.envelope.sequence !== message.sequence
      || opened.envelope.expires_at_ms !== message.expires_at_ms
      || opened.envelope.payload_type !== 'semantic_speech'
    ) throw new Error('semantic_speech_envelope_mismatch');
    return opened.plaintext;
  }

  clear(): void { this.sequences.clear(); }
}

async function sha256Hex(value: Uint8Array): Promise<string> {
  const copy = Uint8Array.from(value);
  const digest = await crypto.subtle.digest('SHA-256', copy.buffer);
  return Array.from(new Uint8Array(digest)).map(byte => byte.toString(16).padStart(2, '0')).join('');
}
