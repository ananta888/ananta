import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';
import { OidcIdentitySource } from './oidc-identity-source';
import { UserAuthService } from '../user-auth.service';
import { OidcAuthService } from '../oidc-auth.service';
import { SecureTokenStorage } from '../secure-token-storage.service';
import { IDENTITY_STORAGE_LAYOUT } from './identity-storage-layout';

const OIDC_ISSUER = 'https://keycloak.ananta.de/realms/ananta';

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.sig`;
}

describe('OidcIdentitySource', () => {
  let source: OidcIdentitySource;
  let oidc: OidcAuthService;

  beforeEach(() => {
    localStorage.clear();
    globalThis.indexedDB = new IDBFactory() as unknown as IDBFactory;
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        OidcIdentitySource,
        UserAuthService,
        OidcAuthService,
        SecureTokenStorage,
      ],
    });
    source = TestBed.inject(OidcIdentitySource);
    oidc = TestBed.inject(OidcAuthService);
    TestBed.inject(SecureTokenStorage)._clearCacheForTesting();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  describe('sphere', () => {
    it('is "oidc"', () => {
      expect(source.sphere).toBe('oidc');
    });
  });

  describe('restoreFromStorage', () => {
    it('emits absent when no access token in storage', async () => {
      await source.restoreFromStorage();
      expect(source.current.status).toBe('absent');
    });

    it('recovers from an encrypted refresh token when the stale access token was already cleared', async () => {
      const future = Math.floor(Date.now() / 1000) + 3600;
      await TestBed.inject(UserAuthService).setOidcRefreshToken('refresh-token');
      vi.spyOn(oidc, 'refreshFromStorage').mockImplementation(async () => {
        TestBed.inject(UserAuthService).setOidcAccessToken(makeJwt({
          iss: OIDC_ISSUER, sub: 'carol', exp: future,
        }));
        return true;
      });

      await source.restoreFromStorage();

      expect(oidc.refreshFromStorage).toHaveBeenCalledOnce();
      expect(source.current.status).toBe('ready');
    });

    it('emits ready when valid JWT is in storage', async () => {
      const future = Math.floor(Date.now() / 1000) + 3600;
      localStorage.setItem('ananta.oidc.access_token', makeJwt({
        iss: OIDC_ISSUER, sub: 'carol', exp: future,
      }));
      await source.restoreFromStorage();
      expect(source.current.status).toBe('ready');
      expect(source.current.subject).toBe('carol');
      expect(source.current.issuer).toBe('oidc');
    });

    it('emits expired when JWT is past-dated', async () => {
      const past = Math.floor(Date.now() / 1000) - 100;
      localStorage.setItem('ananta.oidc.access_token', makeJwt({
        iss: OIDC_ISSUER, sub: 'carol', exp: past,
      }));
      await source.restoreFromStorage();
      expect(source.current.status).toBe('expired');
      expect(localStorage.getItem('ananta.oidc.access_token')).toBeNull();
    });

    it('refreshes an already expired token before finishing boot restore', async () => {
      const past = Math.floor(Date.now() / 1000) - 100;
      const future = Math.floor(Date.now() / 1000) + 3600;
      localStorage.setItem('ananta.oidc.access_token', makeJwt({
        iss: OIDC_ISSUER, sub: 'carol', exp: past,
      }));
      vi.spyOn(oidc, 'refreshFromStorage').mockImplementation(async () => {
        TestBed.inject(UserAuthService).setOidcAccessToken(makeJwt({
          iss: OIDC_ISSUER, sub: 'carol', exp: future,
        }));
        return true;
      });

      await source.restoreFromStorage();

      expect(oidc.refreshFromStorage).toHaveBeenCalledOnce();
      expect(source.current.status).toBe('ready');
      expect(source.current.expiresAt).toBe(future);
    });

    it('does not let a stale boot restore overwrite a newer popup login', async () => {
      const auth = TestBed.inject(UserAuthService);
      const oldToken = makeJwt({
        iss: OIDC_ISSUER,
        sub: 'old-user',
        exp: Math.floor(Date.now() / 1000) + 1_800,
      });
      auth.setOidcAccessToken(oldToken);
      let resolveOldRefreshToken!: (value: string | null) => void;
      vi.spyOn(auth, 'getOidcRefreshToken')
        .mockImplementationOnce(() => new Promise((resolve) => {
          resolveOldRefreshToken = resolve;
        }))
        .mockResolvedValue('new-refresh-token');

      const restore = source.restoreFromStorage();
      await vi.waitFor(() => expect(auth.getOidcRefreshToken).toHaveBeenCalledOnce());
      const newToken = makeJwt({
        iss: OIDC_ISSUER,
        sub: 'new-user',
        exp: Math.floor(Date.now() / 1000) + 3_600,
      });
      await source.onAuthenticated(newToken, 'new-refresh-token');
      resolveOldRefreshToken('old-refresh-token');
      await restore;

      expect(source.current.status).toBe('ready');
      expect(source.current.subject).toBe('new-user');
      expect(auth.oidcAccessTokenValue).toBe(newToken);
    });
  });

  describe('onAuthenticated', () => {
    it('emits ready snapshot, writes access+refresh tokens', async () => {
      const future = Math.floor(Date.now() / 1000) + 3600;
      const jwt = makeJwt({ iss: OIDC_ISSUER, sub: 'dave', exp: future });
      await source.onAuthenticated(jwt, 'oidc-rt-cleartext');

      expect(source.current.status).toBe('ready');
      expect(source.current.subject).toBe('dave');
      expect(localStorage.getItem('ananta.oidc.access_token')).toBe(jwt);
      const stored = localStorage.getItem(IDENTITY_STORAGE_LAYOUT.oidc.refreshToken.key);
      expect(stored).toBeTruthy();
      expect(stored).not.toBe('oidc-rt-cleartext');
    });
  });

  describe('refresh', () => {
    it('marks expired when OidcAuthService.refreshFromStorage returns false', async () => {
      vi.spyOn(oidc, 'refreshFromStorage').mockResolvedValue(false);
      await source.refresh();
      expect(source.current.status).toBe('expired');
      expect(source.current.error).toContain('oidc refresh');
    });

    it('marks expired when OidcAuthService.refreshFromStorage throws', async () => {
      vi.spyOn(oidc, 'refreshFromStorage').mockRejectedValue(new Error('network down'));
      await source.refresh();
      expect(source.current.status).toBe('expired');
      expect(source.current.error).toBe('network down');
    });

    it('does not change status when refreshFromStorage returns true', async () => {
      const future = Math.floor(Date.now() / 1000) + 3600;
      await source.onAuthenticated(makeJwt({ iss: OIDC_ISSUER, sub: 'eve', exp: future }), 'rt');
      vi.spyOn(oidc, 'refreshFromStorage').mockResolvedValue(true);
      await source.refresh();
      // status remains ready
      expect(source.current.status).toBe('ready');
    });

    it('clears an unusable access token returned by a nominally successful refresh', async () => {
      const future = Math.floor(Date.now() / 1000) + 3600;
      await source.onAuthenticated(makeJwt({
        iss: OIDC_ISSUER, sub: 'eve', exp: future,
      }), 'rt');
      vi.spyOn(oidc, 'refreshFromStorage').mockImplementation(async () => {
        TestBed.inject(UserAuthService).setOidcAccessToken('malformed-token');
        return true;
      });

      await source.refresh();

      expect(source.current.status).toBe('expired');
      expect(localStorage.getItem('ananta.oidc.access_token')).toBeNull();
    });

    it('does not let an older failed refresh clear a newer popup login', async () => {
      let resolveOldRefresh!: (value: boolean) => void;
      vi.spyOn(oidc, 'refreshFromStorage').mockImplementation(
        () => new Promise<boolean>((resolve) => { resolveOldRefresh = resolve; }),
      );

      const oldRefresh = source.refresh();
      const future = Math.floor(Date.now() / 1000) + 3_600;
      const newerToken = makeJwt({ iss: OIDC_ISSUER, sub: 'new-user', exp: future });
      await source.onAuthenticated(newerToken, 'new-refresh-token');
      resolveOldRefresh(false);
      await oldRefresh;

      expect(source.current.status).toBe('ready');
      expect(source.current.subject).toBe('new-user');
      expect(TestBed.inject(UserAuthService).oidcAccessTokenValue).toBe(newerToken);
      await expect(TestBed.inject(UserAuthService).getOidcRefreshToken())
        .resolves.toBe('new-refresh-token');
    });

    it('keeps a current session and retries after a transient refresh failure', async () => {
      const auth = TestBed.inject(UserAuthService);
      const accessToken = makeJwt({
        iss: OIDC_ISSUER,
        sub: 'retry-user',
        exp: Math.floor(Date.now() / 1000) + 3_600,
      });
      await source.onAuthenticated(accessToken, 'refresh-token');
      vi.spyOn(auth, 'getOidcRefreshToken').mockResolvedValue('refresh-token');
      vi.spyOn(oidc, 'refreshFromStorage').mockResolvedValue(false);
      const setTimeoutSpy = vi.spyOn(globalThis, 'setTimeout');

      await source.refresh();

      expect(source.current.status).toBe('ready');
      expect(auth.oidcAccessTokenValue).toBe(accessToken);
      expect(setTimeoutSpy).toHaveBeenLastCalledWith(expect.any(Function), 10_000);
    });

    it('keeps an access-token-only login until its real expiry', async () => {
      vi.useFakeTimers();
      vi.setSystemTime(new Date('2026-08-08T20:00:00.000Z'));
      const auth = TestBed.inject(UserAuthService);
      const secureStorage = TestBed.inject(SecureTokenStorage);
      vi.spyOn(secureStorage, 'encrypt').mockRejectedValueOnce(
        new Error('secure refresh storage unavailable'),
      );
      const accessToken = makeJwt({
        iss: OIDC_ISSUER,
        sub: 'access-only-user',
        exp: Math.floor(Date.now() / 1000) + 120,
      });

      await source.onAuthenticated(accessToken, 'refresh-token');
      await vi.advanceTimersByTimeAsync(60_000);

      expect(source.current.status).toBe('ready');
      expect(source.current.refreshToken).toBeUndefined();
      expect(auth.oidcAccessTokenValue).toBe(accessToken);

      await vi.advanceTimersByTimeAsync(60_000);

      expect(source.current.status).toBe('expired');
      expect(auth.oidcAccessTokenValue).toBeNull();
    });
  });

  describe('logout', () => {
    it('clears tokens, emits absent', async () => {
      const future = Math.floor(Date.now() / 1000) + 3600;
      await source.onAuthenticated(makeJwt({ iss: OIDC_ISSUER, sub: 'x', exp: future }), 'rt');
      localStorage.setItem('ananta.oidc.refresh-authority.v1', 'pinned-authority');

      source.logout();
      expect(source.current.status).toBe('absent');
      expect(localStorage.getItem('ananta.oidc.access_token')).toBeNull();
      expect(localStorage.getItem(IDENTITY_STORAGE_LAYOUT.oidc.refreshToken.key)).toBeNull();
      expect(localStorage.getItem(IDENTITY_STORAGE_LAYOUT.oidc.refreshAuthority.key)).toBeNull();
    });
  });
});
