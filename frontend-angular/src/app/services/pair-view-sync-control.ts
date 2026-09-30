import { ControlMessage } from './pair-view-sync.types';
import { isControlMessage } from './pair-view-sync.validators';

/**
 * Control-channel rules of the pair view sync: the session-scoped control
 * grant (T12 default-deny) and the closed snapshot-request schema.
 */

export type ControlMessageOutcome = 'rejected' | 'denied' | 'granted' | 'revoked' | 'ignored';

/**
 * RAM-only control grant of the local partner. A grant is accepted only
 * after an explicit local request; it never follows from view_tui or cursor
 * permissions and is never persisted.
 */
export class PairControlGrantState {
  private grantToken: string | null = null;
  private requestPending = false;

  get hasGrant(): boolean { return this.grantToken !== null; }

  markRequested(): void { this.requestPending = true; }

  cancelRequest(): void { this.requestPending = false; }

  reset(): void {
    this.grantToken = null;
    this.requestPending = false;
  }

  /** Applies one authenticated control message; the caller records the outcome. */
  accept(raw: unknown, sessionId: string, remoteControlPermitted: boolean): ControlMessageOutcome {
    if (!isControlMessage(raw)) return 'rejected';
    const msg = raw as ControlMessage;
    if (msg.sessionId !== sessionId) return 'rejected';
    // T12: control default-deny. The permission must be granted AND
    // the grant token must match a token previously issued. The
    // grant is session-scoped, never persisted.
    if (!remoteControlPermitted) return 'denied';
    if (msg.kind === 'request') {
      // No approval UI exists in compact sharing. Fail closed instead of
      // silently turning a remote request into a control grant.
      return 'denied';
    }
    if (msg.kind === 'grant') {
      // Partner side: only accept a grant after an explicit local request.
      if (!this.requestPending || !msg.grantToken) return 'denied';
      this.requestPending = false;
      this.grantToken = msg.grantToken;
      return 'granted';
    }
    if (msg.kind === 'revoke') {
      this.requestPending = false;
      this.grantToken = null;
      return 'revoked';
    }
    // request_follow / request_unfollow are advisory only: a peer cannot
    // change local follow consent. Compact sharing performs no navigation.
    return 'ignored';
  }
}

/** Exact `{ sessionId }` snapshot request for the bound session. */
export function isSnapshotRequestFor(plaintext: string, sessionId: string): boolean {
  let request: unknown;
  try { request = JSON.parse(plaintext); } catch { return false; }
  return !!request && typeof request === 'object' && !Array.isArray(request)
    && Object.keys(request).length === 1
    && (request as Record<string, unknown>)['sessionId'] === sessionId;
}
