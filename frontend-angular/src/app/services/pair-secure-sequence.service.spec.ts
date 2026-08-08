import { TestBed } from '@angular/core/testing';
import { IDBFactory } from 'fake-indexeddb';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { PairSecureSequenceService } from './pair-secure-sequence.service';
import {
  IndexedDbPairSecureSequenceStore,
  PAIR_SECURE_SEQUENCE_CLOCK,
  PAIR_SECURE_SEQUENCE_STORE,
  PairSecureSequenceDomain,
  PairSecureSequenceStorePort,
} from './pair-secure-sequence.store';

const DB_NAME = 'ananta-pair-sequences';
const STORE_NAME = 'outbound-sequences';
const RETAIN_FOR_MS = 2 * 60 * 60_000;
const MAX_RECORDS = 4096;
const BASE_DOMAIN: PairSecureSequenceDomain = {
  scopeId: 'session-a',
  epoch: 3,
  senderId: `oidc:${'a'.repeat(64)}`,
  trafficClass: 'semantic',
};
let testNowMs = Date.parse('2026-08-08T10:00:00.000Z');

describe('PairSecureSequenceService', () => {
  const originalIndexedDb = globalThis.indexedDB;

  beforeEach(() => {
    testNowMs = Date.parse('2026-08-08T10:00:00.000Z');
    Object.defineProperty(globalThis, 'indexedDB', {
      value: new IDBFactory(), configurable: true, writable: true,
    });
    TestBed.resetTestingModule();
    configureSequenceTestBed();
  });

  afterEach(() => {
    TestBed.resetTestingModule();
    Object.defineProperty(globalThis, 'indexedDB', {
      value: originalIndexedDb, configurable: true, writable: true,
    });
  });

  it('continues monotonically in a second service instance after reload', async () => {
    const first = sequenceService();
    expect(await next(first)).toBe(1);
    expect(await next(first)).toBe(2);

    TestBed.resetTestingModule();
    configureSequenceTestBed();
    const afterReload = sequenceService();
    expect(await next(afterReload)).toBe(3);
  });

  it('allocates atomically across concurrent service instances', async () => {
    const first = sequenceService();
    TestBed.resetTestingModule();
    configureSequenceTestBed();
    const second = sequenceService();
    const allocations = await Promise.all(Array.from({ length: 64 }, (_, index) => (
      next(index % 2 === 0 ? first : second)
    )));

    expect(new Set(allocations).size).toBe(64);
    expect([...allocations].sort((left, right) => left - right)).toEqual(
      Array.from({ length: 64 }, (_, index) => index + 1),
    );
  });

  it('uses the receiver replay tuple as the exact independent domain', async () => {
    const service = sequenceService();
    expect(await next(service)).toBe(1);
    expect(await next(service)).toBe(2);
    expect(await next(service, { scopeId: 'session-b' })).toBe(1);
    expect(await next(service, { epoch: 4 })).toBe(1);
    expect(await next(service, { senderId: `oidc:${'b'.repeat(64)}` })).toBe(1);
    expect(await next(service, { trafficClass: 'control' })).toBe(1);
  });

  it('does not reset a live replay domain on UI forget and isolates a new epoch', async () => {
    const first = sequenceService();
    expect(await next(first)).toBe(1);
    first.clearScope(BASE_DOMAIN.scopeId);

    const rebound = sequenceService();
    expect(await next(rebound)).toBe(2);
    expect(await next(rebound, { epoch: BASE_DOMAIN.epoch + 1 })).toBe(1);
  });

  it('prunes a domain only after twice the remote replay-window lifetime', async () => {
    const service = sequenceService();
    expect(await next(service)).toBe(1);

    testNowMs += RETAIN_FOR_MS - 1;
    expect(await next(service)).toBe(2);
    testNowMs += RETAIN_FOR_MS + 1;
    expect(await next(sequenceService())).toBe(1);
  });

  it('fails closed when the durable record budget is exhausted', async () => {
    await next(sequenceService());
    await replaceRecords(Array.from({ length: MAX_RECORDS }, (_, index) => rawRecord({
      scopeId: `session-${index}`,
      epoch: 1,
      senderId: `sender-${index}`,
      trafficClass: 'semantic',
    }, 1, testNowMs)));

    await expect(next(sequenceService(), { scopeId: 'capacity-overflow' })).rejects.toMatchObject({
      reasonCode: 'secure_sequence_capacity_exceeded',
    });
  });

  it('fails closed on corrupt persisted state', async () => {
    await next(sequenceService());
    const records = await readRecords();
    await replaceRecords([{ ...records[0], sequence: 0 }]);

    await expect(next(sequenceService())).rejects.toMatchObject({
      reasonCode: 'secure_sequence_state_invalid',
    });
  });

  it('fails closed when IndexedDB is unavailable', async () => {
    Object.defineProperty(globalThis, 'indexedDB', {
      value: undefined, configurable: true, writable: true,
    });
    await expect(next(sequenceService())).rejects.toMatchObject({
      reasonCode: 'secure_sequence_store_unavailable',
    });
  });

  it('does not fall back when the injected durable store rejects a write', async () => {
    const failedStore: PairSecureSequenceStorePort = {
      next: async () => { throw new Error('sequence-write-failed'); },
    };
    TestBed.overrideProvider(PAIR_SECURE_SEQUENCE_STORE, { useValue: failedStore });
    await expect(next(sequenceService())).rejects.toThrow('sequence-write-failed');
  });

  it('rejects an invalid result from an injected store port', async () => {
    const corruptStore: PairSecureSequenceStorePort = { next: async () => 0 };
    TestBed.overrideProvider(PAIR_SECURE_SEQUENCE_STORE, { useValue: corruptStore });
    await expect(next(sequenceService())).rejects.toThrow('secure_sequence_state_invalid');
  });
});

function sequenceService(): PairSecureSequenceService {
  return TestBed.runInInjectionContext(() => new PairSecureSequenceService());
}

function configureSequenceTestBed(): void {
  TestBed.configureTestingModule({ providers: [
    IndexedDbPairSecureSequenceStore,
    { provide: PAIR_SECURE_SEQUENCE_CLOCK, useValue: () => testNowMs },
  ] });
}

function next(
  service: PairSecureSequenceService,
  patch: Partial<PairSecureSequenceDomain> = {},
): Promise<number> {
  const domain = { ...BASE_DOMAIN, ...patch };
  return service.next(domain.scopeId, domain.epoch, domain.senderId, domain.trafficClass);
}

function rawRecord(domain: PairSecureSequenceDomain, sequence: number, nowMs: number): Record<string, unknown> {
  return {
    id: JSON.stringify(['session', domain.scopeId, domain.epoch, domain.senderId, domain.trafficClass]),
    version: 1,
    scopeKind: 'session',
    scopeId: domain.scopeId,
    epoch: domain.epoch,
    senderId: domain.senderId,
    trafficClass: domain.trafficClass,
    sequence,
    updatedAtMs: nowMs,
    retainUntilMs: nowMs + RETAIN_FOR_MS,
  };
}

async function readRecords(): Promise<Array<Record<string, unknown>>> {
  const database = await openDatabase();
  try {
    return await request(
      database.transaction(STORE_NAME, 'readonly').objectStore(STORE_NAME).getAll(),
    ) as Array<Record<string, unknown>>;
  } finally {
    database.close();
  }
}

async function replaceRecords(records: ReadonlyArray<Record<string, unknown>>): Promise<void> {
  const database = await openDatabase();
  try {
    const transaction = database.transaction(STORE_NAME, 'readwrite');
    const store = transaction.objectStore(STORE_NAME);
    store.clear();
    for (const record of records) store.put(record);
    await transactionComplete(transaction);
  } finally {
    database.close();
  }
}

function openDatabase(): Promise<IDBDatabase> {
  return request(indexedDB.open(DB_NAME, 1));
}

function request<T>(value: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    value.onsuccess = () => resolve(value.result);
    value.onerror = () => reject(value.error);
  });
}

function transactionComplete(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });
}
