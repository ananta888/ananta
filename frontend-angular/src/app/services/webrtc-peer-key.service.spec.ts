import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { E2eEncryptionService } from './e2e-encryption.service';
import {
  PeerPackageVerificationOptions,
  SignedPeerKeyPackage,
  WebrtcPeerKeyService,
} from './webrtc-peer-key.service';
import { canonicalSecurityJson } from './webrtc-secure-envelope';

const NOW = 1_800_000_000_000;
const CONTRACT_DIGEST = 'd'.repeat(64);
const CONFIRMATION_TAG = btoa('t'.repeat(32));

describe('WebrtcPeerKeyService package refresh verification', () => {
  beforeEach(() => {
    localStorage.clear();
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ providers: [
      WebrtcPeerKeyService,
      { provide: E2eEncryptionService, useValue: {
        fingerprintSpki: (value: string) => sha256B64(value),
        confirmationTag: vi.fn(async () => CONFIRMATION_TAG),
        forgetEpoch: vi.fn(),
      } },
    ] });
  });

  it('re-verifies a same-id refresh and preserves confirmation only for stable identity', async () => {
    const material = await fixture();
    const service = TestBed.inject(WebrtcPeerKeyService);
    const initial = await service.verifyAndRefreshBinding(material.package, material.options);
    await service.createConfirmation();
    await service.acceptPeerConfirmation(CONFIRMATION_TAG);
    expect(service.currentBinding?.confirmed).toBe(true);

    const refreshed = await signPackage(material.signer, {
      ...material.package,
      issued_at_ms: NOW,
      expires_at_ms: NOW + 120_000,
    });
    const binding = await service.verifyAndRefreshBinding(refreshed, material.options);

    expect(binding.packageId).toBe(initial.packageId);
    expect(binding.confirmed).toBe(true);
    expect(binding.authorityKeyId).toBe(material.package.hub_key_id);
  });

  it('rejects an expired refresh even when its package id matches the confirmed binding', async () => {
    const material = await fixture();
    const service = TestBed.inject(WebrtcPeerKeyService);
    await service.verifyAndRefreshBinding(material.package, material.options);
    const expired = await signPackage(material.signer, {
      ...material.package,
      issued_at_ms: NOW - 60_000,
      expires_at_ms: NOW - 1,
    });

    await expect(service.verifyAndRefreshBinding(expired, material.options))
      .rejects.toMatchObject({ reasonCode: 'key_package_expired' });
  });

  it('rejects a valid same-id package signed by a different unpinned authority', async () => {
    const material = await fixture();
    const service = TestBed.inject(WebrtcPeerKeyService);
    await service.verifyAndRefreshBinding(material.package, material.options);
    const otherSigner = await crypto.subtle.generateKey(
      { name: 'Ed25519' }, true, ['sign', 'verify'],
    ) as CryptoKeyPair;
    const otherHubKeyId = await rendezvousKeyId(otherSigner.publicKey);
    const changedSignerPackage = await signPackage(otherSigner, {
      ...material.package,
      hub_key_id: otherHubKeyId,
      issued_at_ms: NOW,
      expires_at_ms: NOW + 120_000,
    });
    const changedSignerOptions: PeerPackageVerificationOptions = {
      ...material.options,
      expectedHubKeyId: otherHubKeyId,
      hubPublicKeyB64: await exportRawB64(otherSigner.publicKey),
    };

    await expect(service.verifyAndRefreshBinding(changedSignerPackage, changedSignerOptions))
      .rejects.toMatchObject({ reasonCode: 'hub_key_changed' });
  });

  it('rejects a substituted public key that falsely claims the pinned rendezvous key id', async () => {
    const material = await fixture();
    const otherSigner = await crypto.subtle.generateKey(
      { name: 'Ed25519' }, true, ['sign', 'verify'],
    ) as CryptoKeyPair;
    const forged = await signPackage(otherSigner, {
      ...material.package,
      hub_key_id: material.package.hub_key_id,
      issued_at_ms: NOW,
      expires_at_ms: NOW + 120_000,
    });
    const forgedOptions = {
      ...material.options,
      hubPublicKeyB64: await exportRawB64(otherSigner.publicKey),
    };
    const service = TestBed.inject(WebrtcPeerKeyService);

    await expect(service.verifyAndRefreshBinding(forged, forgedOptions))
      .rejects.toMatchObject({ reasonCode: 'hub_key_unknown' });
  });
});

async function fixture(): Promise<{
  signer: CryptoKeyPair;
  package: SignedPeerKeyPackage;
  options: PeerPackageVerificationOptions;
}> {
  const signer = await crypto.subtle.generateKey(
    { name: 'Ed25519' }, true, ['sign', 'verify'],
  ) as CryptoKeyPair;
  const peerKey = await crypto.subtle.generateKey(
    { name: 'ECDH', namedCurve: 'P-256' }, true, ['deriveBits'],
  ) as CryptoKeyPair;
  const hubKeyId = await rendezvousKeyId(signer.publicKey);
  const peerSpki = bytesToB64(await crypto.subtle.exportKey('spki', peerKey.publicKey));
  const unsigned = {
    version: 1 as const,
    package_id: 'a'.repeat(64),
    membership_id: 'member:remote',
    membership_version: 1,
    tenant_id: 'public-ananta',
    scope_kind: 'session' as const,
    scope_id: 'session-a',
    epoch: 3,
    peer_id: 'oidc:remote',
    recipient_peer_id: 'oidc:local',
    device_id: 'device-remote',
    device_key_fingerprint: await sha256B64(peerSpki),
    ecdh_public_key_spki_b64: peerSpki,
    issued_at_ms: NOW - 1_000,
    expires_at_ms: NOW + 120_000,
    hub_key_id: hubKeyId,
    security_contract_digest: CONTRACT_DIGEST,
  };
  return {
    signer,
    package: await signPackage(signer, unsigned),
    options: {
      hubPublicKeyB64: await exportRawB64(signer.publicKey),
      expectedHubKeyId: hubKeyId,
      expectedTenantId: 'public-ananta',
      expectedScopeId: 'session-a',
      expectedEpoch: 3,
      localPeerId: 'oidc:local',
      contractDigest: CONTRACT_DIGEST,
      nowMs: NOW,
    },
  };
}

async function signPackage(
  signer: CryptoKeyPair,
  value: Omit<SignedPeerKeyPackage, 'signature_b64'> | SignedPeerKeyPackage,
): Promise<SignedPeerKeyPackage> {
  const { signature_b64: _discarded, ...unsigned } = value as SignedPeerKeyPackage;
  const signature = await crypto.subtle.sign(
    'Ed25519', signer.privateKey, new TextEncoder().encode(canonicalSecurityJson(unsigned)),
  );
  return { ...unsigned, signature_b64: bytesToB64(signature) } as SignedPeerKeyPackage;
}

async function exportRawB64(key: CryptoKey): Promise<string> {
  return bytesToB64(await crypto.subtle.exportKey('raw', key));
}

async function rendezvousKeyId(key: CryptoKey): Promise<string> {
  const raw = await crypto.subtle.exportKey('raw', key);
  const digest = await crypto.subtle.digest('SHA-256', raw);
  return `rv:${Array.from(new Uint8Array(digest))
    .map(byte => byte.toString(16).padStart(2, '0')).join('').slice(0, 24)}`;
}

async function sha256B64(value: string): Promise<string> {
  const raw = Uint8Array.from(atob(value), char => char.charCodeAt(0));
  const digest = await crypto.subtle.digest('SHA-256', raw);
  return Array.from(new Uint8Array(digest))
    .map(byte => byte.toString(16).padStart(2, '0')).join('');
}

function bytesToB64(value: ArrayBuffer): string {
  return btoa(String.fromCharCode(...new Uint8Array(value)));
}
