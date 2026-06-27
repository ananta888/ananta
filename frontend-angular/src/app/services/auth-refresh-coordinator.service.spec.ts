import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { throwError, of } from 'rxjs';

import { AuthRefreshCoordinator } from './auth-refresh-coordinator.service';
import { UserAuthService } from './user-auth.service';
import { OidcAuthService } from './oidc-auth.service';
import { ProfileStateService } from './profile-state.service';

describe('AuthRefreshCoordinator — Welle 6 authRequired$ emission', () => {
  let coordinator: AuthRefreshCoordinator;
  let userAuth: { refreshToken: ReturnType<typeof vi.fn>; logout: ReturnType<typeof vi.fn> };

  beforeEach(() => {
    TestBed.resetTestingModule();
    userAuth = {
      refreshToken: vi.fn(),
      logout: vi.fn(),
    };
    TestBed.configureTestingModule({
      providers: [
        AuthRefreshCoordinator,
        { provide: UserAuthService, useValue: userAuth },
        { provide: OidcAuthService, useValue: { silentRefresh: vi.fn() } },
      ],
    });
    coordinator = TestBed.inject(AuthRefreshCoordinator);
  });

  it('starts with authRequired$ = null', () => {
    expect(coordinator.authRequired$.value).toBeNull();
  });

  it('emits "oidc" on refresh failure when bridge_active=true', () => {
    TestBed.inject(ProfileStateService)._overrideForTesting({
      profile_id: 'public-ananta',
      oidc: { issuer: 'i', client_id: 'c', audience: 'a', pkce_required: true, bridge_active: true },
    });
    userAuth.refreshToken.mockReturnValue(throwError(() => new Error('refresh failed')));
    coordinator
      .handleUnauthorized(
        { clone: () => ({}), url: '/x' } as never,
        { handle: () => ({}) } as never,
        (req, t) => req,
      )
      .subscribe({ error: () => {} });
    expect(coordinator.authRequired$.value).toBe('oidc');
    expect(userAuth.logout).toHaveBeenCalled();
  });

  it('emits "hub" on refresh failure when bridge_active=false', () => {
    TestBed.inject(ProfileStateService)._overrideForTesting({ profile_id: 'local' });
    userAuth.refreshToken.mockReturnValue(throwError(() => new Error('refresh failed')));
    coordinator
      .handleUnauthorized(
        { clone: () => ({}), url: '/x' } as never,
        { handle: () => ({}) } as never,
        (req, t) => req,
      )
      .subscribe({ error: () => {} });
    expect(coordinator.authRequired$.value).toBe('hub');
  });

  it('does NOT emit authRequired$ on successful refresh', () => {
    TestBed.inject(ProfileStateService)._overrideForTesting({
      profile_id: 'public-ananta',
      oidc: { issuer: 'i', client_id: 'c', audience: 'a', pkce_required: true, bridge_active: true },
    });
    const fakeAccess = { access_token: 'new-token' };
    userAuth.refreshToken.mockReturnValue(of(fakeAccess));
    coordinator
      .handleUnauthorized(
        { clone: () => ({}), url: '/x' } as never,
        { handle: () => of({}) } as never,
        (req, t) => req,
      )
      .subscribe({ error: () => {} });
    expect(coordinator.authRequired$.value).toBeNull();
    expect(userAuth.logout).not.toHaveBeenCalled();
  });
});