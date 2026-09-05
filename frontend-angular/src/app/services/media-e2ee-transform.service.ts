import { Injectable } from '@angular/core';

export interface MediaE2eeFrameContext {
  readonly publicationId: string;
  readonly senderId: string;
  readonly recipientScopeId: string;
  readonly codec: string;
  readonly kind: 'audio' | 'video';
  readonly keyEpoch: number;
}

export interface MediaEncodedFrame {
  data: ArrayBuffer;
  readonly timestamp?: number;
  readonly type?: string;
}

interface MediaReplayWindow {
  highest: bigint;
  bitmap: bigint;
  readonly pending: Set<bigint>;
}

const MAGIC = Uint8Array.of(0x41, 0x4e, 0x4d, 0x45); // ANME
const HEADER_BYTES = 32;
const MAX_FRAME_BYTES = 8 * 1024 * 1024;
const MAX_COUNTER = 0xffff_ffff_ffff_ffffn;
const REPLAY_WINDOW = 2_048;
const REPLAY_MASK = (1n << BigInt(REPLAY_WINDOW)) - 1n;

/**
 * Codec-frame AEAD for browsers exposing RTCRtpScriptTransform or encoded
 * streams.  The private ECDH key never enters this service: callers supply a
 * non-extractable purpose-derived AES-GCM key.
 */
@Injectable({ providedIn: 'root' })
export class MediaE2eeTransformService {
  private readonly sendCounters = new Map<string, bigint>();
  private readonly noncePrefixes = new Map<string, Uint8Array>();
  private readonly received = new Map<string, MediaReplayWindow>();

  supportsEncodedTransform(): boolean {
    const scope = globalThis as typeof globalThis & {
      RTCRtpScriptTransform?: unknown;
      RTCRtpSender?: { prototype?: { createEncodedStreams?: unknown } };
    };
    return Boolean(scope.RTCRtpScriptTransform || scope.RTCRtpSender?.prototype?.createEncodedStreams);
  }

  encryptStream(
    context: Readonly<MediaE2eeFrameContext>,
    key: CryptoKey,
  ): TransformStream<MediaEncodedFrame, MediaEncodedFrame> {
    validateContext(context); validateKey(key);
    return new TransformStream({ transform: async (frame, controller) => {
      frame.data = await this.seal(context, key, frame.data, frame.type ?? 'delta');
      controller.enqueue(frame);
    } });
  }

  decryptStream(
    context: Readonly<MediaE2eeFrameContext>,
    key: CryptoKey,
  ): TransformStream<MediaEncodedFrame, MediaEncodedFrame> {
    validateContext(context); validateKey(key);
    return new TransformStream({ transform: async (frame, controller) => {
      frame.data = await this.open(context, key, frame.data, frame.type ?? 'delta');
      controller.enqueue(frame);
    } });
  }

  async seal(
    context: Readonly<MediaE2eeFrameContext>,
    key: CryptoKey,
    plaintext: ArrayBuffer,
    frameType: string,
  ): Promise<ArrayBuffer> {
    validateContext(context); validateKey(key); validatePayload(plaintext); validateFrameType(frameType);
    const binding = contextKey(context);
    const counter = (this.sendCounters.get(binding) ?? 0n) + 1n;
    if (counter > MAX_COUNTER) throw new Error('media_e2ee_counter_exhausted');
    this.sendCounters.set(binding, counter);
    const prefix = this.noncePrefixes.get(binding) ?? crypto.getRandomValues(new Uint8Array(4));
    this.noncePrefixes.set(binding, prefix);
    const nonce = new Uint8Array(new ArrayBuffer(12)); nonce.set(prefix); writeUint64(nonce, 4, counter);
    const additionalData = arrayBufferBackedBytes(aad(context, counter, frameType));
    const header = headerFor(context.keyEpoch, counter, nonce);
    const ciphertext = await crypto.subtle.encrypt(
      { name: 'AES-GCM', iv: nonce, additionalData, tagLength: 128 },
      key,
      plaintext,
    );
    const output = new Uint8Array(HEADER_BYTES + ciphertext.byteLength);
    output.set(header); output.set(new Uint8Array(ciphertext), HEADER_BYTES);
    return output.buffer;
  }

  async open(
    context: Readonly<MediaE2eeFrameContext>,
    key: CryptoKey,
    encoded: ArrayBuffer,
    frameType: string,
  ): Promise<ArrayBuffer> {
    validateContext(context); validateKey(key); validateFrameType(frameType);
    if (encoded.byteLength <= HEADER_BYTES || encoded.byteLength > MAX_FRAME_BYTES + HEADER_BYTES + 16) {
      throw new Error('media_e2ee_frame_size_invalid');
    }
    const frame = new Uint8Array(encoded);
    if (!MAGIC.every((byte, index) => frame[index] === byte) || frame[4] !== 1) {
      throw new Error('media_e2ee_header_invalid');
    }
    const view = new DataView(encoded);
    const epoch = view.getUint32(8);
    const counter = view.getBigUint64(12);
    if (epoch !== context.keyEpoch || counter < 1n) throw new Error('media_e2ee_epoch_stale');
    const nonce = arrayBufferBackedBytes(frame.slice(20, 32));
    if (readUint64(nonce, 4) !== counter) throw new Error('media_e2ee_nonce_binding_invalid');
    const binding = contextKey(context);
    const replay = this.received.get(binding) ?? {
      highest: 0n,
      bitmap: 0n,
      pending: new Set<bigint>(),
    };
    if (outsideReplayWindow(counter, replay.highest)) throw new Error('media_e2ee_replay_too_old');
    if (replayContains(replay, counter) || replay.pending.has(counter)) throw new Error('media_e2ee_replay');
    if (replay.pending.size >= REPLAY_WINDOW) throw new Error('media_e2ee_replay_budget_exceeded');
    replay.pending.add(counter);
    this.received.set(binding, replay);
    let plaintext: ArrayBuffer;
    try {
      const additionalData = arrayBufferBackedBytes(aad(context, counter, frameType));
      plaintext = await crypto.subtle.decrypt(
        { name: 'AES-GCM', iv: nonce, additionalData, tagLength: 128 },
        key,
        frame.slice(HEADER_BYTES),
      );
    } catch (error) {
      replay.pending.delete(counter);
      if (replay.highest === 0n && replay.bitmap === 0n && replay.pending.size === 0) {
        this.received.delete(binding);
      }
      if (error instanceof Error && error.message.startsWith('media_e2ee_')) throw error;
      throw new Error('media_e2ee_authentication_failed');
    }
    replay.pending.delete(counter);
    if (outsideReplayWindow(counter, replay.highest)) throw new Error('media_e2ee_replay_too_old');
    commitReplayCounter(replay, counter);
    return plaintext;
  }

  forgetEpoch(context: Readonly<MediaE2eeFrameContext>): void {
    const binding = contextKey(context);
    this.sendCounters.delete(binding); this.noncePrefixes.delete(binding); this.received.delete(binding);
  }
}

function outsideReplayWindow(counter: bigint, highest: bigint): boolean {
  return highest > 0n && counter + BigInt(REPLAY_WINDOW) <= highest;
}

function replayContains(window: MediaReplayWindow, counter: bigint): boolean {
  if (counter > window.highest || outsideReplayWindow(counter, window.highest)) return false;
  return (window.bitmap & (1n << (window.highest - counter))) !== 0n;
}

function commitReplayCounter(window: MediaReplayWindow, counter: bigint): void {
  if (counter > window.highest) {
    const shift = counter - window.highest;
    window.bitmap = shift >= BigInt(REPLAY_WINDOW)
      ? 0n
      : (window.bitmap << shift) & REPLAY_MASK;
    window.highest = counter;
  }
  window.bitmap |= 1n << (window.highest - counter);
}

function arrayBufferBackedBytes(value: Uint8Array): Uint8Array<ArrayBuffer> {
  return new Uint8Array(value);
}

function headerFor(epoch: number, counter: bigint, nonce: Uint8Array): Uint8Array {
  const header = new Uint8Array(HEADER_BYTES); header.set(MAGIC); header[4] = 1;
  const view = new DataView(header.buffer); view.setUint32(8, epoch); view.setBigUint64(12, counter);
  header.set(nonce, 20); return header;
}

function aad(context: Readonly<MediaE2eeFrameContext>, counter: bigint, frameType: string): Uint8Array {
  return new TextEncoder().encode(JSON.stringify({
    domain: 'ananta.media-frame-e2ee.v1', publication_id: context.publicationId,
    sender_id: context.senderId, recipient_scope_id: context.recipientScopeId,
    codec: context.codec.toLowerCase(), kind: context.kind, key_epoch: context.keyEpoch,
    counter: counter.toString(), frame_type: frameType,
  }));
}

function contextKey(value: Readonly<MediaE2eeFrameContext>): string {
  validateContext(value);
  return [value.publicationId, value.senderId, value.recipientScopeId, value.codec.toLowerCase(), value.kind, value.keyEpoch].join('\x1f');
}

function validateContext(value: Readonly<MediaE2eeFrameContext>): void {
  const identifiers = [value.publicationId, value.senderId, value.recipientScopeId];
  if (identifiers.some(item => !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(item))
      || !/^[A-Za-z0-9][A-Za-z0-9._+-]{0,31}$/.test(value.codec)
      || !['audio', 'video'].includes(value.kind)
      || !Number.isSafeInteger(value.keyEpoch) || value.keyEpoch < 1) throw new Error('media_e2ee_context_invalid');
}

function validateKey(key: CryptoKey): void {
  if (key.type !== 'secret' || key.algorithm.name !== 'AES-GCM' || key.extractable
      || !key.usages.includes('encrypt') || !key.usages.includes('decrypt')) throw new Error('media_e2ee_key_invalid');
}

function validatePayload(value: ArrayBuffer): void {
  if (!value || !Number.isSafeInteger(value.byteLength) || value.byteLength < 1 || value.byteLength > MAX_FRAME_BYTES) {
    throw new Error('media_e2ee_frame_size_invalid');
  }
}

function validateFrameType(value: string): void {
  if (!/^[a-z][a-z0-9_-]{0,31}$/.test(value)) throw new Error('media_e2ee_frame_type_invalid');
}

function writeUint64(target: Uint8Array, offset: number, value: bigint): void {
  new DataView(target.buffer, target.byteOffset, target.byteLength).setBigUint64(offset, value);
}
function readUint64(target: Uint8Array, offset: number): bigint {
  return new DataView(target.buffer, target.byteOffset, target.byteLength).getBigUint64(offset);
}
