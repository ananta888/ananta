import { Injectable, inject } from '@angular/core';
import { Observable, catchError, map, of, retry, tap, throwError } from 'rxjs';

import { AgentDirectoryService } from './agent-directory.service';
import { HubApiCoreService } from './hub-api-core.service';
import { NetworkProfileService } from './network-profile.service';
import { PairDeviceIdentityService } from './pair-device-identity.service';
import {
  PairMembershipAttemptKind,
  PairMembershipAttemptScope,
  PairMembershipAuthorityScope,
  PairMembershipCapabilityStore,
} from './pair-membership-capability.store';
import { PairPublicAuthorityPolicy } from './pair-public-authority.policy';
import { PairPublicSessionContractPolicy } from './pair-public-session-contract.policy';
import { PublicPairMediaRuntimeCapabilityService } from './public-pair-media-runtime-capability.service';
import {
  PairControlPlaneKind,
  PairSessionBinding,
  PairSessionBindingStore,
} from './pair-session-binding.store';
import { UserAuthService } from './user-auth.service';

interface ApiEnvelope<T> {
  readonly ok?: boolean;
  readonly data?: T;
  readonly session?: T;
  readonly local_peer_id?: string;
  readonly session_id?: string;
  readonly membership_capability?: unknown;
}

interface TurnCredentialPayload {
  readonly username?: unknown;
  readonly password?: unknown;
  readonly ttl?: unknown;
  readonly uris?: unknown;
  readonly session_id?: unknown;
  readonly local_peer_id?: unknown;
}

interface BoundTurnCredentials {
  readonly username: string;
  readonly password: string;
  readonly ttl: number;
  readonly uris: string[];
}

interface SessionListPayload<T> {
  readonly items?: readonly T[];
  readonly local_peer_id?: string;
  readonly membership_capability?: unknown;
}

interface SessionListEnvelope<T> extends SessionListPayload<T> {
  readonly ok?: boolean;
  readonly data?: SessionListPayload<T>;
}

interface BoundSessionShape {
  readonly id?: unknown;
  readonly local_peer_id?: unknown;
  readonly local_peer_ids?: unknown;
  readonly identity_binding_version?: unknown;
  readonly membership_capability?: unknown;
}

interface PreparedSession<T> {
  readonly session: T & BoundSessionShape;
  readonly binding: PairSessionBinding;
}

interface RequestAuthority {
  readonly kind: PairControlPlaneKind;
  readonly baseUrl: string;
  readonly token?: string;
  readonly oidcIssuer?: string;
  readonly oidcSubject?: string;
  readonly profileId?: string;
}

/** Secret-free projection of the immutable authority selected for a Pair session. */
export interface PairSessionAuthorityRoute {
  readonly kind: PairControlPlaneKind;
  readonly baseUrl: string;
}

type PublicResponsePurpose = 'v2-mutation' | 'list';

const DEVICE_PEER_ID_RE = /^peer:[a-f0-9]{64}$/;
const ACCOUNT_PEER_ID_RE = /^oidc:[a-f0-9]{64}$/;
const PEER_ID_HEADER = 'X-Ananta-Peer-Id';
const DEVICE_ID_HEADER = 'X-Ananta-Device-Id';
const MEMBERSHIP_CAPABILITY_HEADER = 'X-Ananta-Membership-Capability';
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

/**
 * Selects and pins the control plane for each Pair session.
 *
 * Only the compile-time public rendezvous origin may receive the public OIDC
 * bearer. Once a session is bound, every later call uses that exact authority
 * or fails closed; profile/token changes never cause a Hub fallback.
 */
@Injectable({ providedIn: 'root' })
export class PairSessionControlPlaneService {
  private readonly core = inject(HubApiCoreService);
  private readonly directory = inject(AgentDirectoryService);
  private readonly profiles = inject(NetworkProfileService);
  private readonly auth = inject(UserAuthService);
  private readonly bindings = inject(PairSessionBindingStore);
  private readonly deviceIdentity = inject(PairDeviceIdentityService);
  private readonly capabilities = inject(PairMembershipCapabilityStore);
  private readonly publicAuthority = inject(PairPublicAuthorityPolicy);
  private readonly publicContract = inject(PairPublicSessionContractPolicy);
  private readonly publicMediaRuntime = inject(PublicPairMediaRuntimeCapabilityService);

  /** Compatibility/read-model property for a not-yet-created session. */
  get isPublic(): boolean {
    return this.publicAuthority.ready;
  }

  /** Compatibility read model; active code should use peerIdForSession(). */
  get currentPeerId(): string {
    const hubClaims = this.auth.userPayload;
    return String(hubClaims?.sub || hubClaims?.username || '');
  }

  get signalingUrl(): string {
    return this.isPublic ? this.publicAuthority.signalingUrl : this.profiles.current.signaling_url || '';
  }

  isPublicSession(sessionId: string): boolean {
    return this.bindings.isPublic(sessionId);
  }

  /** Exact authority lookup for policies that must reject an unbound session. */
  authorityKindForSession(sessionId: string): PairControlPlaneKind {
    return this.authorityRouteForSession(sessionId).kind;
  }

  /** Exact, immutable operation route without membership capability or identity claims. */
  authorityRouteForSession(sessionId: string): Readonly<PairSessionAuthorityRoute> {
    const binding = this.bindings.require(sessionId);
    return Object.freeze({ kind: binding.kind, baseUrl: binding.baseUrl });
  }

  peerIdForSession(sessionId: string): string {
    return this.bindings.require(sessionId).localPeerId;
  }

  assertSessionAvailable(sessionId: string): void {
    this.authorityForBinding(this.bindings.require(sessionId));
  }

  forgetSession(sessionId: string): void {
    this.bindings.forget(sessionId);
  }

  /** Explicitly abandons an unresolved mutation after user confirmation. */
  discardPendingPublicMutation(kind: PairMembershipAttemptKind): void {
    this.capabilities.clearPending(kind);
  }

  create<T>(body: Record<string, unknown>): Observable<T> {
    const authority = this.authorityForNewSession();
    if (authority.kind === 'hub') {
      return this.core.post<ApiEnvelope<T>>(
        `${authority.baseUrl}/share-sessions`, body, authority.baseUrl,
      ).pipe(map(response => this.bindResponse(response, authority)));
    }
    const publicBody = {
      ...body,
      owner_device_id: this.deviceId,
      owner_device_fingerprint: body['public_key_fingerprint'],
      allowed_permissions: body['permissions'],
      identity_binding_version: 2,
      ...(this.publicMediaRuntime.membershipAdvertisement() ?? {}),
    };
    return this.publicV2Mutation<T>('create', authority, '/rendezvous/sessions', publicBody);
  }

  join<T>(body: Record<string, unknown>): Observable<T> {
    const authority = this.authorityForNewSession();
    if (authority.kind === 'hub') {
      return this.core.post<ApiEnvelope<T>>(
        `${authority.baseUrl}/share-sessions/join-by-code`, body, authority.baseUrl,
      ).pipe(map(response => this.bindResponse(response, authority)));
    }
    return this.publicV2Mutation<T>('join', authority, '/rendezvous/sessions/join', {
      ...body,
      device_id: this.deviceId,
      device_fingerprint: body['public_key_fingerprint'],
      identity_binding_version: 2,
      ...(this.publicMediaRuntime.membershipAdvertisement() ?? {}),
    });
  }

  /**
   * Lists sessions through one explicit authority and binds every returned
   * session using the server-issued local peer id. A bare id is never enough
   * to reconstruct a public binding after reload.
   */
  list<T>(): Observable<readonly T[]> {
    const authority = this.authorityForNewSession();
    const path = authority.kind === 'public' ? '/rendezvous/sessions' : '/share-sessions';
    const request = authority.kind === 'public'
      ? this.core.request<SessionListEnvelope<T>>('GET', `${authority.baseUrl}${path}`, authority.baseUrl, {
        token: authority.token,
        headers: { [DEVICE_ID_HEADER]: this.deviceId },
      }).pipe(retry(this.core.retryCount))
      : this.core.get<SessionListEnvelope<T>>(
        `${authority.baseUrl}${path}`, authority.baseUrl, authority.token,
      );
    return request.pipe(map(response => {
      if (authority.kind === 'public' && response?.ok !== true) {
        throw new Error('public_pair_session_response_invalid');
      }
      const payload = response?.data ?? response;
      if (!Array.isArray(payload?.items)) throw new Error('pair_session_list_response_invalid');
      if (
        authority.kind === 'public'
        && (response.membership_capability !== undefined || payload.membership_capability !== undefined)
      ) throw new Error('public_membership_capability_exposed');
      const envelopePeerId = optionalIdentifier(response.local_peer_id);
      const payloadPeerId = optionalIdentifier(payload.local_peer_id);
      if (authority.kind === 'public' && envelopePeerId && payloadPeerId && payloadPeerId !== envelopePeerId) {
        throw new Error('public_local_peer_id_mismatch');
      }
      const pagePeerId = envelopePeerId || payloadPeerId;
      // Validate the complete page before materialising any binding. One
      // downgraded item invalidates the authenticated public list atomically.
      const prepared = payload.items.flatMap(session => {
        const candidate = this.prepareListedResponse(session, authority, pagePeerId);
        return candidate ? [candidate] : [];
      });
      return prepared.map(candidate => this.commitBinding(candidate));
    }));
  }

  participants<T>(sessionId: string): Observable<T> {
    const binding = this.bindings.require(sessionId);
    const path = binding.kind === 'public'
      ? `/rendezvous/sessions/${encodeURIComponent(sessionId)}/participants`
      : `/share-sessions/${encodeURIComponent(sessionId)}/participants`;
    return this.boundGet<T>(binding, path);
  }

  heartbeat(sessionId: string): Observable<unknown> {
    const binding = this.bindings.require(sessionId);
    if (binding.kind === 'public') {
      this.authorityForBinding(binding);
      // Public participant listing refreshes presence; validation above still
      // makes authentication/profile loss observable as a closed authority.
      return of({ ok: true });
    }
    return this.boundPost(binding, `/share-sessions/${encodeURIComponent(sessionId)}/heartbeat`, {});
  }

  end(sessionId: string): Observable<unknown> {
    const binding = this.bindings.require(sessionId);
    const path = binding.kind === 'public'
      ? `/rendezvous/sessions/${encodeURIComponent(sessionId)}`
      : `/share-sessions/${encodeURIComponent(sessionId)}`;
    const authority = this.authorityForBinding(binding);
    if (binding.kind === 'public') {
      return this.publicBoundRequest<unknown>('DELETE', binding, path).pipe(
        tap(() => this.retireMembership(binding)),
      );
    }
    return this.core.delete(`${authority.baseUrl}${path}`, authority.baseUrl, authority.token);
  }

  revokeParticipant(sessionId: string, participantId: string): Observable<unknown> {
    const binding = this.bindings.require(sessionId);
    if (binding.kind === 'public') {
      this.authorityForBinding(binding);
      return throwError(() => new Error('public_participant_revoke_unsupported'));
    }
    return this.core.delete(
      `${binding.baseUrl}/share-sessions/${encodeURIComponent(sessionId)}/participants/${encodeURIComponent(participantId)}`,
      binding.baseUrl,
    );
  }

  securityGet<T>(sessionId: string, suffix: string): Observable<T> {
    const binding = this.bindings.require(sessionId);
    const root = binding.kind === 'public' ? '/rendezvous/sessions' : '/share-sessions';
    return this.boundGet<T>(binding, `${root}/${encodeURIComponent(sessionId)}/security/${suffix}`);
  }

  securityPost<T>(sessionId: string, suffix: string, body: unknown): Observable<T> {
    const binding = this.bindings.require(sessionId);
    const root = binding.kind === 'public' ? '/rendezvous/sessions' : '/share-sessions';
    return this.boundPost<T>(binding, `${root}/${encodeURIComponent(sessionId)}/security/${suffix}`, body);
  }

  signalPoll<T>(sessionId: string, cursor: string): Observable<T> {
    const binding = this.bindings.require(sessionId);
    const path = binding.kind === 'public'
      ? `/webrtc/sessions/${encodeURIComponent(sessionId)}/signal?since=${encodeURIComponent(cursor)}`
      : `/api/webrtc/sessions/${encodeURIComponent(sessionId)}/signal?since=${encodeURIComponent(cursor)}`;
    // Signaling polls are cursor based and must not be retried underneath the
    // caller; a retried response can race a later cursor and duplicate SDP/ICE.
    return this.boundGet<T>(binding, path, false);
  }

  signalSend<T>(sessionId: string, body: unknown): Observable<T> {
    const binding = this.bindings.require(sessionId);
    const root = binding.kind === 'public' ? 'webrtc' : 'api/webrtc';
    return this.boundPost<T>(binding, `/${root}/sessions/${encodeURIComponent(sessionId)}/signal`, body);
  }

  turnCredentials(sessionId: string): Observable<BoundTurnCredentials | null> {
    const binding = this.bindings.require(sessionId);
    if (binding.kind !== 'public') return of(null);
    return this.boundGet<ApiEnvelope<TurnCredentialPayload>>(
      binding,
      `/rendezvous/turn-credentials?session_id=${encodeURIComponent(sessionId)}`,
      false,
    ).pipe(map(response => validateBoundTurnCredentials(response, binding)));
  }

  private boundGet<T>(binding: PairSessionBinding, path: string, useRetry = true): Observable<T> {
    const authority = this.authorityForBinding(binding);
    if (binding.kind === 'hub') {
      return this.core.get<T>(
        `${authority.baseUrl}${path}`, authority.baseUrl, authority.token, useRetry,
      );
    }
    return this.publicBoundRequest<T>('GET', binding, path, undefined, useRetry);
  }

  private boundPost<T>(binding: PairSessionBinding, path: string, body: unknown): Observable<T> {
    const authority = this.authorityForBinding(binding);
    if (binding.kind === 'hub') {
      return this.core.post<T>(`${authority.baseUrl}${path}`, body, authority.baseUrl, authority.token);
    }
    return this.publicBoundRequest<T>('POST', binding, path, body);
  }

  private publicV2Mutation<T>(
    kind: PairMembershipAttemptKind,
    authority: RequestAuthority,
    path: string,
    body: Record<string, unknown>,
  ): Observable<T> {
    const scope = membershipAttemptScope(kind, authority);
    const pending = this.capabilities.begin(scope, body, mutationIntent(kind, body));
    return this.core.request<ApiEnvelope<T>>('POST', `${authority.baseUrl}${path}`, authority.baseUrl, {
      body: pending.body,
      token: authority.token,
      headers: { [MEMBERSHIP_CAPABILITY_HEADER]: pending.capability },
    }).pipe(
      map(response => this.prepareResponse(response, authority, 'v2-mutation', pending.capability)),
      map(prepared => this.commitV2MutationBinding(prepared, scope)),
      catchError(error => {
        // Only an explicit HTTP rejection known to precede endpoint commit
        // retires the attempt. Network, 409, 5xx, response-validation and
        // local-storage failures retain it for exact idempotent recovery.
        if (preCommitHttpRejection(error)) this.capabilities.clearPending(kind);
        return throwError(() => error);
      }),
    );
  }

  private publicBoundRequest<T>(
    method: 'GET' | 'POST' | 'DELETE',
    binding: PairSessionBinding,
    path: string,
    body?: unknown,
    useRetry = false,
  ): Observable<T> {
    const authority = this.authorityForBinding(binding);
    const headers: Record<string, string> = { [PEER_ID_HEADER]: binding.localPeerId };
    if (binding.identityBindingVersion === 2) {
      const capability = binding.membershipCapability
        || this.capabilities.require(
          binding.sessionId,
          binding.localPeerId,
          membershipAuthorityScope(binding),
        );
      headers[MEMBERSHIP_CAPABILITY_HEADER] = capability;
    }
    const request = this.core.request<T>(method, `${authority.baseUrl}${path}`, authority.baseUrl, {
      body,
      token: authority.token,
      headers,
    });
    const withRetry = useRetry ? request.pipe(retry(this.core.retryCount)) : request;
    return withRetry.pipe(map(response => validatePublicBoundResponse(response, binding)));
  }

  private bindResponse<T>(response: ApiEnvelope<T>, authority: RequestAuthority): T {
    return this.commitBinding(this.prepareResponse(response, authority));
  }

  private prepareListedResponse<T>(
    session: T,
    authority: RequestAuthority,
    pagePeerId: string,
  ): PreparedSession<T> | null {
    const item = session as T & BoundSessionShape;
    if (authority.kind !== 'public' || item.identity_binding_version !== 2) {
      const localPeerId = optionalIdentifier(item?.local_peer_id) || pagePeerId;
      return this.prepareResponse(
        { ok: true, session, local_peer_id: localPeerId }, authority, 'list',
      );
    }
    this.publicContract.assertValid(item);
    if (item.membership_capability !== undefined) {
      throw new Error('public_membership_capability_exposed');
    }
    const sessionId = validIdentifier(item.id, 'pair_session_id_invalid');
    const advertisedPeerIds = discoveryPeerIds(item.local_peer_id, item.local_peer_ids);
    const selectedPeerId = advertisedPeerIds.find(peerId => (
      this.capabilities.find(sessionId, peerId, membershipAuthorityScope(authority)) !== null
    ));
    if (!selectedPeerId) return null;
    const boundSession = { ...item, local_peer_id: selectedPeerId } as T & BoundSessionShape;
    return this.prepareResponse(
      { ok: true, session: boundSession, local_peer_id: selectedPeerId }, authority, 'list',
    );
  }

  private prepareResponse<T>(
    response: ApiEnvelope<T>,
    authority: RequestAuthority,
    purpose: PublicResponsePurpose = 'list',
    pendingCapability?: string,
  ): PreparedSession<T> {
    if (authority.kind === 'public' && response?.ok !== true) {
      throw new Error('public_pair_session_response_invalid');
    }
    const raw = response?.session ?? response?.data;
    if (!raw || typeof raw !== 'object') throw new Error('pair_session_response_invalid');
    const session = raw as T & BoundSessionShape;
    if (authority.kind === 'public') this.publicContract.assertValid(session);
    const sessionId = validIdentifier(session.id, 'pair_session_id_invalid');
    const envelopePeerId = optionalIdentifier(response.local_peer_id);
    const sessionPeerId = optionalIdentifier(session.local_peer_id);
    if (
      authority.kind === 'public'
      && (!envelopePeerId || (sessionPeerId && sessionPeerId !== envelopePeerId))
    ) throw new Error('public_local_peer_id_mismatch');
    const responsePeerId = envelopePeerId || sessionPeerId;
    const localPeerId = authority.kind === 'public'
      ? validIdentifier(responsePeerId, 'public_local_peer_id_missing')
      : optionalIdentifier(responsePeerId) || this.hubPeerId;
    if (!localPeerId) throw new Error('pair_local_peer_id_missing');
    const identityBindingVersion = authority.kind === 'public'
      ? publicIdentityBindingVersion(session.identity_binding_version, localPeerId)
      : undefined;
    if (authority.kind === 'public' && (
      response.membership_capability !== undefined
      || session.membership_capability !== undefined
    )) throw new Error('public_membership_capability_exposed');
    if (authority.kind === 'public' && purpose === 'v2-mutation' && identityBindingVersion !== 2) {
      throw new Error('public_identity_binding_downgrade');
    }
    if (authority.kind === 'public' && purpose === 'list') {
      validateDiscoveryPeerIds(session.local_peer_ids, localPeerId, identityBindingVersion);
    }
    const membershipCapability = identityBindingVersion === 2
      ? purpose === 'v2-mutation'
        ? requirePendingCapability(pendingCapability)
        : this.capabilities.require(sessionId, localPeerId, membershipAuthorityScope(authority))
      : undefined;
    return { session, binding: {
      sessionId,
      kind: authority.kind,
      baseUrl: authority.baseUrl,
      localPeerId,
      identityBindingVersion,
      membershipCapability,
      oidcIssuer: authority.oidcIssuer,
      oidcSubject: authority.oidcSubject,
      profileId: authority.profileId,
    } };
  }

  private commitBinding<T>(prepared: PreparedSession<T>): T {
    this.bindings.bind(prepared.binding);
    return { ...prepared.session, local_peer_id: prepared.binding.localPeerId };
  }

  private commitV2MutationBinding<T>(
    prepared: PreparedSession<T>,
    scope: PairMembershipAttemptScope,
  ): T {
    this.bindings.assertCompatible(prepared.binding);
    const promoted = this.capabilities.promote(
      scope,
      prepared.binding.sessionId,
      prepared.binding.localPeerId,
      prepared.binding.membershipCapability || '',
    );
    if (promoted.capability !== prepared.binding.membershipCapability) {
      throw new Error('pair_membership_capability_conflict');
    }
    return this.commitBinding(prepared);
  }

  private retireMembership(binding: PairSessionBinding): void {
    this.bindings.forget(binding.sessionId);
    if (binding.identityBindingVersion === 2) {
      this.capabilities.forget(
        binding.sessionId,
        binding.localPeerId,
        membershipAuthorityScope(binding),
      );
    }
  }

  private authorityForNewSession(): RequestAuthority {
    const publicAuthority = this.publicAuthority.forNewSession();
    if (publicAuthority) {
      return {
        kind: 'public',
        ...publicAuthority,
      };
    }
    const baseUrl = this.hubBaseUrl;
    if (!baseUrl) throw new Error('hub_unavailable');
    return { kind: 'hub', baseUrl, profileId: this.profiles.current.profile_id };
  }

  private authorityForBinding(binding: PairSessionBinding): RequestAuthority {
    if (binding.kind === 'hub') return { kind: 'hub', baseUrl: binding.baseUrl };
    return { kind: 'public', ...this.publicAuthority.require(binding) };
  }

  private get hubBaseUrl(): string {
    return String(this.directory.list().find(agent => agent.role === 'hub')?.url || '').replace(/\/$/, '');
  }

  private get hubPeerId(): string {
    const claims = this.auth.userPayload;
    return optionalIdentifier(claims?.sub) || optionalIdentifier(claims?.username);
  }

  private get deviceId(): string {
    return this.deviceIdentity.id;
  }
}

function validateBoundTurnCredentials(
  response: ApiEnvelope<TurnCredentialPayload>,
  binding: PairSessionBinding,
): BoundTurnCredentials {
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
    || !Number.isFinite(data.ttl)
    || data.ttl <= 0
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

function validatePublicBoundResponse<T>(response: T, binding: PairSessionBinding): T {
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

function publicIdentityBindingVersion(value: unknown, localPeerId: string): 1 | 2 {
  if (value === 2 && DEVICE_PEER_ID_RE.test(localPeerId)) return 2;
  if (value === 1 && ACCOUNT_PEER_ID_RE.test(localPeerId)) return 1;
  throw new Error('public_identity_binding_mismatch');
}

function validateDiscoveryPeerIds(
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

function discoveryPeerIds(localPeerId: unknown, localPeerIds: unknown): string[] {
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

function requirePendingCapability(value: unknown): string {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(value)) {
    throw new Error('public_membership_capability_pending_missing');
  }
  return value;
}

function membershipAttemptScope(
  kind: PairMembershipAttemptKind,
  authority: RequestAuthority,
): PairMembershipAttemptScope {
  return {
    kind,
    ...membershipAuthorityScope(authority),
  };
}

function membershipAuthorityScope(
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

function mutationIntent(
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

function preCommitHttpRejection(error: unknown): boolean {
  const status = Number((error as { status?: unknown } | null)?.status);
  const reason = (error as { error?: { error?: unknown } } | null)?.error?.error;
  return status === 400 && typeof reason === 'string' && DEFINITIVE_PRE_COMMIT_ERRORS.has(reason);
}

function validIdentifier(value: unknown, reasonCode: string): string {
  const normalized = optionalIdentifier(value);
  if (!normalized) throw new Error(reasonCode);
  return normalized;
}

function optionalIdentifier(value: unknown): string {
  if (typeof value !== 'string') return '';
  const normalized = value.trim();
  return /^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$/.test(normalized) ? normalized : '';
}
