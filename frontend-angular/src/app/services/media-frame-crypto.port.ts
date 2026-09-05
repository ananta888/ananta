import { Injectable, inject } from '@angular/core';

import {
  MediaE2eeFrameContext,
  MediaE2eeTransformService,
  MediaEncodedFrame,
} from './media-e2ee-transform.service';

export interface MediaFrameKeyScope {
  readonly tenantId: string;
  readonly roomId: string;
  readonly publicationId: string;
  readonly senderId: string;
  readonly mediaKind: 'audio' | 'video';
  readonly keyEpoch: number;
}

/** A transport-neutral capability; callers never receive raw key bytes. */
export interface MediaFrameKeyLease {
  readonly scope: Readonly<MediaFrameKeyScope>;
  readonly expiresAtMs: number;
  use<T>(operation: (key: CryptoKey) => T): T;
  release(): void;
}

export interface MediaFrameCryptoPort {
  encryptStream(
    context: Readonly<MediaE2eeFrameContext>,
    lease: MediaFrameKeyLease,
  ): TransformStream<MediaEncodedFrame, MediaEncodedFrame>;
  decryptStream(
    context: Readonly<MediaE2eeFrameContext>,
    lease: MediaFrameKeyLease,
  ): TransformStream<MediaEncodedFrame, MediaEncodedFrame>;
  releaseEpoch(context: Readonly<MediaE2eeFrameContext>): void;
}

/**
 * Owns the lifetime check around a non-extractable key. Releasing drops the
 * only adapter-owned reference and makes every later operation fail closed.
 */
export class ScopedMediaFrameKeyLease implements MediaFrameKeyLease {
  private key: CryptoKey | null;

  constructor(
    readonly scope: Readonly<MediaFrameKeyScope>,
    readonly expiresAtMs: number,
    key: CryptoKey,
    private readonly clock: () => number = () => Date.now(),
  ) {
    validateScope(scope);
    if (!Number.isSafeInteger(expiresAtMs) || expiresAtMs <= clock()) {
      throw new Error('media_frame_key_lease_expired');
    }
    validateKey(key);
    this.key = key;
  }

  use<T>(operation: (key: CryptoKey) => T): T {
    if (!this.key) throw new Error('media_frame_key_lease_released');
    if (this.expiresAtMs <= this.clock()) throw new Error('media_frame_key_lease_expired');
    return operation(this.key);
  }

  release(): void { this.key = null; }
}

/** Compatibility adapter for the existing ANME frame codec. */
@Injectable({ providedIn: 'root' })
export class AnmeMediaFrameCryptoAdapter implements MediaFrameCryptoPort {
  private readonly codec = inject(MediaE2eeTransformService);

  encryptStream(
    context: Readonly<MediaE2eeFrameContext>,
    lease: MediaFrameKeyLease,
  ): TransformStream<MediaEncodedFrame, MediaEncodedFrame> {
    assertLeaseScope(context, lease.scope);
    return lease.use(key => this.codec.encryptStream(context, key));
  }

  decryptStream(
    context: Readonly<MediaE2eeFrameContext>,
    lease: MediaFrameKeyLease,
  ): TransformStream<MediaEncodedFrame, MediaEncodedFrame> {
    assertLeaseScope(context, lease.scope);
    return lease.use(key => this.codec.decryptStream(context, key));
  }

  releaseEpoch(context: Readonly<MediaE2eeFrameContext>): void {
    this.codec.forgetEpoch(context);
  }
}

function assertLeaseScope(context: MediaE2eeFrameContext, scope: MediaFrameKeyScope): void {
  if (context.publicationId !== scope.publicationId || context.senderId !== scope.senderId
      || context.kind !== scope.mediaKind || context.keyEpoch !== scope.keyEpoch) {
    throw new Error('media_frame_key_lease_scope_mismatch');
  }
}

function validateScope(value: MediaFrameKeyScope): void {
  const ids = [value.tenantId, value.roomId, value.publicationId, value.senderId];
  if (ids.some(item => !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(item))
      || !['audio', 'video'].includes(value.mediaKind)
      || !Number.isSafeInteger(value.keyEpoch) || value.keyEpoch < 1) {
    throw new Error('media_frame_key_lease_scope_invalid');
  }
}

function validateKey(key: CryptoKey): void {
  if (key.type !== 'secret' || key.algorithm.name !== 'AES-GCM' || key.extractable
      || !key.usages.includes('encrypt') || !key.usages.includes('decrypt')) {
    throw new Error('media_frame_key_lease_key_invalid');
  }
}
