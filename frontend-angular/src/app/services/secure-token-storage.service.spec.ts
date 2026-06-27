import { TestBed } from '@angular/core/testing';
import { describe, it, expect, beforeEach } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';
import { SecureTokenStorage } from './secure-token-storage.service';

describe('SecureTokenStorage', () => {
  let service: SecureTokenStorage;
  let originalIndexedDB: IDBFactory;

  beforeEach(() => {
    // Replace the global indexedDB with a fresh in-memory instance per test.
    originalIndexedDB = globalThis.indexedDB;
    globalThis.indexedDB = new IDBFactory() as unknown as IDBFactory;
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ providers: [SecureTokenStorage] });
    service = TestBed.inject(SecureTokenStorage);
  });

  it('encrypts and decrypts a round-trip', async () => {
    const plaintext = 'refresh-token-abc.def.ghi';
    const encrypted = await service.encrypt(plaintext, 'ananta.hub.refresh_token');
    const decrypted = await service.decrypt(encrypted, 'ananta.hub.refresh_token');
    expect(decrypted).toBe(plaintext);
  });

  it('produces different ciphertext for same plaintext (random IV)', async () => {
    const a = await service.encrypt('same-input', 'ananta.hub.refresh_token');
    const b = await service.encrypt('same-input', 'ananta.hub.refresh_token');
    expect(a).not.toBe(b);
    expect(a.split('.')).toHaveLength(2);
    expect(b.split('.')).toHaveLength(2);
  });

  it('throws on tampered ciphertext', async () => {
    const encrypted = await service.encrypt('secret', 'ananta.hub.refresh_token');
    const [iv, ct] = encrypted.split('.');
    const tamperedCt = ct.slice(0, -4) + 'AAAA';
    await expect(service.decrypt(`${iv}.${tamperedCt}`, 'ananta.hub.refresh_token'))
      .rejects.toThrow();
  });

  it('uses different keys for different storage keys', async () => {
    await service.encrypt('hub-rt', 'ananta.hub.refresh_token');
    await service.encrypt('oidc-rt', 'ananta.oidc.refresh_token');
    const hubEncrypted = await service.encrypt('hub-rt', 'ananta.hub.refresh_token');
    await expect(service.decrypt(hubEncrypted, 'ananta.oidc.refresh_token'))
      .rejects.toThrow();
  });

  it('reuses the same key on subsequent calls for the same storage key', async () => {
    const a = await service.encrypt('one', 'shared-key');
    const b = await service.encrypt('two', 'shared-key');
    expect(await service.decrypt(a, 'shared-key')).toBe('one');
    expect(await service.decrypt(b, 'shared-key')).toBe('two');
  });
});
