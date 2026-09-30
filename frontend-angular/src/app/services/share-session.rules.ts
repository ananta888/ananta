/**
 * Fail-closed rules for listed Pair sessions: the authoritative local role
 * and runtime state of a catalog row (identity v2 never derives them from
 * client-side identifiers) and participant presence labels.
 */
import type { ShareParticipant, ShareSession } from './share-session.types';

export function listedSessionRole(session: ShareSession): 'owner' | 'participant' {
  const advertisedRole = session.local_role;
  if (
    advertisedRole !== undefined
    && advertisedRole !== 'owner'
    && advertisedRole !== 'participant'
  ) throw new Error('pair_session_local_role_invalid');

  // Identity-v2 catalog rows are selected from an authenticated membership
  // on the server.  Never reconstruct that operational role from client-side
  // identifiers when the authoritative projection is absent.
  if (session.identity_binding_version === 2 && !advertisedRole) {
    throw new Error('pair_session_local_role_missing');
  }

  const localPeerId = String(session.local_peer_id || '').trim();
  const ownerPeerId = String(session.owner_peer_id || '').trim();
  const derivedRole = localPeerId && ownerPeerId
    ? localPeerId === ownerPeerId ? 'owner' : 'participant'
    : null;
  if (advertisedRole && derivedRole && advertisedRole !== derivedRole) {
    throw new Error('pair_session_local_role_mismatch');
  }
  if (advertisedRole) return advertisedRole;
  if (derivedRole) return derivedRole;

  // Legacy/Hub list responses use the account identity as their peer id.
  const ownerUserId = String(session.owner_user_id || '').trim();
  if (localPeerId && ownerUserId) {
    return localPeerId === ownerUserId ? 'owner' : 'participant';
  }
  throw new Error('pair_session_local_role_missing');
}

export function listedSessionRuntimeState(session: ShareSession): 'active' | 'parked' | null {
  const state = session.local_runtime_state;
  if (session.identity_binding_version !== 2) return null;
  if (state !== 'active' && state !== 'parked') {
    throw new Error('pair_session_runtime_state_invalid');
  }
  return state;
}

/** Human-readable presence label of a participant (German UI copy). */
export function shareParticipantStatus(p: ShareParticipant): string {
  if (p.revoked_at) return 'gesperrt';
  if (!p.last_seen_at) return 'offline';
  const secs = Math.floor(Date.now() / 1000 - p.last_seen_at);
  return secs < 12 ? 'online' : `offline ${secs}s`;
}
