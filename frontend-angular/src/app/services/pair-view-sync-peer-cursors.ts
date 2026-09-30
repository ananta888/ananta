import { BehaviorSubject, Observable } from 'rxjs';

import { CursorPos } from './pair-view-sync.types';
import { isCursorPos } from './pair-view-sync.validators';

/** Out-of-band cursor message: NOT applied to local state. */
export interface CursorMessage {
  sessionId: string;
  senderUserId: string;
  userLabel: string;
  cursor: CursorPos;
  /** Local clock at the sender, used by the receiver to time out. */
  sentAt: number;
}

/** Public peer-cursor entry: a cursor with a freshness timestamp. */
export interface PeerCursor {
  userId: string;
  userLabel: string;
  cursor: CursorPos;
  /** Local clock at the receiver (refreshed on every update). */
  lastSeenAt: number;
}

const PEER_CURSOR_TIMEOUT_MS = 5000;
const PEER_CURSOR_REAP_INTERVAL_MS = 1000;

/**
 * Closed-schema check of a decrypted cursor payload. Returns null for any
 * malformed message; session/sender binding and permissions are checked by
 * the caller, which owns that context.
 */
export function parseCursorMessage(plain: string): CursorMessage | null {
  let parsed: unknown;
  try { parsed = JSON.parse(plain); } catch { return null; }
  if (!parsed || typeof parsed !== 'object') return null;
  const obj = parsed as { sessionId?: unknown; senderUserId?: unknown; userLabel?: unknown; cursor?: unknown; sentAt?: unknown };
  if (
    Object.keys(obj).length !== 5 ||
    !['sessionId', 'senderUserId', 'userLabel', 'cursor', 'sentAt'].every(key => key in obj) ||
    typeof obj.sessionId !== 'string' ||
    typeof obj.senderUserId !== 'string' ||
    typeof obj.userLabel !== 'string' || obj.userLabel.length < 1 || obj.userLabel.length > 32 ||
    typeof obj.sentAt !== 'number' || !Number.isSafeInteger(obj.sentAt) || obj.sentAt < 0 ||
    !isCursorPos(obj.cursor)
  ) return null;
  return obj as CursorMessage;
}

/**
 * Peer cursor presence read model: Map<userId, PeerCursor>, refreshed by
 * validated inbound cursors and reaped when stale. Exposed as an Observable
 * for the remote-cursor overlay. Presence is never written to local view
 * state (that would loop own cursor -> view.cursor -> delta -> own cursor).
 */
export class PairPeerCursorPresence {
  private readonly cursors = new Map<string, PeerCursor>();
  private readonly cursors$ = new BehaviorSubject<ReadonlyMap<string, PeerCursor>>(new Map());
  readonly peerCursors$: Observable<ReadonlyMap<string, PeerCursor>> = this.cursors$.asObservable();
  /** Cursor-overlay rendering is on by default. */
  private overlayEnabled = true;
  private reapHandle: ReturnType<typeof setInterval> | null = null;

  get cursorOverlayEnabled(): boolean { return this.overlayEnabled; }

  setCursorOverlayEnabled(enabled: boolean): void {
    if (this.overlayEnabled === enabled) return;
    this.overlayEnabled = enabled;
    // Re-emit current state so the overlay can show/hide in one tick
    this.publish();
  }

  upsert(message: CursorMessage): void {
    this.cursors.set(message.senderUserId, {
      userId: message.senderUserId,
      userLabel: message.userLabel,
      cursor: message.cursor,
      lastSeenAt: Date.now(),
    });
    this.publish();
  }

  /** Drops all entries without notifying subscribers. */
  clearSilently(): void {
    this.cursors.clear();
  }

  /** Drops all entries and publishes the empty read model. */
  clearAndPublish(): void {
    this.cursors.clear();
    this.publish();
  }

  startReaping(): void {
    if (this.reapHandle !== null) return;
    this.reapHandle = setInterval(() => this.reap(), PEER_CURSOR_REAP_INTERVAL_MS);
  }

  stopReaping(): void {
    if (this.reapHandle === null) return;
    clearInterval(this.reapHandle);
    this.reapHandle = null;
  }

  private reap(): void {
    if (this.cursors.size === 0) return;
    const cutoff = Date.now() - PEER_CURSOR_TIMEOUT_MS;
    let changed = false;
    for (const [uid, p] of this.cursors) {
      if (p.lastSeenAt < cutoff) {
        this.cursors.delete(uid);
        changed = true;
      }
    }
    if (changed) this.publish();
  }

  private publish(): void {
    this.cursors$.next(new Map(this.cursors));
  }
}
