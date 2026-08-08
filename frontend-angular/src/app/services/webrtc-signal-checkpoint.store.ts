import { Injectable } from '@angular/core';

export interface WebrtcSignalCheckpointContext {
  readonly sessionId: string;
  readonly localPeerId: string;
  readonly remotePeerId: string;
}

export interface WebrtcSignalCheckpoint {
  readonly cursor: string;
  readonly seenSignalIds: readonly string[];
}

const STORAGE_PREFIX = 'ananta.webrtc.signal-checkpoint.v1.';
const EMPTY_CHECKPOINT: WebrtcSignalCheckpoint = Object.freeze({
  cursor: '',
  seenSignalIds: Object.freeze([]),
});

/** Persists only ACK metadata, scoped to the immutable session/peer tuple. */
@Injectable({ providedIn: 'root' })
export class WebrtcSignalCheckpointStore {
  private readonly memory = new Map<string, WebrtcSignalCheckpoint>();

  load(context: WebrtcSignalCheckpointContext): WebrtcSignalCheckpoint {
    const key = checkpointKey(context);
    const cached = this.memory.get(key);
    if (cached) return cached;
    try {
      const parsed = parseCheckpoint(sessionStorage.getItem(key));
      if (parsed) {
        this.memory.set(key, parsed);
        return parsed;
      }
    } catch { /* sessionStorage may be unavailable in restricted browsers */ }
    return EMPTY_CHECKPOINT;
  }

  save(context: WebrtcSignalCheckpointContext, checkpoint: WebrtcSignalCheckpoint): void {
    const key = checkpointKey(context);
    const normalized = normalizeCheckpoint(checkpoint);
    this.memory.set(key, normalized);
    try { sessionStorage.setItem(key, JSON.stringify(normalized)); } catch { /* memory remains authoritative */ }
  }

  clearAll(): void {
    this.memory.clear();
    try {
      const keys: string[] = [];
      for (let index = 0; index < sessionStorage.length; index += 1) {
        const key = sessionStorage.key(index);
        if (key?.startsWith(STORAGE_PREFIX)) keys.push(key);
      }
      for (const key of keys) sessionStorage.removeItem(key);
    } catch { /* no persistent storage to clear */ }
  }
}

function checkpointKey(context: WebrtcSignalCheckpointContext): string {
  return `${STORAGE_PREFIX}${encodeURIComponent(context.sessionId)}.${encodeURIComponent(context.localPeerId)}.${encodeURIComponent(context.remotePeerId)}`;
}

function parseCheckpoint(raw: string | null): WebrtcSignalCheckpoint | null {
  if (!raw) return null;
  try { return normalizeCheckpoint(JSON.parse(raw) as WebrtcSignalCheckpoint); } catch { return null; }
}

function normalizeCheckpoint(value: WebrtcSignalCheckpoint): WebrtcSignalCheckpoint {
  const cursor = typeof value?.cursor === 'string' && value.cursor.length <= 128 ? value.cursor : '';
  const seenSignalIds = Array.isArray(value?.seenSignalIds)
    ? value.seenSignalIds.filter(id => (
      typeof id === 'string'
      && /^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$/.test(id)
    )).slice(-256)
    : [];
  return Object.freeze({ cursor, seenSignalIds: Object.freeze([...new Set(seenSignalIds)]) });
}
