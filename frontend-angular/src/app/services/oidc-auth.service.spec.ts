/** Focused OIDC URL and popup-PKCE tests. */
import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { BehaviorSubject } from 'rxjs';
import { Router } from '@angular/router';
import { OidcAuthService, OidcPopupLoginError } from './oidc-auth.service';
import { UserAuthService } from './user-auth.service';
import { AgentDirectoryService } from './agent-directory.service';
import { NetworkProfileService } from './network-profile.service';
import {
  PUBLIC_OIDC_AUTHORIZATION_ENDPOINT,
  PUBLIC_OIDC_CLIENT_ID,
  PUBLIC_OIDC_DEVICE_AUTHORIZATION_ENDPOINT,
  PUBLIC_OIDC_END_SESSION_ENDPOINT,
  PUBLIC_OIDC_ISSUER,
  PUBLIC_OIDC_TOKEN_ENDPOINT,
} from './public-ananta-endpoints';
import {
  OidcPopupCoordinator,
  type OidcPopupParentSession,
} from './oidc-popup-coordinator.service';
import { OidcRefreshLock } from './oidc-refresh-lock.service';

function makeUserAuthStub() {
  const token$ = new BehaviorSubject<string | null>(null);
  const oidcToken$ = new BehaviorSubject<string | null>(null);
  let oidcRefreshToken: string | null = null;
  let oidcSessionGeneration = 0;
  const stub = {
    token$,
    oidcToken$,
    setTokens: vi.fn(async () => undefined),
    setOidcAccessToken: vi.fn((token: string | null) => {
      oidcSessionGeneration += 1;
      if (token) localStorage.setItem('ananta.oidc.access_token', token);
      else localStorage.removeItem('ananta.oidc.access_token');
      oidcToken$.next(token);
    }),
    setOidcRefreshToken: vi.fn(async (token: string | null) => {
      oidcRefreshToken = token;
    }),
    getOidcRefreshToken: vi.fn(async () => oidcRefreshToken),
    commitOidcSession: vi.fn(async (
      accessToken: string | null,
      refreshToken: string | null,
      expectedGeneration?: number,
      expectedAccessToken?: string | null,
    ) => {
      if (
        (expectedGeneration !== undefined && expectedGeneration !== oidcSessionGeneration)
        || (expectedAccessToken !== undefined && expectedAccessToken !== oidcToken$.value)
      ) return { committed: false, refreshTokenPersisted: false };
      oidcSessionGeneration += 1;
      oidcRefreshToken = refreshToken;
      if (accessToken) localStorage.setItem('ananta.oidc.access_token', accessToken);
      else localStorage.removeItem('ananta.oidc.access_token');
      oidcToken$.next(accessToken);
      return { committed: true, refreshTokenPersisted: refreshToken !== null };
    }),
    decodeTokenPayload: vi.fn((token: string | null) => {
      if (!token) return null;
      try {
        const payload = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
        return JSON.parse(atob(payload.padEnd(Math.ceil(payload.length / 4) * 4, '=')));
      } catch {
        return null;
      }
    }),
    userPayload: null,
    logout: vi.fn(),
  };
  Object.defineProperties(stub, {
    oidcAccessTokenValue: { get: () => oidcToken$.value },
    oidcSessionGenerationValue: { get: () => oidcSessionGeneration },
  });
  return stub as unknown as UserAuthService;
}

function makeProfilesStub(overrides: { issuer?: string; clientId?: string; profileId?: string } = {}) {
  const issuer = overrides.issuer ?? 'https://keycloak.ananta.de/realms/ananta';
  const clientId = overrides.clientId ?? 'ananta-tui';
  const profileId = overrides.profileId ?? 'public-ananta';
  return {
    publicPairOptedIn: true,
    current: {
      profile_id: profileId,
      oidc: {
        issuer,
        client_id: clientId,
        audience: 'ananta-hub',
        pkce_required: true,
        enabled: true,
      },
    },
    enablePublicPair: vi.fn(async () => undefined),
  } as unknown as NetworkProfileService;
}

function makeDirStub() {
  return { list: () => [{ role: 'hub', url: 'http://hub.test' }] } as unknown as AgentDirectoryService;
}

function unsignedJwt(payload: Record<string, unknown>): string {
  const encoded = btoa(JSON.stringify(payload))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
  return `header.${encoded}.signature`;
}

describe('OidcAuthService', () => {
  let svc: OidcAuthService;
  let profiles: NetworkProfileService;
  let userAuth: UserAuthService;
  let openSpy: ReturnType<typeof vi.fn>;
  let popupCoordinator: {
    createState: ReturnType<typeof vi.fn>;
    isPopupCallback: ReturnType<typeof vi.fn>;
    beginParentSession: ReturnType<typeof vi.fn>;
    relayCurrentCallback: ReturnType<typeof vi.fn>;
  };

  function buildSvc(overrides: { issuer?: string; clientId?: string; profileId?: string } = {}) {
    TestBed.resetTestingModule();
    profiles = makeProfilesStub(overrides);
    popupCoordinator = {
      createState: vi.fn((randomValue: string) => `p.${randomValue}`),
      isPopupCallback: vi.fn(() => false),
      beginParentSession: vi.fn((state: string) => ({
        result: Promise.resolve({ kind: 'error' as const, state, errorCode: 'access_denied' }),
        acknowledge: vi.fn(),
        dispose: vi.fn(),
      } satisfies OidcPopupParentSession)),
      relayCurrentCallback: vi.fn(async () => undefined),
    };
    TestBed.configureTestingModule({
      providers: [
        OidcAuthService,
        { provide: UserAuthService, useFactory: makeUserAuthStub },
        { provide: AgentDirectoryService, useFactory: makeDirStub },
        { provide: NetworkProfileService, useValue: profiles },
        { provide: OidcPopupCoordinator, useValue: popupCoordinator },
        { provide: Router, useValue: { navigateByUrl: vi.fn(), navigate: vi.fn() } },
      ],
    });
    svc = TestBed.inject(OidcAuthService);
    userAuth = TestBed.inject(UserAuthService);
  }

  beforeEach(() => {
    openSpy = vi.fn();
    vi.spyOn(window, 'open').mockImplementation(openSpy);
  });

  afterEach(() => {
    vi.useRealTimers();
    localStorage.clear();
    sessionStorage.clear();
    window.history.replaceState({}, '', '/');
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  describe('registrationUrl', () => {
    it('returns the standard keycloak /login-actions/registration URL for the configured issuer', () => {
      buildSvc();
      expect(svc.registrationUrl()).toBe(
        'https://keycloak.ananta.de/realms/ananta/login-actions/registration',
      );
    });

    it('strips a trailing slash from the issuer before appending the path', () => {
      buildSvc({ issuer: 'https://keycloak.ananta.de/realms/ananta/' });
      expect(svc.registrationUrl()).toBe(
        'https://keycloak.ananta.de/realms/ananta/login-actions/registration',
      );
    });

    it('does not adopt a missing or mutable public-profile issuer', () => {
      buildSvc({ issuer: 'https://attacker.invalid/realms/phish' });
      expect(svc.registrationUrl()).toBe(
        `${PUBLIC_OIDC_ISSUER}/login-actions/registration`,
      );
    });
  });

  describe('registerWithKeycloak', () => {
    it('opens the registration URL in a new tab via window.open', () => {
      buildSvc();
      svc.registerWithKeycloak();
      expect(openSpy).toHaveBeenCalledWith(
        'https://keycloak.ananta.de/realms/ananta/login-actions/registration',
        '_blank',
      );
    });

    it('selects public Pair and ignores a Hub-supplied registration issuer', () => {
      buildSvc({ issuer: 'https://attacker.invalid/realms/phish' });
      svc.registerWithKeycloak();
      expect(profiles.enablePublicPair).toHaveBeenCalledOnce();
      expect(openSpy).toHaveBeenCalledWith(
        `${PUBLIC_OIDC_ISSUER}/login-actions/registration`,
        '_blank',
      );
    });
  });

  describe('profile configuration', () => {
    it('derives the displayed username from the OIDC token, never the Hub identity', () => {
      buildSvc();
      (userAuth as unknown as { userPayload: unknown }).userPayload = {
        preferred_username: 'hub-user',
      };
      userAuth.setOidcAccessToken(unsignedJwt({
        iss: PUBLIC_OIDC_ISSUER,
        sub: 'oidc-subject',
        preferred_username: 'pair-user',
        exp: Math.floor(Date.now() / 1000) + 3600,
      }));

      expect(svc.currentUsername).toBe('pair-user');
    });

    it('does not advertise an expired stored token as an active login', () => {
      buildSvc();
      const states: boolean[] = [];
      const subscription = svc.loggedIn$.subscribe((state) => states.push(state));

      userAuth.setOidcAccessToken(unsignedJwt({
        iss: PUBLIC_OIDC_ISSUER,
        sub: 'public-user',
        exp: Math.floor(Date.now() / 1000) - 1,
      }));
      userAuth.setOidcAccessToken(unsignedJwt({
        iss: PUBLIC_OIDC_ISSUER,
        sub: 'public-user',
        exp: Math.floor(Date.now() / 1000) + 3600,
      }));

      expect(states).toEqual([false, true]);
      subscription.unsubscribe();
    });

    it('uses bootstrap defaults when the protected profile is not available yet', () => {
      buildSvc({ issuer: '', clientId: '' });
      expect(PUBLIC_OIDC_ISSUER).toBe('https://keycloak.ananta.de/realms/ananta');
      expect(svc.issuer).toBe(PUBLIC_OIDC_ISSUER);
      expect(svc.clientId).toBe(PUBLIC_OIDC_CLIENT_ID);
    });

    it('ignores legacy token storage events inside the popup callback window', () => {
      buildSvc();
      popupCoordinator.isPopupCallback.mockReturnValue(true);

      window.dispatchEvent(new StorageEvent('storage', {
        key: 'ananta.user.token',
        newValue: 'must-not-be-adopted',
      }));
      window.dispatchEvent(new StorageEvent('storage', {
        key: 'ananta.oidc.access_token',
        newValue: 'must-not-be-adopted',
      }));

      expect(userAuth.setTokens).not.toHaveBeenCalled();
      expect(userAuth.setOidcAccessToken).not.toHaveBeenCalled();
    });

    it('never auto-exchanges a public token because mutable Hub link flags are enabled', async () => {
      buildSvc();
      profiles.current.profile_id = 'attacker-controlled-profile';
      Object.assign(profiles.current.oidc, {
        issuer: 'https://attacker.invalid/realms/phish',
        hub_link_enabled: true,
        bridge_active: true,
      });
      const fetchSpy = vi.fn();
      vi.stubGlobal('fetch', fetchSpy);

      window.dispatchEvent(new StorageEvent('storage', {
        key: 'ananta.oidc.access_token',
        newValue: unsignedJwt({ iss: PUBLIC_OIDC_ISSUER, sub: 'public-user' }),
      }));
      await Promise.resolve();

      expect(userAuth.setOidcAccessToken).toHaveBeenCalled();
      expect(fetchSpy).not.toHaveBeenCalled();
    });
  });

  describe('redirect and refresh authority binding', () => {
    it('bounds boot-time refresh when the pinned token endpoint does not answer', async () => {
      vi.useFakeTimers();
      buildSvc();
      localStorage.setItem('ananta.oidc.refresh-authority.v1', JSON.stringify({
        version: 1,
        issuer: PUBLIC_OIDC_ISSUER,
        clientId: PUBLIC_OIDC_CLIENT_ID,
        tokenEndpoint: PUBLIC_OIDC_TOKEN_ENDPOINT,
      }));
      (userAuth.getOidcRefreshToken as ReturnType<typeof vi.fn>)
        .mockResolvedValue('public-refresh-token');
      const fetchSpy = vi.fn((_url: string, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
      }));
      vi.stubGlobal('fetch', fetchSpy);

      const refresh = svc.refreshFromStorage();
      await vi.advanceTimersByTimeAsync(15_000);

      await expect(refresh).resolves.toBe(false);
      expect(fetchSpy).toHaveBeenCalledWith(PUBLIC_OIDC_TOKEN_ENDPOINT, expect.objectContaining({
        signal: expect.any(AbortSignal),
      }));
    });

    it('keeps callback and refresh on the pinned transaction after a profile switch', async () => {
      buildSvc();
      const nonce = 'redirect-nonce';
      sessionStorage.setItem('oidc.pkce', JSON.stringify({
        verifier: 'redirect-verifier',
        state: 'redirect-state',
        nonce,
        redirectPath: '/after-login',
        linkHub: false,
        issuer: PUBLIC_OIDC_ISSUER,
        clientId: PUBLIC_OIDC_CLIENT_ID,
        tokenEndpoint: PUBLIC_OIDC_TOKEN_ENDPOINT,
      }));
      window.history.replaceState({}, '', '/oidc-callback?code=one-time-code&state=redirect-state');
      profiles.current.profile_id = 'attacker-controlled-profile';
      Object.assign(profiles.current.oidc, {
        issuer: 'https://attacker.invalid/realms/phish',
        client_id: 'attacker-client',
        hub_link_enabled: true,
      });
      const fetchSpy = vi.fn()
        .mockResolvedValueOnce({
          ok: true,
          json: vi.fn(async () => ({
            access_token: unsignedJwt({
              iss: PUBLIC_OIDC_ISSUER,
              sub: 'public-user',
              exp: Math.floor(Date.now() / 1000) + 3_600,
            }),
            refresh_token: 'public-refresh-token',
            id_token: unsignedJwt({ iss: PUBLIC_OIDC_ISSUER, nonce }),
          })),
        } as unknown as Response)
        .mockResolvedValueOnce({
          ok: true,
          json: vi.fn(async () => ({
            access_token: unsignedJwt({
              iss: PUBLIC_OIDC_ISSUER,
              sub: 'public-user',
              exp: Math.floor(Date.now() / 1000) + 3_600,
            }),
            refresh_token: 'rotated-public-refresh-token',
          })),
        } as unknown as Response);
      vi.stubGlobal('fetch', fetchSpy);

      await expect(svc.handleCallback()).resolves.toBe(true);
      (userAuth.getOidcRefreshToken as ReturnType<typeof vi.fn>)
        .mockResolvedValue('public-refresh-token');
      await expect(svc.refreshFromStorage()).resolves.toBe(true);

      expect(fetchSpy).toHaveBeenNthCalledWith(1, PUBLIC_OIDC_TOKEN_ENDPOINT, expect.objectContaining({
        method: 'POST',
        body: expect.stringContaining(`client_id=${PUBLIC_OIDC_CLIENT_ID}`),
      }));
      expect(fetchSpy).toHaveBeenNthCalledWith(2, PUBLIC_OIDC_TOKEN_ENDPOINT, expect.objectContaining({
        method: 'POST',
        body: expect.stringContaining('refresh_token=public-refresh-token'),
      }));
      expect(JSON.stringify(fetchSpy.mock.calls)).not.toContain('attacker.invalid');
    });

    it('rejects a tampered redirect authority before exchanging the code', async () => {
      buildSvc();
      sessionStorage.setItem('oidc.pkce', JSON.stringify({
        verifier: 'redirect-verifier', state: 'redirect-state', nonce: 'redirect-nonce',
        redirectPath: '/', linkHub: false, issuer: PUBLIC_OIDC_ISSUER,
        clientId: 'attacker-client', tokenEndpoint: 'https://attacker.invalid/token',
      }));
      window.history.replaceState({}, '', '/oidc-callback?code=one-time-code&state=redirect-state');
      const fetchSpy = vi.fn();
      vi.stubGlobal('fetch', fetchSpy);

      await expect(svc.handleCallback()).resolves.toBe(false);
      expect(fetchSpy).not.toHaveBeenCalled();
    });

    it.each([
      {
        label: 'terminal failure',
        response: { ok: false, status: 400 } as Response,
      },
      {
        label: 'late rotated response',
        response: {
          ok: true,
          json: vi.fn(async () => ({
            access_token: unsignedJwt({
              iss: PUBLIC_OIDC_ISSUER,
              sub: 'old-user',
              exp: Math.floor(Date.now() / 1000) + 3_600,
            }),
            refresh_token: 'rotated-old-refresh-token',
          })),
        } as unknown as Response,
      },
    ])('does not let an older refresh $label replace a newer login', async ({ response }) => {
      buildSvc();
      const oldAccessToken = unsignedJwt({
        iss: PUBLIC_OIDC_ISSUER,
        sub: 'old-user',
        exp: Math.floor(Date.now() / 1000) + 3_600,
      });
      const newAccessToken = unsignedJwt({
        iss: PUBLIC_OIDC_ISSUER,
        sub: 'new-user',
        exp: Math.floor(Date.now() / 1000) + 7_200,
      });
      await userAuth.commitOidcSession(oldAccessToken, 'old-refresh-token');
      localStorage.setItem('ananta.oidc.refresh-authority.v1', JSON.stringify({
        version: 1,
        issuer: PUBLIC_OIDC_ISSUER,
        clientId: PUBLIC_OIDC_CLIENT_ID,
        tokenEndpoint: PUBLIC_OIDC_TOKEN_ENDPOINT,
      }));
      let resolveRefresh!: (value: Response) => void;
      const fetchSpy = vi.fn(() => new Promise<Response>((resolve) => {
        resolveRefresh = resolve;
      }));
      vi.stubGlobal('fetch', fetchSpy);

      const staleRefresh = svc.refreshFromStorage();
      await vi.waitFor(() => expect(fetchSpy).toHaveBeenCalledOnce());
      await userAuth.commitOidcSession(newAccessToken, 'new-refresh-token');
      resolveRefresh(response);

      await expect(staleRefresh).resolves.toBe(true);
      expect(userAuth.oidcAccessTokenValue).toBe(newAccessToken);
      await expect(userAuth.getOidcRefreshToken()).resolves.toBe('new-refresh-token');
    });

    it('shares one in-flight refresh request', async () => {
      buildSvc();
      const accessToken = unsignedJwt({
        iss: PUBLIC_OIDC_ISSUER,
        sub: 'public-user',
        exp: Math.floor(Date.now() / 1000) + 3_600,
      });
      await userAuth.commitOidcSession(accessToken, 'refresh-token');
      localStorage.setItem('ananta.oidc.refresh-authority.v1', JSON.stringify({
        version: 1,
        issuer: PUBLIC_OIDC_ISSUER,
        clientId: PUBLIC_OIDC_CLIENT_ID,
        tokenEndpoint: PUBLIC_OIDC_TOKEN_ENDPOINT,
      }));
      let resolveRefresh!: (value: Response) => void;
      const fetchSpy = vi.fn(() => new Promise<Response>((resolve) => {
        resolveRefresh = resolve;
      }));
      vi.stubGlobal('fetch', fetchSpy);

      const first = svc.refreshFromStorage();
      const second = svc.refreshFromStorage();

      expect(second).toBe(first);
      await vi.waitFor(() => expect(fetchSpy).toHaveBeenCalledOnce());
      resolveRefresh({
        ok: true,
        json: vi.fn(async () => ({
          access_token: unsignedJwt({
            iss: PUBLIC_OIDC_ISSUER,
            sub: 'public-user',
            exp: Math.floor(Date.now() / 1000) + 7_200,
          }),
          refresh_token: 'rotated-refresh-token',
        })),
      } as unknown as Response);

      await expect(first).resolves.toBe(true);
      await expect(second).resolves.toBe(true);
      expect(fetchSpy).toHaveBeenCalledOnce();
    });

    it('re-reads shared session state after waiting for the browser refresh lock', async () => {
      buildSvc();
      const oldAccessToken = unsignedJwt({
        iss: PUBLIC_OIDC_ISSUER,
        sub: 'old-user',
        exp: Math.floor(Date.now() / 1000) + 3_600,
      });
      const newAccessToken = unsignedJwt({
        iss: PUBLIC_OIDC_ISSUER,
        sub: 'new-user',
        exp: Math.floor(Date.now() / 1000) + 7_200,
      });
      await userAuth.commitOidcSession(oldAccessToken, 'old-refresh-token');
      localStorage.setItem('ananta.oidc.refresh-authority.v1', JSON.stringify({
        version: 1,
        issuer: PUBLIC_OIDC_ISSUER,
        clientId: PUBLIC_OIDC_CLIENT_ID,
        tokenEndpoint: PUBLIC_OIDC_TOKEN_ENDPOINT,
      }));
      let releaseLock!: () => void;
      vi.spyOn(TestBed.inject(OidcRefreshLock), 'run').mockImplementationOnce(async (operation) => {
        await new Promise<void>((resolve) => { releaseLock = resolve; });
        return operation();
      });
      const fetchSpy = vi.fn();
      vi.stubGlobal('fetch', fetchSpy);

      const staleRefresh = svc.refreshFromStorage();
      await userAuth.commitOidcSession(newAccessToken, 'new-refresh-token');
      releaseLock();

      await expect(staleRefresh).resolves.toBe(true);
      expect(fetchSpy).not.toHaveBeenCalled();
      expect(userAuth.oidcAccessTokenValue).toBe(newAccessToken);
      await expect(userAuth.getOidcRefreshToken()).resolves.toBe('new-refresh-token');
    });

    it('does not start a refresh while a newer login is being committed', async () => {
      buildSvc();
      const oldAccessToken = unsignedJwt({
        iss: PUBLIC_OIDC_ISSUER,
        sub: 'old-user',
        exp: Math.floor(Date.now() / 1000) + 3_600,
      });
      const newAccessToken = unsignedJwt({
        iss: PUBLIC_OIDC_ISSUER,
        sub: 'new-user',
        exp: Math.floor(Date.now() / 1000) + 7_200,
      });
      await userAuth.commitOidcSession(oldAccessToken, 'old-refresh-token');
      localStorage.setItem('ananta.oidc.refresh-authority.v1', JSON.stringify({
        version: 1,
        issuer: PUBLIC_OIDC_ISSUER,
        clientId: PUBLIC_OIDC_CLIENT_ID,
        tokenEndpoint: PUBLIC_OIDC_TOKEN_ENDPOINT,
      }));
      let releaseLogin!: () => void;
      (userAuth.commitOidcSession as ReturnType<typeof vi.fn>).mockImplementationOnce(
        async (accessToken: string | null, refreshToken: string | null) => {
          await new Promise<void>((resolve) => { releaseLogin = resolve; });
          userAuth.setOidcAccessToken(accessToken);
          await userAuth.setOidcRefreshToken(refreshToken);
          return { committed: true, refreshTokenPersisted: refreshToken !== null };
        },
      );
      const fetchSpy = vi.fn();
      vi.stubGlobal('fetch', fetchSpy);

      const login = svc.commitAuthenticatedSession(newAccessToken, 'new-refresh-token');
      await vi.waitFor(() => expect(userAuth.commitOidcSession).toHaveBeenCalledTimes(2));
      const refresh = svc.refreshFromStorage();
      await Promise.resolve();
      expect(fetchSpy).not.toHaveBeenCalled();
      releaseLogin();

      await expect(login).resolves.toMatchObject({ committed: true });
      await expect(refresh).resolves.toBe(true);
      expect(fetchSpy).not.toHaveBeenCalled();
      expect(userAuth.oidcAccessTokenValue).toBe(newAccessToken);
    });

    it('keeps a still-current session after a transient refresh response', async () => {
      buildSvc();
      const accessToken = unsignedJwt({
        iss: PUBLIC_OIDC_ISSUER,
        sub: 'public-user',
        exp: Math.floor(Date.now() / 1000) + 3_600,
      });
      await userAuth.commitOidcSession(accessToken, 'refresh-token');
      localStorage.setItem('ananta.oidc.refresh-authority.v1', JSON.stringify({
        version: 1,
        issuer: PUBLIC_OIDC_ISSUER,
        clientId: PUBLIC_OIDC_CLIENT_ID,
        tokenEndpoint: PUBLIC_OIDC_TOKEN_ENDPOINT,
      }));
      vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 503 } as Response)));

      await expect(svc.refreshFromStorage()).resolves.toBe(false);

      expect(userAuth.oidcAccessTokenValue).toBe(accessToken);
      await expect(userAuth.getOidcRefreshToken()).resolves.toBe('refresh-token');
    });

    it('retires refresh capability but keeps a valid access token after terminal rejection', async () => {
      buildSvc();
      const accessToken = unsignedJwt({
        iss: PUBLIC_OIDC_ISSUER,
        sub: 'public-user',
        exp: Math.floor(Date.now() / 1000) + 3_600,
      });
      await userAuth.commitOidcSession(accessToken, 'invalid-refresh-token');
      localStorage.setItem('ananta.oidc.refresh-authority.v1', JSON.stringify({
        version: 1,
        issuer: PUBLIC_OIDC_ISSUER,
        clientId: PUBLIC_OIDC_CLIENT_ID,
        tokenEndpoint: PUBLIC_OIDC_TOKEN_ENDPOINT,
      }));
      vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 400 } as Response)));

      await expect(svc.refreshFromStorage()).resolves.toBe(false);

      expect(userAuth.oidcAccessTokenValue).toBe(accessToken);
      await expect(userAuth.getOidcRefreshToken()).resolves.toBeNull();
      expect(localStorage.getItem('ananta.oidc.refresh-authority.v1')).toBeNull();
    });
  });

  describe('startLoginPopup', () => {
    function popupDouble() {
      const replace = vi.fn();
      const close = vi.fn();
      const focus = vi.fn();
      const popup = {
        closed: false,
        close,
        focus,
        location: { replace },
      } as unknown as Window;
      return { popup, replace, close, focus };
    }

    function discoveryResponse(issuer: string, authorizationEndpoint: string) {
      return {
        ok: true,
        json: vi.fn(async () => ({
          issuer,
          authorization_endpoint: authorizationEndpoint,
          token_endpoint: PUBLIC_OIDC_TOKEN_ENDPOINT,
          end_session_endpoint: PUBLIC_OIDC_END_SESSION_ENDPOINT,
        })),
      } as unknown as Response;
    }

    function idToken(nonce: string): string {
      const payload = btoa(JSON.stringify({ nonce }))
        .replace(/\+/g, '-')
        .replace(/\//g, '_')
        .replace(/=+$/, '');
      return `header.${payload}.signature`;
    }

    it('opens synchronously but pins discovery and client against a malicious Hub profile', async () => {
      buildSvc({
        issuer: 'https://attacker.invalid/realms/phish',
        clientId: 'attacker-client',
      });
      const { popup, replace, focus } = popupDouble();
      openSpy.mockReturnValue(popup);

      let resolveDiscovery!: (response: Response) => void;
      const fetchSpy = vi.fn(() => new Promise<Response>((resolve) => {
        resolveDiscovery = resolve;
      }));
      vi.stubGlobal('fetch', fetchSpy);

      const login = svc.startLoginPopup();

      expect(openSpy).toHaveBeenCalledOnce();
      expect(openSpy.mock.calls[0][0]).toBe('about:blank');
      expect(String(openSpy.mock.calls[0][1])).toMatch(/^oidc-login-p\./);
      expect(openSpy.mock.calls[0][2]).toBe('width=560,height=680,left=200,top=80');
      await Promise.resolve();
      expect(fetchSpy).toHaveBeenCalledWith(
        `${PUBLIC_OIDC_ISSUER}/.well-known/openid-configuration`,
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      );
      expect(replace).not.toHaveBeenCalled();

      resolveDiscovery(discoveryResponse(
        PUBLIC_OIDC_ISSUER,
        PUBLIC_OIDC_AUTHORIZATION_ENDPOINT,
      ));
      const error = await login.catch(candidate => candidate);

      const authorizationUrl = new URL(String(replace.mock.calls[0][0]));
      expect(authorizationUrl.origin).toBe('https://keycloak.ananta.de');
      expect(authorizationUrl.pathname).toBe('/realms/ananta/protocol/openid-connect/auth');
      expect(authorizationUrl.searchParams.get('client_id')).toBe(PUBLIC_OIDC_CLIENT_ID);
      expect(authorizationUrl.searchParams.get('code_challenge_method')).toBe('S256');
      expect(authorizationUrl.searchParams.get('state')).toMatch(/^p\./);
      expect(JSON.stringify(fetchSpy.mock.calls)).not.toContain('attacker.invalid');
      expect(String(replace.mock.calls[0][0])).not.toContain('attacker.invalid');
      expect(profiles.enablePublicPair).toHaveBeenCalledOnce();
      expect(focus).toHaveBeenCalledOnce();
      expect(error).toMatchObject({ code: 'authorization_denied' });
      expect(localStorage.getItem('oidc.pkce.popup')).toBeNull();
    });

    it('exchanges the code and commits tokens only in the parent after strict nonce validation', async () => {
      buildSvc({
        issuer: 'https://sso.profile.test/realms/team',
        clientId: 'pair-client',
      });
      const { popup, replace } = popupDouble();
      openSpy.mockReturnValue(popup);
      const loginStates: boolean[] = [];
      const loginSubscription = svc.loggedIn$.subscribe((state) => loginStates.push(state));

      let resolveAuthorization!: (value: { kind: 'code'; state: string; code: string }) => void;
      const acknowledge = vi.fn();
      const dispose = vi.fn();
      popupCoordinator.beginParentSession.mockImplementation((state: string) => ({
        result: new Promise(resolve => { resolveAuthorization = resolve; }),
        acknowledge,
        dispose,
      } satisfies OidcPopupParentSession));

      const fetchSpy = vi.fn()
        .mockResolvedValueOnce(discoveryResponse(
          PUBLIC_OIDC_ISSUER,
          PUBLIC_OIDC_AUTHORIZATION_ENDPOINT,
        ));
      vi.stubGlobal('fetch', fetchSpy);

      const login = svc.startLoginPopup();
      await vi.waitFor(() => expect(replace).toHaveBeenCalledOnce());
      const authorizationUrl = new URL(String(replace.mock.calls[0][0]));
      const state = authorizationUrl.searchParams.get('state')!;
      const nonce = authorizationUrl.searchParams.get('nonce')!;
      fetchSpy.mockResolvedValueOnce({
        ok: true,
        json: vi.fn(async () => ({
          access_token: unsignedJwt({
            iss: PUBLIC_OIDC_ISSUER,
            sub: 'public-user',
            exp: Math.floor(Date.now() / 1000) + 3_600,
          }),
          refresh_token: 'oidc-refresh-token',
          id_token: idToken(nonce),
        })),
      } as unknown as Response);

      resolveAuthorization({ kind: 'code', state, code: 'one-time-code' });
      await login;

      expect(fetchSpy).toHaveBeenNthCalledWith(2,
        PUBLIC_OIDC_TOKEN_ENDPOINT,
        expect.objectContaining({
          method: 'POST',
          body: expect.stringContaining('code=one-time-code'),
        }),
      );
      expect(userAuth.commitOidcSession).toHaveBeenCalledWith(
        expect.any(String),
        'oidc-refresh-token',
      );
      expect(loginStates[loginStates.length - 1]).toBe(true);
      expect(acknowledge).toHaveBeenCalledWith({ ok: true });
      expect(dispose).toHaveBeenCalled();
      loginSubscription.unsubscribe();
    });

    it.each([
      {
        failure: new TypeError('Failed to fetch'),
        expectedCode: 'token_endpoint_unreachable',
        messageFragment: 'keine lesbare Antwort',
      },
      {
        failure: new DOMException('The operation was aborted', 'AbortError'),
        expectedCode: 'token_exchange_timeout',
        messageFragment: 'nicht rechtzeitig',
      },
    ])('classifies token fetch failures as $expectedCode without retrying the code', async ({
      failure,
      expectedCode,
      messageFragment,
    }) => {
      buildSvc({
        issuer: 'https://sso.profile.test/realms/team',
        clientId: 'pair-client',
      });
      const { popup, replace } = popupDouble();
      openSpy.mockReturnValue(popup);

      let resolveAuthorization!: (value: { kind: 'code'; state: string; code: string }) => void;
      const acknowledge = vi.fn();
      popupCoordinator.beginParentSession.mockImplementation((state: string) => ({
        result: new Promise(resolve => { resolveAuthorization = resolve; }),
        acknowledge,
        dispose: vi.fn(),
      } satisfies OidcPopupParentSession));
      const fetchSpy = vi.fn()
        .mockResolvedValueOnce(discoveryResponse(
          PUBLIC_OIDC_ISSUER,
          PUBLIC_OIDC_AUTHORIZATION_ENDPOINT,
        ))
        .mockRejectedValueOnce(failure);
      vi.stubGlobal('fetch', fetchSpy);

      const login = svc.startLoginPopup();
      await vi.waitFor(() => expect(replace).toHaveBeenCalledOnce());
      const state = new URL(String(replace.mock.calls[0][0])).searchParams.get('state')!;
      resolveAuthorization({ kind: 'code', state, code: 'one-time-code' });
      const error = await login.catch(candidate => candidate);

      expect(error).toMatchObject({ code: expectedCode });
      expect(String(error.message)).toContain(messageFragment);
      expect(fetchSpy).toHaveBeenCalledTimes(2);
      expect(userAuth.commitOidcSession).not.toHaveBeenCalled();
      expect(acknowledge).toHaveBeenCalledWith(expect.objectContaining({
        ok: false,
        errorCode: expectedCode,
      }));
    });

    it('rejects a mismatched ID-token nonce without committing an access token', async () => {
      buildSvc({
        issuer: 'https://sso.profile.test/realms/team',
        clientId: 'pair-client',
      });
      const { popup, replace } = popupDouble();
      openSpy.mockReturnValue(popup);

      let resolveAuthorization!: (value: { kind: 'code'; state: string; code: string }) => void;
      const acknowledge = vi.fn();
      popupCoordinator.beginParentSession.mockImplementation((state: string) => ({
        result: new Promise(resolve => { resolveAuthorization = resolve; }),
        acknowledge,
        dispose: vi.fn(),
      } satisfies OidcPopupParentSession));
      const fetchSpy = vi.fn()
        .mockResolvedValueOnce(discoveryResponse(
          PUBLIC_OIDC_ISSUER,
          PUBLIC_OIDC_AUTHORIZATION_ENDPOINT,
        ));
      vi.stubGlobal('fetch', fetchSpy);

      const login = svc.startLoginPopup();
      await vi.waitFor(() => expect(replace).toHaveBeenCalledOnce());
      const state = new URL(String(replace.mock.calls[0][0])).searchParams.get('state')!;
      fetchSpy.mockResolvedValueOnce({
        ok: true,
        json: vi.fn(async () => ({
          access_token: unsignedJwt({
            iss: PUBLIC_OIDC_ISSUER,
            sub: 'public-user',
            exp: Math.floor(Date.now() / 1000) + 3_600,
          }),
          id_token: idToken('different-nonce'),
        })),
      } as unknown as Response);

      resolveAuthorization({ kind: 'code', state, code: 'one-time-code' });
      const error = await login.catch(candidate => candidate);

      expect(error).toMatchObject({ code: 'nonce_mismatch' });
      expect(userAuth.commitOidcSession).not.toHaveBeenCalled();
      expect(acknowledge).toHaveBeenCalledWith(expect.objectContaining({
        ok: false,
        errorCode: 'nonce_mismatch',
      }));
    });

    it('does not acknowledge a malformed access token as a successful login', async () => {
      buildSvc();
      const { popup, replace } = popupDouble();
      openSpy.mockReturnValue(popup);
      let resolveAuthorization!: (value: { kind: 'code'; state: string; code: string }) => void;
      const acknowledge = vi.fn();
      popupCoordinator.beginParentSession.mockImplementation((state: string) => ({
        result: new Promise(resolve => { resolveAuthorization = resolve; }),
        acknowledge,
        dispose: vi.fn(),
      } satisfies OidcPopupParentSession));
      const fetchSpy = vi.fn().mockResolvedValueOnce(discoveryResponse(
        PUBLIC_OIDC_ISSUER,
        PUBLIC_OIDC_AUTHORIZATION_ENDPOINT,
      ));
      vi.stubGlobal('fetch', fetchSpy);

      const login = svc.startLoginPopup();
      await vi.waitFor(() => expect(replace).toHaveBeenCalledOnce());
      const authorizationUrl = new URL(String(replace.mock.calls[0][0]));
      const state = authorizationUrl.searchParams.get('state')!;
      const nonce = authorizationUrl.searchParams.get('nonce')!;
      fetchSpy.mockResolvedValueOnce({
        ok: true,
        json: vi.fn(async () => ({
          access_token: 'malformed-token',
          id_token: idToken(nonce),
        })),
      } as unknown as Response);

      resolveAuthorization({ kind: 'code', state, code: 'one-time-code' });
      const error = await login.catch(candidate => candidate);

      expect(error).toMatchObject({ code: 'token_exchange_failed' });
      expect(userAuth.commitOidcSession).not.toHaveBeenCalled();
      expect(acknowledge).toHaveBeenCalledWith(expect.objectContaining({
        ok: false,
        errorCode: 'token_exchange_failed',
      }));
    });

    it('reports a blocked popup before making any network request', async () => {
      buildSvc();
      openSpy.mockReturnValue(null);
      const fetchSpy = vi.fn();
      vi.stubGlobal('fetch', fetchSpy);

      const error = await svc.startLoginPopup().catch(candidate => candidate);

      expect(error).toBeInstanceOf(OidcPopupLoginError);
      expect(error).toMatchObject({ code: 'popup_blocked' });
      expect(String(error.message)).toContain('Pop-ups');
      expect(fetchSpy).not.toHaveBeenCalled();
    });

    it('closes the placeholder and reports an unreachable issuer when discovery fails', async () => {
      buildSvc({ issuer: 'https://offline.profile.test/realms/team' });
      const { popup, close } = popupDouble();
      openSpy.mockReturnValue(popup);
      vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network unavailable')));

      const error = await svc.startLoginPopup().catch(candidate => candidate);

      expect(error).toBeInstanceOf(OidcPopupLoginError);
      expect(error).toMatchObject({ code: 'issuer_unreachable' });
      expect(String(error.message)).toContain(PUBLIC_OIDC_ISSUER);
      expect(close).toHaveBeenCalledOnce();
      expect(localStorage.getItem('oidc.pkce.popup')).toBeNull();
    });

    it('ignores caller-supplied authority overrides at the public login boundary', async () => {
      buildSvc();
      const { popup, replace } = popupDouble();
      openSpy.mockReturnValue(popup);
      const fetchSpy = vi.fn().mockResolvedValue(discoveryResponse(
        PUBLIC_OIDC_ISSUER,
        PUBLIC_OIDC_AUTHORIZATION_ENDPOINT,
      ));
      vi.stubGlobal('fetch', fetchSpy);

      const error = await svc.startLoginPopup(
        'https://attacker.invalid/realms/phish',
        'attacker-client',
      ).catch(candidate => candidate);

      expect(error).toMatchObject({ code: 'authorization_denied' });
      expect(fetchSpy.mock.calls[0][0]).toBe(
        `${PUBLIC_OIDC_ISSUER}/.well-known/openid-configuration`,
      );
      expect(String(replace.mock.calls[0][0])).not.toContain('attacker.invalid');
      expect(String(replace.mock.calls[0][0])).toContain(`client_id=${PUBLIC_OIDC_CLIENT_ID}`);
    });

    it('rejects discovery metadata whose issuer does not match the configured profile', async () => {
      buildSvc({ issuer: 'https://sso.profile.test/realms/team' });
      const { popup, close } = popupDouble();
      openSpy.mockReturnValue(popup);
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(discoveryResponse(
        'https://different.profile.test/realms/team',
        'https://different.profile.test/protocol/openid-connect/auth',
      )));

      const error = await svc.startLoginPopup().catch(candidate => candidate);

      expect(error).toMatchObject({ code: 'issuer_unreachable' });
      expect(close).toHaveBeenCalledOnce();
    });

    it('keeps cached public discovery pinned when the Hub profile issuer changes', async () => {
      buildSvc({ issuer: 'https://sso.profile.test/realms/one' });
      const first = popupDouble();
      const second = popupDouble();
      openSpy.mockReturnValueOnce(first.popup).mockReturnValueOnce(second.popup);
      const fetchSpy = vi.fn().mockResolvedValue(discoveryResponse(
        PUBLIC_OIDC_ISSUER,
        PUBLIC_OIDC_AUTHORIZATION_ENDPOINT,
      ));
      vi.stubGlobal('fetch', fetchSpy);

      await svc.startLoginPopup().catch(() => undefined);
      profiles.current.oidc.issuer = 'https://sso.profile.test/realms/two';
      await svc.startLoginPopup().catch(() => undefined);

      expect(fetchSpy).toHaveBeenCalledOnce();
      expect(fetchSpy.mock.calls[0][0]).toBe(
        `${PUBLIC_OIDC_ISSUER}/.well-known/openid-configuration`,
      );
      expect(second.replace.mock.calls[0][0]).toContain('/realms/ananta/');
      expect(second.replace.mock.calls[0][0]).not.toContain('sso.profile.test');
    });
  });

  describe('device flow authority binding', () => {
    it('polls an exact device code at its pinned endpoint after the profile changes', async () => {
      buildSvc({
        issuer: 'https://attacker.invalid/realms/phish',
        clientId: 'attacker-client',
      });
      const tokenEndpoint = PUBLIC_OIDC_TOKEN_ENDPOINT;
      const deviceEndpoint = PUBLIC_OIDC_DEVICE_AUTHORIZATION_ENDPOINT;
      const fetchSpy = vi.fn()
        .mockResolvedValueOnce({
          ok: true,
          json: vi.fn(async () => ({
            issuer: PUBLIC_OIDC_ISSUER,
            authorization_endpoint: PUBLIC_OIDC_AUTHORIZATION_ENDPOINT,
            token_endpoint: tokenEndpoint,
            end_session_endpoint: PUBLIC_OIDC_END_SESSION_ENDPOINT,
            device_authorization_endpoint: deviceEndpoint,
          })),
        } as unknown as Response)
        .mockResolvedValueOnce({
          ok: true,
          json: vi.fn(async () => ({
            device_code: 'opaque-device-code',
            user_code: 'ABCD-EFGH',
            verification_uri: `${PUBLIC_OIDC_ISSUER}/device`,
            expires_in: 600,
            interval: 5,
          })),
        } as unknown as Response)
        .mockResolvedValueOnce({
          ok: false,
          status: 400,
          json: vi.fn(async () => ({ error: 'authorization_pending' })),
        } as unknown as Response);
      vi.stubGlobal('fetch', fetchSpy);

      await svc.startDeviceFlow();
      profiles.current.profile_id = 'attacker-controlled-profile';
      Object.assign(profiles.current.oidc, {
        issuer: 'https://attacker.invalid/realms/phish',
        client_id: 'attacker-client',
      });

      await expect(svc.pollDeviceToken('opaque-device-code ', 5))
        .rejects.toThrow('oidc_device_flow_binding_missing');
      expect(fetchSpy).toHaveBeenCalledTimes(2);
      await expect(svc.pollDeviceToken('opaque-device-code', 5)).resolves.toBe(false);

      expect(fetchSpy).toHaveBeenNthCalledWith(3, tokenEndpoint, expect.objectContaining({
        method: 'POST',
        body: expect.stringContaining(`client_id=${PUBLIC_OIDC_CLIENT_ID}`),
      }));
      expect(JSON.stringify(fetchSpy.mock.calls)).not.toContain('attacker.invalid');
    });
  });
});
