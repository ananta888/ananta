/**
 * Pure fail-closed validators and scope helpers of the Pair session control
 * plane: bound response/peer-id checks, runtime activation and TURN
 * credential validation, catalog batching/fingerprints and membership
 * attempt scopes. No state or I/O; PairSessionControlPlaneService owns both.
 */
import { HttpContext } from '@angular/common/http';
import {
  MAX_PUBLIC_PAIR_CATALOG_MEMBERSHIPS,
  PairMembershipAttemptKind,
  PairMembershipAttemptScope,
  PairMembershipAuthorityScope,
} from './pair-membership-capability.store';
import { PairControlPlaneKind, PairSessionBinding } from './pair-session-binding.store';
import { SUPPRESS_GLOBAL_ERROR_NOTIFICATION } from './error-request-context';
import {
  MAX_PUBLIC_TURN_CREDENTIAL_TTL_SECONDS,
  PublicTurnCredentialCacheKey,
  PublicTurnCredentials,
} from './public-turn-credential-cache.service';
import type {
  ApiEnvelope,
  BoundSessionShape,
  PairCatalogMembershipProof,
  PairSessionRuntimeActivation,
  PairSessionRuntimeEnvelope,
  PreparedSession,
  RequestAuthority,
  TurnCredentialPayload,
} from './pair-session-control-plane.types';

const DEVICE_PEER_ID_RE = /^peer:[a-f0-9]{64}$/;
const ACCOUNT_PEER_ID_RE = /^oidc:[a-f0-9]{64}$/;
const DEFINITIVE_PRE_COMMIT_ERRORS = new Set([
  'device_key_must_be_distinct',
  'device_identity_invalid',
  'device_identity_required',
  'device_key_substitution',
  'identity_binding_version_invalid',
  'identity_binding_version_mismatch',
  'invite_code_required',
  'invalid_invite_code',
  'json_object_required',
  'peer_identity_must_be_distinct',
  'request_fields_not_allowed',
  'session_expiry_invalid',
  'strict_e2ee_required',
]);

export function validateBoundTurnCredentials(
  response: ApiEnvelope<TurnCredentialPayload>,
  binding: PairSessionBinding,
): PublicTurnCredentials {
  const data = response?.data;
  if (response?.ok !== true || !data || typeof data !== 'object') {
    throw new Error('public_turn_credentials_response_invalid');
  }
  if (
    response.session_id !== binding.sessionId
    || data.session_id !== binding.sessionId
    || response.local_peer_id !== binding.localPeerId
    || data.local_peer_id !== binding.localPeerId
  ) {
    throw new Error('public_turn_credentials_binding_mismatch');
  }
  const uris = Array.isArray(data.uris) ? data.uris : [];
  if (
    typeof data.username !== 'string'
    || !data.username
    || typeof data.password !== 'string'
    || !data.password
    || typeof data.ttl !== 'number'
    || !Number.isSafeInteger(data.ttl)
    || data.ttl <= 0
    || data.ttl > MAX_PUBLIC_TURN_CREDENTIAL_TTL_SECONDS
    || uris.length === 0
    || uris.some(uri => typeof uri !== 'string' || !/^turns?:/i.test(uri))
  ) {
    throw new Error('public_turn_credentials_response_invalid');
  }
  return {
    username: data.username,
    password: data.password,
    ttl: data.ttl,
    uris: uris as string[],
  };
}

export function locallyHandledPairRequestContext(): HttpContext {
  return new HttpContext().set(SUPPRESS_GLOBAL_ERROR_NOTIFICATION, true);
}

export function catalogMembershipBatches(
  proofs: readonly PairCatalogMembershipProof[],
): readonly (readonly PairCatalogMembershipProof[])[] {
  if (proofs.length === 0) return Object.freeze([Object.freeze([])]);
  const batches: Array<readonly PairCatalogMembershipProof[]> = [];
  for (
    let offset = 0;
    offset < proofs.length;
    offset += MAX_PUBLIC_PAIR_CATALOG_MEMBERSHIPS
  ) {
    batches.push(Object.freeze(
      proofs.slice(offset, offset + MAX_PUBLIC_PAIR_CATALOG_MEMBERSHIPS),
    ));
  }
  return Object.freeze(batches);
}

export function catalogItemFingerprint(value: unknown): string {
  try {
    return JSON.stringify(canonicalCatalogJson(value));
  } catch {
    throw new Error('pair_session_list_response_invalid');
  }
}

function canonicalCatalogJson(value: unknown): unknown {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return value;
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new Error('invalid');
    return value;
  }
  if (Array.isArray(value)) return value.map(item => canonicalCatalogJson(item));
  if (!value || typeof value !== 'object') throw new Error('invalid');
  const canonical: Record<string, unknown> = {};
  for (const key of Object.keys(value as Record<string, unknown>).sort()) {
    canonical[key] = canonicalCatalogJson((value as Record<string, unknown>)[key]);
  }
  return canonical;
}

export function turnCredentialCacheKey(
  binding: PairSessionBinding,
  authority: RequestAuthority,
): PublicTurnCredentialCacheKey {
  if (
    binding.kind !== 'public'
    || authority.kind !== 'public'
    || (binding.identityBindingVersion !== 1 && binding.identityBindingVersion !== 2)
  ) throw new Error('public_turn_cache_key_invalid');
  // The identity authority contains no bearer/capability secret. Including it
  // prevents a cached credential from surviving an account/profile change
  // that happens to retain the same public session and device peer id.
  const identityAuthority = JSON.stringify([
    binding.oidcIssuer || '',
    binding.oidcSubject || '',
    binding.profileId || '',
  ]);
  return Object.freeze({
    authorityBaseUrl: authority.baseUrl,
    sessionId: binding.sessionId,
    localPeerId: binding.localPeerId,
    identityBindingVersion: binding.identityBindingVersion,
    identityAuthority,
  });
}

export function validatePublicBoundResponse<T>(response: T, binding: PairSessionBinding): T {
  if (!response || typeof response !== 'object' || Array.isArray(response)) {
    throw new Error('public_bound_response_invalid');
  }
  const envelope = response as Record<string, unknown>;
  if (envelope['local_peer_id'] !== binding.localPeerId) {
    throw new Error('public_local_peer_id_mismatch');
  }
  const data = envelope['data'];
  if (
    data
    && typeof data === 'object'
    && !Array.isArray(data)
    && Object.prototype.hasOwnProperty.call(data, 'local_peer_id')
    && (data as Record<string, unknown>)['local_peer_id'] !== binding.localPeerId
  ) throw new Error('public_local_peer_id_mismatch');
  if (
    data
    && typeof data === 'object'
    && !Array.isArray(data)
    && Object.prototype.hasOwnProperty.call(data, 'membership_capability')
  ) throw new Error('public_membership_capability_exposed');
  if (Object.prototype.hasOwnProperty.call(envelope, 'membership_capability')) {
    throw new Error('public_membership_capability_exposed');
  }
  return response;
}

export function validateRuntimeActivation(
  response: PairSessionRuntimeEnvelope,
  binding: PairSessionBinding,
): PairSessionRuntimeActivation {
  const data = response?.data;
  if (response?.ok !== true || !data || typeof data !== 'object') {
    throw new Error('pair_session_runtime_response_invalid');
  }
  if (response.local_peer_id !== binding.localPeerId || data.state !== 'active') {
    throw new Error('pair_session_runtime_response_invalid');
  }
  if (
    typeof data.security_epoch !== 'number'
    || !Number.isSafeInteger(data.security_epoch)
    || data.security_epoch < 1
    || typeof data.changed !== 'boolean'
    || !Array.isArray(data.parked_session_ids)
  ) throw new Error('pair_session_runtime_response_invalid');
  const parkedSessionIds = data.parked_session_ids.map(value => (
    validIdentifier(value, 'pair_session_runtime_response_invalid')
  ));
  if (
    new Set(parkedSessionIds).size !== parkedSessionIds.length
    || parkedSessionIds.includes(binding.sessionId)
  ) throw new Error('pair_session_runtime_response_invalid');
  return Object.freeze({
    state: 'active' as const,
    security_epoch: data.security_epoch,
    changed: data.changed,
    parked_session_ids: Object.freeze(parkedSessionIds),
  });
}

export function validatePublicV2MutationSession<T>(
  prepared: PreparedSession<T>,
  kind: PairMembershipAttemptKind,
): PreparedSession<T> {
  const session = prepared.session;
  const expectedRole = kind === 'create' ? 'owner' : 'participant';
  if (session.local_role === undefined) throw new Error('pair_session_local_role_missing');
  if (session.local_role !== expectedRole) throw new Error('pair_session_local_role_mismatch');
  if (session.local_runtime_state !== 'active') {
    throw new Error('pair_session_runtime_state_invalid');
  }
  if (
    typeof session.security_epoch !== 'number'
    || !Number.isSafeInteger(session.security_epoch)
    || session.security_epoch < 1
  ) throw new Error('pair_session_runtime_epoch_invalid');
  return prepared;
}

export function validatePublicV2ListedSession(session: BoundSessionShape): void {
  if (session.local_role !== 'owner' && session.local_role !== 'participant') {
    throw new Error(session.local_role === undefined
      ? 'pair_session_local_role_missing'
      : 'pair_session_local_role_invalid');
  }
  if (session.local_runtime_state !== 'active' && session.local_runtime_state !== 'parked') {
    throw new Error('pair_session_runtime_state_invalid');
  }
  if (
    typeof session.security_epoch !== 'number'
    || !Number.isSafeInteger(session.security_epoch)
    || session.security_epoch < 1
  ) throw new Error('pair_session_runtime_epoch_invalid');
}

export function requireSignalEpoch(value: unknown): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 1) {
    throw new Error('signal_epoch_required');
  }
  return value;
}

export function requireSignalBodyEpoch(body: unknown): number {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    throw new Error('signal_epoch_required');
  }
  return requireSignalEpoch((body as Record<string, unknown>)['security_epoch']);
}

export function publicIdentityBindingVersion(value: unknown, localPeerId: string): 1 | 2 {
  if (value === 2 && DEVICE_PEER_ID_RE.test(localPeerId)) return 2;
  if (value === 1 && ACCOUNT_PEER_ID_RE.test(localPeerId)) return 1;
  throw new Error('public_identity_binding_mismatch');
}

export function validateDiscoveryPeerIds(
  value: unknown,
  localPeerId: string,
  identityBindingVersion: 1 | 2 | undefined,
): void {
  if (identityBindingVersion !== 2) {
    if (value === undefined) return;
    if (!Array.isArray(value) || value.some(item => !ACCOUNT_PEER_ID_RE.test(String(item)))) {
      throw new Error('public_local_peer_ids_invalid');
    }
    return;
  }
  if (
    !Array.isArray(value)
    || value.length === 0
    || value.some(item => !DEVICE_PEER_ID_RE.test(String(item)))
    || new Set(value).size !== value.length
    || !value.includes(localPeerId)
  ) throw new Error('public_local_peer_ids_invalid');
}

export function discoveryPeerIds(localPeerId: unknown, localPeerIds: unknown): string[] {
  const primary = optionalIdentifier(localPeerId);
  if (
    !Array.isArray(localPeerIds)
    || localPeerIds.length === 0
    || localPeerIds.some(value => !DEVICE_PEER_ID_RE.test(String(value)))
  ) throw new Error('public_local_peer_ids_invalid');
  const candidates = [...new Set(localPeerIds as string[])];
  if (candidates.length !== localPeerIds.length || (primary && !candidates.includes(primary))) {
    throw new Error('public_local_peer_ids_invalid');
  }
  return primary ? [primary, ...candidates.filter(value => value !== primary)] : candidates;
}

export function requirePendingCapability(value: unknown): string {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(value)) {
    throw new Error('public_membership_capability_pending_missing');
  }
  return value;
}

export function assertExpectedAuthority(
  authority: Pick<RequestAuthority, 'kind'>,
  expectedAuthority?: PairControlPlaneKind,
): void {
  if (!expectedAuthority || authority.kind === expectedAuthority) return;
  throw new Error(expectedAuthority === 'public'
    ? 'public_pair_authority_required'
    : 'hub_pair_authority_required');
}

export function membershipAttemptScope(
  kind: PairMembershipAttemptKind,
  authority: RequestAuthority,
): PairMembershipAttemptScope {
  return {
    kind,
    ...membershipAuthorityScope(authority),
  };
}

export function membershipAuthorityScope(
  value: Pick<RequestAuthority, 'baseUrl' | 'oidcIssuer' | 'oidcSubject'>,
): PairMembershipAuthorityScope {
  if (!value.baseUrl || !value.oidcIssuer || !value.oidcSubject) {
    throw new Error('public_membership_capability_scope_invalid');
  }
  return {
    baseUrl: value.baseUrl,
    oidcIssuer: value.oidcIssuer,
    oidcSubject: value.oidcSubject,
  };
}

export function mutationIntent(
  kind: PairMembershipAttemptKind,
  body: Record<string, unknown>,
): Record<string, unknown> {
  if (kind !== 'create' || typeof body['expires_at'] !== 'number') return body;
  return {
    ...body,
    expires_at: undefined,
    requested_duration_seconds: Math.max(
      0,
      Math.round(body['expires_at'] - Date.now() / 1000),
    ),
  };
}

export function preCommitHttpRejection(error: unknown): boolean {
  const status = Number((error as { status?: unknown } | null)?.status);
  const reason = (error as { error?: { error?: unknown } } | null)?.error?.error;
  return status === 400 && typeof reason === 'string' && DEFINITIVE_PRE_COMMIT_ERRORS.has(reason);
}

export function validIdentifier(value: unknown, reasonCode: string): string {
  const normalized = optionalIdentifier(value);
  if (!normalized) throw new Error(reasonCode);
  return normalized;
}

export function optionalIdentifier(value: unknown): string {
  if (typeof value !== 'string') return '';
  const normalized = value.trim();
  return /^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$/.test(normalized) ? normalized : '';
}
