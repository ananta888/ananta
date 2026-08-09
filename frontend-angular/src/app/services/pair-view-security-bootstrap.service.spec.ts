import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { AgentDirectoryService } from './agent-directory.service';
import { E2eEncryptionService } from './e2e-encryption.service';
import { HubApiCoreService } from './hub-api-core.service';
import { PairViewSecurityBootstrapService } from './pair-view-security-bootstrap.service';
import { PairSessionControlPlaneService } from './pair-session-control-plane.service';
import {
  PUBLIC_PAIR_MEDIA_GRANTS,
  PUBLIC_PAIR_MEDIA_SLOTS,
} from './public-pair-media-security-contract';
import { PUBLIC_PAIR_MEDIA_FRAME_FORMAT_V2 } from './pair-media-frame-format';
import { ShareSession } from './share-session.service';
import { WebrtcPeerKeyService } from './webrtc-peer-key.service';
import { canonicalSecurityJson } from './webrtc-secure-envelope';
import {
  PUBLIC_RENDEZVOUS_SIGNING_KEY_ID,
  PUBLIC_RENDEZVOUS_SIGNING_PUBLIC_KEY_B64,
} from './public-ananta-endpoints';

const session: ShareSession = {
  id: 'session-a', title: 'Strict Pair', invite_code: 'invite', mode: 'p2p', transport: 'webrtc',
  permissions: { chat: true }, created_at: 1, expires_at: null, revoked_at: null,
  owner_user_id: 'alice', security_epoch: 3, security_contract_version: 1, security_mode: 'strict_e2ee',
};
const localMediaPeerId = `peer:${'1'.repeat(64)}`;
const remoteMediaPeerId = `peer:${'2'.repeat(64)}`;
const localMediaFingerprint = 'a'.repeat(64);
const remoteMediaFingerprint = 'b'.repeat(64);

describe('PairViewSecurityBootstrapService production contract seam', () => {
  it('validates the final transcript before binding and confirming the peer', async () => {
    const response = await keyPackageResponse();
    const peerKeys = fakePeerKeys();
    const posts: unknown[] = [];
    const core = {
      get: vi.fn((url: string) => of(url.includes('key-packages') ? response : {
        ok: true,
        local_peer_id: 'alice',
        confirmation: confirmation(),
      })),
      post: vi.fn((_url: string, body: unknown) => {
        posts.push(body);
        return of({ ok: true, local_peer_id: 'alice' });
      }),
    };
    configure(core, peerKeys);
    const bootstrap = TestBed.inject(PairViewSecurityBootstrapService);

    await expect(bootstrap.ensure(session, 'alice')).resolves.toBe(true);
    expect(peerKeys.verifyAndRefreshBinding).toHaveBeenCalledOnce();
    expect(posts).toEqual([expect.objectContaining({
      recipient_peer_id: 'bob', package_id: 'a'.repeat(64), epoch: 3,
    })]);
    expect(peerKeys.acceptPeerConfirmation).toHaveBeenCalledWith(confirmationTag());
    expect(bootstrap.state$.value.status).toBe('ready');
    expect(bootstrap.confirmedRemotePeerId).toBe('bob');
  });

  it('keeps the signaling audience unavailable while waiting for the peer', async () => {
    const response = await keyPackageResponse();
    response.packages = [];
    response.security_contract = null;
    response.security_contract_digest = null;
    response.local_package_id = null;
    const peerKeys = fakePeerKeys();
    const core = { get: vi.fn(() => of(response)), post: vi.fn(() => of({ ok: true, local_peer_id: 'alice' })) };
    configure(core, peerKeys);
    const bootstrap = TestBed.inject(PairViewSecurityBootstrapService);

    await expect(bootstrap.ensure(session, 'alice')).resolves.toBe(false);
    expect(bootstrap.state$.value.status).toBe('waiting_for_peer');
    expect(bootstrap.confirmedRemotePeerId).toBe('');
    expect(core.post).not.toHaveBeenCalled();
  });

  it('fails closed before peer binding when the negotiated algorithm is mutated', async () => {
    const response = await keyPackageResponse();
    response.security_contract.offer.algorithms = ['AES-256-GCM'];
    const peerKeys = fakePeerKeys();
    const core = { get: vi.fn(() => of(response)), post: vi.fn(() => of({ ok: true, local_peer_id: 'alice' })) };
    configure(core, peerKeys);
    const bootstrap = TestBed.inject(PairViewSecurityBootstrapService);

    await expect(bootstrap.ensure(session, 'alice')).resolves.toBe(false);
    expect(peerKeys.verifyAndRefreshBinding).not.toHaveBeenCalled();
    expect(core.post).not.toHaveBeenCalled();
    expect(bootstrap.state$.value).toMatchObject({ status: 'failed', reasonCode: 'algorithm_invalid' });
  });

  it('rejects a non-self-certifying rendezvous key id for a public session', async () => {
    const response = await keyPackageResponse();
    response.packages = [];
    response.security_contract = null;
    response.security_contract_digest = null;
    response.local_package_id = null;
    response.hub_key_id = 'attacker-controlled-key-id';
    const peerKeys = fakePeerKeys();
    const core = { get: vi.fn(() => of(response)), post: vi.fn(() => of({ ok: true })) };
    configure(core, peerKeys);
    const bootstrap = TestBed.inject(PairViewSecurityBootstrapService);

    await expect(bootstrap.ensure(session, 'alice')).resolves.toBe(false);
    expect(peerKeys.verifyAndRefreshBinding).not.toHaveBeenCalled();
    expect(core.post).not.toHaveBeenCalled();
    expect(bootstrap.state$.value).toMatchObject({
      status: 'failed', reasonCode: 'public_hub_key_id_invalid',
    });
  });

  it('rejects a self-consistent but unpinned alternative rendezvous authority', async () => {
    const response = await keyPackageResponse();
    const alternativeKey = String.fromCharCode(...new Uint8Array(32).fill(7));
    response.hub_public_key_b64 = btoa(alternativeKey);
    response.hub_key_id = `rv:${(await sha256(alternativeKey)).slice(0, 24)}`;
    const peerKeys = fakePeerKeys();
    const core = { get: vi.fn(() => of(response)), post: vi.fn(() => of({ ok: true })) };
    configure(core, peerKeys);
    const bootstrap = TestBed.inject(PairViewSecurityBootstrapService);

    await expect(bootstrap.ensure(session, 'alice')).resolves.toBe(false);
    expect(peerKeys.verifyAndRefreshBinding).not.toHaveBeenCalled();
    expect(bootstrap.state$.value).toMatchObject({
      status: 'failed', reasonCode: 'public_hub_authority_untrusted',
    });
  });

  it('rejects a confirmation for another package before accepting its tag', async () => {
    const response = await keyPackageResponse();
    const peerKeys = fakePeerKeys();
    const core = {
      get: vi.fn((url: string) => of(url.includes('key-packages') ? response : {
        ok: true, local_peer_id: 'alice',
        confirmation: { ...confirmation(), package_id: 'c'.repeat(64) },
      })),
      post: vi.fn(() => of({ ok: true, local_peer_id: 'alice' })),
    };
    configure(core, peerKeys);
    const bootstrap = TestBed.inject(PairViewSecurityBootstrapService);

    await expect(bootstrap.ensure(session, 'alice')).resolves.toBe(false);
    expect(peerKeys.acceptPeerConfirmation).not.toHaveBeenCalled();
    expect(bootstrap.state$.value).toMatchObject({
      status: 'failed', reasonCode: 'key_confirmation_package_mismatch',
    });
  });

  it('rejects an expired confirmation before accepting its tag', async () => {
    const response = await keyPackageResponse();
    const peerKeys = fakePeerKeys();
    const core = {
      get: vi.fn((url: string) => of(url.includes('key-packages') ? response : {
        ok: true,
        local_peer_id: 'alice',
        confirmation: {
          ...confirmation(), created_at_ms: Date.now() - 600_000, expires_at_ms: Date.now() - 300_000,
        },
      })),
      post: vi.fn(() => of({ ok: true, local_peer_id: 'alice' })),
    };
    configure(core, peerKeys);
    const bootstrap = TestBed.inject(PairViewSecurityBootstrapService);

    await expect(bootstrap.ensure(session, 'alice')).resolves.toBe(false);
    expect(peerKeys.acceptPeerConfirmation).not.toHaveBeenCalled();
    expect(bootstrap.state$.value).toMatchObject({ status: 'failed', reasonCode: 'key_confirmation_stale' });
  });

  it('exposes a signed media contract only after the exact peer confirmation completes', async () => {
    const response = await mediaKeyPackageResponse();
    const acceptance = deferred<void>();
    const peerKeys = fakeMediaPeerKeys(acceptance.promise);
    const core = mediaCore(() => response);
    configure(core, peerKeys, localMediaFingerprint);
    const bootstrap = TestBed.inject(PairViewSecurityBootstrapService);
    const verify = vi.spyOn(crypto.subtle, 'verify').mockResolvedValue(true);

    try {
      const pending = bootstrap.ensure(session, localMediaPeerId);
      await vi.waitFor(() => expect(peerKeys.acceptPeerConfirmation).toHaveBeenCalledOnce());
      expect(bootstrap.mediaContractFor(session.id)).toBeNull();

      acceptance.resolve();
      await expect(pending).resolves.toBe(true);
      expect(bootstrap.mediaContractFor(session.id)).toMatchObject({
        domain: 'ananta.public-pair.media-security-contract.v2',
        version: 2,
        frame_format: PUBLIC_PAIR_MEDIA_FRAME_FORMAT_V2,
        session_id: session.id,
        epoch: 3,
        base_security_contract_digest: response.security_contract_digest,
        grants: ['microphone-opus', 'camera-vp8', 'screen-vp8'],
      });
      expect(verify).toHaveBeenCalledWith(
        'Ed25519', expect.anything(), expect.any(ArrayBuffer), expect.any(ArrayBuffer),
      );
    } finally {
      verify.mockRestore();
    }
  });

  it('drops a stale media bootstrap when clear wins the local-key await race', async () => {
    const response = await mediaKeyPackageResponse();
    const localKey = deferred<{ fingerprint: string }>();
    const encryption = { ensureLocalKeyPair: vi.fn(() => localKey.promise) };
    const peerKeys = fakeMediaPeerKeys();
    configure(mediaCore(() => response), peerKeys, localMediaFingerprint, encryption);
    const bootstrap = TestBed.inject(PairViewSecurityBootstrapService);

    const pending = bootstrap.ensure(session, localMediaPeerId);
    await vi.waitFor(() => expect(encryption.ensureLocalKeyPair).toHaveBeenCalledOnce());
    bootstrap.clear();
    localKey.resolve({ fingerprint: localMediaFingerprint });

    await expect(pending).resolves.toBe(false);
    expect(peerKeys.verifyAndRefreshBinding).not.toHaveBeenCalled();
    expect(bootstrap.mediaContractFor(session.id)).toBeNull();
    expect(bootstrap.state$.value).toEqual({ status: 'idle' });
  });

  it('drops a stale media bootstrap when clear wins authority-signature validation', async () => {
    const response = await mediaKeyPackageResponse();
    const validation = deferred<boolean>();
    const peerKeys = fakeMediaPeerKeys();
    configure(mediaCore(() => response), peerKeys, localMediaFingerprint);
    const bootstrap = TestBed.inject(PairViewSecurityBootstrapService);
    const verify = vi.spyOn(crypto.subtle, 'verify').mockImplementation(() => validation.promise);

    try {
      const pending = bootstrap.ensure(session, localMediaPeerId);
      await vi.waitFor(() => expect(verify).toHaveBeenCalledOnce());
      bootstrap.clear();
      validation.resolve(true);

      await expect(pending).resolves.toBe(false);
      expect(peerKeys.verifyAndRefreshBinding).not.toHaveBeenCalled();
      expect(bootstrap.mediaContractFor(session.id)).toBeNull();
      expect(bootstrap.state$.value).toEqual({ status: 'idle' });
    } finally {
      verify.mockRestore();
    }
  });

  it('does not cache null or invalid media contracts', async () => {
    let response = await mediaKeyPackageResponse();
    const peerKeys = fakeMediaPeerKeys();
    configure(mediaCore(() => response), peerKeys, localMediaFingerprint);
    const bootstrap = TestBed.inject(PairViewSecurityBootstrapService);
    const verify = vi.spyOn(crypto.subtle, 'verify').mockResolvedValue(true);

    try {
      await expect(bootstrap.ensure(session, localMediaPeerId)).resolves.toBe(true);
      expect(bootstrap.mediaContractFor(session.id)).not.toBeNull();

      response = await mediaKeyPackageResponse();
      response.public_media_security_contract_v2 = null;
      await expect(bootstrap.ensure(session, localMediaPeerId)).resolves.toBe(true);
      expect(bootstrap.mediaContractFor(session.id)).toBeNull();

      response = await mediaKeyPackageResponse();
      response.public_media_security_contract_v2 = {
        ...response.public_media_security_contract_v2,
        session_id: 'attacker-session',
      };
      const bindingCallsBeforeInvalidContract = peerKeys.verifyAndRefreshBinding.mock.calls.length;
      await expect(bootstrap.ensure(session, localMediaPeerId)).resolves.toBe(false);
      expect(bootstrap.mediaContractFor(session.id)).toBeNull();
      expect(peerKeys.verifyAndRefreshBinding).toHaveBeenCalledTimes(bindingCallsBeforeInvalidContract);
      expect(bootstrap.state$.value).toMatchObject({
        status: 'failed', reasonCode: 'public_media_contract_binding_mismatch',
      });
    } finally {
      verify.mockRestore();
    }
  });

  it('keeps a legacy v1-only response data-only and rejects simultaneous v1/v2 authority', async () => {
    let response = await mediaKeyPackageResponse();
    const peerKeys = fakeMediaPeerKeys();
    configure(mediaCore(() => response), peerKeys, localMediaFingerprint);
    const bootstrap = TestBed.inject(PairViewSecurityBootstrapService);
    const verify = vi.spyOn(crypto.subtle, 'verify').mockResolvedValue(true);

    try {
      response.public_media_security_contract_v1 = {
        domain: 'ananta.public-pair.media-security-contract.v1', version: 1,
      };
      response.public_media_security_contract_v2 = null;
      await expect(bootstrap.ensure(session, localMediaPeerId)).resolves.toBe(true);
      expect(bootstrap.mediaContractFor(session.id)).toBeNull();

      response = await mediaKeyPackageResponse();
      response.public_media_security_contract_v1 = {
        domain: 'ananta.public-pair.media-security-contract.v1', version: 1,
      };
      const bindingCalls = peerKeys.verifyAndRefreshBinding.mock.calls.length;
      await expect(bootstrap.ensure(session, localMediaPeerId)).resolves.toBe(false);
      expect(peerKeys.verifyAndRefreshBinding).toHaveBeenCalledTimes(bindingCalls);
      expect(bootstrap.mediaContractFor(session.id)).toBeNull();
      expect(bootstrap.state$.value).toMatchObject({
        status: 'failed', reasonCode: 'public_media_contract_version_mixed',
      });
    } finally {
      verify.mockRestore();
    }
  });

  it('makes cached media authority unavailable on clear, epoch change and session replacement', async () => {
    let response = await mediaKeyPackageResponse('session-a', 3);
    const peerKeys = fakeMediaPeerKeys();
    const core = mediaCore(() => response);
    configure(core, peerKeys, localMediaFingerprint);
    const bootstrap = TestBed.inject(PairViewSecurityBootstrapService);
    const verify = vi.spyOn(crypto.subtle, 'verify').mockResolvedValue(true);

    try {
      await expect(bootstrap.ensure(session, localMediaPeerId)).resolves.toBe(true);
      expect(bootstrap.mediaContractFor('session-a')).not.toBeNull();

      bootstrap.clear();
      expect(bootstrap.mediaContractFor('session-a')).toBeNull();

      await expect(bootstrap.ensure(session, localMediaPeerId)).resolves.toBe(true);
      expect(bootstrap.mediaContractFor('session-a')).not.toBeNull();
      response = waitingKeyPackageResponse(4);
      const epochReplacement = bootstrap.ensure({ ...session, security_epoch: 4 }, localMediaPeerId);
      expect(bootstrap.mediaContractFor('session-a')).toBeNull();
      await expect(epochReplacement).resolves.toBe(false);
      expect(bootstrap.mediaContractFor('session-a')).toBeNull();

      response = await mediaKeyPackageResponse('session-a', 4);
      await expect(bootstrap.ensure({ ...session, security_epoch: 4 }, localMediaPeerId))
        .resolves.toBe(true);
      expect(bootstrap.mediaContractFor('session-a')).not.toBeNull();
      response = waitingKeyPackageResponse(5);
      const sessionReplacement = bootstrap.ensure(
        { ...session, id: 'session-b', security_epoch: 5 }, localMediaPeerId,
      );
      expect(bootstrap.mediaContractFor('session-a')).toBeNull();
      await expect(sessionReplacement).resolves.toBe(false);
      expect(bootstrap.mediaContractFor('session-a')).toBeNull();
      expect(bootstrap.mediaContractFor('session-b')).toBeNull();
    } finally {
      verify.mockRestore();
    }
  });
});

function configure(
  core: any,
  peerKeys: unknown,
  localFingerprint = localMediaFingerprint,
  encryption?: unknown,
): void {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({ providers: [
    { provide: HubApiCoreService, useValue: core },
    { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: 'http://hub' }] } },
    { provide: PairSessionControlPlaneService, useValue: {
      securityGet: (_sessionId: string, suffix: string) => core.get(suffix),
      securityPost: (_sessionId: string, suffix: string, body: unknown) => core.post(suffix, body),
      isPublicSession: () => true,
    } },
    { provide: WebrtcPeerKeyService, useValue: peerKeys },
    {
      provide: E2eEncryptionService,
      useValue: encryption ?? {
        ensureLocalKeyPair: vi.fn(async () => ({ fingerprint: localFingerprint })),
      },
    },
  ] });
}

function fakePeerKeys() {
  const binding: any = {
    packageId: 'a'.repeat(64), scopeId: 'session-a', epoch: 3,
    remotePeerId: 'bob', peerFingerprint: 'f'.repeat(64), confirmed: false,
    fingerprintChanged: false,
  };
  return {
    currentBinding: null as any,
    clear: vi.fn(), approveFingerprintChange: vi.fn(),
    verifyAndRefreshBinding: vi.fn(async function (this: any) { this.currentBinding = binding; return binding; }),
    createConfirmation: vi.fn(async () => 'local-tag'),
    acceptPeerConfirmation: vi.fn(async function (this: any) { binding.confirmed = true; }),
  };
}

function fakeMediaPeerKeys(acceptance: Promise<void> = Promise.resolve()) {
  let binding: any = null;
  return {
    get currentBinding() { return binding; },
    clear: vi.fn(() => { binding = null; }),
    approveFingerprintChange: vi.fn(),
    verifyAndRefreshBinding: vi.fn(async (remotePackage: any, options: any) => {
      if (
        binding?.packageId === remotePackage.package_id
        && binding.scopeId === options.expectedScopeId
        && binding.epoch === options.expectedEpoch
      ) return binding;
      binding = {
        packageId: remotePackage.package_id,
        scopeKind: 'session',
        scopeId: options.expectedScopeId,
        epoch: options.expectedEpoch,
        localPeerId: options.localPeerId,
        remotePeerId: remotePackage.peer_id,
        peerPublicKeySpkiB64: remotePackage.ecdh_public_key_spki_b64 ?? 'spki',
        keyId: 'media-key-id',
        contractDigest: options.contractDigest,
        tenantId: 'tenant-a',
        deviceId: remotePackage.device_id ?? 'device-b',
        membershipId: remotePackage.membership_id,
        membershipVersion: remotePackage.membership_version,
        peerFingerprint: remotePackage.device_key_fingerprint,
        transcriptDigest: 'transcript-digest',
        authorityKeyId: options.expectedHubKeyId,
        confirmed: false,
        fingerprintChanged: false,
      };
      return binding;
    }),
    createConfirmation: vi.fn(async () => 'local-tag'),
    acceptPeerConfirmation: vi.fn(async () => {
      await acceptance;
      if (binding) binding.confirmed = true;
    }),
  };
}

function mediaCore(response: () => any) {
  return {
    get: vi.fn((url: string) => {
      const current = response();
      return of(url.includes('key-packages') ? current : {
        ok: true,
        local_peer_id: localMediaPeerId,
        confirmation: confirmation(current.epoch, current.local_package_id),
      });
    }),
    post: vi.fn(() => of({ ok: true, local_peer_id: localMediaPeerId })),
  };
}

async function mediaKeyPackageResponse(scopeId = 'session-a', epoch = 3): Promise<any> {
  const response = await keyPackageResponse(scopeId, epoch);
  response.local_peer_id = localMediaPeerId;
  response.packages = [{
    ...response.packages[0],
    membership_version: 1,
    peer_id: remoteMediaPeerId,
    device_id: 'device-b',
    device_key_fingerprint: remoteMediaFingerprint,
  }];
  const unsigned = {
    domain: 'ananta.public-pair.media-security-contract.v2',
    version: 2,
    session_id: scopeId,
    epoch,
    identity_binding_version: 2,
    base_security_contract_digest: response.security_contract_digest,
    memberships: [
      {
        membership_id: 'owner-session-a', membership_version: 1,
        peer_id: localMediaPeerId, device_key_fingerprint: localMediaFingerprint,
        public_media_e2ee_version: 2,
      },
      {
        membership_id: 'participant-a', membership_version: 1,
        peer_id: remoteMediaPeerId, device_key_fingerprint: remoteMediaFingerprint,
        public_media_e2ee_version: 2,
      },
    ],
    grants: [...PUBLIC_PAIR_MEDIA_GRANTS],
    slots: PUBLIC_PAIR_MEDIA_SLOTS.map(slot => ({ ...slot })),
    transform: 'RTCRtpScriptTransform',
    frame_format: PUBLIC_PAIR_MEDIA_FRAME_FORMAT_V2,
    algorithms: { aead: 'AES-256-GCM', kdf: 'HKDF-SHA-256' },
    expires_at_ms: Date.now() + 240_000,
    authority_key_id: PUBLIC_RENDEZVOUS_SIGNING_KEY_ID,
  };
  const digest = await sha256(canonicalSecurityJson(unsigned));
  response.public_media_security_contract_v1 = null;
  response.public_media_security_contract_v2 = {
    ...unsigned,
    digest,
    signature_algorithm: 'Ed25519',
    signature_b64: btoa('s'.repeat(64)),
  };
  return response;
}

function waitingKeyPackageResponse(epoch: number): any {
  return {
    ok: true,
    epoch,
    tenant_id: 'tenant-a',
    security_contract_digest: null,
    security_contract: null,
    hub_key_id: PUBLIC_RENDEZVOUS_SIGNING_KEY_ID,
    hub_public_key_b64: PUBLIC_RENDEZVOUS_SIGNING_PUBLIC_KEY_B64,
    local_membership_id: 'owner-session-a',
    local_peer_id: localMediaPeerId,
    local_package_id: null,
    packages: [],
    public_media_security_contract_v1: null,
    public_media_security_contract_v2: null,
  };
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  const promise = new Promise<T>(innerResolve => { resolve = innerResolve; });
  return { promise, resolve };
}

async function keyPackageResponse(scopeId = 'session-a', epoch = 3): Promise<any> {
  const offer: any = proposal('owner-session-a', 'participant-a', scopeId, epoch);
  const answer: any = proposal('participant-a', 'owner-session-a', scopeId, epoch);
  const digest = await sha256(canonicalSecurityJson({
    domain: 'ananta.webrtc.security-negotiation.v1', offer, answer,
  }));
  return {
    ok: true,
    epoch,
    tenant_id: 'tenant-a',
    security_contract_digest: digest,
    security_contract: {
      version: 1, negotiation_id: offer.negotiation_id, offer, answer, digest,
      signature: 'b'.repeat(64), signature_algorithm: 'HMAC-SHA256',
    },
    hub_key_id: PUBLIC_RENDEZVOUS_SIGNING_KEY_ID,
    hub_public_key_b64: PUBLIC_RENDEZVOUS_SIGNING_PUBLIC_KEY_B64,
    local_membership_id: 'owner-session-a',
    local_peer_id: 'alice',
    local_package_id: 'b'.repeat(64),
    packages: [{ package_id: 'a'.repeat(64), membership_id: 'participant-a' }],
  };
}

function confirmation(epoch = 3, packageId = 'b'.repeat(64)) {
  const now = Date.now();
  return {
    confirmation_tag: confirmationTag(), package_id: packageId, epoch,
    created_at_ms: now - 1_000, expires_at_ms: now + 240_000,
  };
}

function confirmationTag(): string {
  return btoa('t'.repeat(32));
}

function proposal(sender: string, recipient: string, scopeId = 'session-a', epoch = 3) {
  return {
    version: 1, negotiation_id: 'neg:0123456789abcdef', scope_kind: 'session', scope_id: scopeId,
    sender_id: sender, recipient_id: recipient, minimum_mode: 'strict_e2ee', selected_mode: 'strict_e2ee',
    algorithms: ['AES-256-GCM', 'ECDH-P256-HKDF-SHA256'], key_epoch: epoch,
    payload_classes: ['bulk', 'control', 'semantic'], expires_at_ms: Number.MAX_SAFE_INTEGER,
  };
}

async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, '0')).join('');
}
