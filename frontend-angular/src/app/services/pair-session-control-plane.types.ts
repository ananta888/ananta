/**
 * Wire envelopes and request-authority shapes of the Pair session control
 * plane, plus its public, secret-free result/option contracts.
 */
import { PairControlPlaneKind, PairSessionBinding } from './pair-session-binding.store';

export interface ApiEnvelope<T> {
  readonly ok?: boolean;
  readonly data?: T;
  readonly session?: T;
  readonly local_peer_id?: string;
  readonly session_id?: string;
  readonly membership_capability?: unknown;
}

export interface TurnCredentialPayload {
  readonly username?: unknown;
  readonly password?: unknown;
  readonly ttl?: unknown;
  readonly uris?: unknown;
  readonly session_id?: unknown;
  readonly local_peer_id?: unknown;
}

export interface SessionListPayload<T> {
  readonly items?: readonly T[];
  readonly local_peer_id?: string;
  readonly membership_capability?: unknown;
}

export interface SessionListEnvelope<T> extends SessionListPayload<T> {
  readonly ok?: boolean;
  readonly data?: SessionListPayload<T>;
}

export interface PairCatalogMembershipProof {
  readonly session_id: string;
  readonly local_peer_id: string;
  readonly membership_capability: string;
}

export interface BoundSessionShape {
  readonly id?: unknown;
  readonly local_peer_id?: unknown;
  readonly local_peer_ids?: unknown;
  readonly identity_binding_version?: unknown;
  readonly local_role?: unknown;
  readonly local_runtime_state?: unknown;
  readonly security_epoch?: unknown;
  readonly membership_capability?: unknown;
}

export interface PairSessionRuntimeEnvelope {
  readonly ok?: unknown;
  readonly local_peer_id?: unknown;
  readonly data?: {
    readonly state?: unknown;
    readonly security_epoch?: unknown;
    readonly changed?: unknown;
    readonly parked_session_ids?: unknown;
  };
}

export interface PreparedSession<T> {
  readonly session: T & BoundSessionShape;
  readonly binding: PairSessionBinding;
}

export interface RequestAuthority {
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

export interface PairSessionMutationOptions {
  /** Fail closed instead of falling back to another control plane. */
  readonly expectedAuthority?: PairControlPlaneKind;
}

/** Validated result of one exact public membership-runtime transition. */
export interface PairSessionRuntimeActivation {
  readonly state: 'active';
  readonly security_epoch: number;
  readonly changed: boolean;
  readonly parked_session_ids: readonly string[];
}

export type PublicResponsePurpose = 'v2-mutation' | 'list';
