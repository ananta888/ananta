import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';

import { UserAuthService } from './user-auth.service';
import { SecureTokenStorage } from './secure-token-storage.service';
import { AgentDirectoryService } from './agent-directory.service';
import { HttpClient } from '@angular/common/http';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';

describe('UserAuthService — storage migration (Task 0.2)', () => {
  let service: UserAuthService;
  let secureStorage: SecureTokenStorage;
  let originalIndexedDB: IDBFactory;

  beforeEach(() => {
    localStorage.clear();
    originalIndexedDB = globalThis.indexedDB;
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
          useValue: { list: () => [] },
        },
      ],
    });
    service = TestBed.inject(UserAuthService);
    secureStorage = TestBed.inject(SecureTokenStorage);
    secureStorage._clearCacheForTesting();
  });

  it('migrates legacy cleartext refresh token to encrypted hub storage', async () => {
    localStorage.setItem('ananta.user.refresh_token', 'legacy-cleartext-rt');

    await service.runStorageMigration();

    expect(localStorage.getItem('ananta.user.refresh_token')).toBeNull();
    expect(await service.getHubRefreshToken()).toBe('legacy-cleartext-rt');
  });

  it('is a no-op when no legacy token is present', async () => {
    await service.runStorageMigration();
    expect(localStorage.getItem('ananta.hub.refresh_token')).toBeNull();
    expect(await service.getHubRefreshToken()).toBeNull();
  });

  it('encrypted value is not the plaintext', async () => {
    localStorage.setItem('ananta.user.refresh_token', 'super-secret');
    await service.runStorageMigration();
    const stored = localStorage.getItem('ananta.hub.refresh_token');
    expect(stored).toBeTruthy();
    expect(stored).not.toContain('super-secret');
  });
});

describe('UserAuthService — setTokens encrypts hub RT (Task 0.3)', () => {
  let service: UserAuthService;
  let secureStorage: SecureTokenStorage;
  let originalIndexedDB: IDBFactory;

  beforeEach(() => {
    localStorage.clear();
    originalIndexedDB = globalThis.indexedDB;
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
          useValue: { list: () => [] },
        },
      ],
    });
    service = TestBed.inject(UserAuthService);
    secureStorage = TestBed.inject(SecureTokenStorage);
    secureStorage._clearCacheForTesting();
  });

  it('encrypts hub refresh token on setTokens', async () => {
    await service.setTokens('hub-access-token', 'hub-rt-cleartext');

    const stored = localStorage.getItem('ananta.hub.refresh_token');
    expect(stored).toBeTruthy();
    expect(stored).not.toBe('hub-rt-cleartext');
    expect(stored).toMatch(/^[A-Za-z0-9+/=]+\.[A-Za-z0-9+/=]+$/);
    expect(localStorage.getItem('ananta.user.refresh_token')).toBeNull();
    expect(await service.getHubRefreshToken()).toBe('hub-rt-cleartext');
  });

  it('clears hub refresh token on logout (setTokens(null, null))', async () => {
    await service.setTokens('hub-access', 'hub-rt');
    expect(localStorage.getItem('ananta.hub.refresh_token')).toBeTruthy();

    await service.setTokens(null, null);
    expect(localStorage.getItem('ananta.hub.refresh_token')).toBeNull();
    expect(localStorage.getItem('ananta.user.token')).toBeNull();
  });

  it('still writes access token in cleartext (browser constraint)', async () => {
    await service.setTokens('hub-access-cleartext', 'hub-rt');
    expect(localStorage.getItem('ananta.user.token')).toBe('hub-access-cleartext');
  });

  it('keeps the access-token session when secure refresh-token storage is unavailable', async () => {
    vi.spyOn(secureStorage, 'encrypt').mockRejectedValueOnce(new Error('Web Crypto unavailable'));

    await expect(service.setTokens('hub-access-over-http', 'hub-rt')).resolves.toBeUndefined();

    expect(localStorage.getItem('ananta.user.token')).toBe('hub-access-over-http');
    expect(localStorage.getItem('ananta.hub.refresh_token')).toBeNull();
    expect(localStorage.getItem('ananta.user.refresh_token')).toBeNull();
    expect(service.refreshTokenValue).toBeNull();
  });
});

describe('UserAuthService — OIDC RT encryption (Task 0.4)', () => {
  let service: UserAuthService;
  let secureStorage: SecureTokenStorage;
  let originalIndexedDB: IDBFactory;

  beforeEach(() => {
    localStorage.clear();
    originalIndexedDB = globalThis.indexedDB;
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
          useValue: { list: () => [] },
        },
      ],
    });
    service = TestBed.inject(UserAuthService);
    secureStorage = TestBed.inject(SecureTokenStorage);
    secureStorage._clearCacheForTesting();
  });

  it('setOidcRefreshToken encrypts the refresh token', async () => {
    await service.setOidcRefreshToken('oidc-rt-cleartext');
    const stored = localStorage.getItem('ananta.oidc.refresh_token');
    expect(stored).toBeTruthy();
    expect(stored).not.toBe('oidc-rt-cleartext');
    expect(await service.getOidcRefreshToken()).toBe('oidc-rt-cleartext');
  });

  it('migrates legacy OIDC RT (if present) to encrypted form', async () => {
    localStorage.setItem('ananta.oidc.refresh_token', 'legacy-oidc-rt');

    await service.runStorageMigration();

    // After migration, the storage key holds the encrypted form.
    // The encrypted form must not contain the plaintext.
    const stored = localStorage.getItem('ananta.oidc.refresh_token');
    expect(stored).toBeTruthy();
    expect(stored).not.toBe('legacy-oidc-rt');
    expect(stored).not.toContain('legacy-oidc-rt');
    expect(stored).toMatch(/^[A-Za-z0-9+/=]+\.[A-Za-z0-9+/=]+$/);
    expect(await service.getOidcRefreshToken()).toBe('legacy-oidc-rt');
  });

  it('clears OIDC RT on setOidcRefreshToken(null)', async () => {
    await service.setOidcRefreshToken('oidc-rt');
    await service.setOidcRefreshToken(null);
    expect(localStorage.getItem('ananta.oidc.refresh_token')).toBeNull();
  });

  it('commits an OIDC access/refresh pair together', async () => {
    const before = service.oidcSessionGenerationValue;

    await expect(service.commitOidcSession('oidc-at', 'oidc-rt')).resolves.toEqual({
      committed: true,
      refreshTokenPersisted: true,
    });

    expect(service.oidcAccessTokenValue).toBe('oidc-at');
    expect(await service.getOidcRefreshToken()).toBe('oidc-rt');
    expect(service.oidcSessionGenerationValue).toBeGreaterThan(before);
  });

  it('does not let a stale encrypted refresh overwrite a newer login', async () => {
    await service.commitOidcSession('old-at', 'old-rt');
    const oldEncrypted = localStorage.getItem('ananta.oidc.refresh_token');
    const generation = service.oidcSessionGenerationValue;
    let releaseEncryption!: (value: string) => void;
    vi.spyOn(secureStorage, 'encrypt').mockImplementationOnce(() => new Promise((resolve) => {
      releaseEncryption = resolve;
    }));

    const staleCommit = service.commitOidcSession('stale-at', 'stale-rt', generation);
    service.setOidcAccessToken('new-at');
    releaseEncryption('stale-encrypted-value');

    await expect(staleCommit).resolves.toEqual({
      committed: false,
      refreshTokenPersisted: false,
    });
    expect(service.oidcAccessTokenValue).toBe('new-at');
    expect(localStorage.getItem('ananta.oidc.refresh_token')).toBe(oldEncrypted);
  });

  it('keeps the access token when secure refresh-token storage is unavailable', async () => {
    await service.commitOidcSession('old-at', 'old-refresh-token');
    expect(localStorage.getItem('ananta.oidc.refresh_token')).toBeTruthy();
    vi.spyOn(secureStorage, 'encrypt').mockRejectedValueOnce(new Error('indexeddb unavailable'));

    await expect(service.commitOidcSession('short-lived-at', 'must-not-be-plaintext'))
      .resolves.toEqual({ committed: true, refreshTokenPersisted: false });

    expect(service.oidcAccessTokenValue).toBe('short-lived-at');
    expect(localStorage.getItem('ananta.oidc.access_token')).toBe('short-lived-at');
    expect(localStorage.getItem('ananta.oidc.refresh_token')).toBeNull();
  });
});
