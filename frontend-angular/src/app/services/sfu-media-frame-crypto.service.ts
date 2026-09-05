import { Injectable } from '@angular/core';
import { decodeB64, encodeB64 } from './webrtc-secure-envelope';

export interface SfuMediaFrameContext {
  readonly roomId: string;
  readonly publicationId: string;
  readonly senderId: string;
  readonly receiverScope: string;
  readonly codec: string;
  readonly layerId: string;
  readonly keyEpoch: number;
}

export interface SfuEncryptedMediaFrame {
  readonly version: 1;
  readonly room_id: string;
  readonly publication_id: string;
  readonly sender_id: string;
  readonly receiver_scope: string;
  readonly codec: string;
  readonly layer_id: string;
  readonly key_epoch: number;
  readonly counter: number;
  readonly nonce_b64: string;
  readonly ciphertext_b64: string;
}

export class SfuMediaCryptoError extends Error {
  constructor(readonly reasonCode: string) { super(reasonCode); }
}

interface ReplayWindow { highest: number; seen: Set<number> }

@Injectable({ providedIn: 'root' })
export class SfuMediaFrameCryptoService {
  private readonly keys = new Map<string, { epoch: number; key: CryptoKey }>();
  private readonly counters = new Map<string, number>();
  private readonly replay = new Map<string, ReplayWindow>();
  private readonly blockedRooms = new Set<string>();
  private readonly replayWindowSize = 128;

  /** Blocks new frames synchronously before an asynchronous group rekey starts. */
  beginRotation(roomId: string): void {
    validateId(roomId, 'sfu_media_room_invalid');
    this.blockedRooms.add(roomId);
  }

  async activateKey(roomId: string, keyEpoch: number, keyMaterial: Uint8Array): Promise<void> {
    validateId(roomId, 'sfu_media_room_invalid');
    if (!Number.isSafeInteger(keyEpoch) || keyEpoch < 1 || keyMaterial.byteLength !== 32) {
      throw new SfuMediaCryptoError('sfu_media_key_invalid');
    }
    const current = this.keys.get(roomId);
    if (current && keyEpoch <= current.epoch) throw new SfuMediaCryptoError('sfu_media_key_epoch_stale');
    const key = await crypto.subtle.importKey('raw', arrayBuffer(keyMaterial), 'AES-GCM', false, ['encrypt', 'decrypt']);
    this.keys.set(roomId, { epoch: keyEpoch, key });
    this.deleteRoomState(roomId);
    this.blockedRooms.delete(roomId);
  }

  revokeRoom(roomId: string): void {
    this.blockedRooms.add(roomId);
    this.keys.delete(roomId);
    this.deleteRoomState(roomId);
  }

  async seal(context: Readonly<SfuMediaFrameContext>, payload: Uint8Array): Promise<SfuEncryptedMediaFrame> {
    validateContext(context);
    if (payload.byteLength < 1 || payload.byteLength > 2 * 1024 * 1024) {
      throw new SfuMediaCryptoError('sfu_media_frame_size_invalid');
    }
    const active = this.activeKey(context);
    const scope = contextKey(context);
    const counter = (this.counters.get(scope) ?? 0) + 1;
    if (!Number.isSafeInteger(counter)) throw new SfuMediaCryptoError('sfu_media_counter_exhausted');
    this.counters.set(scope, counter);
    const nonce = crypto.getRandomValues(new Uint8Array(12));
    const metadata = frameMetadata(context, counter, nonce);
    const ciphertext = await crypto.subtle.encrypt(
      { name: 'AES-GCM', iv: arrayBuffer(nonce), additionalData: arrayBuffer(aad(metadata)), tagLength: 128 },
      active.key, arrayBuffer(payload),
    );
    return Object.freeze({ ...metadata, ciphertext_b64: encodeB64(ciphertext) });
  }

  async open(
    expected: Readonly<SfuMediaFrameContext>,
    envelope: Readonly<SfuEncryptedMediaFrame>,
  ): Promise<Uint8Array> {
    validateContext(expected);
    const active = this.activeKey(expected);
    validateEnvelope(expected, envelope);
    const scope = contextKey(expected);
    if (!this.acceptCounter(scope, envelope.counter)) throw new SfuMediaCryptoError('sfu_media_frame_replayed');
    const nonce = decodeB64(envelope.nonce_b64);
    if (nonce.byteLength !== 12) {
      this.forgetCounter(scope, envelope.counter);
      throw new SfuMediaCryptoError('sfu_media_nonce_invalid');
    }
    const { ciphertext_b64: _ciphertext, ...metadata } = envelope;
    try {
      const cleartext = await crypto.subtle.decrypt(
        { name: 'AES-GCM', iv: arrayBuffer(nonce), additionalData: arrayBuffer(aad(metadata)), tagLength: 128 },
        active.key, arrayBuffer(decodeB64(envelope.ciphertext_b64)),
      );
      return new Uint8Array(cleartext);
    } catch {
      this.forgetCounter(scope, envelope.counter);
      throw new SfuMediaCryptoError('sfu_media_authentication_failed');
    }
  }

  private activeKey(context: SfuMediaFrameContext): { epoch: number; key: CryptoKey } {
    if (this.blockedRooms.has(context.roomId)) throw new SfuMediaCryptoError('sfu_media_rekey_pending');
    const active = this.keys.get(context.roomId);
    if (!active) throw new SfuMediaCryptoError('sfu_media_key_missing');
    if (active.epoch !== context.keyEpoch) throw new SfuMediaCryptoError('sfu_media_key_epoch_stale');
    return active;
  }

  private acceptCounter(scope: string, counter: number): boolean {
    if (!Number.isSafeInteger(counter) || counter < 1) return false;
    const window = this.replay.get(scope) ?? { highest: 0, seen: new Set<number>() };
    if (counter <= window.highest - this.replayWindowSize || window.seen.has(counter)) return false;
    window.highest = Math.max(window.highest, counter);
    window.seen.add(counter);
    for (const value of window.seen) if (value <= window.highest - this.replayWindowSize) window.seen.delete(value);
    this.replay.set(scope, window);
    return true;
  }

  private forgetCounter(scope: string, counter: number): void { this.replay.get(scope)?.seen.delete(counter); }
  private deleteRoomState(roomId: string): void {
    for (const key of this.counters.keys()) if (key.startsWith(`${roomId}\x1f`)) this.counters.delete(key);
    for (const key of this.replay.keys()) if (key.startsWith(`${roomId}\x1f`)) this.replay.delete(key);
  }
}

function validateContext(value: SfuMediaFrameContext): void {
  validateId(value.roomId, 'sfu_media_room_invalid');
  validateId(value.publicationId, 'sfu_media_publication_invalid');
  validateId(value.senderId, 'sfu_media_sender_invalid');
  validateId(value.receiverScope, 'sfu_media_receiver_invalid');
  validateId(value.layerId, 'sfu_media_layer_invalid');
  if (!/^[A-Za-z0-9][A-Za-z0-9._+-]{0,31}$/.test(value.codec)) throw new SfuMediaCryptoError('sfu_media_codec_invalid');
  if (!Number.isSafeInteger(value.keyEpoch) || value.keyEpoch < 1) throw new SfuMediaCryptoError('sfu_media_key_epoch_invalid');
}

function validateEnvelope(expected: SfuMediaFrameContext, value: SfuEncryptedMediaFrame): void {
  if (value.version !== 1 || value.room_id !== expected.roomId || value.publication_id !== expected.publicationId
      || value.sender_id !== expected.senderId || value.receiver_scope !== expected.receiverScope
      || value.codec !== expected.codec || value.layer_id !== expected.layerId
      || value.key_epoch !== expected.keyEpoch) {
    throw new SfuMediaCryptoError('sfu_media_frame_context_mismatch');
  }
  if (!Number.isSafeInteger(value.counter) || value.counter < 1) throw new SfuMediaCryptoError('sfu_media_counter_invalid');
}

function validateId(value: string, reason: string): void {
  if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value)) throw new SfuMediaCryptoError(reason);
}

function contextKey(value: SfuMediaFrameContext): string {
  return [
    value.roomId, value.publicationId, value.senderId, value.receiverScope,
    value.codec, value.layerId, value.keyEpoch,
  ].join('\x1f');
}

function frameMetadata(context: SfuMediaFrameContext, counter: number, nonce: Uint8Array) {
  return {
    version: 1 as const, room_id: context.roomId, publication_id: context.publicationId,
    sender_id: context.senderId, receiver_scope: context.receiverScope, codec: context.codec,
    layer_id: context.layerId, key_epoch: context.keyEpoch, counter, nonce_b64: encodeB64(nonce),
  };
}

function aad(metadata: Omit<SfuEncryptedMediaFrame, 'ciphertext_b64'>): Uint8Array {
  return new TextEncoder().encode(JSON.stringify({ domain: 'ananta.sfu-media-frame.v1', ...metadata }));
}

function arrayBuffer(value: Uint8Array): ArrayBuffer {
  const copy = new Uint8Array(value.byteLength); copy.set(value); return copy.buffer;
}
