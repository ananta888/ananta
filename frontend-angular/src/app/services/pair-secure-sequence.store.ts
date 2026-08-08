import { Injectable, InjectionToken, inject } from '@angular/core';

import { MAX_SECURE_SEQUENCE, SecurityTrafficClass } from './webrtc-secure-envelope';

export interface PairSecureSequenceDomain {
  readonly scopeId: string;
  readonly epoch: number;
  readonly senderId: string;
  readonly trafficClass: SecurityTrafficClass;
}

export interface PairSecureSequenceStorePort {
  next(domain: PairSecureSequenceDomain): Promise<number>;
}

export class PairSecureSequenceStoreError extends Error {
  constructor(readonly reasonCode: string) { super(reasonCode); }
}

export const PAIR_SECURE_SEQUENCE_CLOCK = new InjectionToken<() => number>(
  'PAIR_SECURE_SEQUENCE_CLOCK',
  { providedIn: 'root', factory: () => Date.now },
);

interface StoredSequenceRecord {
  readonly id: string;
  readonly version: 1;
  readonly scopeKind: 'session';
  readonly scopeId: string;
  readonly epoch: number;
  readonly senderId: string;
  readonly trafficClass: SecurityTrafficClass;
  readonly sequence: number;
  readonly updatedAtMs: number;
  readonly retainUntilMs: number;
}

const DB_NAME = 'ananta-pair-sequences';
const DB_VERSION = 1;
const STORE_NAME = 'outbound-sequences';
const RETAIN_FOR_MS = 2 * 60 * 60_000;
const MAX_RECORDS = 4096;
const RECORD_FIELDS = [
  'epoch', 'id', 'retainUntilMs', 'scopeId', 'scopeKind', 'senderId', 'sequence',
  'trafficClass', 'updatedAtMs', 'version',
] as const;

/**
 * Allocates outbound sequence numbers in one cross-tab atomic transaction.
 *
 * IndexedDB serializes read-write transactions for this object store. That
 * makes a read/increment/write indivisible across reloads, tabs and Angular
 * injectors which share the persistent device identity.
 */
@Injectable({ providedIn: 'root' })
export class IndexedDbPairSecureSequenceStore implements PairSecureSequenceStorePort {
  private readonly now = inject(PAIR_SECURE_SEQUENCE_CLOCK);

  async next(domain: PairSecureSequenceDomain): Promise<number> {
    const database = await this.open();
    try {
      return await this.increment(database, domain);
    } finally {
      database.close();
    }
  }

  private increment(database: IDBDatabase, domain: PairSecureSequenceDomain): Promise<number> {
    const id = sequenceDomainId(domain);
    const nowMs = this.now();
    return new Promise((resolve, reject) => {
      let failure: PairSecureSequenceStoreError | null = null;
      let allocated: number | null = null;
      let transaction: IDBTransaction;
      try {
        transaction = database.transaction(STORE_NAME, 'readwrite');
      } catch {
        reject(new PairSecureSequenceStoreError('secure_sequence_store_write_failed'));
        return;
      }

      const store = transaction.objectStore(STORE_NAME);
      const prune = store.index('retainUntilMs').openCursor();
      prune.onerror = () => {
        failure = new PairSecureSequenceStoreError('secure_sequence_store_read_failed');
      };
      prune.onsuccess = () => {
        const cursor = prune.result;
        const retainUntilMs = cursor?.key;
        if (cursor && typeof retainUntilMs !== 'number') {
          failure = new PairSecureSequenceStoreError('secure_sequence_state_invalid');
          transaction.abort();
          return;
        }
        if (!cursor || (retainUntilMs as number) > nowMs) {
          this.allocate(store, transaction, id, domain, nowMs, (value) => {
            allocated = value;
          }, (error) => {
            failure = error;
          });
          return;
        }
        if (!isSelfConsistentSequenceRecord(cursor.value)) {
          failure = new PairSecureSequenceStoreError('secure_sequence_state_invalid');
          transaction.abort();
          return;
        }
        const removal = cursor.delete();
        removal.onerror = () => {
          failure = new PairSecureSequenceStoreError('secure_sequence_store_write_failed');
        };
        removal.onsuccess = () => cursor.continue();
      };
      transaction.oncomplete = () => {
        if (allocated === null) {
          reject(new PairSecureSequenceStoreError('secure_sequence_store_write_failed'));
          return;
        }
        resolve(allocated);
      };
      transaction.onerror = () => {
        failure ??= new PairSecureSequenceStoreError('secure_sequence_store_write_failed');
      };
      transaction.onabort = () => {
        reject(failure ?? new PairSecureSequenceStoreError('secure_sequence_store_write_failed'));
      };
    });
  }

  private allocate(
    store: IDBObjectStore,
    transaction: IDBTransaction,
    id: string,
    domain: PairSecureSequenceDomain,
    nowMs: number,
    onAllocated: (value: number) => void,
    onFailure: (error: PairSecureSequenceStoreError) => void,
  ): void {
    const read = store.get(id);
    read.onerror = () => onFailure(
      new PairSecureSequenceStoreError('secure_sequence_store_read_failed'),
    );
    read.onsuccess = () => {
      const existing = read.result as unknown;
      if (existing !== undefined) {
        if (!isStoredSequenceRecord(existing, id, domain)) {
          onFailure(new PairSecureSequenceStoreError('secure_sequence_state_invalid'));
          transaction.abort();
          return;
        }
        this.writeNext(store, transaction, id, domain, existing.sequence, nowMs, onAllocated, onFailure);
        return;
      }

      const count = store.count();
      count.onerror = () => onFailure(
        new PairSecureSequenceStoreError('secure_sequence_store_read_failed'),
      );
      count.onsuccess = () => {
        if (count.result >= MAX_RECORDS) {
          onFailure(new PairSecureSequenceStoreError('secure_sequence_capacity_exceeded'));
          transaction.abort();
          return;
        }
        this.writeNext(store, transaction, id, domain, 0, nowMs, onAllocated, onFailure);
      };
    };
  }

  private writeNext(
    store: IDBObjectStore,
    transaction: IDBTransaction,
    id: string,
    domain: PairSecureSequenceDomain,
    current: number,
    nowMs: number,
    onAllocated: (value: number) => void,
    onFailure: (error: PairSecureSequenceStoreError) => void,
  ): void {
    if (current >= MAX_SECURE_SEQUENCE) {
      onFailure(new PairSecureSequenceStoreError('secure_sequence_exhausted'));
      transaction.abort();
      return;
    }
    const allocated = current + 1;
    const write = store.put(sequenceRecord(id, domain, allocated, nowMs));
    write.onerror = () => onFailure(
      new PairSecureSequenceStoreError('secure_sequence_store_write_failed'),
    );
    write.onsuccess = () => onAllocated(allocated);
  }

  private open(): Promise<IDBDatabase> {
    if (!globalThis.indexedDB) {
      return Promise.reject(new PairSecureSequenceStoreError('secure_sequence_store_unavailable'));
    }
    return new Promise((resolve, reject) => {
      const opening = indexedDB.open(DB_NAME, DB_VERSION);
      opening.onupgradeneeded = () => {
        if (!opening.result.objectStoreNames.contains(STORE_NAME)) {
          const store = opening.result.createObjectStore(STORE_NAME, { keyPath: 'id' });
          store.createIndex('retainUntilMs', 'retainUntilMs', { unique: false });
        }
      };
      opening.onsuccess = () => {
        const database = opening.result;
        if (
          !database.objectStoreNames.contains(STORE_NAME)
          || !database.transaction(STORE_NAME, 'readonly')
            .objectStore(STORE_NAME).indexNames.contains('retainUntilMs')
        ) {
          database.close();
          reject(new PairSecureSequenceStoreError('secure_sequence_store_schema_invalid'));
          return;
        }
        database.onversionchange = () => database.close();
        resolve(database);
      };
      opening.onerror = () => reject(new PairSecureSequenceStoreError('secure_sequence_store_open_failed'));
      opening.onblocked = () => reject(new PairSecureSequenceStoreError('secure_sequence_store_blocked'));
    });
  }
}

export const PAIR_SECURE_SEQUENCE_STORE = new InjectionToken<PairSecureSequenceStorePort>(
  'PAIR_SECURE_SEQUENCE_STORE',
  { providedIn: 'root', factory: () => inject(IndexedDbPairSecureSequenceStore) },
);

function sequenceDomainId(domain: PairSecureSequenceDomain): string {
  // JSON encoding is collision-free for the exact receiver replay-window
  // tuple. Payload type is intentionally absent: all payloads in one traffic
  // class share a replay window.
  return JSON.stringify(['session', domain.scopeId, domain.epoch, domain.senderId, domain.trafficClass]);
}

function sequenceRecord(
  id: string,
  domain: PairSecureSequenceDomain,
  sequence: number,
  nowMs: number,
): StoredSequenceRecord {
  return {
    id,
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

function isStoredSequenceRecord(
  value: unknown,
  id: string,
  domain: PairSecureSequenceDomain,
): value is StoredSequenceRecord {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  const fields = Object.keys(record).sort();
  if (fields.length !== RECORD_FIELDS.length || fields.some((field, index) => field !== RECORD_FIELDS[index])) {
    return false;
  }
  return record['id'] === id
    && record['version'] === 1
    && record['scopeKind'] === 'session'
    && record['scopeId'] === domain.scopeId
    && record['epoch'] === domain.epoch
    && record['senderId'] === domain.senderId
    && record['trafficClass'] === domain.trafficClass
    && Number.isSafeInteger(record['sequence'])
    && (record['sequence'] as number) >= 1
    && (record['sequence'] as number) <= MAX_SECURE_SEQUENCE
    && validRetention(record['updatedAtMs'], record['retainUntilMs']);
}

function isSelfConsistentSequenceRecord(value: unknown): value is StoredSequenceRecord {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  if (
    record['scopeKind'] !== 'session'
    || typeof record['scopeId'] !== 'string'
    || !Number.isSafeInteger(record['epoch'])
    || typeof record['senderId'] !== 'string'
    || !['control', 'media', 'semantic', 'bulk'].includes(String(record['trafficClass']))
  ) return false;
  const domain: PairSecureSequenceDomain = {
    scopeId: record['scopeId'],
    epoch: record['epoch'] as number,
    senderId: record['senderId'],
    trafficClass: record['trafficClass'] as SecurityTrafficClass,
  };
  return isStoredSequenceRecord(value, sequenceDomainId(domain), domain);
}

function validRetention(updatedAtMs: unknown, retainUntilMs: unknown): boolean {
  return Number.isSafeInteger(updatedAtMs)
    && (updatedAtMs as number) >= 0
    && Number.isSafeInteger(retainUntilMs)
    && (retainUntilMs as number) > (updatedAtMs as number)
    && (retainUntilMs as number) - (updatedAtMs as number) === RETAIN_FOR_MS;
}
