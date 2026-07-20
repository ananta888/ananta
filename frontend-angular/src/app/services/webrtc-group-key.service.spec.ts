import { describe, expect, it } from 'vitest';

import { WebrtcGroupKeyService, GroupKeyEpochAuthorization } from './webrtc-group-key.service';
import { canonicalSecurityJson, encodeB64 } from './webrtc-secure-envelope';

describe('WebrtcGroupKeyService', () => {
  it('keeps only the current member epoch and rejects stale packages', async () => {
    const hub = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
    const hubPublicKeyB64 = encodeB64(await crypto.subtle.exportKey('raw', hub.publicKey));
    const service = new WebrtcGroupKeyService();
    const key1 = await crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
    const first = await signedAuthorization(hub.privateKey, 1, 0, ['alice'], 1000);
    await service.install(first, key1, { localMemberId: 'alice', hubPublicKeyB64, expectedHubKeyId: 'hub', nowMs: 1000 });
    expect(service.getKey('room', 'pub', 1, 1001)).toBe(key1);

    const key2 = await crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
    const joined = await signedAuthorization(hub.privateKey, 2, 1, ['alice', 'bob'], 1000);
    await service.install(joined, key2, { localMemberId: 'bob', hubPublicKeyB64, expectedHubKeyId: 'hub', nowMs: 1000 });
    expect(service.getKey('room', 'pub', 2, 1001)).toBe(key2);
    expect(() => service.getKey('room', 'pub', 1, 1001)).toThrow('group_key_missing');
    await expect(service.install(first, key1, {
      localMemberId: 'alice', hubPublicKeyB64, expectedHubKeyId: 'hub', nowMs: 1000,
    })).rejects.toThrow('group_epoch_stale');
  });

  it('purges a revoked member by the bounded rekey deadline', async () => {
    const hub = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
    const hubPublicKeyB64 = encodeB64(await crypto.subtle.exportKey('raw', hub.publicKey));
    const service = new WebrtcGroupKeyService();
    const key = await crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
    await service.install(await signedAuthorization(hub.privateKey, 1, 0, ['alice'], 1000), key, {
      localMemberId: 'alice', hubPublicKeyB64, expectedHubKeyId: 'hub', nowMs: 1000,
    });
    const revoked = await signedAuthorization(hub.privateKey, 2, 1, ['bob'], 1000);
    await expect(service.install(revoked, key, {
      localMemberId: 'alice', hubPublicKeyB64, expectedHubKeyId: 'hub', nowMs: 1000,
    })).rejects.toThrow('member_revoked');
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(() => service.getKey('room', 'pub', 1, 1001)).toThrow('group_key_missing');
  });
});

async function signedAuthorization(
  privateKey: CryptoKey, epoch: number, previousEpoch: number, members: string[], nowMs: number,
): Promise<GroupKeyEpochAuthorization> {
  const sorted = [...members].sort();
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(sorted)));
  const unsigned = {
    version: 1 as const, authorization_id: `auth-${epoch}`, tenant_id: 'tenant', room_id: 'room',
    publication_id: 'pub', epoch, previous_epoch: previousEpoch,
    member_set_digest: Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, '0')).join(''),
    member_ids: sorted, key_package_refs: Object.fromEntries(sorted.map((member) => [member, `pkg-${member}-${epoch}`])),
    valid_from_ms: nowMs, expires_at_ms: nowMs + 100_000, rekey_deadline_ms: nowMs,
    reason: (epoch === 1 ? 'create' : members.includes('alice') ? 'join' : 'revoke') as GroupKeyEpochAuthorization['reason'],
    hub_key_id: 'hub',
  };
  const signature = await crypto.subtle.sign(
    'Ed25519', privateKey, new TextEncoder().encode(canonicalSecurityJson(unsigned)),
  );
  return { ...unsigned, signature_b64: encodeB64(signature) };
}
