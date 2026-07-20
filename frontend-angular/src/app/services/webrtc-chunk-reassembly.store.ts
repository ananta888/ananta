import { Inject, Injectable, InjectionToken, Optional } from '@angular/core';

export type SemanticTrafficClass =
  | 'control' | 'transcript' | 'audio_recovery'
  | 'visual_semantic' | 'evidence_bulk' | 'diagnostic';

export interface BoundedChunk {
  version: 'ananta.webrtc-bounded-chunk.v1';
  chunk_id: string;
  message_id: string;
  session_id: string;
  epoch: number;
  sender_id: string;
  traffic_class: SemanticTrafficClass;
  index: number;
  total: number;
  chunk_bytes: number;
  total_bytes: number;
  expires_at_ms: number;
  payload_digest: string;
  data: string;
}

export interface ReassemblyLimits {
  maxChunksPerMessage: number;
  maxBytesPerMessage: number;
  maxStatesPerPeer: number;
  maxStatesPerSession: number;
  maxBytesPerPeer: number;
  maxBytesPerSession: number;
  maxGlobalBytes: number;
  maxStates: number;
  maxTtlMs: number;
}

export const WEBRTC_REASSEMBLY_LIMITS = new InjectionToken<ReassemblyLimits>(
  'WEBRTC_REASSEMBLY_LIMITS',
);

export const DEFAULT_REASSEMBLY_LIMITS: Readonly<ReassemblyLimits> = Object.freeze({
  maxChunksPerMessage: 256,
  maxBytesPerMessage: 1_500_000,
  maxStatesPerPeer: 64,
  maxStatesPerSession: 256,
  maxBytesPerPeer: 4 * 1_048_576,
  maxBytesPerSession: 16 * 1_048_576,
  maxGlobalBytes: 32 * 1_048_576,
  maxStates: 512,
  maxTtlMs: 300_000,
});

interface State {
  readonly key: string;
  readonly sessionId: string;
  readonly epoch: number;
  readonly senderId: string;
  readonly trafficClass: SemanticTrafficClass;
  readonly chunkId: string;
  readonly messageId: string;
  readonly total: number;
  readonly totalBytes: number;
  readonly digest: string;
  readonly expiresAt: number;
  readonly ordinal: number;
  readonly parts: Map<number, Uint8Array>;
  bytes: number;
}

export type ReassemblyResult =
  | { status: 'pending' | 'duplicate' }
  | { status: 'complete'; value: Uint8Array }
  | { status: 'rejected'; reason: string };

@Injectable({ providedIn: 'root' })
export class WebrtcChunkReassemblyStore {
  private readonly instanceId = crypto.randomUUID?.() ?? `instance-${Date.now()}`;
  private readonly states = new Map<string, State>();
  private ordinal = 0;

  private readonly limits: Readonly<ReassemblyLimits>;

  constructor(
    @Optional() @Inject(WEBRTC_REASSEMBLY_LIMITS) limits?: ReassemblyLimits,
  ) {
    this.limits = Object.freeze({ ...(limits ?? DEFAULT_REASSEMBLY_LIMITS) });
  }

  async accept(chunk: BoundedChunk, nowMs = Date.now()): Promise<ReassemblyResult> {
    this.expire(nowMs);
    const reason = this.validateShape(chunk, nowMs);
    if (reason) return { status: 'rejected', reason };
    const decoded = this.decodeBase64(chunk.data, chunk.chunk_bytes);
    if (!decoded) return { status: 'rejected', reason: 'invalid_chunk_base64' };
    const expectedChunkId = await this.sha256(
      `${chunk.session_id}\n${chunk.epoch}\n${chunk.sender_id}\n${chunk.payload_digest}`
    );
    if (expectedChunkId !== chunk.chunk_id) return { status: 'rejected', reason: 'chunk_context_mismatch' };
    const key = this.key(chunk);
    let state = this.states.get(key);
    if (!state) {
      this.evictUntilFits(chunk);
      if (!this.canAllocate(chunk)) return { status: 'rejected', reason: 'reassembly_budget_exceeded' };
      state = {
        key, sessionId: chunk.session_id, epoch: chunk.epoch, senderId: chunk.sender_id,
        trafficClass: chunk.traffic_class, chunkId: chunk.chunk_id, messageId: chunk.message_id,
        total: chunk.total,
        totalBytes: chunk.total_bytes, digest: chunk.payload_digest,
        expiresAt: chunk.expires_at_ms, ordinal: ++this.ordinal, parts: new Map(), bytes: 0,
      };
      this.states.set(key, state);
    } else if (
      state.messageId !== chunk.message_id || state.total !== chunk.total || state.totalBytes !== chunk.total_bytes
      || state.digest !== chunk.payload_digest || state.expiresAt !== chunk.expires_at_ms
    ) {
      this.states.delete(key);
      return { status: 'rejected', reason: 'chunk_metadata_conflict' };
    }
    const existing = state.parts.get(chunk.index);
    if (existing) {
      if (this.equalBytes(existing, decoded)) return { status: 'duplicate' };
      this.states.delete(key);
      return { status: 'rejected', reason: 'chunk_duplicate_conflict' };
    }
    state.parts.set(chunk.index, decoded);
    state.bytes += chunk.chunk_bytes;
    if (state.bytes > state.totalBytes) {
      this.states.delete(key);
      return { status: 'rejected', reason: 'chunk_bytes_exceed_total' };
    }
    if (state.parts.size !== state.total) return { status: 'pending' };
    const value = new Uint8Array(state.totalBytes);
    let offset = 0;
    for (let index = 0; index < state.total; index += 1) {
      const part = state.parts.get(index);
      if (!part || offset + part.byteLength > value.byteLength) {
        this.states.delete(key);
        return { status: 'rejected', reason: 'chunk_set_incomplete' };
      }
      value.set(part, offset);
      offset += part.byteLength;
    }
    this.states.delete(key);
    if (offset !== state.totalBytes || await this.sha256Bytes(value) !== state.digest) {
      return { status: 'rejected', reason: 'payload_digest_mismatch' };
    }
    return { status: 'complete', value };
  }

  clearContext(sessionId: string, epoch?: number, senderId?: string): void {
    for (const [key, state] of this.states) {
      if (state.sessionId !== sessionId) continue;
      if (epoch !== undefined && state.epoch !== epoch) continue;
      if (senderId !== undefined && state.senderId !== senderId) continue;
      this.states.delete(key);
    }
  }

  expire(nowMs = Date.now()): number {
    let removed = 0;
    for (const [key, state] of this.states) {
      if (state.expiresAt > nowMs) continue;
      this.states.delete(key);
      removed += 1;
    }
    return removed;
  }

  snapshot(): Readonly<{ states: number; bytes: number; timers: number }> {
    return Object.freeze({
      states: this.states.size,
      bytes: Array.from(this.states.values()).reduce((sum, state) => sum + state.totalBytes, 0),
      timers: 0,
    });
  }

  private validateShape(chunk: BoundedChunk, nowMs: number): string {
    const integers = [chunk.epoch, chunk.index, chunk.total, chunk.chunk_bytes, chunk.total_bytes, chunk.expires_at_ms];
    if (integers.some(value => !Number.isSafeInteger(value))) return 'invalid_chunk_integer';
    if (chunk.version !== 'ananta.webrtc-bounded-chunk.v1') return 'unsupported_chunk_version';
    if (!chunk.session_id || !chunk.sender_id || !chunk.message_id || !/^[0-9a-f]{64}$/.test(chunk.payload_digest)) {
      return 'invalid_chunk_identity';
    }
    if (chunk.epoch < 1 || chunk.total < 1 || chunk.total > this.limits.maxChunksPerMessage) return 'invalid_chunk_total';
    if (chunk.index < 0 || chunk.index >= chunk.total) return 'invalid_chunk_index';
    if (chunk.chunk_bytes < 0 || chunk.total_bytes < 0 || chunk.chunk_bytes > chunk.total_bytes) return 'invalid_chunk_bytes';
    const classLimit = TRAFFIC_CLASS_LIMITS[chunk.traffic_class];
    if (classLimit === undefined) return 'unknown_traffic_class';
    if (chunk.total_bytes > Math.min(this.limits.maxBytesPerMessage, classLimit)) return 'message_budget_exceeded';
    const maxEncodedLength = Math.ceil(chunk.chunk_bytes / 3) * 4;
    if (chunk.data.length > maxEncodedLength || chunk.data.length > 349_528) return 'chunk_size_mismatch';
    if (chunk.expires_at_ms <= nowMs || chunk.expires_at_ms - nowMs > this.limits.maxTtlMs) return 'invalid_chunk_expiry';
    return '';
  }

  private key(chunk: BoundedChunk): string {
    return [this.instanceId, chunk.session_id, chunk.epoch, chunk.sender_id, chunk.traffic_class, chunk.chunk_id].join('\x1f');
  }

  private canAllocate(chunk: BoundedChunk): boolean {
    const rows = Array.from(this.states.values());
    const globalBytes = rows.reduce((sum, state) => sum + state.totalBytes, 0);
    const sessionBytes = rows.filter(state => state.sessionId === chunk.session_id)
      .reduce((sum, state) => sum + state.totalBytes, 0);
    const peerBytes = rows.filter(state => state.sessionId === chunk.session_id && state.senderId === chunk.sender_id)
      .reduce((sum, state) => sum + state.totalBytes, 0);
    const sessionStates = rows.filter(state => state.sessionId === chunk.session_id).length;
    const peerStates = rows.filter(
      state => state.sessionId === chunk.session_id && state.senderId === chunk.sender_id,
    ).length;
    return this.states.size < this.limits.maxStates
      && sessionStates < this.limits.maxStatesPerSession
      && peerStates < this.limits.maxStatesPerPeer
      && globalBytes + chunk.total_bytes <= this.limits.maxGlobalBytes
      && sessionBytes + chunk.total_bytes <= this.limits.maxBytesPerSession
      && peerBytes + chunk.total_bytes <= this.limits.maxBytesPerPeer;
  }

  private evictUntilFits(chunk: BoundedChunk): void {
    const ordered = (predicate: (state: State) => boolean): State | undefined =>
      Array.from(this.states.values())
        .filter(predicate)
        .sort((left, right) => left.ordinal - right.ordinal || left.key.localeCompare(right.key))[0];
    const dropUntil = (fits: () => boolean, predicate: (state: State) => boolean): void => {
      while (!fits()) {
        const oldest = ordered(predicate);
        if (!oldest) return;
        this.states.delete(oldest.key);
      }
    };
    const matching = (state: State): boolean => state.sessionId === chunk.session_id;
    const peer = (state: State): boolean => matching(state) && state.senderId === chunk.sender_id;
    const count = (predicate: (state: State) => boolean): number =>
      Array.from(this.states.values()).filter(predicate).length;
    const bytes = (predicate: (state: State) => boolean): number =>
      Array.from(this.states.values()).filter(predicate)
        .reduce((sum, state) => sum + state.totalBytes, 0);
    dropUntil(
      () => count(peer) < this.limits.maxStatesPerPeer
        && bytes(peer) + chunk.total_bytes <= this.limits.maxBytesPerPeer,
      peer,
    );
    dropUntil(
      () => count(matching) < this.limits.maxStatesPerSession
        && bytes(matching) + chunk.total_bytes <= this.limits.maxBytesPerSession,
      matching,
    );
    dropUntil(
      () => this.states.size < this.limits.maxStates
        && bytes(() => true) + chunk.total_bytes <= this.limits.maxGlobalBytes,
      () => true,
    );
  }

  private async sha256(value: string): Promise<string> {
    return this.sha256Bytes(new TextEncoder().encode(value));
  }

  private async sha256Bytes(value: Uint8Array): Promise<string> {
    const owned = Uint8Array.from(value);
    const digest = await crypto.subtle.digest('SHA-256', owned.buffer);
    return Array.from(new Uint8Array(digest)).map(byte => byte.toString(16).padStart(2, '0')).join('');
  }

  private decodeBase64(value: string, declaredBytes: number): Uint8Array | null {
    if (!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value)) return null;
    try {
      const decoded = atob(value);
      if (decoded.length !== declaredBytes) return null;
      return Uint8Array.from(decoded, character => character.charCodeAt(0));
    } catch {
      return null;
    }
  }

  private equalBytes(left: Uint8Array, right: Uint8Array): boolean {
    return left.byteLength === right.byteLength && left.every((value, index) => value === right[index]);
  }
}

const TRAFFIC_CLASS_LIMITS: Readonly<Record<SemanticTrafficClass, number>> = Object.freeze({
  control: 25_944,
  transcript: 91_480,
  audio_recovery: 353_624,
  visual_semantic: 703_148,
  evidence_bulk: 1_402_200,
  diagnostic: 15_020,
});
