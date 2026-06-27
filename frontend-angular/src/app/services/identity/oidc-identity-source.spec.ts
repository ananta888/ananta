import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';
import { OidcIdentitySource } from './oidc-identity-source';
import { UserAuthService } from '../user-auth.service';
import { OidcAuthService } from '../oidc-auth.service';
import { SecureTokenStorage } from '../secure-token-storage.service';
import { IDENTITY_STORAGE_LAYOUT } from './identity-storage-layout';

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

    it('emits ready when valid JWT is in storage', async () => {
      const future = Math.floor(Date.now() / 1000) + 3600;
      localStorage.setItem('ananta.oidc.access_token', makeJwt({ sub: 'carol', exp: future }));
      await source.restoreFromStorage();
      expect(source.current.status).toBe('ready');
      expect(source.current.subject).toBe('carol');
      expect(source.current.issuer).toBe('oidc');
    });

    it('emits expired when JWT is past-dated', async () => {
      const past = Math.floor(Date.now() / 1000) - 100;
      localStorage.setItem('ananta.oidc.access_token', makeJwt({ sub: 'carol', exp: past }));
      await source.restoreFromStorage();
      expect(source.current.status).toBe('expired');
    });
  });

  describe('onAuthenticated', () => {
    it('emits ready snapshot, writes access+refresh tokens', async () => {
      const future = Math.floor(Date.now() / 1000) + 3600;
      const jwt = makeJwt({ sub: 'dave', exp: future });
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
      await source.onAuthenticated(makeJwt({ sub: 'eve', exp: future }), 'rt');
      vi.spyOn(oidc, 'refreshFromStorage').mockResolvedValue(true);
      await source.refresh();
      // status remains ready
      expect(source.current.status).toBe('ready');
    });
  });

  describe('logout', () => {
    it('clears tokens, emits absent', async () => {
      const future = Math.floor(Date.now() / 1000) + 3600;
      await source.onAuthenticated(makeJwt({ sub: 'x', exp: future }), 'rt');

      source.logout();
      expect(source.current.status).toBe('absent');
      expect(localStorage.getItem('ananta.oidc.access_token')).toBeNull();
      expect(localStorage.getItem(IDENTITY_STORAGE_LAYOUT.oidc.refreshToken.key)).toBeNull();
    });
  });
});