import { Injectable } from '@angular/core';

export interface SpeechDelaySegmentContext {
  sessionId: string;
  epoch: number;
  segmentId: string;
  sourceDigest: string;
  expiresAtMs: number;
}

interface EncryptedSpeechDelaySegment extends SpeechDelaySegmentContext {
  nonce: Uint8Array<ArrayBuffer>;
  ciphertext: ArrayBuffer;
  createdAtMs: number;
  bytes: number;
}

export interface SpeechDelayBufferSnapshot {
  segments: number;
  encryptedBytes: number;
  plaintextBytes: number;
  timers: number;
  keyReady: boolean;
}

export type SpeechDelayBufferUseResult<T> =
  | Readonly<{ available: true; value: T }>
  | Readonly<{ available: false; value: null }>;

const MAX_SEGMENTS = 5;
const MAX_ENCRYPTED_BYTES = 24 * 1024 * 1024;
const MAX_SEGMENT_BYTES = 8 * 1024 * 1024;
const DIGEST = /^[a-f0-9]{64}$/;
const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;

@Injectable({ providedIn: 'root' })
export class SpeechDelayBufferService {
  private readonly segments = new Map<string, EncryptedSpeechDelaySegment>();
  private key: CryptoKey | null = null;
  private keyPromise: Promise<CryptoKey> | null = null;
  private mutationVersion = 0;
  private pendingPuts = 0;

  async put(context: SpeechDelaySegmentContext, plaintext: Uint8Array, nowMs = Date.now()): Promise<void> {
    this.validateContext(context, nowMs);
    if (!(plaintext instanceof Uint8Array) || plaintext.byteLength === 0 || plaintext.byteLength > MAX_SEGMENT_BYTES) {
      throw new Error('speech_delay_segment_too_large');
    }
    this.pendingPuts += 1;
    try {
      this.purgeExpired(nowMs);
      const mutationVersion = this.mutationVersion;
      const plaintextCopy = new Uint8Array(plaintext.byteLength);
      plaintextCopy.set(plaintext);
      const actualDigest = await crypto.subtle.digest('SHA-256', plaintextCopy);
      if (this.hex(actualDigest) !== context.sourceDigest) {
        plaintextCopy.fill(0);
        throw new Error('speech_delay_source_digest_mismatch');
      }
      const key = await this.currentKey(mutationVersion);
      const nonce = crypto.getRandomValues(new Uint8Array(12));
      let ciphertext: ArrayBuffer;
      try {
        ciphertext = await crypto.subtle.encrypt(
          { name: 'AES-GCM', iv: nonce, additionalData: this.aad(context) },
          key,
          plaintextCopy,
        );
      } finally {
        plaintextCopy.fill(0);
      }
      if (mutationVersion !== this.mutationVersion || key !== this.key) {
        throw new Error('speech_delay_operation_invalidated');
      }
      if (ciphertext.byteLength > MAX_SEGMENT_BYTES + 16) throw new Error('speech_delay_segment_too_large');
      this.delete(context.segmentId);
      while (
        this.segments.size >= MAX_SEGMENTS
        || this.encryptedBytes() + ciphertext.byteLength > MAX_ENCRYPTED_BYTES
      ) {
        const oldest = this.oldestSegmentId();
        if (!oldest) throw new Error('speech_delay_quota_exceeded');
        this.delete(oldest);
      }
      this.segments.set(context.segmentId, {
        ...context,
        nonce,
        ciphertext,
        createdAtMs: nowMs,
        bytes: ciphertext.byteLength,
      });
    } finally {
      this.pendingPuts -= 1;
      if (!this.pendingPuts && !this.segments.size) {
        this.key = null;
        this.keyPromise = null;
      }
    }
  }

  /**
   * Keep the decrypted copy scoped to one caller operation and erase it even
   * when that operation fails.  No dataset/export consumer is exposed.
   */
  async use<T>(
    segmentId: string,
    operation: (plaintext: Uint8Array) => Promise<T>,
    nowMs = Date.now(),
  ): Promise<SpeechDelayBufferUseResult<T>> {
    const plaintext = await this.decrypt(segmentId, nowMs);
    if (!plaintext) return Object.freeze({ available: false, value: null });
    try {
      return Object.freeze({ available: true, value: await operation(plaintext) });
    } finally {
      plaintext.fill(0);
    }
  }

  confirm(segmentId: string): void {
    this.mutationVersion += 1;
    this.delete(segmentId);
  }
  correctionComplete(segmentId: string): void { this.confirm(segmentId); }

  revoke(sessionId?: string): void {
    this.mutationVersion += 1;
    if (!sessionId) {
      this.segments.clear();
      this.key = null;
      this.keyPromise = null;
      return;
    }
    for (const [segmentId, segment] of this.segments) {
      if (segment.sessionId === sessionId) this.delete(segmentId);
    }
    if (!this.segments.size && !this.pendingPuts) this.key = null;
  }

  purgeExpired(nowMs = Date.now()): number {
    let removed = 0;
    for (const [segmentId, segment] of this.segments) {
      if (segment.expiresAtMs <= nowMs) {
        this.delete(segmentId);
        removed += 1;
      }
    }
    if (!this.segments.size && !this.pendingPuts) this.key = null;
    return removed;
  }

  containTransportFailure(status: number, segmentId?: string): void {
    if (status === 413 && segmentId) this.delete(segmentId);
    if (status === 404 || status === 409) this.revoke();
  }

  discardKey(): void { this.revoke(); }

  snapshot(): SpeechDelayBufferSnapshot {
    return Object.freeze({
      segments: this.segments.size,
      encryptedBytes: this.encryptedBytes(),
      plaintextBytes: 0,
      timers: 0,
      keyReady: this.key !== null,
    });
  }

  private delete(segmentId: string): void { this.segments.delete(segmentId); }

  private async decrypt(segmentId: string, nowMs: number): Promise<Uint8Array | null> {
    this.purgeExpired(nowMs);
    const segment = this.segments.get(segmentId);
    if (!segment || !this.key) return null;
    const mutationVersion = this.mutationVersion;
    const key = this.key;
    try {
      const plaintext = await crypto.subtle.decrypt(
        { name: 'AES-GCM', iv: segment.nonce, additionalData: this.aad(segment) },
        key,
        segment.ciphertext,
      );
      const value = new Uint8Array(plaintext);
      if (mutationVersion !== this.mutationVersion || key !== this.key || !this.segments.has(segmentId)) {
        value.fill(0);
        return null;
      }
      return value;
    } catch {
      this.delete(segmentId);
      return null;
    }
  }

  private async currentKey(expectedMutationVersion: number): Promise<CryptoKey> {
    if (this.key) return this.key;
    if (!this.keyPromise) {
      const promise = crypto.subtle.generateKey(
        { name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt'],
      ).then(key => {
        if (expectedMutationVersion !== this.mutationVersion) {
          throw new Error('speech_delay_key_generation_invalidated');
        }
        this.key = key;
        return key;
      }).finally(() => {
        if (this.keyPromise === promise) this.keyPromise = null;
      });
      this.keyPromise = promise;
    }
    return this.keyPromise;
  }

  private aad(context: SpeechDelaySegmentContext): Uint8Array<ArrayBuffer> {
    return new TextEncoder().encode(JSON.stringify({
      domain: 'ananta.speech-delay-buffer.v1',
      epoch: context.epoch,
      expiresAtMs: context.expiresAtMs,
      segmentId: context.segmentId,
      sessionId: context.sessionId,
      sourceDigest: context.sourceDigest,
    }));
  }

  private validateContext(context: SpeechDelaySegmentContext, nowMs: number): void {
    if (!ID.test(context.sessionId) || !ID.test(context.segmentId) || !DIGEST.test(context.sourceDigest)) {
      throw new Error('speech_delay_context_invalid');
    }
    if (!Number.isSafeInteger(context.epoch) || context.epoch < 1) throw new Error('speech_delay_epoch_invalid');
    if (!Number.isSafeInteger(context.expiresAtMs) || context.expiresAtMs <= nowMs || context.expiresAtMs > nowMs + 600_000) {
      throw new Error('speech_delay_expiry_invalid');
    }
  }

  private encryptedBytes(): number {
    return Array.from(this.segments.values()).reduce((sum, segment) => sum + segment.bytes, 0);
  }

  private oldestSegmentId(): string | null {
    return Array.from(this.segments.values())
      .sort((left, right) => left.expiresAtMs - right.expiresAtMs || left.createdAtMs - right.createdAtMs)[0]
      ?.segmentId ?? null;
  }

  private hex(value: ArrayBuffer): string {
    return Array.from(new Uint8Array(value), byte => byte.toString(16).padStart(2, '0')).join('');
  }
}
