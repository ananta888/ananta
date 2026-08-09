export const PUBLIC_PAIR_MEDIA_FRAME_FORMAT_V2 = 'ananta.public-pair.media-frame.v2' as const;

export type PairMediaCanonicalFrameType = 'audio' | 'key' | 'delta';

export interface PairMediaCodecContext {
  readonly codec: string;
  readonly kind: 'audio' | 'video';
}

export interface PairMediaPlaintextParts {
  readonly prefix: Uint8Array;
  readonly suffix: ArrayBuffer;
  readonly frameType: PairMediaCanonicalFrameType;
}

export interface PairMediaEncodedPrefix {
  readonly prefix: Uint8Array;
  readonly prefixBytes: number;
  readonly frameType: PairMediaCanonicalFrameType;
}

const VP8_DELTA_PREFIX_BYTES = 3;
const VP8_KEY_PREFIX_BYTES = 10;
const OPUS_PREFIX_BYTES = 1;

/**
 * Preserve only the codec bytes needed by the browser packetizer. They remain
 * clear but are authenticated by the frame AEAD; all remaining media bytes
 * stay confidential.
 */
export function splitPairMediaPlaintext(
  context: Readonly<PairMediaCodecContext>,
  plaintext: ArrayBuffer,
): PairMediaPlaintextParts {
  const frame = new Uint8Array(plaintext);
  if (frame.byteLength === 0) throw new Error('media_e2ee_frame_empty');
  const frameType = canonicalFrameType(context, frame[0]);
  const prefixBytes = prefixLength(frameType);
  if (frame.byteLength < prefixBytes) throw new Error('media_e2ee_codec_frame_invalid');
  if (
    frameType === 'key'
    && (frame[3] !== 0x9d || frame[4] !== 0x01 || frame[5] !== 0x2a)
  ) throw new Error('media_e2ee_codec_frame_invalid');
  return Object.freeze({
    prefix: Uint8Array.from(frame.subarray(0, prefixBytes)),
    suffix: frame.slice(prefixBytes).buffer,
    frameType,
  });
}

/** Locate the authenticated clear prefix without trusting receiver metadata. */
export function readPairMediaEncodedPrefix(
  context: Readonly<PairMediaCodecContext>,
  encoded: Uint8Array,
): PairMediaEncodedPrefix {
  if (encoded.byteLength === 0) throw new Error('media_e2ee_frame_size_invalid');
  const frameType = canonicalFrameType(context, encoded[0]);
  const prefixBytes = prefixLength(frameType);
  if (encoded.byteLength < prefixBytes) throw new Error('media_e2ee_frame_size_invalid');
  return Object.freeze({
    prefix: Uint8Array.from(encoded.subarray(0, prefixBytes)),
    prefixBytes,
    frameType,
  });
}

export function restorePairMediaPlaintext(
  context: Readonly<PairMediaCodecContext>,
  prefix: Uint8Array,
  suffix: ArrayBuffer,
): ArrayBuffer {
  const plaintext = new Uint8Array(prefix.byteLength + suffix.byteLength);
  plaintext.set(prefix);
  plaintext.set(new Uint8Array(suffix), prefix.byteLength);
  // Re-parse only after authentication so malformed clear codec bytes cannot
  // be mistaken for a valid decoded frame.
  splitPairMediaPlaintext(context, plaintext.buffer);
  return plaintext.buffer;
}

function canonicalFrameType(
  context: Readonly<PairMediaCodecContext>,
  firstByte: number,
): PairMediaCanonicalFrameType {
  if (context.kind === 'audio' && context.codec === 'opus') return 'audio';
  if (context.kind === 'video' && context.codec === 'vp8') {
    return (firstByte & 0x01) === 0 ? 'key' : 'delta';
  }
  throw new Error('media_e2ee_codec_unsupported');
}

function prefixLength(frameType: PairMediaCanonicalFrameType): number {
  switch (frameType) {
    case 'audio': return OPUS_PREFIX_BYTES;
    case 'key': return VP8_KEY_PREFIX_BYTES;
    case 'delta': return VP8_DELTA_PREFIX_BYTES;
  }
}
