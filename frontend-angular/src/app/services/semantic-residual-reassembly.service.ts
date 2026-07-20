import { VisualResidualChunk } from './semantic-residual-chunker.service';

export interface SemanticResidualLimits {
  readonly maxChunks: number;
  readonly maxFrameBytes: number;
  readonly maxStatesPerReceiver: number;
  readonly maxBytesPerReceiver: number;
  readonly maxGlobalStates: number;
  readonly maxGlobalBytes: number;
  readonly maxTtlMs: number;
}

export const DEFAULT_SEMANTIC_RESIDUAL_LIMITS: Readonly<SemanticResidualLimits> = Object.freeze({
  maxChunks: 256, maxFrameBytes: 512 * 1024, maxStatesPerReceiver: 16,
  maxBytesPerReceiver: 8 * 1024 * 1024, maxGlobalStates: 128,
  maxGlobalBytes: 32 * 1024 * 1024, maxTtlMs: 60_000,
});

interface ReassemblyState {
  readonly key: string; readonly receiverId: string; readonly sessionId: string; readonly epoch: number;
  readonly frameDigest: string; readonly totalChunks: number; readonly totalBytes: number;
  readonly expiresAtMs: number; readonly ordinal: number; readonly parts: Map<number, Uint8Array>;
  bytes: number;
}

export type SemanticResidualResult =
  | { readonly status: 'pending' | 'duplicate' }
  | { readonly status: 'complete'; readonly bytes: Uint8Array }
  | { readonly status: 'recovery'; readonly reasonCode: string };

export class SemanticResidualReassemblyService {
  private readonly states = new Map<string, ReassemblyState>();
  private ordinal = 0;
  constructor(private readonly limits = DEFAULT_SEMANTIC_RESIDUAL_LIMITS) {}

  async accept(receiverId: string, chunk: VisualResidualChunk, nowMs = Date.now()): Promise<SemanticResidualResult> {
    this.expire(nowMs);
    const reason = this.validate(receiverId, chunk, nowMs);
    if (reason) return Object.freeze({ status: 'recovery', reasonCode: reason });
    const decoded = decodeBase64(chunk.data, chunk.chunk_bytes);
    if (!decoded) return Object.freeze({ status: 'recovery', reasonCode: 'invalid_chunk_encoding' });
    const key = [receiverId, chunk.session_id, chunk.epoch, chunk.frame_digest].join('\x1f');
    let state = this.states.get(key);
    if (!state) {
      const candidate = { receiverId, totalBytes: chunk.total_bytes };
      this.evictUntilFits(candidate);
      if (!this.fits(candidate)) return Object.freeze({ status: 'recovery', reasonCode: 'reassembly_budget_exceeded' });
      state = {
        key, receiverId, sessionId: chunk.session_id, epoch: chunk.epoch,
        frameDigest: chunk.frame_digest, totalChunks: chunk.total_chunks, totalBytes: chunk.total_bytes,
        expiresAtMs: chunk.expires_at_ms, ordinal: ++this.ordinal, parts: new Map(), bytes: 0,
      };
      this.states.set(key, state);
    } else if (state.totalChunks !== chunk.total_chunks || state.totalBytes !== chunk.total_bytes
      || state.expiresAtMs !== chunk.expires_at_ms) {
      this.drop(key);
      return Object.freeze({ status: 'recovery', reasonCode: 'chunk_metadata_conflict' });
    }
    const previous = state.parts.get(chunk.index);
    if (previous) {
      if (equal(previous, decoded)) return Object.freeze({ status: 'duplicate' });
      this.drop(key);
      return Object.freeze({ status: 'recovery', reasonCode: 'duplicate_chunk_conflict' });
    }
    state.parts.set(chunk.index, decoded);
    state.bytes += decoded.byteLength;
    if (state.bytes > state.totalBytes) {
      this.drop(key);
      return Object.freeze({ status: 'recovery', reasonCode: 'chunk_bytes_exceed_total' });
    }
    if (state.parts.size < state.totalChunks) return Object.freeze({ status: 'pending' });
    const value = new Uint8Array(state.totalBytes);
    let offset = 0;
    for (let index = 0; index < state.totalChunks; index += 1) {
      const part = state.parts.get(index);
      if (!part || offset + part.byteLength > value.byteLength) {
        this.drop(key);
        return Object.freeze({ status: 'recovery', reasonCode: 'chunk_set_incomplete' });
      }
      value.set(part, offset); offset += part.byteLength;
    }
    this.drop(key);
    if (offset !== value.byteLength || await sha256(value) !== chunk.frame_digest) {
      value.fill(0);
      return Object.freeze({ status: 'recovery', reasonCode: 'frame_digest_mismatch' });
    }
    return Object.freeze({ status: 'complete', bytes: value });
  }

  clearContext(sessionId: string, epoch?: number, receiverId?: string): void {
    for (const [key, state] of this.states) {
      if (state.sessionId !== sessionId || epoch !== undefined && state.epoch !== epoch
          || receiverId !== undefined && state.receiverId !== receiverId) continue;
      this.drop(key);
    }
  }

  expire(nowMs = Date.now()): number {
    let removed = 0;
    for (const [key, state] of this.states) if (state.expiresAtMs <= nowMs) { this.drop(key); removed += 1; }
    return removed;
  }

  snapshot(): Readonly<{ states: number; allocatedBytes: number; receivedBytes: number; timers: number }> {
    const values = Array.from(this.states.values());
    return Object.freeze({
      states: values.length,
      allocatedBytes: values.reduce((sum, state) => sum + state.totalBytes, 0),
      receivedBytes: values.reduce((sum, state) => sum + state.bytes, 0), timers: 0,
    });
  }

  private validate(receiverId: string, chunk: VisualResidualChunk, nowMs: number): string | null {
    if (!receiverId || chunk.schema !== 'ananta.visual-residual-chunk.v1' || !chunk.session_id
        || !chunk.contract_id || !chunk.lease_id || !/^[0-9a-f]{64}$/.test(chunk.frame_digest)) return 'invalid_chunk_context';
    const ints = [chunk.epoch, chunk.sequence, chunk.index, chunk.total_chunks, chunk.chunk_bytes, chunk.total_bytes, chunk.expires_at_ms];
    if (ints.some(value => !Number.isSafeInteger(value)) || chunk.epoch < 1 || chunk.sequence < 0
        || chunk.total_chunks < 1 || chunk.total_chunks > this.limits.maxChunks
        || chunk.index < 0 || chunk.index >= chunk.total_chunks || chunk.chunk_bytes < 1
        || chunk.chunk_bytes > 64 * 1024 || chunk.total_bytes < chunk.chunk_bytes
        || chunk.total_bytes > this.limits.maxFrameBytes) return 'invalid_chunk_bounds';
    if (chunk.expires_at_ms <= nowMs || chunk.expires_at_ms - nowMs > this.limits.maxTtlMs) return 'invalid_chunk_expiry';
    if (!['image/webp', 'image/avif', 'video/vp9'].includes(chunk.codec)) return 'invalid_chunk_codec';
    if (chunk.data.length > Math.ceil(chunk.chunk_bytes / 3) * 4 || chunk.data.length > 87_384) return 'invalid_chunk_size';
    return null;
  }

  private fits(candidate: { receiverId: string; totalBytes: number }): boolean {
    const values = Array.from(this.states.values());
    const receiver = values.filter(state => state.receiverId === candidate.receiverId);
    return values.length < this.limits.maxGlobalStates && receiver.length < this.limits.maxStatesPerReceiver
      && total(values) + candidate.totalBytes <= this.limits.maxGlobalBytes
      && total(receiver) + candidate.totalBytes <= this.limits.maxBytesPerReceiver;
  }

  private evictUntilFits(candidate: { receiverId: string; totalBytes: number }): void {
    while (!this.fits(candidate) && this.states.size) {
      const values = Array.from(this.states.values());
      const receiver = values.filter(state => state.receiverId === candidate.receiverId);
      const pool = receiver.length >= this.limits.maxStatesPerReceiver
        || total(receiver) + candidate.totalBytes > this.limits.maxBytesPerReceiver ? receiver : values;
      const oldest = pool.sort((a, b) => a.ordinal - b.ordinal || a.key.localeCompare(b.key))[0];
      if (!oldest) return;
      this.drop(oldest.key);
    }
  }

  private drop(key: string): void {
    const state = this.states.get(key);
    if (!state) return;
    for (const part of state.parts.values()) part.fill(0);
    this.states.delete(key);
  }
}

function total(states: readonly ReassemblyState[]): number {
  return states.reduce((sum, state) => sum + state.totalBytes, 0);
}
function decodeBase64(value: string, expected: number): Uint8Array | null {
  try {
    const binary = atob(value);
    if (binary.length !== expected) return null;
    return Uint8Array.from(binary, character => character.charCodeAt(0));
  } catch { return null; }
}
function equal(left: Uint8Array, right: Uint8Array): boolean {
  return left.byteLength === right.byteLength && left.every((value, index) => value === right[index]);
}
async function sha256(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', bytes.slice().buffer as ArrayBuffer);
  return Array.from(new Uint8Array(digest)).map(value => value.toString(16).padStart(2, '0')).join('');
}
