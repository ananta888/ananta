import { Injectable, inject } from '@angular/core';
import { Observable, map, of, throwError } from 'rxjs';

import { AgentDirectoryService } from './agent-directory.service';
import { HubApiCoreService } from './hub-api-core.service';
import { NetworkProfileService } from './network-profile.service';
import { PairPublicAuthorityPolicy } from './pair-public-authority.policy';
import { PairPublicSessionContractPolicy } from './pair-public-session-contract.policy';
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
}

interface SessionListEnvelope<T> extends SessionListPayload<T> {
  readonly ok?: boolean;
  readonly data?: SessionListPayload<T>;
}

interface BoundSessionShape {
  readonly id?: unknown;
  readonly local_peer_id?: unknown;
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
  private readonly publicAuthority = inject(PairPublicAuthorityPolicy);
  private readonly publicContract = inject(PairPublicSessionContractPolicy);

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
    return this.bindings.require(sessionId).kind;
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
    };
    return this.publicPost<ApiEnvelope<T>>(authority, '/rendezvous/sessions', publicBody).pipe(
      map(response => this.bindResponse(response, authority)),
    );
  }

  join<T>(body: Record<string, unknown>): Observable<T> {
    const authority = this.authorityForNewSession();
    if (authority.kind === 'hub') {
      return this.core.post<ApiEnvelope<T>>(
        `${authority.baseUrl}/share-sessions/join-by-code`, body, authority.baseUrl,
      ).pipe(map(response => this.bindResponse(response, authority)));
    }
    return this.publicPost<ApiEnvelope<T>>(authority, '/rendezvous/sessions/join', {
      ...body,
      device_id: this.deviceId,
      device_fingerprint: body['public_key_fingerprint'],
    }).pipe(map(response => this.bindResponse(response, authority)));
  }

  /**
   * Lists sessions through one explicit authority and binds every returned
   * session using the server-issued local peer id. A bare id is never enough
   * to reconstruct a public binding after reload.
   */
  list<T>(): Observable<readonly T[]> {
    const authority = this.authorityForNewSession();
    const path = authority.kind === 'public' ? '/rendezvous/sessions' : '/share-sessions';
    return this.core.get<SessionListEnvelope<T>>(
      `${authority.baseUrl}${path}`, authority.baseUrl, authority.token,
    ).pipe(map(response => {
      if (authority.kind === 'public' && response?.ok !== true) {
        throw new Error('public_pair_session_response_invalid');
      }
      const payload = response?.data ?? response;
      if (!Array.isArray(payload?.items)) throw new Error('pair_session_list_response_invalid');
      const envelopePeerId = optionalIdentifier(response.local_peer_id);
      const payloadPeerId = optionalIdentifier(payload.local_peer_id);
      if (
        authority.kind === 'public'
        && (!envelopePeerId || (payloadPeerId && payloadPeerId !== envelopePeerId))
      ) throw new Error('public_local_peer_id_mismatch');
      const localPeerId = envelopePeerId || payloadPeerId;
      // Validate the complete page before materialising any binding. One
      // downgraded item invalidates the authenticated public list atomically.
      const prepared = payload.items.map(session => this.prepareResponse(
        { ok: true, session, local_peer_id: localPeerId }, authority,
      ));
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
    return this.core.get<T>(
      `${authority.baseUrl}${path}`, authority.baseUrl, authority.token, useRetry,
    );
  }

  private boundPost<T>(binding: PairSessionBinding, path: string, body: unknown): Observable<T> {
    const authority = this.authorityForBinding(binding);
    return this.core.post<T>(`${authority.baseUrl}${path}`, body, authority.baseUrl, authority.token);
  }

  private publicPost<T>(authority: RequestAuthority, path: string, body: unknown): Observable<T> {
    return this.core.post<T>(`${authority.baseUrl}${path}`, body, authority.baseUrl, authority.token);
  }

  private bindResponse<T>(response: ApiEnvelope<T>, authority: RequestAuthority): T {
    return this.commitBinding(this.prepareResponse(response, authority));
  }

  private prepareResponse<T>(response: ApiEnvelope<T>, authority: RequestAuthority): PreparedSession<T> {
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
    return { session, binding: {
      sessionId,
      kind: authority.kind,
      baseUrl: authority.baseUrl,
      localPeerId,
      oidcIssuer: authority.oidcIssuer,
      oidcSubject: authority.oidcSubject,
      profileId: authority.profileId,
    } };
  }

  private commitBinding<T>(prepared: PreparedSession<T>): T {
    this.bindings.bind(prepared.binding);
    return { ...prepared.session, local_peer_id: prepared.binding.localPeerId };
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
    const key = 'ananta.pair-device-id.v1';
    try {
      const existing = localStorage.getItem(key);
      if (existing) return existing;
      const created = crypto.randomUUID ? crypto.randomUUID() : `device-${Date.now()}`;
      localStorage.setItem(key, created);
      return created;
    } catch {
      return `device-${this.hubPeerId || 'browser'}`;
    }
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
