import { TestBed } from '@angular/core/testing';
import { provideRouter, Router, UrlTree } from '@angular/router';
import { describe, expect, it } from 'vitest';

import { publicPairGuard } from './public-pair.guard';
import { NetworkProfileService } from '../services/network-profile.service';
import { PairPublicAuthorityPolicy } from '../services/pair-public-authority.policy';
import { PUBLIC_OIDC_ISSUER, PUBLIC_WEBRTC_BASE_URL } from '../services/public-ananta-endpoints';
import { UserAuthService } from '../services/user-auth.service';

function jwt(payload: Record<string, unknown>): string {
  const encoded = btoa(JSON.stringify(payload))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
  return `header.${encoded}.signature`;
}

function trustedProfile(): Record<string, unknown> {
  return {
    profile_id: 'public-ananta',
    require_e2e_payload_encryption: true,
    transport_order: ['webrtc'],
    oidc: { issuer: PUBLIC_OIDC_ISSUER },
    rendezvous: { base_url: PUBLIC_WEBRTC_BASE_URL, transport_order: ['webrtc'] },
  };
}

function runGuard(token: string | null, profile = trustedProfile(), optedIn = true): boolean | UrlTree {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({
    providers: [
      provideRouter([]),
      PairPublicAuthorityPolicy,
      { provide: UserAuthService, useValue: { oidcAccessTokenValue: token } },
      {
        provide: NetworkProfileService,
        useValue: { current: profile, publicPairOptedIn: optedIn },
      },
    ],
  });
  return TestBed.runInInjectionContext(() => publicPairGuard({} as never, {} as never)) as boolean | UrlTree;
}

describe('publicPairGuard', () => {
  it('allows a current token bound to the pinned public authority', () => {
    const result = runGuard(jwt({
      iss: PUBLIC_OIDC_ISSUER,
      sub: 'public-user',
      exp: Math.floor(Date.now() / 1000) + 300,
    }));

    expect(result).toBe(true);
  });

  it.each([
    ['missing', null],
    ['malformed', 'not-a-jwt'],
    ['expired', jwt({
      iss: PUBLIC_OIDC_ISSUER,
      sub: 'public-user',
      exp: Math.floor(Date.now() / 1000) - 1,
    })],
    ['foreign issuer', jwt({
      iss: 'https://attacker.invalid/realms/foreign',
      sub: 'public-user',
      exp: Math.floor(Date.now() / 1000) + 300,
    })],
  ])('redirects an %s OIDC identity to the pinned login entry', (_label, token) => {
    const result = runGuard(token);
    const router = TestBed.inject(Router);

    expect(result).toBeInstanceOf(UrlTree);
    expect(router.serializeUrl(result as UrlTree)).toBe('/login?sphere=oidc');
  });

  it('rejects a current token when the selected profile is not the trusted public profile', () => {
    const profile = { ...trustedProfile(), profile_id: 'mutable-profile' };
    const result = runGuard(jwt({
      iss: PUBLIC_OIDC_ISSUER,
      sub: 'public-user',
      exp: Math.floor(Date.now() / 1000) + 300,
    }), profile);

    expect(result).toBeInstanceOf(UrlTree);
  });
});
