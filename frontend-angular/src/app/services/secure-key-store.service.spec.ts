import { TestBed } from '@angular/core/testing';
import { IDBFactory } from 'fake-indexeddb';
import { beforeEach, describe, expect, it } from 'vitest';

import { E2eEncryptionService } from './e2e-encryption.service';
import {
  SECURE_KEY_STORE,
  SecureKeyStorePort,
  SecureKeyStoreService,
  StoredDeviceKeyPair,
} from './secure-key-store.service';

describe('SecureKeyStoreService', () => {
  beforeEach(() => {
    globalThis.indexedDB = new IDBFactory() as unknown as IDBFactory;
    localStorage.clear();
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ providers: [SecureKeyStoreService, E2eEncryptionService] });
  });

  it('persists a non-extractable private CryptoKey across service restart', async () => {
    const firstService = TestBed.inject(E2eEncryptionService);
    const first = await firstService.ensureLocalKeyPair();
    const stored = await TestBed.inject(SecureKeyStoreService).loadCurrent();
    expect(stored?.keyPair.privateKey.extractable).toBe(false);
    await expect(crypto.subtle.exportKey('pkcs8', stored!.keyPair.privateKey)).rejects.toThrow();

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ providers: [SecureKeyStoreService, E2eEncryptionService] });
    const afterRestart = await TestBed.inject(E2eEncryptionService).ensureLocalKeyPair();
    expect(afterRestart.fingerprint).toBe(first.fingerprint);
    expect(afterRestart.generation).toBe(1);
  });

  it('discards legacy Local-Storage PKCS8 and requires peer rebind', async () => {
    localStorage.setItem(SecureKeyStoreService.LEGACY_LOCAL_STORAGE_KEY, JSON.stringify({
      privateKeyPkcs8B64: 'must-never-be-imported',
    }));
    const created = await TestBed.inject(E2eEncryptionService).ensureLocalKeyPair();
    expect(created.peerRebindRequired).toBe(true);
    expect(localStorage.getItem(SecureKeyStoreService.LEGACY_LOCAL_STORAGE_KEY)).toBeNull();
  });

  it('rotates atomically and clears all stored material idempotently', async () => {
    const encryption = TestBed.inject(E2eEncryptionService);
    const first = await encryption.ensureLocalKeyPair();
    const rotated = await encryption.rotateLocalKeyPair();
    expect(rotated.generation).toBe(2);
    expect(rotated.fingerprint).not.toBe(first.fingerprint);
    await encryption.clearAllKeyMaterial();
    await encryption.clearAllKeyMaterial();
    expect(await TestBed.inject(SecureKeyStoreService).loadCurrent()).toBeNull();
  });

  it('does not expose a half-written key after storage failure', async () => {
    const failed: SecureKeyStorePort = {
      loadCurrent: async () => null,
      replaceCurrent: async () => { throw new Error('quota'); },
      clear: async () => {},
      discardLegacyLocalStorageKey: () => false,
    };
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ providers: [
      E2eEncryptionService, { provide: SECURE_KEY_STORE, useValue: failed },
    ] });
    await expect(TestBed.inject(E2eEncryptionService).ensureLocalKeyPair()).rejects.toThrow('quota');
    expect(await failed.loadCurrent()).toBeNull();
  });
});
