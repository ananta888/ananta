import { SemanticReferenceCacheService } from './semantic-reference-cache.service';
import { SemanticResidualReassemblyService, SemanticResidualLimits } from './semantic-residual-reassembly.service';
import { VisualResidualChunk } from './semantic-residual-chunker.service';

const LIMITS: SemanticResidualLimits = {
  maxChunks: 4, maxFrameBytes: 32, maxStatesPerReceiver: 2, maxBytesPerReceiver: 48,
  maxGlobalStates: 3, maxGlobalBytes: 64, maxTtlMs: 1000,
};
async function digest(bytes: Uint8Array): Promise<string> {
  const value = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(value)).map(item => item.toString(16).padStart(2, '0')).join('');
}
function b64(bytes: Uint8Array): string { return btoa(String.fromCharCode(...bytes)); }
async function chunks(): Promise<VisualResidualChunk[]> {
  const all = new Uint8Array([1, 2, 3, 4]);
  const frame = await digest(all);
  return [all.slice(0, 2), all.slice(2)].map((part, index) => ({
    schema: 'ananta.visual-residual-chunk.v1', chunk_id: `chunk-${index}`, session_id: 'session',
    contract_id: 'contract', lease_id: 'lease', epoch: 1, sequence: 1, frame_digest: frame,
    index, total_chunks: 2, chunk_bytes: part.byteLength, total_bytes: 4,
    codec: 'image/webp', expires_at_ms: 1500, data: b64(part),
  }));
}

describe('SemanticResidualReassemblyService', () => {
  it('handles reorder and duplicate idempotently and verifies the completed digest', async () => {
    const store = new SemanticResidualReassemblyService(LIMITS);
    const values = await chunks();
    expect((await store.accept('receiver', values[1], 1000)).status).toBe('pending');
    expect((await store.accept('receiver', values[1], 1000)).status).toBe('duplicate');
    const completed = await store.accept('receiver', values[0], 1000);
    expect(completed.status).toBe('complete');
    expect(completed.status === 'complete' ? [...completed.bytes] : []).toEqual([1, 2, 3, 4]);
    expect(store.snapshot()).toEqual({ states: 0, allocatedBytes: 0, receivedBytes: 0, timers: 0 });
  });

  it('rejects 10,000 hostile chunks before allocation and never creates timers', async () => {
    const store = new SemanticResidualReassemblyService(LIMITS);
    const [valid] = await chunks();
    for (let index = 0; index < 10_000; index += 1) {
      const result = await store.accept('receiver', { ...valid, total_chunks: 1_000_000, index }, 1000);
      expect(result).toMatchObject({ status: 'recovery', reasonCode: 'invalid_chunk_bounds' });
    }
    expect(store.snapshot()).toEqual({ states: 0, allocatedBytes: 0, receivedBytes: 0, timers: 0 });
  });

  it('clears reassembly and plaintext cache on epoch/revoke/fallback/session end', async () => {
    const store = new SemanticResidualReassemblyService(LIMITS);
    const [first] = await chunks();
    await store.accept('receiver', first, 1000);
    store.clearContext('session', 1, 'receiver');
    expect(store.snapshot().states).toBe(0);

    const cache = new SemanticReferenceCacheService({
      maxEntriesPerReceiver: 2, maxBytesPerReceiver: 8, maxGlobalEntries: 4,
      maxGlobalBytes: 16, maxReferenceAgeMs: 1000, maxDeltaChain: 2, maxTtlMs: 1000,
    });
    const referenceBytes = new Uint8Array([1, 2]);
    expect(await cache.put({
      receiverId: 'receiver', sessionId: 'session', epoch: 1, referenceId: 'ref', digest: 'a'.repeat(64),
      createdAtMs: 1000, expiresAtMs: 1500, deltaChainLength: 0, bytes: referenceBytes,
    }, 1000)).toBe(false);
    const referenceDigest = await digest(referenceBytes);
    expect(await cache.put({
      receiverId: 'receiver', sessionId: 'session', epoch: 1, referenceId: 'ref', digest: referenceDigest,
      createdAtMs: 1000, expiresAtMs: 1500, deltaChainLength: 0, bytes: referenceBytes,
    }, 1000)).toBe(true);
    cache.clearContext('session');
    expect(cache.snapshot()).toEqual({ entries: 0, bytes: 0, timers: 0 });
  });

  it('returns recovery for wrong hashes without retaining the large allocation', async () => {
    const store = new SemanticResidualReassemblyService(LIMITS);
    const values = await chunks();
    const forged = values.map(value => ({ ...value, frame_digest: 'f'.repeat(64) }));
    await store.accept('receiver', forged[0], 1000);
    const result = await store.accept('receiver', forged[1], 1000);
    expect(result).toMatchObject({ status: 'recovery', reasonCode: 'frame_digest_mismatch' });
    expect(store.snapshot().allocatedBytes).toBe(0);
  });

  it('enforces cache byte/count/age/delta-chain/TTL limits per receiver and globally', async () => {
    const cache = new SemanticReferenceCacheService({
      maxEntriesPerReceiver: 1, maxBytesPerReceiver: 4, maxGlobalEntries: 2,
      maxGlobalBytes: 8, maxReferenceAgeMs: 100, maxDeltaChain: 1, maxTtlMs: 200,
    });
    const bytes = new Uint8Array([1, 2]);
    const hash = await digest(bytes);
    const base = {
      sessionId: 'session', epoch: 1, digest: hash, createdAtMs: 1000,
      expiresAtMs: 1150, deltaChainLength: 0, bytes,
    };
    expect(await cache.put({ ...base, receiverId: 'a', referenceId: 'a1' }, 1000)).toBe(true);
    expect(await cache.put({ ...base, receiverId: 'a', referenceId: 'a2' }, 1000)).toBe(true);
    expect(cache.get('a', 'session', 1, 'a1', 1000)).toBeUndefined();
    expect(cache.get('a', 'session', 1, 'a2', 1101)).toBeUndefined();
    expect(await cache.put({ ...base, receiverId: 'b', referenceId: 'bad-chain', deltaChainLength: 2 }, 1000)).toBe(false);
    expect(await cache.put({ ...base, receiverId: 'b', referenceId: 'bad-ttl', expiresAtMs: 1300 }, 1000)).toBe(false);
    expect(cache.snapshot().timers).toBe(0);
  });
});
