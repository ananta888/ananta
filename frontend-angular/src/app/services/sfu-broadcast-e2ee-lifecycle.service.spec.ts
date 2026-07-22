import { describe, expect, it, vi } from 'vitest';

import { SfuBroadcastE2eeLifecycleService } from './sfu-broadcast-e2ee-lifecycle.service';
import type {
  SfuBroadcastCryptographicScope,
  SfuBroadcastGroupKeyLease,
  SfuBroadcastGroupKeyPort,
} from './sfu-broadcast-group-key.port';

const scope: SfuBroadcastCryptographicScope = Object.freeze({
  tenantRef: 'tenant-a', roomRef: 'room-a', publicationRef: 'publication-a',
  audienceRef: 'audience-a', localHandle: 'receiver-a', membershipEpoch: 2,
  routeEpoch: 3, keyEpoch: 4, fencingToken: 'fence-a',
});

describe('SfuBroadcastE2eeLifecycleService', () => {
  it('installs only a verified leased epoch and zeroes its callback-owned copy', async () => {
    const key = await aesKey();
    const material = new Uint8Array(32).fill(7);
    const release = vi.fn();
    const port: SfuBroadcastGroupKeyPort = {
      acquire: vi.fn(async () => lease(key, scope, material, release)),
    };
    const rotate = vi.fn(async (owned: Uint8Array, epoch: number) => {
      expect(owned).toEqual(new Uint8Array(32).fill(7));
      expect(epoch).toBe(4);
    });
    const session = fakeSession(rotate);
    const lifecycle = new SfuBroadcastE2eeLifecycleService(port);

    await lifecycle.activate(scope, session, 1_000);

    lifecycle.guard(scope, 1_001);
    expect(lifecycle.snapshot()).toMatchObject({ state: 'active', keyEpoch: 4 });
    expect(material).toEqual(new Uint8Array(32));
    await lifecycle.revoke(session, 'sfu_e2ee_membership_revoked');
    expect(release).toHaveBeenCalledOnce();
    expect(session.lifecycle.disconnect).toHaveBeenCalledOnce();
  });

  it('rejects stale epochs and fences the room on SDK installation failure', async () => {
    const key = await aesKey();
    const firstPort: SfuBroadcastGroupKeyPort = {
      acquire: vi.fn(async (_scope, _now) => lease(key, scope, new Uint8Array(32), vi.fn())),
    };
    const lifecycle = new SfuBroadcastE2eeLifecycleService(firstPort);
    const session = fakeSession(vi.fn(async () => undefined));
    await lifecycle.activate(scope, session, 1_000);
    await expect(lifecycle.activate({ ...scope, fencingToken: 'fence-b' }, session, 1_001))
      .rejects.toThrow('sfu_e2ee_epoch_stale');

    const failing = new SfuBroadcastE2eeLifecycleService(firstPort);
    const failedSession = fakeSession(vi.fn(async () => { throw new Error('sfu_e2ee_key_index_exhausted'); }));
    await expect(failing.activate(scope, failedSession, 1_000))
      .rejects.toThrow('sfu_e2ee_key_index_exhausted');
    expect(failing.snapshot()).toMatchObject({ state: 'fenced' });
    expect(failedSession.lifecycle.disconnect).toHaveBeenCalledOnce();
  });
});

function lease(
  key: CryptoKey,
  leaseScope: SfuBroadcastCryptographicScope,
  material: Uint8Array,
  release: () => void,
): SfuBroadcastGroupKeyLease {
  return {
    authorizationRef: 'authorization-a', keyId: 'key-a', scope: leaseScope,
    expiresAtMs: 30_000, authorizedDestinationHandles: new Set(['receiver-a']),
    contentKey: key, release,
    withLivekitKeyMaterial: async consumer => {
      const owned = material;
      try { return await consumer(owned); } finally { owned.fill(0); }
    },
  };
}

function fakeSession(rotateKeyAtEpoch: (value: Uint8Array, epoch: number) => Promise<void>) {
  return {
    lifecycle: {
      e2eeSupported: true,
      connect: vi.fn(async () => undefined),
      disconnect: vi.fn(async () => undefined),
      destroy: vi.fn(async () => undefined),
    },
    key: { rotateKey: vi.fn(async () => undefined), rotateKeyAtEpoch },
  };
}

async function aesKey(): Promise<CryptoKey> {
  return crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
}
