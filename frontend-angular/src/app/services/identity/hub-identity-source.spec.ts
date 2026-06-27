import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';
import { of, throwError } from 'rxjs';
import { HubIdentitySource } from './hub-identity-source';
import { UserAuthService } from '../user-auth.service';
import { AgentDirectoryService } from '../agent-directory.service';
import { SecureTokenStorage } from '../secure-token-storage.service';
import { HttpClient } from '@angular/common/http';
import { IDENTITY_STORAGE_LAYOUT } from './identity-storage-layout';

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
      build(buildStubHttpClient(() => throwError(() => new Error('invalid_token'))));

      await source.onAuthenticated(makeJwt({ sub: 'alice', exp: future }), 'rt-1');

      await source.refresh();

      expect(source.current.status).toBe('expired');
      expect(localStorage.getItem('ananta.user.token')).toBeNull();
      expect(localStorage.getItem(IDENTITY_STORAGE_LAYOUT.hub.refreshToken.key)).toBeNull();
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