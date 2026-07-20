import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { E2eEncryptionService, PeerCipherContext } from './e2e-encryption.service';
import { SECURE_KEY_STORE, SecureKeyStorePort, StoredDeviceKeyPair } from './secure-key-store.service';
import { decodeB64, encodeB64 } from './webrtc-secure-envelope';

class MemoryKeyStore implements SecureKeyStorePort {
  current: StoredDeviceKeyPair | null = null;
  async loadCurrent(): Promise<StoredDeviceKeyPair | null> { return this.current; }
  async replaceCurrent(record: StoredDeviceKeyPair): Promise<void> { this.current = record; }
  async clear(): Promise<void> { this.current = null; }
  discardLegacyLocalStorageKey(): boolean { return false; }
}

function serviceWith(store: MemoryKeyStore): E2eEncryptionService {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({ providers: [
    E2eEncryptionService, { provide: SECURE_KEY_STORE, useValue: store },
  ] });
  return TestBed.inject(E2eEncryptionService);
}

describe('E2eEncryptionService AEAD', () => {
  beforeEach(() => localStorage.clear());

  it('round-trips with ECDH+HKDF and authenticates payload type/AAD', async () => {
    const alice = serviceWith(new MemoryKeyStore());
    const alicePublic = await alice.ensureLocalKeyPair();
    const bobStore = new MemoryKeyStore();
    const bob = serviceWith(bobStore);
    const bobPublic = await bob.ensureLocalKeyPair();
    const common = { scopeKind: 'session' as const, scopeId: 'session-1', epoch: 2,
      keyId: 'key-1', contractDigest: 'a'.repeat(64) };
    const aliceContext: PeerCipherContext = {
      ...common, localPeerId: 'alice', remotePeerId: 'bob',
      peerPublicKeySpkiB64: bobPublic.publicKeySpkiB64,
    };
    const bobContext: PeerCipherContext = {
      ...common, localPeerId: 'bob', remotePeerId: 'alice',
      peerPublicKeySpkiB64: alicePublic.publicKeySpkiB64,
    };
    const sealed = await alice.seal(
      aliceContext, new TextEncoder().encode('highly-sensitive-plaintext'),
      { sequence: 1, payloadType: 'pair.view_delta', trafficClass: 'semantic' },
    );
    expect(JSON.stringify(sealed)).not.toContain('highly-sensitive-plaintext');
    const opened = await bob.open(bobContext, sealed);
    expect(new TextDecoder().decode(opened.plaintext)).toBe('highly-sensitive-plaintext');
    await expect(bob.open(bobContext, sealed)).rejects.toThrow('nonce_reuse');

    const swapped = { ...sealed, payload_type: 'pair.control' };
    // A fresh service with the persisted private CryptoKey avoids the replay
    // short-circuit and reaches the AEAD authentication check.
    const restartedBob = serviceWith(bobStore);
    await expect(restartedBob.open(bobContext, swapped)).rejects.toThrow('authentication_failed');
    const ciphertext = decodeB64(sealed.ciphertext_b64);
    ciphertext[0] ^= 1;
    const tampered = { ...sealed, nonce_b64: encodeB64(crypto.getRandomValues(new Uint8Array(12))), ciphertext_b64: encodeB64(ciphertext) };
    await expect(bob.open(bobContext, tampered)).rejects.toThrow('authentication_failed');
  });

  it('fails closed without a local private key', async () => {
    const service = serviceWith(new MemoryKeyStore());
    const context: PeerCipherContext = {
      scopeKind: 'session', scopeId: 's', localPeerId: 'alice', remotePeerId: 'bob',
      peerPublicKeySpkiB64: 'AAAA', epoch: 1, keyId: 'key', contractDigest: '0'.repeat(64),
    };
    await expect(service.seal(
      context, new TextEncoder().encode('secret'),
      { sequence: 1, payloadType: 'pair.view_delta', trafficClass: 'semantic' },
    )).rejects.toThrow('missing_private_key');
  });

  it('derives equal purpose-bound adapter material while keeping device keys non-extractable', async () => {
    const aliceStore = new MemoryKeyStore(); const bobStore = new MemoryKeyStore();
    const alice = serviceWith(aliceStore); const alicePublic = await alice.ensureLocalKeyPair();
    const bob = serviceWith(bobStore); const bobPublic = await bob.ensureLocalKeyPair();
    const common = { scopeKind: 'room' as const, scopeId: 'room-1', epoch: 3,
      keyId: 'key-3', contractDigest: 'b'.repeat(64) };
    const left = await alice.derivePurposeKeyMaterial({
      ...common, localPeerId: 'alice', remotePeerId: 'bob', peerPublicKeySpkiB64: bobPublic.publicKeySpkiB64,
    }, 'media-sfu', 'room-1');
    const right = await bob.derivePurposeKeyMaterial({
      ...common, localPeerId: 'bob', remotePeerId: 'alice', peerPublicKeySpkiB64: alicePublic.publicKeySpkiB64,
    }, 'media-sfu', 'room-1');
    expect(left).toEqual(right); expect(left.byteLength).toBe(32);
    expect(aliceStore.current?.keyPair.privateKey.extractable).toBe(false);
    left.fill(0); right.fill(0);
  });
});
