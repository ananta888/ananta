/**
 * Tests for the Self-Registration URL builder in OidcAuthService.
 *
 * Single responsibility: ensure that:
 *  - registrationUrl() returns a properly constructed keycloak
 *    /login-actions/registration URL for the configured issuer+client
 *  - registerWithKeycloak() opens it in a new tab via window.open
 *  - Both methods no-op safely when no issuer is configured
 */
import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { BehaviorSubject } from 'rxjs';
import { OidcAuthService } from './oidc-auth.service';
import { UserAuthService } from './user-auth.service';
import { AgentDirectoryService } from './agent-directory.service';
import { NetworkProfileService } from './network-profile.service';

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
  const issuer = overrides.issuer ?? 'https://keycloak.ananta.de/realms/ananta-e2e';
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

describe('OidcAuthService — Self-Registration', () => {
  let svc: OidcAuthService;
  let openSpy: ReturnType<typeof vi.fn>;

  function buildSvc(overrides: { issuer?: string; clientId?: string; profileId?: string } = {}) {
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        OidcAuthService,
        { provide: UserAuthService, useFactory: makeUserAuthStub },
        { provide: AgentDirectoryService, useFactory: makeDirStub },
        { provide: NetworkProfileService, useFactory: () => makeProfilesStub(overrides) },
      ],
    });
    svc = TestBed.inject(OidcAuthService);
  }

  beforeEach(() => {
    openSpy = vi.fn();
    vi.spyOn(window, 'open').mockImplementation(openSpy);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('registrationUrl', () => {
    it('returns the standard keycloak /login-actions/registration URL for the configured issuer', () => {
      buildSvc();
      expect(svc.registrationUrl()).toBe(
        'https://keycloak.ananta.de/realms/ananta-e2e/login-actions/registration',
      );
    });

    it('strips a trailing slash from the issuer before appending the path', () => {
      buildSvc({ issuer: 'https://keycloak.ananta.de/realms/ananta-e2e/' });
      expect(svc.registrationUrl()).toBe(
        'https://keycloak.ananta.de/realms/ananta-e2e/login-actions/registration',
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
        'https://keycloak.ananta.de/realms/ananta-e2e/login-actions/registration',
        '_blank',
      );
    });

    it('is a no-op when no issuer is configured (does not call window.open)', () => {
      buildSvc({ issuer: '' });
      svc.registerWithKeycloak();
      expect(openSpy).not.toHaveBeenCalled();
    });
  });
});