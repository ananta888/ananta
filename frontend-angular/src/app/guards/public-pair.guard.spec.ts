import { TestBed } from '@angular/core/testing';
import { provideRouter, Router, UrlTree } from '@angular/router';
import { describe, expect, it, vi } from 'vitest';

import { publicPairGuard } from './public-pair.guard';
import { NetworkProfileService } from '../services/network-profile.service';
import { PairPublicAuthorityPolicy } from '../services/pair-public-authority.policy';
import { PairSessionBindingStore } from '../services/pair-session-binding.store';
import { NotificationService } from '../services/notification.service';
import { PUBLIC_OIDC_ISSUER, PUBLIC_WEBRTC_BASE_URL } from '../services/public-ananta-endpoints';
import { ShareSessionService } from '../services/share-session.service';
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

function runGuard(
  token: string | null,
  profile = trustedProfile(),
  optedIn = true,
  activeSession: Readonly<{ id: string; public: boolean }> | null = null,
  sessionMutationPending = false,
): boolean | UrlTree {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({
    providers: [
      provideRouter([]),
      PairPublicAuthorityPolicy,
      {
        provide: PairSessionBindingStore,
        useValue: {
          get: (sessionId: string) => activeSession?.id === sessionId ? {
            sessionId,
            kind: activeSession.public ? 'public' : 'hub',
            baseUrl: activeSession.public ? PUBLIC_WEBRTC_BASE_URL : 'https://hub.example.test',
            localPeerId: 'peer:local',
            oidcIssuer: activeSession.public ? PUBLIC_OIDC_ISSUER : undefined,
            oidcSubject: activeSession.public ? 'public-user' : undefined,
            profileId: activeSession.public ? 'public-ananta' : undefined,
          } : null,
        },
      },
      { provide: NotificationService, useValue: { error: vi.fn() } },
      {
        provide: ShareSessionService,
        useValue: {
          sessionMutationPending,
          state$: { value: { session: activeSession ? { id: activeSession.id } : null } },
        },
      },
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

  it('blocks Public Pair while a Hub-bound Share session is active', () => {
    const result = runGuard(jwt({
      iss: PUBLIC_OIDC_ISSUER,
      sub: 'public-user',
      exp: Math.floor(Date.now() / 1000) + 300,
    }), trustedProfile(), true, { id: 'hub-session', public: false });
    const notifications = TestBed.inject(NotificationService);

    expect(result).toBe(false);
    expect(notifications.error).toHaveBeenCalledWith(
      'Eine aktive Hub-Share-Session muss vor Public Pair beendet werden.',
    );
  });

  it('blocks Public Pair while an earlier create or join is still in flight', () => {
    const result = runGuard(jwt({
      iss: PUBLIC_OIDC_ISSUER,
      sub: 'public-user',
      exp: Math.floor(Date.now() / 1000) + 300,
    }), trustedProfile(), true, null, true);
    const notifications = TestBed.inject(NotificationService);

    expect(result).toBe(false);
    expect(notifications.error).toHaveBeenCalledWith(
      'Eine laufende Session-Erstellung muss vor Public Pair abgeschlossen werden.',
    );
  });

  it('allows an already active session only when it is bound to Public Pair', () => {
    const result = runGuard(jwt({
      iss: PUBLIC_OIDC_ISSUER,
      sub: 'public-user',
      exp: Math.floor(Date.now() / 1000) + 300,
    }), trustedProfile(), true, { id: 'public-session', public: true });

    expect(result).toBe(true);
  });

  it('blocks an active Public session bound to another OIDC subject', () => {
    const result = runGuard(jwt({
      iss: PUBLIC_OIDC_ISSUER,
      sub: 'replacement-user',
      exp: Math.floor(Date.now() / 1000) + 300,
    }), trustedProfile(), true, { id: 'public-session', public: true });
    const notifications = TestBed.inject(NotificationService);

    expect(result).toBe(false);
    expect(notifications.error).toHaveBeenCalledWith(
      'Die aktive Public-Pair-Session gehört zu einer anderen OIDC-Identität.',
    );
  });
});
