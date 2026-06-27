import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach } from 'vitest';
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

  it('still writes access token in cleartext (browser constraint)', () => {
    service.setTokens('hub-access-cleartext', 'hub-rt');
    expect(localStorage.getItem('ananta.user.token')).toBe('hub-access-cleartext');
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

  it('clears OIDC RT on setOidcRefreshToken(null)', () => {
    service.setOidcRefreshToken('oidc-rt');
    service.setOidcRefreshToken(null);
    expect(localStorage.getItem('ananta.oidc.refresh_token')).toBeNull();
  });
});
