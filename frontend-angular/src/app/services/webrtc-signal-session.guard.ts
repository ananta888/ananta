import { Injectable } from '@angular/core';

const STORAGE_PREFIX = 'ananta.webrtc.signal-session-blocked.v1.';
export const SIGNAL_SESSION_RECREATION_REQUIRED = 'public_signaling_session_recreation_required';

/** Remembers public sessions whose last server write has an ambiguous outcome. */
@Injectable({ providedIn: 'root' })
export class WebrtcSignalSessionGuard {
  private readonly blockedSessions = new Set<string>();

  block(sessionId: string): void {
    this.blockedSessions.add(sessionId);
    try { sessionStorage.setItem(storageKey(sessionId), '1'); } catch { /* memory remains authoritative */ }
  }

  assertReusable(sessionId: string): void {
    if (this.isBlocked(sessionId)) throw new Error(SIGNAL_SESSION_RECREATION_REQUIRED);
  }

  isBlocked(sessionId: string): boolean {
    if (this.blockedSessions.has(sessionId)) return true;
    try {
      if (sessionStorage.getItem(storageKey(sessionId)) === '1') {
        this.blockedSessions.add(sessionId);
        return true;
      }
    } catch { /* no persistent storage is available */ }
    return false;
  }
}

function storageKey(sessionId: string): string {
  return `${STORAGE_PREFIX}${encodeURIComponent(sessionId)}`;
}
