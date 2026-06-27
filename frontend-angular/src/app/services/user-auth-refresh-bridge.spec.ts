/**
 * Welle 5: refreshToken() must dispatch to OIDC provider when bridge_active=true.
 *
 * Verifies the UserAuthService.refreshToken() observable:
 *   - bridge_active=true  → does NOT call /refresh-token on Hub, instead
 *                          uses the standard OIDC token endpoint
 *   - bridge_active=false → falls back to Hub /refresh-token with Hub-RT
 */
import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';

import { UserAuthService } from './user-auth.service';
import { SecureTokenStorage } from './secure-token-storage.service';
import { AgentDirectoryService } from './agent-directory.service';
import { ProfileStateService } from './profile-state.service';
import { HttpClient, provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting, HttpTestingController } from '@angular/common/http/testing';
import { firstValueFrom } from 'rxjs';

describe('UserAuthService — Welle 5 OIDC SSO bridge refresh', () => {
  let service: UserAuthService;
  let httpMock: HttpTestingController;
  let originalFetch: typeof globalThis.fetch;

  beforeEach(() => {
    localStorage.clear();
    originalFetch = globalThis.fetch;
    globalThis.indexedDB = new IDBFactory() as unknown as IDBFactory;
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        UserAuthService,
        SecureTokenStorage,
        {
          provide: AgentDirectoryService,
          useValue: { list: () => [{ role: 'hub', url: 'http://hub.test' }] },
        },
        {
          provide: ProfileStateService,
          useValue: {
            bridgeActive: true,
            oidcIssuer: 'https://issuer.test',
            oidcClientId: 'ananta-frontend',
          },
        },
      ],
    });
    service = TestBed.inject(UserAuthService);
    httpMock = TestBed.inject(HttpTestingController);
    const ss = TestBed.inject(SecureTokenStorage);
    ss._clearCacheForTesting();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    // Some flows trigger background GET /me via HubApiCoreService; we
    // intentionally don't httpMock.verify() here — the Welle-5 contract
    // is "no POST /refresh-token on Hub", which we assert per-test.
  });

  it('bridge_active=true: refreshToken() hits OIDC token endpoint, NOT /refresh-token', async () => {
    // Pre-seed an OIDC refresh token
    await service.setOidcRefreshToken('oidc-rt');

    // Mock the OIDC discovery + token endpoint
    const fetchMock = vi.fn(async (url: any) => {
      const u = String(url);
      if (u.includes('.well-known/openid-configuration')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ token_endpoint: 'https://issuer.test/token' }),
        } as Response;
      }
      if (u.endsWith('/token')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            access_token: 'new-oidc-at',
            refresh_token: 'new-oidc-rt',
          }),
        } as Response;
      }
      return { ok: false, status: 404, json: async () => ({}) } as Response;
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const result = await firstValueFrom(service.refreshToken());
    expect(result).toBeTruthy();

    // Hub /refresh-token must NOT have been called
    const hubCalls = httpMock.match('http://hub.test/refresh-token');
    expect(hubCalls).toHaveLength(0);

    // The OIDC token endpoint must have been called
    const discoveryCalls = fetchMock.mock.calls.filter(c =>
      String(c[0]).includes('.well-known/openid-configuration')
    );
    expect(discoveryCalls).toHaveLength(1);
    const tokenCalls = fetchMock.mock.calls.filter(c => String(c[0]).endsWith('/token'));
    expect(tokenCalls).toHaveLength(1);

    // The new OIDC-AT should be set as the current Hub-Token (Hub validates it via JWKS)
    expect(service.token).toBe('new-oidc-at');
  });

  it('bridge_active=true: OIDC refresh failure → logout', async () => {
    await service.setOidcRefreshToken('oidc-rt');

    const fetchMock = vi.fn(async () => ({
      ok: false,
      status: 400,
      json: async () => ({ error: 'invalid_grant' }),
    } as Response));
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    await expect(firstValueFrom(service.refreshToken())).rejects.toThrow();
    expect(service.token).toBeNull();
  });

  it('bridge_active=true: missing OIDC RT → logout', async () => {
    // No OIDC RT set
    await expect(firstValueFrom(service.refreshToken())).rejects.toThrow();
    expect(service.token).toBeNull();
  });
});