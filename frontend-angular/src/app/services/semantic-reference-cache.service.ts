
export interface SemanticReferenceCacheLimits {
  readonly maxEntriesPerReceiver: number;
  readonly maxBytesPerReceiver: number;
  readonly maxGlobalEntries: number;
  readonly maxGlobalBytes: number;
  readonly maxReferenceAgeMs: number;
  readonly maxDeltaChain: number;
  readonly maxTtlMs: number;
}

export const DEFAULT_SEMANTIC_REFERENCE_CACHE_LIMITS: Readonly<SemanticReferenceCacheLimits> = Object.freeze({
  maxEntriesPerReceiver: 16,
  maxBytesPerReceiver: 8 * 1024 * 1024,
  maxGlobalEntries: 128,
  maxGlobalBytes: 32 * 1024 * 1024,
  maxReferenceAgeMs: 20_000,
  maxDeltaChain: 24,
  maxTtlMs: 60_000,
});

export interface SemanticReferenceEntry {
  readonly receiverId: string;
  readonly sessionId: string;
  readonly epoch: number;
  readonly referenceId: string;
  readonly digest: string;
  readonly createdAtMs: number;
  readonly expiresAtMs: number;
  readonly deltaChainLength: number;
  readonly bytes: Uint8Array;
}

interface StoredReference extends SemanticReferenceEntry { readonly ordinal: number }

export class SemanticReferenceCacheService {
  private readonly entries = new Map<string, StoredReference>();
  private ordinal = 0;

  constructor(private readonly limits = DEFAULT_SEMANTIC_REFERENCE_CACHE_LIMITS) {}

  async put(entry: SemanticReferenceEntry, nowMs = Date.now()): Promise<boolean> {
    this.expire(nowMs);
    if (!validEntry(entry, nowMs, this.limits)) return false;
    if (await sha256(entry.bytes) !== entry.digest) return false;
    const key = this.key(entry.receiverId, entry.sessionId, entry.epoch, entry.referenceId);
    const previous = this.entries.get(key);
    if (previous) {
      if (previous.digest === entry.digest && equal(previous.bytes, entry.bytes)) return true;
      this.entries.delete(key);
    }
    const stored: StoredReference = Object.freeze({ ...entry, bytes: entry.bytes.slice(), ordinal: ++this.ordinal });
    this.evictUntilFits(stored);
    if (!this.fits(stored)) return false;
    this.entries.set(key, stored);
    return true;
  }

  get(receiverId: string, sessionId: string, epoch: number, referenceId: string, nowMs = Date.now()): SemanticReferenceEntry | undefined {
    this.expire(nowMs);
    const entry = this.entries.get(this.key(receiverId, sessionId, epoch, referenceId));
    if (!entry || nowMs - entry.createdAtMs > this.limits.maxReferenceAgeMs) {
      if (entry) this.entries.delete(this.key(receiverId, sessionId, epoch, referenceId));
      return undefined;
    }
    return Object.freeze({ ...entry, bytes: entry.bytes.slice() });
  }

  clearContext(sessionId: string, epoch?: number, receiverId?: string): void {
    for (const [key, entry] of this.entries) {
      if (entry.sessionId !== sessionId || epoch !== undefined && entry.epoch !== epoch
          || receiverId !== undefined && entry.receiverId !== receiverId) continue;
      entry.bytes.fill(0);
      this.entries.delete(key);
    }
  }

  clearAll(): void {
    for (const entry of this.entries.values()) entry.bytes.fill(0);
    this.entries.clear();
  }

  expire(nowMs = Date.now()): number {
    let removed = 0;
    for (const [key, entry] of this.entries) {
      if (entry.expiresAtMs > nowMs && nowMs - entry.createdAtMs <= this.limits.maxReferenceAgeMs) continue;
      entry.bytes.fill(0);
      this.entries.delete(key);
      removed += 1;
    }
    return removed;
  }

  snapshot(): Readonly<{ entries: number; bytes: number; timers: number }> {
    return Object.freeze({
      entries: this.entries.size,
      bytes: Array.from(this.entries.values()).reduce((sum, entry) => sum + entry.bytes.byteLength, 0),
      timers: 0,
    });
  }

  private fits(candidate: StoredReference): boolean {
    const values = Array.from(this.entries.values());
    const receiver = values.filter(entry => entry.receiverId === candidate.receiverId && entry.sessionId === candidate.sessionId);
    return values.length < this.limits.maxGlobalEntries
      && receiver.length < this.limits.maxEntriesPerReceiver
      && sumBytes(values) + candidate.bytes.byteLength <= this.limits.maxGlobalBytes
      && sumBytes(receiver) + candidate.bytes.byteLength <= this.limits.maxBytesPerReceiver;
  }

  private evictUntilFits(candidate: StoredReference): void {
    while (!this.fits(candidate) && this.entries.size) {
      const values = Array.from(this.entries.entries());
      const receiver = values.filter(([, entry]) => entry.receiverId === candidate.receiverId && entry.sessionId === candidate.sessionId);
      const pool = receiver.length >= this.limits.maxEntriesPerReceiver
        || sumBytes(receiver.map(([, entry]) => entry)) + candidate.bytes.byteLength > this.limits.maxBytesPerReceiver
        ? receiver : values;
      const oldest = pool.sort((left, right) => left[1].ordinal - right[1].ordinal || left[0].localeCompare(right[0]))[0];
      if (!oldest) return;
      oldest[1].bytes.fill(0);
      this.entries.delete(oldest[0]);
    }
  }

  private key(receiverId: string, sessionId: string, epoch: number, referenceId: string): string {
    return [receiverId, sessionId, epoch, referenceId].join('\x1f');
  }
}

function validEntry(entry: SemanticReferenceEntry, nowMs: number, limits: SemanticReferenceCacheLimits): boolean {
  return Boolean(entry.receiverId && entry.sessionId && entry.referenceId && /^[0-9a-f]{64}$/.test(entry.digest))
    && Number.isSafeInteger(entry.epoch) && entry.epoch >= 1
    && Number.isSafeInteger(entry.createdAtMs) && entry.createdAtMs <= nowMs
    && Number.isSafeInteger(entry.expiresAtMs) && entry.expiresAtMs > nowMs
    && entry.expiresAtMs - nowMs <= limits.maxTtlMs
    && Number.isSafeInteger(entry.deltaChainLength) && entry.deltaChainLength >= 0
    && entry.deltaChainLength <= limits.maxDeltaChain
    && entry.bytes instanceof Uint8Array && entry.bytes.byteLength > 0
    && entry.bytes.byteLength <= limits.maxBytesPerReceiver;
}

function sumBytes(entries: readonly StoredReference[]): number {
  return entries.reduce((sum, entry) => sum + entry.bytes.byteLength, 0);
}
function equal(left: Uint8Array, right: Uint8Array): boolean {
  return left.byteLength === right.byteLength && left.every((value, index) => value === right[index]);
}
async function sha256(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', bytes.slice().buffer as ArrayBuffer);
  return Array.from(new Uint8Array(digest)).map(value => value.toString(16).padStart(2, '0')).join('');
}
