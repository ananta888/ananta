import { describe, expect, it } from 'vitest';

import {
  parseSfuProjectionTrustedKeysetBootstrap,
  SfuProjectionTrustedKeysetService,
  type SfuProjectionTrustedKey,
  type SfuProjectionTrustedKeysetBootstrap,
  WebCryptoSfuProjectionSignatureVerifier,
} from './sfu-projection-signature-verifier.service';

describe('WebCryptoSfuProjectionSignatureVerifier', () => {
  it('supports Ed25519 overlap rotation and rejects a revoked key', async () => {
    const first = await ed25519Key('hub-key:v1', 1);
    const second = await ed25519Key('hub-key:v2', 2);
    const keys = new SfuProjectionTrustedKeysetService(keyset(0, []));
    const verifier = new WebCryptoSfuProjectionSignatureVerifier(keys);
    const document = { room_ref: 'room-a', projection_version: 1 };
    const canonical = JSON.stringify({ projection_version: 1, room_ref: 'room-a' });
    const digest = hex(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(canonical))));
    const input = async (candidate: Awaited<ReturnType<typeof ed25519Key>>) => ({
      contractId: 'ananta.sfu-room-session-projection.v1', document, digest,
      signature: await signEd25519(candidate.privateKey, digest), keyId: candidate.trusted.keyId,
      algorithm: 'Ed25519' as const, algorithmVersion: 1, keyVersion: candidate.trusted.keyVersion,
    });

    expect(await verifier.verify(await input(first))).toBe(false);
    keys.replace(keyset(1, [{ ...first.trusted, status: 'active' }]));
    expect(await verifier.verify(await input(first))).toBe(true);
    keys.replace(keyset(2, [
      { ...first.trusted, status: 'overlap' },
      { ...second.trusted, status: 'active' },
    ]));
    expect(await verifier.verify(await input(first))).toBe(true);
    expect(await verifier.verify(await input(second))).toBe(true);
    keys.replace(keyset(3, [
      { ...first.trusted, status: 'revoked' },
      { ...second.trusted, status: 'active' },
    ]));
    expect(await verifier.verify(await input(first))).toBe(false);
  });

  it('admits HMAC only when the bootstrap key is explicitly legacy', async () => {
    const secret = Uint8Array.from({ length: 32 }, (_, index) => index + 1);
    const invalid = keyset(1, [{
      keyId: 'legacy-test:v1', algorithm: 'HMAC-SHA-256', algorithmVersion: 1,
      keyVersion: 1, status: 'active', format: 'raw', keyMaterialBase64Url: base64Url(secret),
    } as SfuProjectionTrustedKey]);
    expect(() => parseSfuProjectionTrustedKeysetBootstrap(invalid)).toThrow('sfu_projection_key_invalid');

    const keys = new SfuProjectionTrustedKeysetService(keyset(1, [{
      ...invalid.keys[0], legacy: true,
    }]));
    const verifier = new WebCryptoSfuProjectionSignatureVerifier(keys);
    const document = { room_ref: 'room-a' };
    const digest = hex(new Uint8Array(await crypto.subtle.digest(
      'SHA-256', new TextEncoder().encode(JSON.stringify(document)),
    )));
    expect(await verifier.verify({
      contractId: 'ananta.sfu-room-session-projection.v1', document, digest,
      signature: await signHmac(secret, digest), keyId: 'legacy-test:v1',
      algorithm: 'HMAC-SHA-256', algorithmVersion: 1, keyVersion: 1,
    })).toBe(true);
  });
});

function keyset(version: number, keys: readonly SfuProjectionTrustedKey[]): SfuProjectionTrustedKeysetBootstrap {
  return {
    schema: 'ananta.sfu-projection-trusted-keyset.v1', keysetVersion: version, keys,
  };
}

async function ed25519Key(keyId: string, keyVersion: number) {
  const pair = await crypto.subtle.generateKey({ name: 'Ed25519' }, true, ['sign', 'verify']) as CryptoKeyPair;
  const publicKey = new Uint8Array(await crypto.subtle.exportKey('raw', pair.publicKey));
  return {
    privateKey: pair.privateKey,
    trusted: {
      keyId, algorithm: 'Ed25519' as const, algorithmVersion: 1, keyVersion,
      status: 'active' as const, format: 'raw' as const, keyMaterialBase64Url: base64Url(publicKey),
    },
  };
}

async function signEd25519(privateKey: CryptoKey, digest: string): Promise<string> {
  const signature = await crypto.subtle.sign(
    { name: 'Ed25519' }, privateKey, new TextEncoder().encode(`ananta:sfu-projection:v1:${digest}`),
  );
  return base64Url(new Uint8Array(signature));
}

async function signHmac(secret: Uint8Array, digest: string): Promise<string> {
  const key = await crypto.subtle.importKey('raw', secret, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const signature = await crypto.subtle.sign(
    'HMAC', key, new TextEncoder().encode(`ananta:sfu-projection:v1:${digest}`),
  );
  return base64Url(new Uint8Array(signature));
}

function base64Url(value: Uint8Array): string {
  return btoa(String.fromCharCode(...value)).replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_');
}

function hex(value: Uint8Array): string {
  return [...value].map(byte => byte.toString(16).padStart(2, '0')).join('');
}
