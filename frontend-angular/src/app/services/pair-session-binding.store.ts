import { Injectable } from '@angular/core';

export type PairControlPlaneKind = 'public' | 'hub';

export interface PairSessionBinding {
  readonly sessionId: string;
  readonly kind: PairControlPlaneKind;
  readonly baseUrl: string;
  readonly localPeerId: string;
  readonly identityBindingVersion?: 1 | 2;
  /** V2 proof; never serialize this binding to localStorage or a URL. */
  readonly membershipCapability?: string;
  readonly oidcIssuer?: string;
  readonly oidcSubject?: string;
  readonly profileId?: string;
}

/**
 * Keeps the authority selected when a Pair session is created or joined.
 *
 * A session id can only be bound once. Runtime profile/authentication changes
 * may make that authority unavailable, but can never redirect the session to
 * another control plane.
 */
@Injectable({ providedIn: 'root' })
export class PairSessionBindingStore {
  private readonly bindings = new Map<string, Readonly<PairSessionBinding>>();

  assertCompatible(candidate: PairSessionBinding): void {
    const existing = this.bindings.get(candidate.sessionId);
    if (existing && !sameBinding(existing, candidate)) {
      throw new Error('pair_control_plane_binding_conflict');
    }
  }

  bind(candidate: PairSessionBinding): Readonly<PairSessionBinding> {
    const binding = Object.freeze({ ...candidate });
    const existing = this.bindings.get(binding.sessionId);
    if (existing) {
      this.assertCompatible(binding);
      return existing;
    }
    this.bindings.set(binding.sessionId, binding);
    return binding;
  }

  require(sessionId: string): Readonly<PairSessionBinding> {
    const binding = this.bindings.get(sessionId);
    if (!binding) throw new Error('pair_control_plane_binding_missing');
    return binding;
  }

  get(sessionId: string): Readonly<PairSessionBinding> | null {
    return this.bindings.get(sessionId) ?? null;
  }

  forget(sessionId: string): void {
    this.bindings.delete(sessionId);
  }

  isPublic(sessionId: string): boolean {
    return this.bindings.get(sessionId)?.kind === 'public';
  }
}

function sameBinding(left: PairSessionBinding, right: PairSessionBinding): boolean {
  return left.sessionId === right.sessionId
    && left.kind === right.kind
    && left.baseUrl === right.baseUrl
    && left.localPeerId === right.localPeerId
    && left.identityBindingVersion === right.identityBindingVersion
    && left.membershipCapability === right.membershipCapability
    && left.oidcIssuer === right.oidcIssuer
    && left.oidcSubject === right.oidcSubject
    && left.profileId === right.profileId;
}
