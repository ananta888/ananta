import { TestBed } from '@angular/core/testing';
import { provideRouter, Router, UrlTree } from '@angular/router';
import { describe, expect, it } from 'vitest';

import { authGuard } from './auth.guard';
import { IdentityRegistry } from './services/identity/identity-registry';
import type { IdentityStatus } from './services/identity/identity.types';

function runGuard(hubStatus: IdentityStatus, unionAuthenticated: boolean): boolean | UrlTree {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({
    providers: [
      provideRouter([]),
      {
        provide: IdentityRegistry,
        useValue: {
          hub: { current: { status: hubStatus } },
          isAuthenticated: unionAuthenticated,
        },
      },
    ],
  });
  return TestBed.runInInjectionContext(() => authGuard({} as never, {} as never)) as boolean | UrlTree;
}

describe('authGuard Hub trust boundary', () => {
  it('preserves access for a ready Hub identity', () => {
    expect(runGuard('ready', true)).toBe(true);
  });

  it('does not promote an OIDC-only identity into Hub-route access', () => {
    const result = runGuard('absent', true);
    const router = TestBed.inject(Router);

    expect(result).toBeInstanceOf(UrlTree);
    expect(router.serializeUrl(result as UrlTree)).toBe('/login');
  });

  it('fails closed for an expired Hub identity even when a raw token remains', () => {
    const result = runGuard('expired', true);
    const router = TestBed.inject(Router);

    expect(result).toBeInstanceOf(UrlTree);
    expect(router.serializeUrl(result as UrlTree)).toBe('/login');
  });
});
