import { Injectable, inject } from '@angular/core';

import { SECURE_KEY_STORE, SecureKeyStorePort, StoredDeviceKeyPair } from './secure-key-store.service';
import {
  SECURE_ENVELOPE_VERSION,
  SecureEnvelopeError,
  SecureEnvelopeV1,
  SecurityTrafficClass,
  decodeB64,
  encodeB64,
  parseSecureEnvelope,
  secureEnvelopeAad,
} from './webrtc-secure-envelope';
import { E2eOutboundNoncePolicy } from './e2e-nonce-policy';
import {
  INBOUND_NONCE_REPLAY_STORE,
  InboundNonceReplayDomain,
  InboundNonceReplayStorePort,
} from './e2e-replay.store';

export interface KeyEnvelope {
  publicKeySpkiB64: string;
  fingerprint: string;
  generation: number;
  peerRebindRequired: boolean;
}
export interface PeerCipherContext {
  scopeKind: 'session' | 'room';
  scopeId: string;
  localPeerId: string;
  remotePeerId: string;
  peerPublicKeySpkiB64: string;
  epoch: number;
  keyId: string;
  contractDigest: string;
}

export interface SealOptions {
  sequence: number;
  payloadType: string;
  trafficClass: SecurityTrafficClass;
  expiresAtMs?: number;
}

@Injectable({ providedIn: 'root' })
export class E2eEncryptionService {
  private readonly store: SecureKeyStorePort = inject(SECURE_KEY_STORE);
  private readonly replayStore: InboundNonceReplayStorePort = inject(INBOUND_NONCE_REPLAY_STORE);
  private readonly nonces = new E2eOutboundNoncePolicy();

  async ensureLocalKeyPair(): Promise<KeyEnvelope> {
    const peerRebindRequired = this.store.discardLegacyLocalStorageKey();
    const existing = await this.store.loadCurrent();
    if (existing) return this.keyEnvelope(existing, peerRebindRequired);
    const generated = await this.generateRecord(1);
    await this.store.replaceCurrent(generated);
    return this.keyEnvelope(generated, peerRebindRequired);
  }

  async rotateLocalKeyPair(): Promise<KeyEnvelope> {
    const previous = await this.store.loadCurrent();
    const generated = await this.generateRecord((previous?.generation ?? 0) + 1);
    // IndexedDB replaces the single current record in one transaction.
    await this.store.replaceCurrent(generated);
    this.nonces.clear();
    return this.keyEnvelope(generated, true);
  }

  async clearAllKeyMaterial(): Promise<void> {
    await this.store.clear();
    this.nonces.clear();
  }

  async seal(
    context: PeerCipherContext,
    plaintext: Uint8Array,
    options: SealOptions,
  ): Promise<SecureEnvelopeV1> {
    this.validateContext(context);
    const nonceScope = this.nonceScope(context.keyId, context.epoch);
    const nonce = this.nonces.nextOutbound(nonceScope);
    const envelope: SecureEnvelopeV1 = {
      version: SECURE_ENVELOPE_VERSION,
      scope: { kind: context.scopeKind, id: context.scopeId },
      sender_id: context.localPeerId,
      recipient: { kind: 'peer', id: context.remotePeerId },
      epoch: context.epoch,
      sequence: options.sequence,
      key_id: context.keyId,
      payload_type: options.payloadType,
      expires_at_ms: options.expiresAtMs ?? Date.now() + 120_000,
      nonce_b64: encodeB64(nonce),
      aad: {
        traffic_class: options.trafficClass,
        content_encoding: 'json',
        contract_digest: context.contractDigest,
      },
      // A 16-byte placeholder gives the metadata the same validated shape
      // before WebCrypto computes the real ciphertext/tag.
      ciphertext_b64: encodeB64(new Uint8Array(16)),
    };
    const key = await this.deriveAesKey(context);
    const ciphertext = await crypto.subtle.encrypt(
      {
        name: 'AES-GCM', iv: asArrayBuffer(nonce),
        additionalData: asArrayBuffer(secureEnvelopeAad(envelope)), tagLength: 128,
      },
      key,
      asArrayBuffer(plaintext),
    );
    return parseSecureEnvelope({ ...envelope, ciphertext_b64: encodeB64(ciphertext) });
  }

  async open(context: PeerCipherContext, raw: unknown): Promise<{ envelope: SecureEnvelopeV1; plaintext: Uint8Array }> {
    this.validateContext(context);
    const envelope = parseSecureEnvelope(raw);
    if (
      envelope.scope.kind !== context.scopeKind
      || envelope.scope.id !== context.scopeId
      || envelope.sender_id !== context.remotePeerId
      || envelope.recipient.kind !== 'peer'
      || envelope.recipient.id !== context.localPeerId
      || envelope.epoch !== context.epoch
      || envelope.key_id !== context.keyId
      || envelope.aad.contract_digest !== context.contractDigest
    ) {
      throw new SecureEnvelopeError('cipher_context_mismatch');
    }
    const nonceDomain = this.inboundNonceDomain(context);
    const key = await this.deriveAesKey(context);
    let plaintext: ArrayBuffer;
    try {
      plaintext = await crypto.subtle.decrypt(
        {
          name: 'AES-GCM',
          iv: asArrayBuffer(decodeB64(envelope.nonce_b64)),
          additionalData: asArrayBuffer(secureEnvelopeAad(envelope)),
          tagLength: 128,
        },
        key,
        asArrayBuffer(decodeB64(envelope.ciphertext_b64)),
      );
    } catch (error) {
      if (error instanceof SecureEnvelopeError) throw error;
      if (await this.replayStore.hasNonce(nonceDomain, envelope.nonce_b64, Date.now())) {
        throw new SecureEnvelopeError('nonce_reuse');
      }
      throw new SecureEnvelopeError('authentication_failed');
    }
    const nonceClaim = await this.replayStore.claimNonce(
      nonceDomain,
      envelope.nonce_b64,
      envelope.expires_at_ms + 30_000,
      Date.now(),
    );
    if (nonceClaim === 'duplicate') throw new SecureEnvelopeError('nonce_reuse');
    if (nonceClaim === 'capacity_exceeded') {
      throw new SecureEnvelopeError('nonce_replay_capacity_exceeded');
    }
    if (nonceClaim !== 'claimed') throw new SecureEnvelopeError('nonce_replay_store_invalid_result');
    return { envelope, plaintext: new Uint8Array(plaintext) };
  }

  async confirmationTag(context: PeerCipherContext, transcriptDigest: string): Promise<string> {
    this.validateContext(context);
    if (!/^[a-f0-9]{64}$/.test(transcriptDigest)) throw new SecureEnvelopeError('transcript_invalid');
    const secret = await this.deriveSharedBits(context.peerPublicKeySpkiB64);
    const hkdf = await crypto.subtle.importKey('raw', secret, 'HKDF', false, ['deriveKey']);
    const key = await crypto.subtle.deriveKey(
      {
        name: 'HKDF', hash: 'SHA-256',
        salt: new TextEncoder().encode(`${context.scopeId}:${context.epoch}:${context.keyId}`),
        info: new TextEncoder().encode('ananta.webrtc.key-confirmation.v1'),
      },
      hkdf,
      { name: 'HMAC', hash: 'SHA-256', length: 256 },
      false,
      ['sign'],
    );
    return encodeB64(await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(transcriptDigest)));
  }

  /**
   * Derive a non-extractable, purpose-bound pair key without exposing the
   * ECDH shared secret.  Higher-level protocols must still bind and validate
   * their own AAD; this method only owns key derivation.
   */
  async derivePurposeAesKey(
    context: PeerCipherContext,
    purpose: string,
    bindingId: string,
  ): Promise<CryptoKey> {
    this.validateContext(context);
    if (
      !/^[a-z][a-z0-9._-]{2,63}$/.test(purpose)
      || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(bindingId)
    ) throw new SecureEnvelopeError('purpose_key_context_invalid');
    const secret = await this.deriveSharedBits(context.peerPublicKeySpkiB64);
    const hkdf = await crypto.subtle.importKey('raw', secret, 'HKDF', false, ['deriveKey']);
    const peers = [context.localPeerId, context.remotePeerId].sort().join(':');
    return crypto.subtle.deriveKey(
      {
        name: 'HKDF',
        hash: 'SHA-256',
        salt: new TextEncoder().encode(
          `${context.scopeKind}:${context.scopeId}:${context.epoch}:${context.keyId}:${bindingId}`,
        ),
        info: new TextEncoder().encode(`ananta.${purpose}.v1:${peers}`),
      },
      hkdf,
      { name: 'AES-GCM', length: 256 },
      false,
      ['encrypt', 'decrypt'],
    );
  }

  /**
   * Derive short-lived symmetric material for adapters (such as LiveKit)
   * whose public API imports raw E2EE bytes.  The non-extractable device
   * private key remains inside WebCrypto; callers must zero the returned
   * buffer immediately after the adapter has imported it.
   */
  async derivePurposeKeyMaterial(
    context: PeerCipherContext,
    purpose: string,
    bindingId: string,
  ): Promise<Uint8Array> {
    this.validateContext(context);
    if (
      !/^[a-z][a-z0-9._-]{2,63}$/.test(purpose)
      || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(bindingId)
    ) throw new SecureEnvelopeError('purpose_key_context_invalid');
    const secret = await this.deriveSharedBits(context.peerPublicKeySpkiB64);
    const hkdf = await crypto.subtle.importKey('raw', secret, 'HKDF', false, ['deriveBits']);
    const peers = [context.localPeerId, context.remotePeerId].sort().join(':');
    const bits = await crypto.subtle.deriveBits(
      {
        name: 'HKDF', hash: 'SHA-256',
        salt: new TextEncoder().encode(
          `${context.scopeKind}:${context.scopeId}:${context.epoch}:${context.keyId}:${bindingId}`,
        ),
        info: new TextEncoder().encode(`ananta.${purpose}.v1:${peers}`),
      },
      hkdf,
      256,
    );
    return new Uint8Array(bits);
  }

  forgetEpoch(keyId: string, epoch: number): void {
    this.nonces.forget(this.nonceScope(keyId, epoch));
  }

  async fingerprintSpki(publicKeySpkiB64: string): Promise<string> {
    const digest = await crypto.subtle.digest('SHA-256', asArrayBuffer(decodeB64(publicKeySpkiB64)));
    return Array.from(new Uint8Array(digest)).map((byte) => byte.toString(16).padStart(2, '0')).join('');
  }

  private async generateRecord(generation: number): Promise<StoredDeviceKeyPair> {
    const keyPair = await crypto.subtle.generateKey(
      { name: 'ECDH', namedCurve: 'P-256' },
      false,
      ['deriveKey', 'deriveBits'],
    );
    if (keyPair.privateKey.extractable) throw new SecureEnvelopeError('private_key_extractable');
    const publicKeySpkiB64 = encodeB64(await crypto.subtle.exportKey('spki', keyPair.publicKey));
    return {
      id: 'device-ecdh-current', keyPair, publicKeySpkiB64,
      fingerprint: await this.fingerprintSpki(publicKeySpkiB64),
      generation, createdAtMs: Date.now(),
    };
  }

  private async deriveAesKey(context: PeerCipherContext): Promise<CryptoKey> {
    const secret = await this.deriveSharedBits(context.peerPublicKeySpkiB64);
    const hkdf = await crypto.subtle.importKey('raw', secret, 'HKDF', false, ['deriveKey']);
    const peers = [context.localPeerId, context.remotePeerId].sort().join(':');
    return crypto.subtle.deriveKey(
      {
        name: 'HKDF', hash: 'SHA-256',
        salt: new TextEncoder().encode(`${context.scopeKind}:${context.scopeId}:${context.epoch}:${context.keyId}`),
        info: new TextEncoder().encode(`ananta.webrtc.aead.v1:${peers}`),
      },
      hkdf,
      { name: 'AES-GCM', length: 256 },
      false,
      ['encrypt', 'decrypt'],
    );
  }

  private async deriveSharedBits(peerPublicKeySpkiB64: string): Promise<ArrayBuffer> {
    const local = await this.store.loadCurrent();
    if (!local) throw new SecureEnvelopeError('missing_private_key');
    const peer = await crypto.subtle.importKey(
      'spki', asArrayBuffer(decodeB64(peerPublicKeySpkiB64)),
      { name: 'ECDH', namedCurve: 'P-256' }, false, [],
    );
    return crypto.subtle.deriveBits({ name: 'ECDH', public: peer }, local.keyPair.privateKey, 256);
  }

  private nonceScope(keyId: string, epoch: number): string {
    return `${keyId}\u0000${epoch}`;
  }

  private inboundNonceDomain(context: PeerCipherContext): InboundNonceReplayDomain {
    return {
      scopeKind: context.scopeKind,
      scopeId: context.scopeId,
      epoch: context.epoch,
      keyId: context.keyId,
      senderId: context.remotePeerId,
      recipientId: context.localPeerId,
    };
  }

  private validateContext(context: PeerCipherContext): void {
    if (
      context.epoch < 1 || context.localPeerId === context.remotePeerId
      || !/^[a-f0-9]{64}$/.test(context.contractDigest)
    ) throw new SecureEnvelopeError('cipher_context_invalid');
  }

  private keyEnvelope(record: StoredDeviceKeyPair, peerRebindRequired: boolean): KeyEnvelope {
    return {
      publicKeySpkiB64: record.publicKeySpkiB64,
      fingerprint: record.fingerprint,
      generation: record.generation,
      peerRebindRequired,
    };
  }
}

function asArrayBuffer(value: Uint8Array): ArrayBuffer {
  const copy = new Uint8Array(value.byteLength);
  copy.set(value);
  return copy.buffer;
}
