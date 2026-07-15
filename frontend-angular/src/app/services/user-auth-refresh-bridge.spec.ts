/**
 * Regression tests for the Hub/OIDC identity boundary.
 *
 * UserAuthService owns only Hub sessions. OIDC refresh is owned by
 * OidcAuthService and must never replace the Hub access token.
 */
import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';
import { firstValueFrom } from 'rxjs';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';

import { HUB_REFRESH_TIMEOUT, UserAuthService } from './user-auth.service';
import { SecureTokenStorage } from './secure-token-storage.service';
import { AgentDirectoryService } from './agent-directory.service';
import { HubRefreshSupersededError } from './hub-refresh-error';

describe('UserAuthService — Hub/OIDC boundary', () => {
  let service: UserAuthService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    vi.useRealTimers();
    localStorage.clear();
    globalThis.indexedDB = new IDBFactory() as unknown as IDBFactory;
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        UserAuthService,
        SecureTokenStorage,
        { provide: HUB_REFRESH_TIMEOUT, useValue: 500 },
        {
          provide: AgentDirectoryService,
          useValue: { list: () => [{ role: 'hub', url: 'http://hub.test' }] },
        },
      ],
    });
    service = TestBed.inject(UserAuthService);
    httpMock = TestBed.inject(HttpTestingController);
    TestBed.inject(SecureTokenStorage)._clearCacheForTesting();
  });

  afterEach(() => {
    vi.useRealTimers();
    for (const request of httpMock.match('http://hub.test/me')) {
      if (!request.cancelled) request.flush({ status: 'success', data: { id: 'user-a' } });
    }
    httpMock.verify({ ignoreCancelled: true });
  });

  it('refreshes a Hub session only through the Hub refresh endpoint', async () => {
    await service.setTokens('old-hub-at', 'hub-rt');
    await service.setOidcRefreshToken('independent-oidc-rt');

    const resultPromise = firstValueFrom(service.refreshToken());
    let request: ReturnType<HttpTestingController['expectOne']> | undefined;
    await vi.waitFor(() => {
      request = httpMock.expectOne('http://hub.test/refresh-token');
    });
    expect(request).toBeDefined();
    expect(request!.request.body).toEqual({ refresh_token: 'hub-rt' });
    request!.flush({ status: 'success', data: { access_token: 'new-hub-at' } });

    const result = await resultPromise;
    expect(result.access_token).toBe('new-hub-at');
    expect(service.token).toBe('new-hub-at');
    expect(await service.getOidcRefreshToken()).toBe('independent-oidc-rt');
  });

  it('does not use an OIDC refresh token when the Hub refresh token is absent', async () => {
    await service.setOidcRefreshToken('oidc-rt');

    await expect(firstValueFrom(service.refreshToken())).rejects.toThrow('No refresh token');
    expect(httpMock.match('http://hub.test/refresh-token')).toHaveLength(0);
  });

  it('returns one stable object shape for refresh consumers', async () => {
    await service.setTokens('old-hub-at', 'hub-rt');

    const resultPromise = firstValueFrom(service.refreshToken());
    let request: ReturnType<HttpTestingController['expectOne']> | undefined;
    await vi.waitFor(() => {
      request = httpMock.expectOne('http://hub.test/refresh-token');
    });
    request!.flush({
      status: 'success',
      data: { access_token: 'new-hub-at', refresh_token: 'rotated-hub-rt' },
    });

    await expect(resultPromise).resolves.toEqual({
      access_token: 'new-hub-at',
      refresh_token: 'rotated-hub-rt',
    });
  });

  it('shares one HTTP rotation and persists the rotated token before all waiters resolve', async () => {
    await service.setTokens('old-hub-at', 'hub-rt');

    const first = firstValueFrom(service.refreshToken());
    const second = firstValueFrom(service.refreshToken());
    let request: ReturnType<HttpTestingController['expectOne']> | undefined;
    await vi.waitFor(() => {
      request = httpMock.expectOne('http://hub.test/refresh-token');
    });
    request!.flush({
      status: 'success',
      data: { access_token: 'new-hub-at', refresh_token: 'rotated-hub-rt' },
    });

    await Promise.all([first, second]);
    expect(httpMock.match('http://hub.test/refresh-token')).toHaveLength(0);
    expect(service.token).toBe('new-hub-at');
    expect(await service.getHubRefreshToken()).toBe('rotated-hub-rt');
  });

  it('fences a late terminal refresh error after a newer Hub login', async () => {
    await service.setTokens('old-hub-at', 'old-hub-rt');
    const refreshing = firstValueFrom(service.refreshToken());
    let request: ReturnType<HttpTestingController['expectOne']> | undefined;
    await vi.waitFor(() => {
      request = httpMock.expectOne('http://hub.test/refresh-token');
    });

    await service.setTokens('new-login-at', 'new-login-rt');
    request!.flush({ error: 'invalid refresh' }, {
      status: 401,
      statusText: 'Unauthorized',
    });

    await expect(refreshing).rejects.toBeInstanceOf(HubRefreshSupersededError);
    expect(service.token).toBe('new-login-at');
    expect(await service.getHubRefreshToken()).toBe('new-login-rt');
  });

  it('fences a late refresh success after logout without resurrecting the Hub session', async () => {
    await service.setTokens('old-hub-at', 'old-hub-rt');
    const refreshing = firstValueFrom(service.refreshToken());
    let request: ReturnType<HttpTestingController['expectOne']> | undefined;
    await vi.waitFor(() => {
      request = httpMock.expectOne('http://hub.test/refresh-token');
    });

    service.logoutHub();
    request!.flush({
      status: 'success',
      data: { access_token: 'late-at', refresh_token: 'late-rt' },
    });

    await expect(refreshing).rejects.toBeInstanceOf(HubRefreshSupersededError);
    expect(service.token).toBeNull();
    expect(await service.getHubRefreshToken()).toBeNull();
  });

  it('keeps OIDC credentials when only the Hub session is logged out', async () => {
    await service.setTokens('hub-at', 'hub-rt');
    service.setOidcAccessToken('oidc-at');
    await service.setOidcRefreshToken('oidc-rt');

    service.logoutHub();

    expect(service.token).toBeNull();
    expect(await service.getHubRefreshToken()).toBeNull();
    expect(service.oidcAccessTokenValue).toBe('oidc-at');
    expect(await service.getOidcRefreshToken()).toBe('oidc-rt');
  });

  it('times out a hung refresh as transient and permits a later refresh attempt', async () => {
    await service.setTokens('old-hub-at', 'hub-rt');
    const hung = firstValueFrom(service.refreshToken());
    let request: ReturnType<HttpTestingController['expectOne']> | undefined;
    await vi.waitFor(() => {
      request = httpMock.expectOne('http://hub.test/refresh-token');
    });
    const timedOut = expect(hung).rejects.toEqual(expect.objectContaining({ name: 'TimeoutError' }));
    await timedOut;
    expect(service.token).toBe('old-hub-at');
    expect(await service.getHubRefreshToken()).toBe('hub-rt');
    expect(request!.cancelled).toBe(true);

    const retry = firstValueFrom(service.refreshToken());
    let retriedRequest: ReturnType<HttpTestingController['expectOne']> | undefined;
    await vi.waitFor(() => {
      retriedRequest = httpMock.expectOne('http://hub.test/refresh-token');
    });
    retriedRequest!.flush({ status: 'success', data: { access_token: 'renewed-hub-at' } });
    await expect(retry).resolves.toEqual({ access_token: 'renewed-hub-at' });
  });
});
