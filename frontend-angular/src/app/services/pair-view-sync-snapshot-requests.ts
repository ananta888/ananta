const SNAPSHOT_REQUEST_COOLDOWN_MS = 500;
const SNAPSHOT_REQUEST_TIMEOUT_MS = 5000;

/** Exact session, epoch and authenticated sender a snapshot request targets. */
export function snapshotRequestKey(sessionId: string, securityEpoch: number, senderUserId: string): string {
  return `${sessionId}:${securityEpoch}:${senderUserId}`;
}

/**
 * Outstanding snapshot requests of the pair view sync. A request per
 * (session, epoch, sender) is coalesced during a cooldown and stays
 * outstanding until answered by a snapshot, released on a failed send, or
 * expired.
 */
export class PairSnapshotRequestTracker {
  private readonly pending = new Map<string, { requestedAt: number; expiresAt: number }>();

  /** Reserves a request for key; false while an earlier one is cooling down or outstanding. */
  reserve(key: string, now: number): boolean {
    for (const [pendingKey, pending] of this.pending) {
      if (pending.expiresAt <= now) this.pending.delete(pendingKey);
    }
    const pending = this.pending.get(key);
    if (pending && now - pending.requestedAt < SNAPSHOT_REQUEST_COOLDOWN_MS) return false;
    if (pending && pending.expiresAt > now) return false;
    this.pending.set(key, {
      requestedAt: now,
      expiresAt: now + SNAPSHOT_REQUEST_TIMEOUT_MS,
    });
    return true;
  }

  release(key: string): void {
    this.pending.delete(key);
  }

  clear(): void {
    this.pending.clear();
  }
}
