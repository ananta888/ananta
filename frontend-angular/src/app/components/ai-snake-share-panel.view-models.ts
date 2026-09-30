import { PairSecurityBootstrapState } from '../services/pair-view-security-bootstrap.service';
import { RemoteViewProjection } from '../services/pair-view-sync.types';

/**
 * Pure presentation helpers of the AI Snake share panel. They carry no Angular
 * state so the component only coordinates services and view state.
 */

export interface RemoteViewEntry {
  senderId: string;
  label: string;
  route: string;
  surface: string;
}

export function pairSecurityLabel(state: PairSecurityBootstrapState): string {
  const labels: Record<PairSecurityBootstrapState['status'], string> = {
    idle: 'E2EE: inaktiv',
    legacy: 'Legacy-Modus: nicht Ende-zu-Ende verschlüsselt',
    waiting_for_peer: 'E2EE: wartet auf authentifizierten Peer',
    confirming: 'E2EE: Schlüsselbestätigung läuft',
    ready: 'E2EE: Peer und Schlüssel bestätigt',
    fingerprint_changed: 'E2EE blockiert: Peer-Fingerprint hat sich geändert',
    failed: `E2EE blockiert: ${'reasonCode' in state ? state.reasonCode : 'unbekannter Fehler'}`,
  };
  return labels[state.status];
}

export function pairSecurityFingerprint(state: PairSecurityBootstrapState): string {
  return 'fingerprint' in state ? state.fingerprint ?? '' : '';
}

export function permissionEntries(perms: Record<string, boolean>): Array<{ key: string; val: boolean }> {
  return Object.entries(perms ?? {}).map(([key, val]) => ({ key, val }));
}

export function toRemoteViewEntries(views: ReadonlyMap<string, RemoteViewProjection>): RemoteViewEntry[] {
  return Array.from(views.values()).map(projection => ({
    senderId: projection.senderUserId,
    label: compactPeerViewLabel(projection.senderUserId),
    route: projection.state.route,
    surface: projection.state.activeSurface,
  }));
}

/** Stable, non-identifying short label derived from a peer id. */
export function compactPeerViewLabel(peerId: string): string {
  let hash = 0;
  for (let index = 0; index < peerId.length; index += 1) hash = (Math.imul(hash, 31) + peerId.charCodeAt(index)) >>> 0;
  return `Peer-${hash.toString(16).padStart(8, '0').slice(0, 6)}`;
}
