import { Injectable, InjectionToken, inject } from '@angular/core';

import {
  SPEECH_EVIDENCE_MAX_CHUNK_BYTES,
  SPEECH_EVIDENCE_MAX_IN_FLIGHT_BYTES,
  SpeechEvidenceMessage,
  SpeechEvidenceValidationError,
  sha256Canonical,
  validateSpeechEvidenceMessage,
  SpeechEvidenceMessageType,
} from './speech-evidence-sync.validators';

export interface SpeechEvidenceMessageSignerPort {
  sign(
    type: SpeechEvidenceMessageType,
    payload: Record<string, unknown>,
    expiresAtMs: number,
  ): Promise<SpeechEvidenceMessage>;
}

export interface SpeechEvidenceAeadKeyPort {
  resolve(offerId: string, epoch: number, keyId: string): Promise<CryptoKey | null>;
}

export interface SpeechEvidenceSendPort {
  send(
    trafficClass: 'control' | 'evidence_bulk',
    payload: string,
    expiresAtMs: number,
  ): Promise<boolean>;
}

export const SPEECH_EVIDENCE_SEND_QUEUE = new InjectionToken<SpeechEvidenceSendPort>(
  'SPEECH_EVIDENCE_SEND_QUEUE',
  {
    providedIn: 'root',
    // The root fallback is deliberately disconnected.  A scoped composition
    // must bind evidence to an authenticated semantic transport explicitly.
    factory: () => ({ send: async () => false }),
  },
);
export const SPEECH_EVIDENCE_SIGNER = new InjectionToken<SpeechEvidenceMessageSignerPort>('SPEECH_EVIDENCE_SIGNER');
export const SPEECH_EVIDENCE_AEAD_KEYS = new InjectionToken<SpeechEvidenceAeadKeyPort>('SPEECH_EVIDENCE_AEAD_KEYS');

export interface SpeechEvidenceTransferBinding {
  offerId: string;
  groupId: string;
  epoch: number;
  keyId: string;
  expiresAtMs: number;
  dataClass: string;
}

export interface SpeechEvidenceTransferSnapshot {
  offerId: string;
  groupId: string;
  state: 'active' | 'paused' | 'completed' | 'revoked' | 'failed';
  chunkCount: number;
  acknowledgedChunks: number;
  firstMissingIndex: number;
  inFlightBytes: number;
  retries: number;
  reasonCode: string | null;
}

interface OutboundChunk {
  index: number;
  plaintextBytes: number;
  payload: Record<string, unknown>;
  sent: boolean;
  acknowledged: boolean;
  retries: number;
}

interface OutboundTransfer {
  binding: SpeechEvidenceTransferBinding;
  chunks: OutboundChunk[];
  state: SpeechEvidenceTransferSnapshot['state'];
  firstMissingIndex: number;
  inFlightBytes: number;
  retries: number;
  reasonCode: string | null;
}

const MAX_RETRIES = 5;
const FORBIDDEN_BULK_CLASSES = new Set([
  'raw_audio', 'audio', 'adapter_export', 'model_artifact', 'export',
]);

@Injectable({ providedIn: 'root' })
export class SpeechEvidenceSyncService {
  private readonly queue = inject(SPEECH_EVIDENCE_SEND_QUEUE);
  private readonly signer = inject(SPEECH_EVIDENCE_SIGNER);
  private readonly keys = inject(SPEECH_EVIDENCE_AEAD_KEYS);
  private readonly transfers = new Map<string, OutboundTransfer>();
  private readonly nonces = new Map<string, string>();
  private pausedGlobally = false;

  async prepareTransfer(binding: SpeechEvidenceTransferBinding, plaintext: Uint8Array): Promise<SpeechEvidenceTransferSnapshot> {
    if (FORBIDDEN_BULK_CLASSES.has(binding.dataClass)) throw new SpeechEvidenceValidationError('speech_evidence_bulk_class_forbidden');
    if (!plaintext.byteLength || plaintext.byteLength > 1024 * 1024 * 1024) {
      throw new SpeechEvidenceValidationError('speech_evidence_total_bytes_invalid');
    }
    if (binding.expiresAtMs <= Date.now()) throw new SpeechEvidenceValidationError('speech_evidence_expired');
    const key = await this.keys.resolve(binding.offerId, binding.epoch, binding.keyId);
    if (!key) throw new SpeechEvidenceValidationError('speech_evidence_key_unknown');
    const chunkCount = Math.ceil(plaintext.byteLength / SPEECH_EVIDENCE_MAX_CHUNK_BYTES);
    if (chunkCount > 4096) throw new SpeechEvidenceValidationError('speech_evidence_chunk_count_invalid');
    const chunks: OutboundChunk[] = [];
    for (let index = 0; index < chunkCount; index += 1) {
      const clear = plaintext.slice(index * SPEECH_EVIDENCE_MAX_CHUNK_BYTES, (index + 1) * SPEECH_EVIDENCE_MAX_CHUNK_BYTES);
      const nonce = crypto.getRandomValues(new Uint8Array(12));
      const nonceB64 = bytesToB64(nonce);
      this.claimNonce(binding, nonceB64, 'outbound', 'reserved');
      const aad = new TextEncoder().encode(JSON.stringify({
        domain: 'ananta.speech-evidence-chunk-aad.v1', offer_id: binding.offerId, group_id: binding.groupId,
        chunk_index: index, chunk_count: chunkCount, epoch: binding.epoch, key_id: binding.keyId,
        expires_at_ms: binding.expiresAtMs,
      }));
      const ciphertext = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv: nonce, additionalData: aad }, key, clear));
      const plaintextDigest = await digestBytes(clear);
      const ciphertextDigest = await digestBytes(ciphertext);
      chunks.push({
        index,
        plaintextBytes: clear.byteLength,
        sent: false,
        acknowledged: false,
        retries: 0,
        payload: {
          traffic_class: 'evidence_bulk', offer_id: binding.offerId, group_id: binding.groupId,
          chunk_index: index, chunk_count: chunkCount, plaintext_bytes: clear.byteLength,
          plaintext_digest: plaintextDigest, ciphertext_digest: ciphertextDigest,
          nonce_b64: nonceB64, ciphertext_b64: bytesToB64(ciphertext),
        },
      });
    }
    const transfer: OutboundTransfer = {
      binding: Object.freeze({ ...binding }), chunks, state: 'active', firstMissingIndex: 0,
      inFlightBytes: 0, retries: 0, reasonCode: null,
    };
    this.transfers.set(this.transferKey(binding.offerId, binding.groupId), transfer);
    await this.flushTransfer(transfer);
    return this.snapshot(binding.offerId, binding.groupId);
  }

  async acknowledge(raw: unknown): Promise<SpeechEvidenceTransferSnapshot> {
    const message = validateSpeechEvidenceMessage(raw);
    if (message.message_type !== 'chunk_ack') throw new SpeechEvidenceValidationError('speech_evidence_ack_required');
    const payload = message.payload;
    const transfer = this.requireTransfer(String(payload['offer_id']), String(payload['group_id']));
    if (message.epoch !== transfer.binding.epoch || message.expires_at_ms <= Date.now()) {
      throw new SpeechEvidenceValidationError('speech_evidence_epoch_stale');
    }
    const indices = payload['acknowledged_indices'] as number[];
    const advertisedCursor = Number(payload['first_missing_index']);
    if (advertisedCursor < transfer.firstMissingIndex) throw new SpeechEvidenceValidationError('speech_evidence_ack_cursor_rollback');
    const acknowledged = new Set(
      transfer.chunks.filter(chunk => chunk.acknowledged).map(chunk => chunk.index),
    );
    for (const index of indices) {
      const chunk = transfer.chunks[index];
      if (!chunk) throw new SpeechEvidenceValidationError('speech_evidence_ack_inconsistent');
      acknowledged.add(index);
    }
    const expectedReceivedBytes = [...acknowledged]
      .reduce((total, index) => total + transfer.chunks[index].plaintextBytes, 0);
    if (Number(payload['received_bytes']) !== expectedReceivedBytes) {
      throw new SpeechEvidenceValidationError('speech_evidence_ack_inconsistent');
    }
    let firstMissing = 0;
    while (acknowledged.has(firstMissing)) firstMissing += 1;
    if (advertisedCursor !== firstMissing) throw new SpeechEvidenceValidationError('speech_evidence_ack_cursor_invalid');
    for (const index of indices) {
      const chunk = transfer.chunks[index];
      if (!chunk.acknowledged) {
        chunk.acknowledged = true;
        transfer.inFlightBytes = Math.max(0, transfer.inFlightBytes - chunk.plaintextBytes);
      }
    }
    transfer.firstMissingIndex = firstMissing;
    if (firstMissing === transfer.chunks.length) transfer.state = 'completed';
    else await this.flushTransfer(transfer);
    return this.snapshot(transfer.binding.offerId, transfer.binding.groupId);
  }

  async resume(offerId: string, groupId: string): Promise<SpeechEvidenceTransferSnapshot> {
    const transfer = this.requireTransfer(offerId, groupId);
    if (transfer.state === 'revoked' || transfer.state === 'completed') return this.snapshot(offerId, groupId);
    transfer.state = 'active';
    transfer.inFlightBytes = 0;
    for (const chunk of transfer.chunks) if (!chunk.acknowledged) chunk.sent = false;
    await this.flushTransfer(transfer);
    return this.snapshot(offerId, groupId);
  }

  pause(offerId?: string): void {
    if (!offerId) this.pausedGlobally = true;
    for (const transfer of this.transfers.values()) {
      if (!offerId || transfer.binding.offerId === offerId) transfer.state = 'paused';
    }
  }

  async resumeAll(): Promise<void> {
    this.pausedGlobally = false;
    for (const transfer of this.transfers.values()) {
      if (transfer.state === 'paused') await this.resume(transfer.binding.offerId, transfer.binding.groupId);
    }
  }

  revoke(offerId: string, reasonCode = 'speech_evidence_revoked'): void {
    for (const transfer of this.transfers.values()) {
      if (transfer.binding.offerId !== offerId) continue;
      transfer.state = 'revoked';
      transfer.reasonCode = reasonCode;
      transfer.inFlightBytes = 0;
    }
  }

  clear(): void {
    this.transfers.clear();
    this.nonces.clear();
    this.pausedGlobally = false;
  }

  snapshot(offerId: string, groupId: string): SpeechEvidenceTransferSnapshot {
    const transfer = this.requireTransfer(offerId, groupId);
    return Object.freeze({
      offerId, groupId, state: transfer.state, chunkCount: transfer.chunks.length,
      acknowledgedChunks: transfer.chunks.filter(chunk => chunk.acknowledged).length,
      firstMissingIndex: transfer.firstMissingIndex, inFlightBytes: transfer.inFlightBytes,
      retries: transfer.retries, reasonCode: transfer.reasonCode,
    });
  }

  async decryptChunk(message: SpeechEvidenceMessage): Promise<Uint8Array> {
    if (message.message_type !== 'chunk') throw new SpeechEvidenceValidationError('speech_evidence_chunk_required');
    const payload = message.payload;
    const binding: SpeechEvidenceTransferBinding = {
      offerId: String(payload['offer_id']), groupId: String(payload['group_id']), epoch: message.epoch,
      keyId: message.key_id, expiresAtMs: message.expires_at_ms, dataClass: 'encrypted_evidence',
    };
    const nonceB64 = String(payload['nonce_b64']);
    this.claimNonce(binding, nonceB64, 'inbound', String(payload['ciphertext_digest']));
    const key = await this.keys.resolve(binding.offerId, binding.epoch, binding.keyId);
    if (!key) throw new SpeechEvidenceValidationError('speech_evidence_key_unknown');
    const ciphertext = b64ToBytes(String(payload['ciphertext_b64']));
    if (await digestBytes(ciphertext) !== payload['ciphertext_digest']) {
      throw new SpeechEvidenceValidationError('speech_evidence_ciphertext_digest_mismatch');
    }
    const aad = new TextEncoder().encode(JSON.stringify({
      domain: 'ananta.speech-evidence-chunk-aad.v1', offer_id: binding.offerId, group_id: binding.groupId,
      chunk_index: payload['chunk_index'], chunk_count: payload['chunk_count'], epoch: binding.epoch,
      key_id: binding.keyId, expires_at_ms: binding.expiresAtMs,
    }));
    try {
      const clear = new Uint8Array(await crypto.subtle.decrypt(
        { name: 'AES-GCM', iv: arrayBuffer(b64ToBytes(nonceB64)), additionalData: arrayBuffer(aad) }, key,
        arrayBuffer(ciphertext),
      ));
      if (await digestBytes(clear) !== payload['plaintext_digest']) {
        throw new SpeechEvidenceValidationError('speech_evidence_plaintext_digest_mismatch');
      }
      return clear;
    } catch (error) {
      if (error instanceof SpeechEvidenceValidationError) throw error;
      throw new SpeechEvidenceValidationError('speech_evidence_authentication_failed');
    }
  }

  private async flushTransfer(transfer: OutboundTransfer): Promise<void> {
    if (this.pausedGlobally || transfer.state !== 'active') return;
    for (const chunk of transfer.chunks) {
      if (chunk.acknowledged || chunk.sent) continue;
      if (transfer.inFlightBytes + chunk.plaintextBytes > SPEECH_EVIDENCE_MAX_IN_FLIGHT_BYTES) return;
      if (chunk.retries >= MAX_RETRIES) {
        transfer.state = 'failed'; transfer.reasonCode = 'speech_evidence_retry_limit'; return;
      }
      const message = await this.signer.sign('chunk', chunk.payload, transfer.binding.expiresAtMs);
      if (await sha256Canonical(message.payload) !== message.payload_digest) {
        transfer.state = 'failed'; transfer.reasonCode = 'speech_evidence_payload_digest_mismatch'; return;
      }
      const queued = await this.queue.send('evidence_bulk', JSON.stringify(message), transfer.binding.expiresAtMs);
      if (!queued) {
        transfer.state = 'paused'; transfer.reasonCode = 'speech_evidence_backpressure'; return;
      }
      chunk.sent = true;
      chunk.retries += 1;
      transfer.retries += 1;
      transfer.inFlightBytes += chunk.plaintextBytes;
    }
  }

  private claimNonce(
    binding: SpeechEvidenceTransferBinding,
    nonceB64: string,
    direction: string,
    ciphertextDigest: string,
  ): void {
    const key = [binding.keyId, binding.epoch, direction, nonceB64].join(':');
    const existing = this.nonces.get(key);
    if (existing !== undefined) {
      if (direction === 'inbound' && existing === ciphertextDigest) return;
      throw new SpeechEvidenceValidationError('speech_evidence_nonce_reused');
    }
    this.nonces.set(key, ciphertextDigest);
    if (this.nonces.size > 65_536) throw new SpeechEvidenceValidationError('speech_evidence_nonce_state_exhausted');
  }
  private transferKey(offerId: string, groupId: string): string { return `${offerId}\0${groupId}`; }
  private requireTransfer(offerId: string, groupId: string): OutboundTransfer {
    const transfer = this.transfers.get(this.transferKey(offerId, groupId));
    if (!transfer) throw new SpeechEvidenceValidationError('speech_evidence_transfer_not_found');
    return transfer;
  }
}

async function digestBytes(value: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', arrayBuffer(value));
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('');
}
function bytesToB64(value: Uint8Array): string {
  let binary = ''; for (const byte of value) binary += String.fromCharCode(byte); return btoa(binary);
}
function b64ToBytes(value: string): Uint8Array { return Uint8Array.from(atob(value), char => char.charCodeAt(0)); }
function arrayBuffer(value: Uint8Array): ArrayBuffer {
  const copy = new Uint8Array(value.byteLength); copy.set(value); return copy.buffer;
}
