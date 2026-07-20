import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { E2eEncryptionService } from './e2e-encryption.service';
import { SemanticSfuGroupKeyApiService } from './semantic-sfu-group-key-api.service';
import { SemanticSfuGroupContext, SemanticSfuGroupKeyService } from './semantic-sfu-group-key.service';
import { WebrtcGroupKeyService } from './webrtc-group-key.service';
import { WebrtcPeerKeyService } from './webrtc-peer-key.service';

const roomId = `sfu-${'a'.repeat(32)}`;
const context = (localPeerId: string): SemanticSfuGroupContext => ({
  hubUrl: 'http://hub.test', tenantId: 'tenant-a', sessionId: 'session-a', membershipEpoch: 7, localPeerId,
});

describe('SemanticSfuGroupKeyService', () => {
  it('wraps once per member, installs the current epoch and lets a late receiver decrypt only its package', async () => {
    let actor = 'alice';
    let delivered: any = null;
    const authorization: any = {
      version: 1, authorization_id: 'auth-1', tenant_id: 'tenant-a', room_id: roomId,
      publication_id: 'mic-a', epoch: 3, previous_epoch: 2, member_set_digest: 'a'.repeat(64),
      member_ids: ['alice', 'bob'], key_package_refs: { alice: 'pkg-a', bob: 'pkg-b' },
      valid_from_ms: Date.now() - 1_000, expires_at_ms: Date.now() + 60_000,
      rekey_deadline_ms: Date.now() + 10_000, reason: 'join', hub_key_id: 'hub-key',
      membership_epoch: 7, signature_b64: 'A'.repeat(88),
    };
    const peerPackage = (peerId: string, recipient: string) => ({
      version: 1, package_id: 'b'.repeat(64), membership_id: `membership-${peerId}`, membership_version: 1,
      tenant_id: 'tenant-a', scope_kind: 'session', scope_id: 'session-a', epoch: 7,
      peer_id: peerId, recipient_peer_id: recipient, device_id: `device-${peerId}`,
      device_key_fingerprint: 'c'.repeat(64), ecdh_public_key_spki_b64: 'A'.repeat(88),
      issued_at_ms: Date.now() - 1_000, expires_at_ms: Date.now() + 60_000,
      hub_key_id: 'hub-key', security_contract_digest: 'd'.repeat(64), signature_b64: 'A'.repeat(88),
    });
    const api = {
      peerPackages: vi.fn(() => of({
        epoch: 7, tenantId: 'tenant-a', securityContractDigest: 'd'.repeat(64),
        hubKeyId: 'hub-key', hubPublicKeyB64: 'A'.repeat(44),
        packages: [peerPackage(actor === 'alice' ? 'bob' : 'alice', actor)],
      })),
      prepareEpoch: vi.fn(() => of({
        authorization, hubKeyId: 'hub-key', hubPublicKeyB64: 'A'.repeat(44),
      })),
      deliverPackages: vi.fn((_hub: string, _auth: string, _key: string, packages: any[]) => {
        delivered = { ...packages[0], authorization, publisherId: 'alice', recipientId: 'bob' };
        return of({ deliveredMemberIds: ['bob'], pendingMemberIds: [] });
      }),
      packages: vi.fn(() => of({ packages: [delivered], cursor: 'pkg-b' })),
      acknowledge: vi.fn(() => of(undefined)),
      status: vi.fn(() => of({ acknowledgedMemberIds: ['bob'], pendingMemberIds: [] })),
    };
    const wrappingKey = await crypto.subtle.generateKey(
      { name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt'],
    );
    const e2ee = { derivePurposeAesKey: vi.fn(async () => wrappingKey) };
    const peers = {
      verifyPackage: vi.fn(async (value: any, options: any) => ({
        scopeKind: 'session', scopeId: 'session-a', localPeerId: options.localPeerId,
        remotePeerId: value.peer_id, peerPublicKeySpkiB64: value.ecdh_public_key_spki_b64,
        epoch: 7, keyId: 'e'.repeat(64), contractDigest: 'd'.repeat(64), packageId: value.package_id,
        tenantId: 'tenant-a', deviceId: value.device_id, membershipId: value.membership_id,
        membershipVersion: 1, peerFingerprint: value.device_key_fingerprint, transcriptDigest: 'f'.repeat(64),
      })),
    };
    const groupKeys = {
      install: vi.fn(async () => undefined), verifyAuthorization: vi.fn(async () => undefined),
      purge: vi.fn(), clear: vi.fn(),
    };
    TestBed.configureTestingModule({ providers: [
      SemanticSfuGroupKeyService,
      { provide: SemanticSfuGroupKeyApiService, useValue: api },
      { provide: E2eEncryptionService, useValue: e2ee },
      { provide: WebrtcPeerKeyService, useValue: peers },
      { provide: WebrtcGroupKeyService, useValue: groupKeys },
    ] });
    const service = TestBed.inject(SemanticSfuGroupKeyService);
    const publisher = await service.createPublisherEpoch(context('alice'), 'mic-a', ['alice', 'bob']);
    const expectedKey = Uint8Array.from(publisher.keyMaterial);
    expect(api.deliverPackages).toHaveBeenCalledOnce();
    expect(delivered.opaquePackageB64).not.toContain(encodeURIComponent(String(expectedKey)));

    actor = 'bob';
    const received = await service.receiveAvailable(context('bob'));
    expect(received.installed?.publisherId).toBe('alice');
    expect(received.installed?.keyMaterial).toEqual(expectedKey);
    expect(groupKeys.verifyAuthorization).toHaveBeenCalled();
    expect(groupKeys.install).toHaveBeenCalledTimes(2);
    await service.acknowledge(context('bob'), received.installed!);
    expect(api.acknowledge).toHaveBeenCalledWith('http://hub.test', 'auth-1', 'pkg-b', 7);
    publisher.keyMaterial.fill(0); received.installed?.keyMaterial.fill(0);
  });
});
