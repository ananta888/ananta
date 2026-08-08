import { Injectable } from '@angular/core';

export type PairControlPlaneKind = 'public' | 'hub';

export interface PairSessionBinding {
  readonly sessionId: string;
  readonly kind: PairControlPlaneKind;
  readonly baseUrl: string;
  readonly localPeerId: string;
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

  bind(candidate: PairSessionBinding): Readonly<PairSessionBinding> {
    const binding = Object.freeze({ ...candidate });
    const existing = this.bindings.get(binding.sessionId);
    if (existing) {
      if (!sameBinding(existing, binding)) throw new Error('pair_control_plane_binding_conflict');
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
    && left.oidcIssuer === right.oidcIssuer
    && left.oidcSubject === right.oidcSubject
    && left.profileId === right.profileId;
}
