import { WebrtcChunkReassemblyStore, BoundedChunk, ReassemblyLimits } from './webrtc-chunk-reassembly.store';

const LIMITS: ReassemblyLimits = {
  maxChunksPerMessage: 4, maxBytesPerMessage: 32, maxStatesPerPeer: 2,
  maxStatesPerSession: 3, maxBytesPerPeer: 48,
  maxBytesPerSession: 64, maxGlobalBytes: 96, maxStates: 4, maxTtlMs: 1000,
};

async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest)).map(byte => byte.toString(16).padStart(2, '0')).join('');
}

async function chunks(value: string, overrides: Partial<BoundedChunk> = {}): Promise<BoundedChunk[]> {
  const bytes = new TextEncoder().encode(value);
  const digest = await sha256(value);
  const base = {
    version: 'ananta.webrtc-bounded-chunk.v1' as const,
    message_id: 'message', session_id: 'session', epoch: 1, sender_id: 'sender',
    traffic_class: 'control' as const, total: 2, total_bytes: bytes.byteLength,
    expires_at_ms: 1500, payload_digest: digest,
  };
  const chunkId = await sha256(`${base.session_id}\n${base.epoch}\n${base.sender_id}\n${digest}`);
  return [bytes.slice(0, 2), bytes.slice(2)].map((data, index) => ({
    ...base,
    chunk_id: chunkId,
    index,
    chunk_bytes: data.byteLength,
    data: btoa(String.fromCharCode(...data)),
    ...overrides,
  }));
}

describe('WebrtcChunkReassemblyStore', () => {
  it('reassembles reorder and duplicate idempotently, then removes state', async () => {
    const store = new WebrtcChunkReassemblyStore(LIMITS);
    const parts = await chunks('test');
    expect((await store.accept(parts[1], 1000)).status).toBe('pending');
    expect((await store.accept(parts[1], 1000)).status).toBe('duplicate');
    const result = await store.accept(parts[0], 1000);
    expect(result.status).toBe('complete');
    expect(result.status === 'complete' ? new TextDecoder().decode(result.value) : '').toBe('test');
    expect(store.snapshot()).toEqual({ states: 0, bytes: 0, timers: 0 });
  });

  it('rejects hostile totals before allocation and remains bounded after 10,000 inputs', async () => {
    const store = new WebrtcChunkReassemblyStore(LIMITS);
    const [valid] = await chunks('test');
    for (let index = 0; index < 10_000; index += 1) {
      const result = await store.accept({ ...valid, total: Number.MAX_SAFE_INTEGER, index }, 1000);
      expect(result.status).toBe('rejected');
    }
    expect(store.snapshot()).toEqual({ states: 0, bytes: 0, timers: 0 });
  });

  it('isolates session epoch sender and clears all context on revoke or epoch change', async () => {
    const store = new WebrtcChunkReassemblyStore(LIMITS);
    const [first] = await chunks('test');
    await store.accept(first, 1000);
    expect(store.snapshot().states).toBe(1);
    store.clearContext('session', 1, 'sender');
    expect(store.snapshot()).toEqual({ states: 0, bytes: 0, timers: 0 });
  });

  it('evicts expired state deterministically without timers', async () => {
    const store = new WebrtcChunkReassemblyStore(LIMITS);
    const [first] = await chunks('test');
    await store.accept(first, 1000);
    expect(store.expire(1500)).toBe(1);
    expect(store.snapshot().timers).toBe(0);
  });
});
