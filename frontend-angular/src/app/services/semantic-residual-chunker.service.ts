import { Injectable } from '@angular/core';
import { SemanticFrameEnvelope, SemanticStandardCodec } from './semantic-frame-encoder.service';

export interface VisualResidualChunk {
  readonly schema: 'ananta.visual-residual-chunk.v1';
  readonly chunk_id: string;
  readonly session_id: string;
  readonly contract_id: string;
  readonly lease_id: string;
  readonly epoch: number;
  readonly sequence: number;
  readonly frame_digest: string;
  readonly index: number;
  readonly total_chunks: number;
  readonly chunk_bytes: number;
  readonly total_bytes: number;
  readonly codec: SemanticStandardCodec;
  readonly expires_at_ms: number;
  readonly data: string;
}

@Injectable({ providedIn: 'root' })
export class SemanticResidualChunkerService {
  async chunk(
    frame: SemanticFrameEnvelope,
    bytes: Uint8Array,
    chunkBytes = 64 * 1024,
  ): Promise<readonly VisualResidualChunk[]> {
    if (!(bytes instanceof Uint8Array) || bytes.byteLength !== frame.total_bytes || bytes.byteLength > 512 * 1024
        || !Number.isSafeInteger(chunkBytes) || chunkBytes < 1 || chunkBytes > 64 * 1024
        || await sha256(bytes) !== frame.encoded_digest) throw new Error('invalid_frame_payload');
    const total = Math.ceil(bytes.byteLength / chunkBytes);
    if (total > 256) throw new Error('chunk_count_exceeded');
    const chunks: VisualResidualChunk[] = [];
    for (let index = 0; index < total; index += 1) {
      const part = bytes.slice(index * chunkBytes, Math.min(bytes.byteLength, (index + 1) * chunkBytes));
      chunks.push(Object.freeze({
        schema: 'ananta.visual-residual-chunk.v1',
        chunk_id: `${frame.frame_id}-${index}`,
        session_id: frame.session_id, contract_id: frame.contract_id, lease_id: frame.lease_id,
        epoch: frame.epoch, sequence: frame.sequence, frame_digest: frame.encoded_digest,
        index, total_chunks: total, chunk_bytes: part.byteLength, total_bytes: bytes.byteLength,
        codec: frame.algorithm.codec, expires_at_ms: frame.expires_at_ms, data: base64(part),
      }));
    }
    return Object.freeze(chunks);
  }
}

function base64(bytes: Uint8Array): string {
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

async function sha256(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', bytes.slice().buffer as ArrayBuffer);
  return Array.from(new Uint8Array(digest)).map(value => value.toString(16).padStart(2, '0')).join('');
}
