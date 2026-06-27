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

import { UserAuthService } from './user-auth.service';
import { SecureTokenStorage } from './secure-token-storage.service';
import { AgentDirectoryService } from './agent-directory.service';

describe('UserAuthService — Hub/OIDC boundary', () => {
  let service: UserAuthService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    localStorage.clear();
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
      ],
    });
    service = TestBed.inject(UserAuthService);
    httpMock = TestBed.inject(HttpTestingController);
    TestBed.inject(SecureTokenStorage)._clearCacheForTesting();
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
});
