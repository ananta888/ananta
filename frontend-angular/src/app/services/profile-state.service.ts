/**
 * Network profile state — extracted to break the UserAuthService cycle.
 *
 * UserAuthService needs `profile.bridge_active` to decide between
 * legacy Hub-RT refresh and direct OIDC refresh (Welle 5).
 * NetworkProfileService injects HubApiCoreService which in turn
 * injects UserAuthService for auth headers. With Angular's DI, this
 * is technically lazy and works at runtime, but breaks unit-test
 * setup (NG0200 Circular dependency).
 *
 * This module owns the profile signal in a way that:
 *   - has no upward dependency on UserAuthService / HubApiCoreService
 *   - is consumed by UserAuthService.refreshToken() and IdentityBridge.mode()
 *
 * The NetworkProfileService stays as the loader/HTTP layer; this is
 * the pure-state read accessor.
 */
import { Injectable, signal, type Signal } from '@angular/core';

export interface ProfileOidcState {
  issuer: string;
  client_id: string;
  audience: string;
  pkce_required: boolean;
  bridge_active?: boolean;
}

export interface ProfileState {
  profile_id: string;
  oidc?: ProfileOidcState;
}

@Injectable({ providedIn: 'root' })
export class ProfileStateService {
  private readonly _profile = signal<ProfileState>({ profile_id: 'public-ananta' });

  readonly profile: Signal<ProfileState> = this._profile.asReadonly();

  setProfile(next: ProfileState): void {
    this._profile.set(next);
  }

  get bridgeActive(): boolean {
    return this._profile().oidc?.bridge_active === true;
  }

  get oidcIssuer(): string {
    return this._profile().oidc?.issuer ?? '';
  }

  get oidcClientId(): string {
    return this._profile().oidc?.client_id ?? '';
  }

  /**
   * Test helper: replace the signal value bypassing setProfile.
   * Production code MUST use setProfile.
   */
  _overrideForTesting(next: ProfileState): void {
    this._profile.set(next);
  }
}