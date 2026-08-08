/** Focused OIDC URL and popup-PKCE tests. */
import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { BehaviorSubject } from 'rxjs';
import { OidcAuthService, OidcPopupLoginError } from './oidc-auth.service';
import { UserAuthService } from './user-auth.service';
import { AgentDirectoryService } from './agent-directory.service';
import { NetworkProfileService } from './network-profile.service';
import { PUBLIC_OIDC_CLIENT_ID, PUBLIC_OIDC_ISSUER } from './public-ananta-endpoints';
import {
  OidcPopupCoordinator,
  type OidcPopupParentSession,
} from './oidc-popup-coordinator.service';

function makeUserAuthStub() {
  const token$ = new BehaviorSubject<string | null>(null);
  const oidcToken$ = new BehaviorSubject<string | null>(null);
  return {
    token$,
    oidcToken$,
    setTokens: vi.fn(async () => undefined),
    setOidcAccessToken: vi.fn((token: string | null) => oidcToken$.next(token)),
    setOidcRefreshToken: vi.fn(async (_t: string | null) => undefined),
    getOidcRefreshToken: async () => null,
    userPayload: null,
    logout: vi.fn(),
  } as unknown as UserAuthService;
}

function makeProfilesStub(overrides: { issuer?: string; clientId?: string; profileId?: string } = {}) {
  const issuer = overrides.issuer ?? 'https://keycloak.ananta.de/realms/ananta';
  const clientId = overrides.clientId ?? 'ananta-tui';
  const profileId = overrides.profileId ?? 'public-ananta';
  return {
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
  } as unknown as NetworkProfileService;
}

function makeDirStub() {
  return { list: () => [{ role: 'hub', url: 'http://hub.test' }] } as unknown as AgentDirectoryService;
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
    localStorage.clear();
    sessionStorage.clear();
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

    it('returns empty string when no issuer is configured', () => {
      buildSvc({ issuer: '' });
      expect(svc.registrationUrl()).toBe('');
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

    it('is a no-op when no issuer is configured (does not call window.open)', () => {
      buildSvc({ issuer: '' });
      svc.registerWithKeycloak();
      expect(openSpy).not.toHaveBeenCalled();
    });
  });

  describe('profile configuration', () => {
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
          token_endpoint: 'https://sso.profile.test/token',
          end_session_endpoint: 'https://sso.profile.test/logout',
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

    it('opens the placeholder synchronously before discovery and navigates it with profile SSOT values', async () => {
      buildSvc({
        issuer: 'https://sso.profile.test/realms/team/',
        clientId: 'pair-client',
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
      expect(fetchSpy).toHaveBeenCalledWith(
        'https://sso.profile.test/realms/team/.well-known/openid-configuration',
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      );
      expect(replace).not.toHaveBeenCalled();

      resolveDiscovery(discoveryResponse(
        'https://sso.profile.test/realms/team',
        'https://sso.profile.test/realms/team/protocol/openid-connect/auth',
      ));
      const error = await login.catch(candidate => candidate);

      const authorizationUrl = new URL(String(replace.mock.calls[0][0]));
      expect(authorizationUrl.origin).toBe('https://sso.profile.test');
      expect(authorizationUrl.pathname).toBe('/realms/team/protocol/openid-connect/auth');
      expect(authorizationUrl.searchParams.get('client_id')).toBe('pair-client');
      expect(authorizationUrl.searchParams.get('code_challenge_method')).toBe('S256');
      expect(authorizationUrl.searchParams.get('state')).toMatch(/^p\./);
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
          'https://sso.profile.test/realms/team',
          'https://sso.profile.test/realms/team/protocol/openid-connect/auth',
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
          access_token: 'oidc-access-token',
          refresh_token: 'oidc-refresh-token',
          id_token: idToken(nonce),
        })),
      } as unknown as Response);

      resolveAuthorization({ kind: 'code', state, code: 'one-time-code' });
      await login;

      expect(fetchSpy).toHaveBeenNthCalledWith(2,
        'https://sso.profile.test/token',
        expect.objectContaining({
          method: 'POST',
          body: expect.stringContaining('code=one-time-code'),
        }),
      );
      expect(userAuth.setOidcRefreshToken).toHaveBeenCalledWith('oidc-refresh-token');
      expect(userAuth.setOidcAccessToken).toHaveBeenCalledWith('oidc-access-token');
      expect(acknowledge).toHaveBeenCalledWith({ ok: true });
      expect(dispose).toHaveBeenCalled();
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
          'https://sso.profile.test/realms/team',
          'https://sso.profile.test/realms/team/protocol/openid-connect/auth',
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
      expect(userAuth.setOidcAccessToken).not.toHaveBeenCalled();
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
          'https://sso.profile.test/realms/team',
          'https://sso.profile.test/realms/team/protocol/openid-connect/auth',
        ));
      vi.stubGlobal('fetch', fetchSpy);

      const login = svc.startLoginPopup();
      await vi.waitFor(() => expect(replace).toHaveBeenCalledOnce());
      const state = new URL(String(replace.mock.calls[0][0])).searchParams.get('state')!;
      fetchSpy.mockResolvedValueOnce({
        ok: true,
        json: vi.fn(async () => ({
          access_token: 'must-not-be-committed',
          id_token: idToken('different-nonce'),
        })),
      } as unknown as Response);

      resolveAuthorization({ kind: 'code', state, code: 'one-time-code' });
      const error = await login.catch(candidate => candidate);

      expect(error).toMatchObject({ code: 'nonce_mismatch' });
      expect(userAuth.setOidcAccessToken).not.toHaveBeenCalled();
      expect(acknowledge).toHaveBeenCalledWith(expect.objectContaining({
        ok: false,
        errorCode: 'nonce_mismatch',
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
      expect(String(error.message)).toContain('https://offline.profile.test/realms/team');
      expect(close).toHaveBeenCalledOnce();
      expect(localStorage.getItem('oidc.pkce.popup')).toBeNull();
    });

    it('closes the placeholder and explains a missing profile issuer', async () => {
      buildSvc();
      const { popup, close } = popupDouble();
      openSpy.mockReturnValue(popup);
      const fetchSpy = vi.fn();
      vi.stubGlobal('fetch', fetchSpy);

      const error = await svc.startLoginPopup('').catch(candidate => candidate);

      expect(error).toBeInstanceOf(OidcPopupLoginError);
      expect(error).toMatchObject({ code: 'configuration_missing' });
      expect(String(error.message)).toContain('kein OIDC-Issuer');
      expect(close).toHaveBeenCalledOnce();
      expect(fetchSpy).not.toHaveBeenCalled();
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

    it('reloads discovery metadata after the network profile issuer changes', async () => {
      buildSvc({ issuer: 'https://sso.profile.test/realms/one' });
      const first = popupDouble();
      const second = popupDouble();
      openSpy.mockReturnValueOnce(first.popup).mockReturnValueOnce(second.popup);
      const fetchSpy = vi.fn()
        .mockResolvedValueOnce(discoveryResponse(
          'https://sso.profile.test/realms/one',
          'https://sso.profile.test/realms/one/protocol/openid-connect/auth',
        ))
        .mockResolvedValueOnce(discoveryResponse(
          'https://sso.profile.test/realms/two',
          'https://sso.profile.test/realms/two/protocol/openid-connect/auth',
        ));
      vi.stubGlobal('fetch', fetchSpy);

      await svc.startLoginPopup().catch(() => undefined);
      profiles.current.oidc.issuer = 'https://sso.profile.test/realms/two';
      await svc.startLoginPopup().catch(() => undefined);

      expect(fetchSpy).toHaveBeenCalledTimes(2);
      expect(fetchSpy.mock.calls[1][0]).toBe(
        'https://sso.profile.test/realms/two/.well-known/openid-configuration',
      );
      expect(second.replace.mock.calls[0][0]).toContain('/realms/two/');
    });
  });
});
