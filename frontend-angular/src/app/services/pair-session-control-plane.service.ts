import { HttpContext } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import {
  Observable,
  TimeoutError,
  catchError,
  concatMap,
  defer,
  firstValueFrom,
  from,
  map,
  of,
  retry,
  tap,
  throwError,
  timer,
  toArray,
} from 'rxjs';

import { AgentDirectoryService } from './agent-directory.service';
import { HubApiCoreService } from './hub-api-core.service';
import { NetworkProfileService } from './network-profile.service';
import { PairDeviceIdentityService } from './pair-device-identity.service';
import {
  PairMembershipAttemptKind,
  PairMembershipAttemptScope,
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
import { rateLimitRetryAfterMs } from './http-rate-limit';
import { SUPPRESS_GLOBAL_ERROR_NOTIFICATION } from './error-request-context';
import {
  PublicTurnCredentialCacheKey,
  PublicTurnCredentialCacheService,
  PublicTurnCredentials,
} from './public-turn-credential-cache.service';
import { terminalPairSessionReason } from './pair-session-terminal-error';
import type {
  ApiEnvelope,
  BoundSessionShape,
  PairCatalogMembershipProof,
  PairSessionAuthorityRoute,
  PairSessionMutationOptions,
  PairSessionRuntimeActivation,
  PairSessionRuntimeEnvelope,
  PreparedSession,
  PublicResponsePurpose,
  RequestAuthority,
  SessionListEnvelope,
  TurnCredentialPayload,
} from './pair-session-control-plane.types';
import {
  assertExpectedAuthority,
  catalogItemFingerprint,
  catalogMembershipBatches,
  discoveryPeerIds,
  locallyHandledPairRequestContext,
  membershipAttemptScope,
  membershipAuthorityScope,
  mutationIntent,
  optionalIdentifier,
  preCommitHttpRejection,
  publicIdentityBindingVersion,
  requirePendingCapability,
  requireSignalBodyEpoch,
  requireSignalEpoch,
  turnCredentialCacheKey,
  validateBoundTurnCredentials,
  validateDiscoveryPeerIds,
  validatePublicBoundResponse,
  validatePublicV2ListedSession,
  validatePublicV2MutationSession,
  validateRuntimeActivation,
  validIdentifier,
} from './pair-session-control-plane.validators';

export type {
  PairSessionAuthorityRoute,
  PairSessionMutationOptions,
  PairSessionRuntimeActivation,
} from './pair-session-control-plane.types';

const PEER_ID_HEADER = 'X-Ananta-Peer-Id';
const DEVICE_ID_HEADER = 'X-Ananta-Device-Id';
const MEMBERSHIP_CAPABILITY_HEADER = 'X-Ananta-Membership-Capability';

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
  private readonly turnCredentialCache = inject(PublicTurnCredentialCacheService);

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

  /** Whether this immutable binding uses the epoch-fenced Public v2 signal wire. */
  requiresSignalEpoch(sessionId: string): boolean {
    const binding = this.bindings.require(sessionId);
    return binding.kind === 'public' && binding.identityBindingVersion === 2;
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
    this.turnCredentialCache.invalidateSession(sessionId);
    this.bindings.forget(sessionId);
  }

  /**
   * Drops an unactivated response binding while retaining a v2 capability for
   * exact-authority recovery of a mutation that may already have committed.
   */
  abandonSessionActivation(sessionId: string): void {
    this.forgetSession(sessionId);
  }

  /** Retires all local authority for a session proven terminal by its server. */
  retireSession(sessionId: string): void {
    const binding = this.bindings.get(sessionId);
    if (binding) this.retireMembership(binding);
    else this.turnCredentialCache.invalidateSession(sessionId);
  }

  /** Explicitly abandons an unresolved mutation after user confirmation. */
  discardPendingPublicMutation(kind: PairMembershipAttemptKind): void {
    this.capabilities.clearPending(kind);
  }

  create<T>(
    body: Record<string, unknown>,
    options: PairSessionMutationOptions = {},
  ): Observable<T> {
    const authority = this.authorityForNewSession(options.expectedAuthority);
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

  join<T>(
    body: Record<string, unknown>,
    options: PairSessionMutationOptions = {},
  ): Observable<T> {
    const authority = this.authorityForNewSession(options.expectedAuthority);
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
  list<T>(options: PairSessionMutationOptions = {}): Observable<readonly T[]> {
    const authority = this.authorityForNewSession(options.expectedAuthority);
    if (authority.kind === 'public') {
      const batches = catalogMembershipBatches(this.publicCatalogMembershipProofs(authority));
      return from(batches).pipe(
        // Keep requests ordered and below the authenticated catalogue's
        // account rate limit. No binding is materialised until every batch
        // has returned and the combined snapshot validates atomically.
        concatMap(memberships => this.core.request<SessionListEnvelope<T>>(
          'POST', `${authority.baseUrl}/rendezvous/sessions/catalog`, authority.baseUrl, {
            body: { memberships },
            token: authority.token,
            context: locallyHandledPairRequestContext(),
          },
        )),
        toArray(),
        map(responses => this.prepareListedResponses(responses, authority)),
      );
    }
    return this.core.get<SessionListEnvelope<T>>(
      `${authority.baseUrl}/share-sessions`, authority.baseUrl, authority.token,
    ).pipe(map(response => this.prepareListedResponses([response], authority)));
  }

  private prepareListedResponses<T>(
    responses: readonly SessionListEnvelope<T>[],
    authority: RequestAuthority,
  ): readonly T[] {
    if (authority.kind === 'public') this.assertPublicCatalogAuthorityCurrent(authority);
    const uniqueItems = new Map<string, {
      readonly fingerprint: string;
      readonly pagePeerId: string;
      readonly session: T;
    }>();
    for (const response of responses) {
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
      for (const session of payload.items) {
        const item = session as T & BoundSessionShape;
        const sessionId = validIdentifier(item?.id, 'pair_session_id_invalid');
        const fingerprint = catalogItemFingerprint(session);
        const duplicate = uniqueItems.get(sessionId);
        if (duplicate) {
          if (
            duplicate.fingerprint !== fingerprint
            || duplicate.pagePeerId !== pagePeerId
          ) {
            throw new Error('pair_session_list_duplicate_conflict');
          }
          continue;
        }
        uniqueItems.set(sessionId, { fingerprint, pagePeerId, session });
      }
    }
    // Validate the complete combined snapshot before materialising any
    // binding. One downgraded or conflicting row rejects every batch.
    const prepared = [...uniqueItems.values()].flatMap(({ session, pagePeerId }) => {
      const candidate = this.prepareListedResponse(session, authority, pagePeerId);
      return candidate ? [candidate] : [];
    });
    for (const candidate of prepared) this.bindings.assertCompatible(candidate.binding);
    return Object.freeze(prepared.map(candidate => this.commitBinding(candidate)));
  }

  private assertPublicCatalogAuthorityCurrent(
    authority: Extract<RequestAuthority, { kind: 'public' }> | RequestAuthority,
  ): void {
    if (authority.kind !== 'public') return;
    const current = this.publicAuthority.forNewSession();
    if (!current) throw new Error('public_session_profile_changed');
    if (
      current.oidcIssuer !== authority.oidcIssuer
      || current.oidcSubject !== authority.oidcSubject
    ) throw new Error('public_session_identity_changed');
    if (
      current.baseUrl !== authority.baseUrl
      || current.profileId !== authority.profileId
    ) throw new Error('public_session_profile_changed');
  }

  private publicCatalogMembershipProofs(
    authority: Extract<RequestAuthority, { kind: 'public' }> | RequestAuthority,
  ): readonly PairCatalogMembershipProof[] {
    if (authority.kind !== 'public') return [];
    return this.capabilities.listBound(membershipAuthorityScope(authority)).map(binding => (
      Object.freeze({
        session_id: binding.sessionId,
        local_peer_id: binding.localPeerId,
        membership_capability: binding.capability,
      })
    ));
  }

  /**
   * Makes one bound v2 membership server-authoritatively active. The server
   * parks this device's other memberships atomically; Hub sessions keep their
   * established lifecycle and never pass through this public-only endpoint.
   */
  activateSessionRuntime(sessionId: string): Observable<PairSessionRuntimeActivation> {
    const binding = this.bindings.require(sessionId);
    if (binding.kind !== 'public' || binding.identityBindingVersion !== 2) {
      return throwError(() => new Error('public_pair_runtime_v2_required'));
    }
    const path = `/rendezvous/sessions/${encodeURIComponent(sessionId)}/membership/runtime`;
    return this.publicBoundRequest<PairSessionRuntimeEnvelope>(
      'PUT', binding, path, { state: 'active' }, false, locallyHandledPairRequestContext(),
    ).pipe(map(response => validateRuntimeActivation(response, binding)));
  }

  participants<T>(sessionId: string): Observable<T> {
    const binding = this.bindings.require(sessionId);
    const path = binding.kind === 'public'
      ? `/rendezvous/sessions/${encodeURIComponent(sessionId)}/participants`
      : `/share-sessions/${encodeURIComponent(sessionId)}/participants`;
    return this.boundGet<T>(
      binding,
      path,
      binding.kind !== 'public',
      binding.kind === 'public' ? locallyHandledPairRequestContext() : undefined,
    );
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
      return this.publicRetirementRequest(binding, path);
    }
    return this.core.delete(`${authority.baseUrl}${path}`, authority.baseUrl, authority.token);
  }

  /**
   * Removes the exact authenticated public participant membership. Hub Pair
   * sessions keep their established local-only participant leave behavior.
   */
  leave(sessionId: string): Observable<unknown> {
    const binding = this.bindings.require(sessionId);
    if (binding.kind !== 'public') return of({ ok: true });
    const path = `/rendezvous/sessions/${encodeURIComponent(sessionId)}/membership`;
    return this.publicRetirementRequest(binding, path);
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
    return this.boundGet<T>(
      binding,
      `${root}/${encodeURIComponent(sessionId)}/security/${suffix}`,
      binding.kind !== 'public',
      binding.kind === 'public' ? locallyHandledPairRequestContext() : undefined,
    );
  }

  securityPost<T>(sessionId: string, suffix: string, body: unknown): Observable<T> {
    const binding = this.bindings.require(sessionId);
    const root = binding.kind === 'public' ? '/rendezvous/sessions' : '/share-sessions';
    const path = `${root}/${encodeURIComponent(sessionId)}/security/${suffix}`;
    return binding.kind === 'public'
      ? this.publicBoundRequest<T>(
        'POST', binding, path, body, false, locallyHandledPairRequestContext(),
      )
      : this.boundPost<T>(binding, path, body);
  }

  signalPoll<T>(sessionId: string, cursor: string, securityEpoch?: number): Observable<T> {
    const binding = this.bindings.require(sessionId);
    let path: string;
    if (binding.kind === 'public') {
      const epochQuery = binding.identityBindingVersion === 2
        ? `&security_epoch=${encodeURIComponent(String(requireSignalEpoch(securityEpoch)))}`
        : '';
      path = `/webrtc/sessions/${encodeURIComponent(sessionId)}/signal?since=${encodeURIComponent(cursor)}${epochQuery}`;
    } else {
      path = `/api/webrtc/sessions/${encodeURIComponent(sessionId)}/signal?since=${encodeURIComponent(cursor)}`;
    }
    // Signaling polls are cursor based and must not be retried underneath the
    // caller; a retried response can race a later cursor and duplicate SDP/ICE.
    return this.boundGet<T>(
      binding,
      path,
      false,
      binding.kind === 'public' ? locallyHandledPairRequestContext() : undefined,
    );
  }

  signalSend<T>(sessionId: string, body: unknown): Observable<T> {
    const binding = this.bindings.require(sessionId);
    if (binding.kind === 'public' && binding.identityBindingVersion === 2) {
      requireSignalBodyEpoch(body);
    }
    const root = binding.kind === 'public' ? 'webrtc' : 'api/webrtc';
    const path = `/${root}/sessions/${encodeURIComponent(sessionId)}/signal`;
    return binding.kind === 'public'
      ? this.publicBoundRequest<T>(
        'POST', binding, path, body, false, locallyHandledPairRequestContext(),
      )
      : this.boundPost<T>(binding, path, body);
  }

  turnCredentials(sessionId: string): Observable<PublicTurnCredentials | null> {
    return defer(() => {
      const binding = this.bindings.require(sessionId);
      if (binding.kind !== 'public') return of(null);
      let cacheKey: PublicTurnCredentialCacheKey;
      try {
        const authority = this.authorityForBinding(binding);
        cacheKey = turnCredentialCacheKey(binding, authority);
      } catch (error) {
        this.turnCredentialCache.invalidateSession(sessionId);
        return throwError(() => error);
      }
      return from(this.turnCredentialCache.get(cacheKey, async () => (
        firstValueFrom(this.publicBoundRequest<ApiEnvelope<TurnCredentialPayload>>(
          'GET',
          binding,
          `/rendezvous/turn-credentials?session_id=${encodeURIComponent(sessionId)}`,
          undefined,
          false,
          new HttpContext().set(SUPPRESS_GLOBAL_ERROR_NOTIFICATION, true),
        ).pipe(map(response => validateBoundTurnCredentials(response, binding))))
      )));
    }).pipe(catchError(error => {
      const status = Number((error as { status?: unknown } | null)?.status);
      if (status === 401 || status === 403) this.turnCredentialCache.invalidateSession(sessionId);
      return throwError(() => error);
    }));
  }

  private boundGet<T>(
    binding: PairSessionBinding,
    path: string,
    useRetry = true,
    context?: HttpContext,
  ): Observable<T> {
    const authority = this.authorityForBinding(binding);
    if (binding.kind === 'hub') {
      return this.core.get<T>(
        `${authority.baseUrl}${path}`, authority.baseUrl, authority.token, useRetry,
      );
    }
    return this.publicBoundRequest<T>('GET', binding, path, undefined, useRetry, context);
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
      context: locallyHandledPairRequestContext(),
    }).pipe(
      retry({
        count: 1,
        delay: error => {
          const retryAfterMs = rateLimitRetryAfterMs(error);
          return retryAfterMs === null ? throwError(() => error) : timer(retryAfterMs);
        },
      }),
      map(response => this.prepareResponse(response, authority, 'v2-mutation', pending.capability)),
      map(prepared => validatePublicV2MutationSession(prepared, kind)),
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
    method: 'GET' | 'POST' | 'PUT' | 'DELETE',
    binding: PairSessionBinding,
    path: string,
    body?: unknown,
    useRetry = false,
    context?: HttpContext,
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
      context,
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
    validatePublicV2ListedSession(item);
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
    if (prepared.binding.kind === 'public') {
      // The HTTP request captured an authority before awaiting the server.
      // Revalidate the exact identity/profile at commit time so a concurrent
      // logout or account switch cannot activate the stale membership.
      this.publicAuthority.require(prepared.binding);
    }
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
    this.turnCredentialCache.invalidateSession(binding.sessionId);
    this.bindings.forget(binding.sessionId);
    if (binding.identityBindingVersion === 2) {
      try {
        this.capabilities.forget(
          binding.sessionId,
          binding.localPeerId,
          membershipAuthorityScope(binding),
        );
      } catch {
        // Server retirement remains authoritative even when restricted
        // browser storage cannot be scrubbed. The in-memory binding is gone,
        // so the stale proof cannot authorize another request in this run.
      }
    }
  }

  private retireTerminalMembership(
    binding: PairSessionBinding,
    error: unknown,
  ): Observable<never> {
    if (terminalPairSessionReason(error)) this.retireMembership(binding);
    return throwError(() => error);
  }

  private publicRetirementRequest(
    binding: PairSessionBinding,
    path: string,
  ): Observable<unknown> {
    return this.publicBoundRequest<unknown>(
      'DELETE',
      binding,
      path,
      undefined,
      false,
      new HttpContext().set(SUPPRESS_GLOBAL_ERROR_NOTIFICATION, true),
    ).pipe(
      // End and exact-membership leave are idempotent. Retry once, in order,
      // only when the server cannot yet have given a definitive rejection.
      retry({
        count: 1,
        delay: error => {
          const retryAfterMs = rateLimitRetryAfterMs(error);
          if (retryAfterMs !== null) return timer(retryAfterMs);
          const status = Number((error as { status?: unknown } | null)?.status);
          const timedOut = error instanceof TimeoutError
            || (error as { name?: unknown } | null)?.name === 'TimeoutError';
          return timedOut || status === 0 || status === 408 || (status >= 500 && status <= 599)
            ? timer(250)
            : throwError(() => error);
        },
      }),
      tap(() => this.retireMembership(binding)),
      catchError(error => this.retireTerminalMembership(binding, error)),
    );
  }

  private authorityForNewSession(expectedAuthority?: PairControlPlaneKind): RequestAuthority {
    const publicAuthority = this.publicAuthority.forNewSession();
    if (publicAuthority) {
      const authority: RequestAuthority = {
        kind: 'public',
        ...publicAuthority,
      };
      assertExpectedAuthority(authority, expectedAuthority);
      return authority;
    }
    const baseUrl = this.hubBaseUrl;
    if (!baseUrl) throw new Error('hub_unavailable');
    const authority: RequestAuthority = {
      kind: 'hub', baseUrl, profileId: this.profiles.current.profile_id,
    };
    assertExpectedAuthority(authority, expectedAuthority);
    return authority;
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
