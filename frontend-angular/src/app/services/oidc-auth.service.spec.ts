/** Focused OIDC URL and popup-PKCE tests. */
import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { BehaviorSubject } from 'rxjs';
import { OidcAuthService, OidcPopupLoginError } from './oidc-auth.service';
import { UserAuthService } from './user-auth.service';
import { AgentDirectoryService } from './agent-directory.service';
import { NetworkProfileService } from './network-profile.service';
import { PUBLIC_OIDC_CLIENT_ID, PUBLIC_OIDC_ISSUER } from './public-ananta-endpoints';

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
  let openSpy: ReturnType<typeof vi.fn>;

  function buildSvc(overrides: { issuer?: string; clientId?: string; profileId?: string } = {}) {
    TestBed.resetTestingModule();
    profiles = makeProfilesStub(overrides);
    TestBed.configureTestingModule({
      providers: [
        OidcAuthService,
        { provide: UserAuthService, useFactory: makeUserAuthStub },
        { provide: AgentDirectoryService, useFactory: makeDirStub },
        { provide: NetworkProfileService, useValue: profiles },
      ],
    });
    svc = TestBed.inject(OidcAuthService);
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

      expect(openSpy).toHaveBeenCalledWith(
        'about:blank',
        'oidc-login',
        'width=560,height=680,left=200,top=80',
      );
      expect(fetchSpy).toHaveBeenCalledWith(
        'https://sso.profile.test/realms/team/.well-known/openid-configuration',
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      );
      expect(replace).not.toHaveBeenCalled();

      resolveDiscovery(discoveryResponse(
        'https://sso.profile.test/realms/team',
        'https://sso.profile.test/realms/team/protocol/openid-connect/auth',
      ));
      await login;

      const authorizationUrl = new URL(String(replace.mock.calls[0][0]));
      expect(authorizationUrl.origin).toBe('https://sso.profile.test');
      expect(authorizationUrl.pathname).toBe('/realms/team/protocol/openid-connect/auth');
      expect(authorizationUrl.searchParams.get('client_id')).toBe('pair-client');
      expect(authorizationUrl.searchParams.get('code_challenge_method')).toBe('S256');
      expect(focus).toHaveBeenCalledOnce();

      const stored = JSON.parse(localStorage.getItem('oidc.pkce.popup') || '{}');
      expect(stored).toMatchObject({
        issuer: 'https://sso.profile.test/realms/team',
        clientId: 'pair-client',
      });
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

      await svc.startLoginPopup();
      profiles.current.oidc.issuer = 'https://sso.profile.test/realms/two';
      await svc.startLoginPopup();

      expect(fetchSpy).toHaveBeenCalledTimes(2);
      expect(fetchSpy.mock.calls[1][0]).toBe(
        'https://sso.profile.test/realms/two/.well-known/openid-configuration',
      );
      expect(second.replace.mock.calls[0][0]).toContain('/realms/two/');
    });
  });
});
