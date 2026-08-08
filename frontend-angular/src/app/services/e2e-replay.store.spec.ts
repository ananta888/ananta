import { describe, expect, it } from 'vitest';

import {
  E2eReplayStoreError,
  InboundNonceReplayDomain,
  IndexedDbE2eReplayStore,
  PairReplayWindowDomain,
} from './e2e-replay.store';

const DB_NAME = 'ananta-e2e-replay';
const NONCE_STORE_NAME = 'inbound-nonce-claims';

describe('IndexedDbE2eReplayStore', () => {
  it('retains exact nonce claims across reloads and through the grace boundary', async () => {
    const first = new IndexedDbE2eReplayStore();
    expect(await first.claimNonce(nonceDomain(), 'AAAAAAAAAAAAAAAA', 31_000, 1_000))
      .toBe('claimed');

    const afterReload = new IndexedDbE2eReplayStore();
    expect(await afterReload.hasNonce(nonceDomain(), 'AAAAAAAAAAAAAAAA', 31_000)).toBe(true);
    expect(await afterReload.claimNonce(nonceDomain(), 'AAAAAAAAAAAAAAAA', 40_000, 31_000))
      .toBe('duplicate');
    expect(await afterReload.hasNonce(nonceDomain(), 'AAAAAAAAAAAAAAAA', 31_001)).toBe(false);
    expect(await afterReload.claimNonce(nonceDomain(), 'AAAAAAAAAAAAAAAA', 40_000, 31_001))
      .toBe('claimed');
  });

  it('serializes concurrent cross-tab nonce claims so exactly one succeeds', async () => {
    const firstTab = new IndexedDbE2eReplayStore();
    const secondTab = new IndexedDbE2eReplayStore();
    const results = await Promise.all([
      firstTab.claimNonce(nonceDomain(), 'AQEBAQEBAQEBAQEB', 50_000, 1_000),
      secondTab.claimNonce(nonceDomain(), 'AQEBAQEBAQEBAQEB', 50_000, 1_000),
    ]);

    expect(results.filter(result => result === 'claimed')).toHaveLength(1);
    expect(results.filter(result => result === 'duplicate')).toHaveLength(1);
  });

  it('keeps nonce and sequence replay domains independent', async () => {
    const store = new IndexedDbE2eReplayStore();
    expect(await store.claimNonce(nonceDomain(), 'AgICAgICAgICAgIC', 50_000, 1_000)).toBe('claimed');
    expect(await store.claimNonce(
      { ...nonceDomain(), scopeId: 'session-b' }, 'AgICAgICAgICAgIC', 50_000, 1_000,
    )).toBe('claimed');

    expect(await store.claimSequence(windowDomain(), 1, 1_000)).toBe('accepted');
    expect(await store.claimSequence(
      { ...windowDomain(), trafficClass: 'control' }, 1, 1_000,
    )).toBe('accepted');
    expect(await store.claimSequence(
      { ...windowDomain(), senderId: 'carol' }, 1, 1_000,
    )).toBe('accepted');
  });

  it('persists the full sequence window and rejects fresh-nonce old sequences after reload', async () => {
    const first = new IndexedDbE2eReplayStore();
    expect(await first.claimSequence(windowDomain(), 200, 1_000)).toBe('accepted');

    const afterReload = new IndexedDbE2eReplayStore();
    expect(await afterReload.claimSequence(windowDomain(), 200, 2_000)).toBe('duplicate');
    expect(await afterReload.claimSequence(windowDomain(), 1, 2_000)).toBe('too_old');
    expect(await afterReload.claimSequence(windowDomain(), 4_297, 2_000)).toBe('too_far_ahead');
    expect(await afterReload.claimSequence(windowDomain(), 201, 2_000)).toBe('accepted');
  });

  it('serializes concurrent sequence claims and expires windows only after their TTL boundary', async () => {
    const firstTab = new IndexedDbE2eReplayStore();
    const secondTab = new IndexedDbE2eReplayStore();
    const results = await Promise.all([
      firstTab.claimSequence(windowDomain(), 7, 1_000),
      secondTab.claimSequence(windowDomain(), 7, 1_000),
    ]);
    expect(results.filter(result => result === 'accepted')).toHaveLength(1);
    expect(results.filter(result => result === 'duplicate')).toHaveLength(1);

    expect(await new IndexedDbE2eReplayStore().claimSequence(windowDomain(), 7, 3_601_000))
      .toBe('duplicate');
    expect(await new IndexedDbE2eReplayStore().claimSequence(windowDomain(), 7, 3_601_001))
      .toBe('accepted');
  });

  it('fails closed when IndexedDB is unavailable or a claim record is corrupt', async () => {
    const unavailableFactory = globalThis.indexedDB;
    Object.defineProperty(globalThis, 'indexedDB', { value: undefined, writable: true, configurable: true });
    await expect(new IndexedDbE2eReplayStore().claimNonce(
      nonceDomain(), 'AwMDAwMDAwMDAwMD', 50_000, 1_000,
    )).rejects.toMatchObject({ reasonCode: 'e2e_replay_store_unavailable' });
    globalThis.indexedDB = unavailableFactory;

    const store = new IndexedDbE2eReplayStore();
    const domain = nonceDomain();
    const nonce = 'BAQEBAQEBAQEBAQE';
    expect(await store.claimNonce(domain, nonce, 50_000, 1_000)).toBe('claimed');
    await overwriteNonceClaimWithCorruptRecord(domain, nonce);

    await expect(new IndexedDbE2eReplayStore().claimNonce(domain, nonce, 60_000, 2_000))
      .rejects.toMatchObject({ reasonCode: 'nonce_replay_state_invalid' });
  });

  it('rejects malformed caller state before touching persistent storage', async () => {
    const store = new IndexedDbE2eReplayStore();
    await expect(store.claimNonce(
      { ...nonceDomain(), senderId: '' }, 'AAAAAAAAAAAAAAAA', 50_000, 1_000,
    )).rejects.toBeInstanceOf(E2eReplayStoreError);
    await expect(store.claimSequence(windowDomain(), 0, 1_000))
      .rejects.toMatchObject({ reasonCode: 'pair_replay_claim_invalid' });
  });
});

function nonceDomain(): InboundNonceReplayDomain {
  return {
    scopeKind: 'session', scopeId: 'session-a', epoch: 3, keyId: 'key-3',
    senderId: 'alice', recipientId: 'bob',
  };
}

function windowDomain(): PairReplayWindowDomain {
  return {
    scopeKind: 'session', scopeId: 'session-a', epoch: 3,
    senderId: 'alice', trafficClass: 'semantic',
  };
}

async function overwriteNonceClaimWithCorruptRecord(
  domain: InboundNonceReplayDomain,
  nonceB64: string,
): Promise<void> {
  const database = await openDatabase();
  try {
    const transaction = database.transaction(NONCE_STORE_NAME, 'readwrite');
    transaction.objectStore(NONCE_STORE_NAME).put({
      id: JSON.stringify([
        domain.scopeKind, domain.scopeId, domain.epoch, domain.keyId,
        domain.senderId, domain.recipientId, nonceB64,
      ]),
      retainUntilMs: 60_000,
    });
    await transactionComplete(transaction);
  } finally {
    database.close();
  }
}

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function transactionComplete(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });
}
