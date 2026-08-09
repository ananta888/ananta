import { canonicalSecurityJson } from './webrtc-secure-envelope';

export interface PairMediaE2eeFrameContext {
  readonly sessionId: string;
  readonly mediaContractDigest: string;
  readonly connectionId: string;
  readonly senderId: string;
  readonly recipientId: string;
  readonly slot: string;
  readonly codec: string;
  readonly kind: 'audio' | 'video';
  readonly keyEpoch: number;
  readonly contractExpiresAtMs: number;
}

const MAGIC = Uint8Array.of(0x41, 0x4e, 0x4d, 0x46); // ANMF
const VERSION = 1;
const HEADER_BYTES = 20;
const TAG_BYTES = 16;
const MAX_FRAME_BYTES = 8 * 1024 * 1024;
const MAX_COUNTER = 0xffff_ffff_ffff_ffffn;
const REPLAY_WINDOW = 2_048n;
const DIGEST_RE = /^[a-f0-9]{64}$/;
const IDENTIFIER_RE = /^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$/;
const CODEC_RE = /^[a-z0-9][a-z0-9._+-]{0,31}$/;

type FrameType = 'audio' | 'key' | 'delta' | 'empty';

/**
 * Stateful AEAD codec for exactly one direction/slot/connection key.
 *
 * The 96-bit nonce is epoch || uint64(counter). The caller must derive a
 * fresh key for every connectionId/direction/slot. A 128-bit salt from each
 * peer is included in that connectionId, so a counter reset cannot reuse a
 * nonce under the same key after reconnect or reload.
 */
export class PairMediaE2eeFrameCipher {
  private sendCounter = 0n;
  private highestReceived = 0n;
  private readonly received = new Set<bigint>();
  private readonly pendingReceived = new Set<bigint>();

  constructor(
    readonly context: Readonly<PairMediaE2eeFrameContext>,
    private readonly now: () => number = () => Date.now(),
  ) {
    validateContext(context);
  }

  async seal(key: CryptoKey, plaintext: ArrayBuffer, frameType: string): Promise<ArrayBuffer> {
    this.assertContractLive();
    validateKey(key);
    validatePayload(plaintext);
    const normalizedType = normalizeFrameType(frameType, this.context.kind);
    const counter = this.sendCounter + 1n;
    if (counter > MAX_COUNTER) throw new Error('media_e2ee_counter_exhausted');
    // Reserve before the first await. Concurrent callers may leave a gap on
    // encryption failure, but can never reuse a nonce under this key.
    this.sendCounter = counter;
    const header = encodeHeader(this.context.keyEpoch, counter, normalizedType);
    const nonce = nonceFor(this.context.keyEpoch, counter);
    const ciphertext = await crypto.subtle.encrypt(
      {
        name: 'AES-GCM',
        iv: arrayBuffer(nonce),
        additionalData: arrayBuffer(aad(this.context, header)),
        tagLength: 128,
      },
      key,
      plaintext,
    );
    this.assertContractLive();
    const output = new Uint8Array(HEADER_BYTES + ciphertext.byteLength);
    output.set(header);
    output.set(new Uint8Array(ciphertext), HEADER_BYTES);
    return output.buffer;
  }

  async open(
    key: CryptoKey,
    encoded: ArrayBuffer,
    expectedFrameType?: string,
  ): Promise<ArrayBuffer> {
    this.assertContractLive();
    validateKey(key);
    if (
      !isArrayBuffer(encoded)
      || encoded.byteLength < HEADER_BYTES + TAG_BYTES
      || encoded.byteLength > HEADER_BYTES + TAG_BYTES + MAX_FRAME_BYTES
    ) throw new Error('media_e2ee_frame_size_invalid');
    const frame = new Uint8Array(encoded);
    const parsed = decodeHeader(frame);
    if (parsed.epoch !== this.context.keyEpoch) throw new Error('media_e2ee_epoch_stale');
    if (expectedFrameType !== undefined) {
      const expected = normalizeFrameType(expectedFrameType, this.context.kind);
      if (parsed.frameType !== expected) throw new Error('media_e2ee_frame_type_mismatch');
    }
    this.assertReplayAdmissible(parsed.counter);
    // TransformStream serializes frames in production, but the primitive is
    // independently safe when multiple callers race the same ciphertext.
    this.pendingReceived.add(parsed.counter);
    let plaintext: ArrayBuffer;
    try {
      plaintext = await crypto.subtle.decrypt(
        {
          name: 'AES-GCM',
          iv: arrayBuffer(nonceFor(parsed.epoch, parsed.counter)),
          additionalData: arrayBuffer(aad(this.context, frame.slice(0, HEADER_BYTES))),
          tagLength: 128,
        },
        key,
        frame.slice(HEADER_BYTES),
      );
    } catch (error) {
      if (error instanceof Error && error.message.startsWith('media_e2ee_')) throw error;
      throw new Error('media_e2ee_authentication_failed');
    } finally {
      this.pendingReceived.delete(parsed.counter);
    }
    this.assertContractLive();
    this.claimCounter(parsed.counter);
    return plaintext;
  }

  clear(): void {
    this.sendCounter = 0n;
    this.highestReceived = 0n;
    this.received.clear();
    this.pendingReceived.clear();
  }

  private assertReplayAdmissible(counter: bigint): void {
    if (counter < 1n) throw new Error('media_e2ee_counter_invalid');
    if (this.received.has(counter) || this.pendingReceived.has(counter)) {
      throw new Error('media_e2ee_replay');
    }
    const floor = replayFloor(this.highestReceived);
    if (counter < floor) throw new Error('media_e2ee_replay_too_old');
  }

  private claimCounter(counter: bigint): void {
    if (counter > this.highestReceived) this.highestReceived = counter;
    this.received.add(counter);
    const floor = replayFloor(this.highestReceived);
    for (const seen of this.received) {
      if (seen < floor) this.received.delete(seen);
    }
  }

  private assertContractLive(): void {
    if (this.context.contractExpiresAtMs <= this.now()) {
      throw new Error('media_e2ee_contract_expired');
    }
  }
}

function replayFloor(highest: bigint): bigint {
  return highest >= REPLAY_WINDOW ? highest - REPLAY_WINDOW + 1n : 1n;
}

function encodeHeader(epoch: number, counter: bigint, frameType: FrameType): Uint8Array {
  const header = new Uint8Array(HEADER_BYTES);
  header.set(MAGIC, 0);
  header[4] = VERSION;
  header[5] = frameTypeCode(frameType);
  const view = new DataView(header.buffer);
  view.setUint32(8, epoch);
  view.setBigUint64(12, counter);
  return header;
}

function decodeHeader(frame: Uint8Array): { epoch: number; counter: bigint; frameType: FrameType } {
  if (
    frame.byteLength < HEADER_BYTES
    || !MAGIC.every((byte, index) => frame[index] === byte)
    || frame[4] !== VERSION
    || frame[6] !== 0
    || frame[7] !== 0
  ) throw new Error('media_e2ee_header_invalid');
  const frameType = frameTypeFromCode(frame[5]);
  const view = new DataView(frame.buffer, frame.byteOffset, HEADER_BYTES);
  const epoch = view.getUint32(8);
  const counter = view.getBigUint64(12);
  if (epoch < 1 || counter < 1n) throw new Error('media_e2ee_header_invalid');
  return { epoch, counter, frameType };
}

function nonceFor(epoch: number, counter: bigint): Uint8Array {
  const nonce = new Uint8Array(12);
  const view = new DataView(nonce.buffer);
  view.setUint32(0, epoch);
  view.setBigUint64(4, counter);
  return nonce;
}

function aad(context: Readonly<PairMediaE2eeFrameContext>, header: Uint8Array): Uint8Array {
  return new TextEncoder().encode(canonicalSecurityJson({
    domain: 'ananta.public-pair.media-frame.v1',
    session_id: context.sessionId,
    media_contract_digest: context.mediaContractDigest,
    connection_id: context.connectionId,
    sender_id: context.senderId,
    recipient_id: context.recipientId,
    slot: context.slot,
    kind: context.kind,
    codec: context.codec,
    key_epoch: context.keyEpoch,
    contract_expires_at_ms: context.contractExpiresAtMs,
    header_hex: [...header].map(byte => byte.toString(16).padStart(2, '0')).join(''),
  }));
}

function frameTypeCode(value: FrameType): number {
  switch (value) {
    case 'audio': return 0;
    case 'key': return 1;
    case 'delta': return 2;
    case 'empty': return 3;
  }
}

function frameTypeFromCode(value: number): FrameType {
  switch (value) {
    case 0: return 'audio';
    case 1: return 'key';
    case 2: return 'delta';
    case 3: return 'empty';
    default: throw new Error('media_e2ee_header_invalid');
  }
}

function normalizeFrameType(value: string, kind: 'audio' | 'video'): FrameType {
  if (kind === 'audio') return 'audio';
  if (value === 'key' || value === 'delta' || value === 'empty') return value;
  throw new Error('media_e2ee_frame_type_invalid');
}

function validateContext(value: Readonly<PairMediaE2eeFrameContext>): void {
  if (
    !IDENTIFIER_RE.test(value.sessionId)
    || !DIGEST_RE.test(value.mediaContractDigest)
    || !DIGEST_RE.test(value.connectionId)
    || !IDENTIFIER_RE.test(value.senderId)
    || !IDENTIFIER_RE.test(value.recipientId)
    || value.senderId === value.recipientId
    || !IDENTIFIER_RE.test(value.slot)
    || !CODEC_RE.test(value.codec)
    || (value.kind !== 'audio' && value.kind !== 'video')
    || !Number.isSafeInteger(value.keyEpoch)
    || value.keyEpoch < 1
    || value.keyEpoch > 0xffff_ffff
    || !Number.isSafeInteger(value.contractExpiresAtMs)
    || value.contractExpiresAtMs < 1
  ) throw new Error('media_e2ee_context_invalid');
}

function validateKey(key: CryptoKey): void {
  if (
    !key
    || key.type !== 'secret'
    || key.algorithm.name !== 'AES-GCM'
    || key.extractable
    || !key.usages.includes('encrypt')
    || !key.usages.includes('decrypt')
  ) throw new Error('media_e2ee_key_invalid');
}

function validatePayload(value: ArrayBuffer): void {
  if (
    !isArrayBuffer(value)
    || value.byteLength > MAX_FRAME_BYTES
  ) throw new Error('media_e2ee_frame_size_invalid');
}

function isArrayBuffer(value: unknown): value is ArrayBuffer {
  // Media frames may cross a Worker/Window realm. `instanceof` is therefore
  // not a valid brand check even though WebCrypto accepts the ArrayBuffer.
  return Object.prototype.toString.call(value) === '[object ArrayBuffer]';
}

function arrayBuffer(value: Uint8Array): ArrayBuffer {
  return Uint8Array.from(value).buffer;
}
