import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';
import { Subject, firstValueFrom, of, throwError } from 'rxjs';
import { HubIdentitySource } from './hub-identity-source';
import { UserAuthService } from '../user-auth.service';
import { AgentDirectoryService } from '../agent-directory.service';
import { SecureTokenStorage } from '../secure-token-storage.service';
import { HttpClient, HttpErrorResponse, HttpEventType, HttpRequest } from '@angular/common/http';
import { IDENTITY_STORAGE_LAYOUT } from './identity-storage-layout';
import { AuthRefreshCoordinator } from '../auth-refresh-coordinator.service';

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.sig`;
}

/**
 * Build a stub HttpClient that only intercepts POST {hub}/refresh-token.
 * Every other call returns an empty observable (e.g. /me → {}).
 */
function buildStubHttpClient(refreshHandler: (body: any) => any): HttpClient {
  return {
    post: vi.fn((url: string, body: any) => {
      if (url.includes('/refresh-token')) {
        return refreshHandler(body);
      }
      return of({});
    }),
    get: vi.fn(() => of({})),
  } as unknown as HttpClient;
}

describe('HubIdentitySource', () => {
  let source: HubIdentitySource;
  let auth: UserAuthService;
  let httpStub: HttpClient;
  const hubList = [{ role: 'hub', url: 'http://hub.test' }];

  beforeEach(() => {
    vi.useRealTimers();
    localStorage.clear();
    globalThis.indexedDB = new IDBFactory() as unknown as IDBFactory;
    TestBed.resetTestingModule();
  });

  function build(http: HttpClient) {
    TestBed.configureTestingModule({
      providers: [
        HubIdentitySource,
        UserAuthService,
        SecureTokenStorage,
        { provide: HttpClient, useValue: http },
        { provide: AgentDirectoryService, useValue: { list: () => hubList } },
      ],
    });
    source = TestBed.inject(HubIdentitySource);
    auth = TestBed.inject(UserAuthService);
    TestBed.inject(SecureTokenStorage)._clearCacheForTesting();
  }

  describe('restoreFromStorage', () => {
    it('emits absent when no access token in storage', async () => {
      build(buildStubHttpClient(() => of({})));
      await source.restoreFromStorage();
      expect(source.current.status).toBe('absent');
    });

    it('emits ready when valid JWT is in storage', async () => {
      const future = Math.floor(Date.now() / 1000) + 3600;
      localStorage.setItem('ananta.user.token', makeJwt({ sub: 'alice', exp: future }));
      build(buildStubHttpClient(() => of({})));
      await source.restoreFromStorage();
      expect(source.current.status).toBe('ready');
      expect(source.current.subject).toBe('alice');
      expect(source.current.issuer).toBe('hub');
    });

    it('emits expired when JWT is past-dated', async () => {
      const past = Math.floor(Date.now() / 1000) - 100;
      localStorage.setItem('ananta.user.token', makeJwt({ sub: 'alice', exp: past }));
      build(buildStubHttpClient(() => of({})));
      await source.restoreFromStorage();
      expect(source.current.status).toBe('expired');
    });

    it('actively refreshes an expired stored access token when a Hub refresh token exists', async () => {
      const now = Math.floor(Date.now() / 1000);
      const renewed = makeJwt({ sub: 'alice', exp: now + 3_600 });
      build(buildStubHttpClient(() => of({ access_token: renewed, refresh_token: 'rt-2' })));
      await auth.setTokens(makeJwt({ sub: 'alice', exp: now - 60 }), 'rt-1');

      await source.restoreFromStorage();

      expect(source.current.status).toBe('ready');
      expect(source.current.token).toBe(renewed);
      expect(await auth.getHubRefreshToken()).toBe('rt-2');
    });
  });

  describe('onAuthenticated', () => {
    it('emits ready snapshot, writes tokens to storage', async () => {
      build(buildStubHttpClient(() => of({})));
      const future = Math.floor(Date.now() / 1000) + 3600;
      const jwt = makeJwt({ sub: 'bob', exp: future });
      await source.onAuthenticated(jwt, 'new-rt');

      expect(source.current.status).toBe('ready');
      expect(source.current.subject).toBe('bob');
      expect(localStorage.getItem('ananta.user.token')).toBe(jwt);
      const stored = localStorage.getItem(IDENTITY_STORAGE_LAYOUT.hub.refreshToken.key);
      expect(stored).toBeTruthy();
      expect(stored).not.toBe('new-rt');
      expect(await auth.getHubRefreshToken()).toBe('new-rt');
    });
  });

  describe('refresh', () => {
    it('posts to /refresh-token and stores new tokens on success', async () => {
      const future = Math.floor(Date.now() / 1000) + 3600;
      const newFuture = future + 7200;
      const newJwt = makeJwt({ sub: 'alice', exp: newFuture });

      const refreshObs = of({ access_token: newJwt, refresh_token: 'rt-2' });
      build(buildStubHttpClient(() => refreshObs));

      await source.onAuthenticated(makeJwt({ sub: 'alice', exp: future }), 'rt-1');

      await source.refresh();

      expect(source.current.status).toBe('ready');
      expect(source.current.expiresAt).toBe(newFuture);
      expect(await auth.getHubRefreshToken()).toBe('rt-2');
    });

    it('marks expired and clears tokens on refresh failure', async () => {
      const future = Math.floor(Date.now() / 1000) + 3600;
      build(buildStubHttpClient(() => throwError(() => (
        new HttpErrorResponse({ status: 401, statusText: 'invalid_token' })
      ))));

      await source.onAuthenticated(makeJwt({ sub: 'alice', exp: future }), 'rt-1');
      auth.setOidcAccessToken('oidc-access');
      await auth.setOidcRefreshToken('oidc-refresh');

      await source.refresh();

      expect(source.current.status).toBe('expired');
      expect(localStorage.getItem('ananta.user.token')).toBeNull();
      expect(localStorage.getItem(IDENTITY_STORAGE_LAYOUT.hub.refreshToken.key)).toBeNull();
      expect(auth.oidcAccessTokenValue).toBe('oidc-access');
      expect(await auth.getOidcRefreshToken()).toBe('oidc-refresh');
    });

    it('preserves Hub and OIDC credentials after a transient failure and can refresh later', async () => {
      const now = Math.floor(Date.now() / 1000);
      const renewed = makeJwt({ sub: 'alice', exp: now + 7_200 });
      let attempts = 0;
      build(buildStubHttpClient(() => {
        attempts += 1;
        return attempts === 1
          ? throwError(() => new HttpErrorResponse({ status: 503, statusText: 'offline' }))
          : of({ access_token: renewed, refresh_token: 'rt-2' });
      }));
      const original = makeJwt({ sub: 'alice', exp: now + 3_600 });
      await source.onAuthenticated(original, 'rt-1');
      auth.setOidcAccessToken('oidc-access');
      await auth.setOidcRefreshToken('oidc-refresh');

      await source.refresh();

      expect(source.current.status).toBe('ready');
      expect(auth.token).toBe(original);
      expect(await auth.getHubRefreshToken()).toBe('rt-1');
      expect(auth.oidcAccessTokenValue).toBe('oidc-access');
      expect(await auth.getOidcRefreshToken()).toBe('oidc-refresh');

      await source.refresh();
      expect(source.current.token).toBe(renewed);
      expect(await auth.getHubRefreshToken()).toBe('rt-2');
    });

    it('does not let a late transient failure overwrite a newer Hub login snapshot', async () => {
      const response = new Subject<{ access_token: string; refresh_token?: string }>();
      const http = buildStubHttpClient(() => response);
      build(http);
      const now = Math.floor(Date.now() / 1000);
      await source.onAuthenticated(makeJwt({ sub: 'old', exp: now + 3_600 }), 'old-rt');
      const refreshing = source.refresh();
      await vi.waitFor(() => expect(http.post).toHaveBeenCalledTimes(1));

      const newer = makeJwt({ sub: 'new', exp: now + 7_200 });
      await source.onAuthenticated(newer, 'new-rt');
      response.error(new HttpErrorResponse({ status: 503, statusText: 'late offline' }));
      await refreshing;

      expect(source.current.token).toBe(newer);
      expect(auth.token).toBe(newer);
      expect(await auth.getHubRefreshToken()).toBe('new-rt');
    });

    it('is a no-op when no hub is in directory', async () => {
      TestBed.resetTestingModule();
      TestBed.configureTestingModule({
        providers: [
          HubIdentitySource,
          UserAuthService,
          SecureTokenStorage,
          { provide: HttpClient, useValue: buildStubHttpClient(() => of({})) },
          { provide: AgentDirectoryService, useValue: { list: () => [] } },
        ],
      });
      const s = TestBed.inject(HubIdentitySource);
      TestBed.inject(SecureTokenStorage)._clearCacheForTesting();

      const future = Math.floor(Date.now() / 1000) + 3600;
      await s.onAuthenticated(makeJwt({ sub: 'x', exp: future }), 'rt-1');

      await s.refresh();

      expect(s.current.status).toBe('expired');
      expect(s.current.error).toBe('no hub in directory');
    });

    it('is a no-op when no refresh token is in storage', async () => {
      build(buildStubHttpClient(() => of({})));
      const future = Math.floor(Date.now() / 1000) + 3600;
      // onAuthenticated without RT
      await source.onAuthenticated(makeJwt({ sub: 'x', exp: future }));

      // Force-clear RT to simulate "no RT in storage"
      localStorage.removeItem(IDENTITY_STORAGE_LAYOUT.hub.refreshToken.key);
      TestBed.inject(SecureTokenStorage)._clearCacheForTesting();

      await source.refresh();

      expect(source.current.status).toBe('expired');
      expect(source.current.error).toBe('no refresh token');
    });

    it('keeps exactly one proactive refresh timer and tears it down with the source', async () => {
      const cleared = vi.spyOn(globalThis, 'clearTimeout');
      build(buildStubHttpClient(() => of({})));
      const now = Math.floor(Date.now() / 1000);

      await source.onAuthenticated(makeJwt({ sub: 'alice', exp: now + 3_600 }), 'rt-1');
      const firstTimer = (source as any).refreshTimer;
      expect(firstTimer).toBeTruthy();

      await source.onAuthenticated(makeJwt({ sub: 'alice', exp: now + 7_200 }), 'rt-2');
      const secondTimer = (source as any).refreshTimer;
      expect(secondTimer).toBeTruthy();
      expect(secondTimer).not.toBe(firstTimer);
      expect(cleared).toHaveBeenCalledWith(firstTimer);

      source.ngOnDestroy();
      expect((source as any).refreshTimer).toBeNull();
      expect(cleared).toHaveBeenCalledWith(secondTimer);
      cleared.mockRestore();
    });

    it('shares one refresh between the proactive timer and a simultaneous 401 retry', async () => {
      let timerCallback: (() => void) | null = null;
      const schedule = vi.spyOn(globalThis, 'setTimeout').mockImplementation(((handler: TimerHandler) => {
        if (typeof handler === 'function') timerCallback = handler as () => void;
        return 1 as unknown as ReturnType<typeof setTimeout>;
      }) as typeof setTimeout);
      try {
        const refreshResponse = new Subject<{ access_token: string; refresh_token?: string }>();
        const http = buildStubHttpClient(() => refreshResponse);
        build(http);
        const now = Math.floor(Date.now() / 1000);
        await source.onAuthenticated(makeJwt({ sub: 'alice', exp: now + 30 }), 'rt-1');
        expect(timerCallback).toBeTruthy();
        schedule.mockRestore();
        timerCallback!();
        await vi.waitFor(() => expect(http.post).toHaveBeenCalledTimes(1));

        const coordinator = TestBed.inject(AuthRefreshCoordinator);
        const retryHandler = {
          handle: vi.fn(() => of({ type: HttpEventType.Sent })),
        };
        const retry = firstValueFrom(coordinator.handleUnauthorized(
          new HttpRequest('PUT', 'http://hub.test/v1/voice/live-runs/run-a/segments/0'),
          retryHandler,
          (request, token) => request.clone({
            setHeaders: { Authorization: `Bearer ${token}` },
          }),
        ));
        expect(http.post).toHaveBeenCalledTimes(1);

        const renewed = makeJwt({ sub: 'alice', exp: now + 3_600 });
        refreshResponse.next({ access_token: renewed, refresh_token: 'rt-2' });
        refreshResponse.complete();

        await retry;
        await vi.waitFor(() => expect(source.current.token).toBe(renewed));
        expect(http.post).toHaveBeenCalledTimes(1);
        expect(retryHandler.handle).toHaveBeenCalledTimes(1);
        expect(retryHandler.handle.mock.calls[0][0].headers.get('Authorization'))
          .toBe(`Bearer ${renewed}`);
      } finally {
        schedule.mockRestore();
        source?.ngOnDestroy();
      }
    });
  });

  describe('logout', () => {
    it('clears storage, emits absent', async () => {
      build(buildStubHttpClient(() => of({})));
      const future = Math.floor(Date.now() / 1000) + 3600;
      await source.onAuthenticated(makeJwt({ sub: 'x', exp: future }), 'rt-1');
      expect(localStorage.getItem('ananta.user.token')).toBeTruthy();

      source.logout();
      expect(source.current.status).toBe('absent');
      expect(localStorage.getItem('ananta.user.token')).toBeNull();
      expect(localStorage.getItem(IDENTITY_STORAGE_LAYOUT.hub.refreshToken.key)).toBeNull();
    });
  });

  describe('clearStorage', () => {
    it('removes all identity keys and emits absent', async () => {
      build(buildStubHttpClient(() => of({})));
      const future = Math.floor(Date.now() / 1000) + 3600;
      await source.onAuthenticated(makeJwt({ sub: 'x', exp: future }), 'rt-1');

      source.clearStorage();
      expect(source.current.status).toBe('absent');
      expect(localStorage.getItem('ananta.user.token')).toBeNull();
    });
  });

  describe('sphere', () => {
    it('is "hub"', () => {
      build(buildStubHttpClient(() => of({})));
      expect(source.sphere).toBe('hub');
    });
  });
});
