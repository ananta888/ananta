import { Injectable } from '@angular/core';

interface EncryptedCacheEntry {
  readonly nonce: Uint8Array;
  readonly ciphertext: Uint8Array;
  readonly expiresAtMs: number;
  readonly plaintextBytes: number;
}

export interface ReceiverSpeechAdapterCachePort {
  put(artifactSha256: string, plaintext: Uint8Array, expiresAtMs: number): Promise<void>;
  read(artifactSha256: string, nowMs?: number): Promise<Uint8Array | null>;
  remove(artifactSha256: string): void;
  clear(): void;
}

const MAX_CACHE_ENTRIES = 2;
const MAX_CACHE_BYTES = 64 * 1024 * 1024;

/**
 * Receiver-only encrypted ring cache.
 *
 * The service retains ciphertext and a non-extractable process-local key.
 * Plaintext returned by `read` is owned by the caller and must be zeroed after
 * the model loader consumes it. Nothing is persisted or exposed to Hub/peer
 * services.
 */
@Injectable({ providedIn: 'root' })
export class ReceiverSpeechAdapterCacheService implements ReceiverSpeechAdapterCachePort {
  private readonly entries = new Map<string, EncryptedCacheEntry>();
  private keyPromise: Promise<CryptoKey> | null = null;
  private plaintextBytes = 0;

  async put(artifactSha256: string, plaintext: Uint8Array, expiresAtMs: number): Promise<void> {
    this.validateDigest(artifactSha256);
    if (
      !(plaintext instanceof Uint8Array) || plaintext.byteLength < 1
      || plaintext.byteLength > MAX_CACHE_BYTES
      || !Number.isSafeInteger(expiresAtMs) || expiresAtMs <= Date.now()
    ) throw new Error('speech_adapter_cache_entry_invalid');
    const actual = await sha256(plaintext);
    if (actual !== artifactSha256) throw new Error('speech_adapter_cache_digest_mismatch');
    const nonce = crypto.getRandomValues(new Uint8Array(12));
    const encrypted = await crypto.subtle.encrypt(
      {
        name: 'AES-GCM', iv: toArrayBuffer(nonce),
        additionalData: toArrayBuffer(aad(artifactSha256, expiresAtMs)), tagLength: 128,
      },
      await this.key(),
      toArrayBuffer(plaintext),
    );
    this.remove(artifactSha256);
    while (
      this.entries.size >= MAX_CACHE_ENTRIES
      || (this.entries.size > 0 && this.plaintextBytes + plaintext.byteLength > MAX_CACHE_BYTES)
    ) this.remove(this.entries.keys().next().value!);
    this.entries.set(artifactSha256, Object.freeze({
      nonce: nonce.slice(),
      ciphertext: new Uint8Array(encrypted),
      expiresAtMs,
      plaintextBytes: plaintext.byteLength,
    }));
    this.plaintextBytes += plaintext.byteLength;
  }

  async read(artifactSha256: string, nowMs = Date.now()): Promise<Uint8Array | null> {
    this.validateDigest(artifactSha256);
    const entry = this.entries.get(artifactSha256);
    if (!entry) return null;
    if (nowMs >= entry.expiresAtMs) {
      this.remove(artifactSha256);
      return null;
    }
    this.entries.delete(artifactSha256);
    this.entries.set(artifactSha256, entry);
    try {
      const plaintext = await crypto.subtle.decrypt(
        {
          name: 'AES-GCM', iv: toArrayBuffer(entry.nonce),
          additionalData: toArrayBuffer(aad(artifactSha256, entry.expiresAtMs)), tagLength: 128,
        },
        await this.key(),
        toArrayBuffer(entry.ciphertext),
      );
      const result = new Uint8Array(plaintext);
      if (result.byteLength !== entry.plaintextBytes || await sha256(result) !== artifactSha256) {
        result.fill(0);
        this.remove(artifactSha256);
        throw new Error('speech_adapter_cache_digest_mismatch');
      }
      return result;
    } catch (error) {
      this.remove(artifactSha256);
      if (error instanceof Error && error.message === 'speech_adapter_cache_digest_mismatch') throw error;
      throw new Error('speech_adapter_cache_authentication_failed');
    }
  }

  remove(artifactSha256: string): void {
    const current = this.entries.get(artifactSha256);
    if (!current) return;
    current.nonce.fill(0);
    current.ciphertext.fill(0);
    this.entries.delete(artifactSha256);
    this.plaintextBytes = Math.max(0, this.plaintextBytes - current.plaintextBytes);
  }

  clear(): void {
    for (const digest of [...this.entries.keys()]) this.remove(digest);
    this.keyPromise = null;
  }

  snapshot(): Readonly<{ entries: number; ciphertextBytes: number; plaintextBytes: number }> {
    let ciphertextBytes = 0;
    for (const entry of this.entries.values()) ciphertextBytes += entry.ciphertext.byteLength;
    return Object.freeze({ entries: this.entries.size, ciphertextBytes, plaintextBytes: 0 });
  }

  private key(): Promise<CryptoKey> {
    this.keyPromise ??= crypto.subtle.generateKey(
      { name: 'AES-GCM', length: 256 },
      false,
      ['encrypt', 'decrypt'],
    );
    return this.keyPromise;
  }

  private validateDigest(value: string): void {
    if (!/^[0-9a-f]{64}$/.test(value)) throw new Error('speech_adapter_cache_digest_invalid');
  }
}

function aad(artifactSha256: string, expiresAtMs: number): Uint8Array {
  return new TextEncoder().encode(JSON.stringify({
    artifact_sha256: artifactSha256,
    domain: 'ananta.receiver-speech-adapter-cache.v1',
    expires_at_ms: expiresAtMs,
  }));
}

async function sha256(value: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', toArrayBuffer(value));
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
}

function toArrayBuffer(value: Uint8Array): ArrayBuffer {
  const copy = new Uint8Array(value.byteLength);
  copy.set(value);
  return copy.buffer;
}
