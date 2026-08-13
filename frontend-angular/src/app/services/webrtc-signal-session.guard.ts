import { Injectable } from '@angular/core';

const STORAGE_PREFIX = 'ananta.webrtc.signal-session-blocked.v1.';
const EPOCH_STORAGE_PREFIX = `${STORAGE_PREFIX}epoch.`;
export const SIGNAL_SESSION_RECREATION_REQUIRED = 'public_signaling_session_recreation_required';

/** Remembers public sessions whose last server write has an ambiguous outcome. */
@Injectable({ providedIn: 'root' })
export class WebrtcSignalSessionGuard {
  private readonly blockedSessions = new Set<string>();

  block(sessionId: string, securityEpoch?: number): void {
    const key = storageKey(sessionId, securityEpoch);
    this.blockedSessions.add(key);
    try { sessionStorage.setItem(key, '1'); } catch { /* memory remains authoritative */ }
  }

  assertReusable(sessionId: string, securityEpoch?: number): void {
    if (this.isBlocked(sessionId, securityEpoch)) {
      throw new Error(SIGNAL_SESSION_RECREATION_REQUIRED);
    }
  }

  isBlocked(sessionId: string, securityEpoch?: number): boolean {
    const key = storageKey(sessionId, securityEpoch);
    const legacyKey = storageKey(sessionId);
    if (
      this.blockedSessions.has(key)
      || (securityEpoch !== undefined && this.blockedSessions.has(legacyKey))
    ) return true;
    try {
      if (
        sessionStorage.getItem(key) === '1'
        || (securityEpoch !== undefined && sessionStorage.getItem(legacyKey) === '1')
      ) {
        this.blockedSessions.add(key);
        return true;
      }
    } catch { /* no persistent storage is available */ }
    return false;
  }

  /** Clears one exact signaling generation after a server-proven terminal response. */
  clear(sessionId: string, securityEpoch?: number): void {
    const key = storageKey(sessionId, securityEpoch);
    this.blockedSessions.delete(key);
    try { sessionStorage.removeItem(key); } catch { /* memory is already clear */ }
  }

  /** Clears every epoch only after the owning control plane retires the session. */
  clearSession(sessionId: string): void {
    const legacyKey = storageKey(sessionId);
    for (const key of this.blockedSessions) {
      if (key === legacyKey || epochKeyBelongsToSession(key, sessionId)) {
        this.blockedSessions.delete(key);
      }
    }
    try {
      const keys: string[] = [];
      for (let index = 0; index < sessionStorage.length; index += 1) {
        const key = sessionStorage.key(index);
        if (key === legacyKey || (key && epochKeyBelongsToSession(key, sessionId))) {
          keys.push(key);
        }
      }
      for (const key of keys) sessionStorage.removeItem(key);
    } catch { /* memory is already clear */ }
  }
}

function storageKey(sessionId: string, securityEpoch?: number): string {
  return securityEpoch === undefined
    ? `${STORAGE_PREFIX}${encodeURIComponent(sessionId)}`
    : `${EPOCH_STORAGE_PREFIX}${encodeURIComponent(JSON.stringify([
      sessionId,
      validSecurityEpoch(securityEpoch),
    ]))}`;
}

function epochKeyBelongsToSession(key: string, sessionId: string): boolean {
  if (!key.startsWith(EPOCH_STORAGE_PREFIX)) return false;
  try {
    const decoded = JSON.parse(
      decodeURIComponent(key.slice(EPOCH_STORAGE_PREFIX.length)),
    ) as unknown;
    return Array.isArray(decoded) && decoded.length === 2 && decoded[0] === sessionId;
  } catch {
    return false;
  }
}

function validSecurityEpoch(epoch: number): number {
  if (!Number.isSafeInteger(epoch) || epoch < 1) {
    throw new Error('webrtc_signal_epoch_invalid');
  }
  return epoch;
}
