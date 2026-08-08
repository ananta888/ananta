import { Injectable } from '@angular/core';

const PUBLIC_PERMISSION_KEYS = Object.freeze([
  'chat',
  'view_tui',
  'remote_cursor',
  'artifact_share',
  'remote_control',
] as const);

/**
 * Validates the immutable security/capability floor of public Pair sessions.
 *
 * The public rendezvous has no legacy or application-payload relay capability.
 * Treating missing fields as defaults would therefore turn a malformed server
 * response into a security downgrade.
 */
@Injectable({ providedIn: 'root' })
export class PairPublicSessionContractPolicy {
  assertValid(value: unknown): void {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      throw new Error('public_pair_session_contract_invalid');
    }
    const session = value as Record<string, unknown>;
    if (
      session['security_mode'] !== 'strict_e2ee'
      || session['security_contract_version'] !== 1
    ) throw new Error('public_pair_security_contract_invalid');
    if (session['mode'] !== 'p2p' || session['transport'] !== 'webrtc') {
      throw new Error('public_pair_transport_contract_invalid');
    }
    if (session['identity_binding_version'] !== 1 || session['permissions_version'] !== 1) {
      throw new Error('public_pair_capability_contract_invalid');
    }
    const permissions = exactPublicPermissions(session['permissions']);
    if (permissions['remote_control']) {
      throw new Error('public_pair_capability_contract_invalid');
    }
    if (
      session['allowed_permissions'] !== undefined
      && !samePermissions(permissions, exactPublicPermissions(session['allowed_permissions']))
    ) throw new Error('public_pair_capability_contract_invalid');
  }
}

function exactPublicPermissions(value: unknown): Readonly<Record<string, boolean>> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('public_pair_capability_contract_invalid');
  }
  const permissions = value as Record<string, unknown>;
  const keys = Object.keys(permissions);
  if (
    keys.length !== PUBLIC_PERMISSION_KEYS.length
    || keys.some(key => !(PUBLIC_PERMISSION_KEYS as readonly string[]).includes(key))
    || PUBLIC_PERMISSION_KEYS.some(key => typeof permissions[key] !== 'boolean')
  ) throw new Error('public_pair_capability_contract_invalid');
  return permissions as Readonly<Record<string, boolean>>;
}

function samePermissions(
  left: Readonly<Record<string, boolean>>,
  right: Readonly<Record<string, boolean>>,
): boolean {
  return PUBLIC_PERMISSION_KEYS.every(key => left[key] === right[key]);
}
