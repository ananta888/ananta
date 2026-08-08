import { Injectable, InjectionToken, inject } from '@angular/core';

import { MAX_SECURE_SEQUENCE, SecurityTrafficClass } from './webrtc-secure-envelope';

export interface InboundNonceReplayDomain {
  readonly scopeKind: 'session' | 'room';
  readonly scopeId: string;
  readonly epoch: number;
  readonly keyId: string;
  readonly senderId: string;
  readonly recipientId: string;
}

export type InboundNonceClaimResult = 'claimed' | 'duplicate' | 'capacity_exceeded';

export interface InboundNonceReplayStorePort {
  hasNonce(
    domain: InboundNonceReplayDomain,
    nonceB64: string,
    nowMs: number,
  ): Promise<boolean>;
  claimNonce(
    domain: InboundNonceReplayDomain,
    nonceB64: string,
    retainUntilMs: number,
    nowMs: number,
  ): Promise<InboundNonceClaimResult>;
}

export interface PairReplayWindowDomain {
  readonly scopeKind: 'session' | 'room';
  readonly scopeId: string;
  readonly epoch: number;
  readonly senderId: string;
  readonly trafficClass: SecurityTrafficClass;
}

export type PairReplayWindowClaimResult =
  | 'accepted'
  | 'duplicate'
  | 'too_old'
  | 'too_far_ahead'
  | 'capacity_exceeded';

export interface PairReplayWindowStorePort {
  claimSequence(
    domain: PairReplayWindowDomain,
    sequence: number,
    nowMs: number,
  ): Promise<PairReplayWindowClaimResult>;
}

export class E2eReplayStoreError extends Error {
  constructor(readonly reasonCode: string) { super(reasonCode); }
}

interface StoredNonceClaim extends InboundNonceReplayDomain {
  readonly id: string;
  readonly version: 1;
  readonly nonceB64: string;
  readonly retainUntilMs: number;
}

interface StoredReplayWindow extends PairReplayWindowDomain {
  readonly id: string;
  readonly version: 1;
  readonly highest: number;
  readonly accepted: readonly number[];
  readonly touchedAtMs: number;
  readonly retainUntilMs: number;
}

const DB_NAME = 'ananta-e2e-replay';
const DB_VERSION = 1;
const NONCE_STORE_NAME = 'inbound-nonce-claims';
const WINDOW_STORE_NAME = 'pair-replay-windows';
const RETAIN_UNTIL_INDEX = 'by-retain-until';
const MAX_ACTIVE_NONCE_CLAIMS = 200_000;
const MAX_REPLAY_WINDOWS = 1024;
const REPLAY_WINDOW_SIZE = 128;
const MAX_SEQUENCE_ADVANCE = 4096;
const REPLAY_WINDOW_TTL_MS = 60 * 60_000;
const ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const NONCE_B64_RE = /^[A-Za-z0-9+/]{16}$/;
const NONCE_RECORD_FIELDS = [
  'epoch', 'id', 'keyId', 'nonceB64', 'recipientId', 'retainUntilMs',
  'scopeId', 'scopeKind', 'senderId', 'version',
] as const;
const WINDOW_RECORD_FIELDS = [
  'accepted', 'epoch', 'highest', 'id', 'retainUntilMs', 'scopeId',
  'scopeKind', 'senderId', 'touchedAtMs', 'trafficClass', 'version',
] as const;

/**
 * Persistent, cross-tab replay state for authenticated Pair traffic.
 *
 * IndexedDB serializes read-write transactions that overlap an object store.
 * Pruning, the global capacity decision and each claim consequently form one
 * indivisible operation across tabs and reloads. No RAM fallback is used: a
 * missing or malformed store fails closed.
 */
@Injectable({ providedIn: 'root' })
export class IndexedDbE2eReplayStore
implements InboundNonceReplayStorePort, PairReplayWindowStorePort {
  private databasePromise: Promise<IDBDatabase> | null = null;

  async hasNonce(
    domain: InboundNonceReplayDomain,
    nonceB64: string,
    nowMs: number,
  ): Promise<boolean> {
    validateNonceLookup(domain, nonceB64, nowMs);
    const database = await this.open();
    const id = nonceClaimId(domain, nonceB64);
    return new Promise((resolve, reject) => {
      let result: boolean | null = null;
      let failure: E2eReplayStoreError | null = null;
      let transaction: IDBTransaction;
      try {
        transaction = database.transaction(NONCE_STORE_NAME, 'readonly');
      } catch {
        reject(new E2eReplayStoreError('nonce_replay_store_read_failed'));
        return;
      }
      const read = transaction.objectStore(NONCE_STORE_NAME).get(id);
      read.onerror = () => { failure = new E2eReplayStoreError('nonce_replay_store_read_failed'); };
      read.onsuccess = () => {
        const existing = read.result as unknown;
        if (existing === undefined) {
          result = false;
          return;
        }
        if (!isStoredNonceClaim(existing, id, domain, nonceB64)) {
          failure = new E2eReplayStoreError('nonce_replay_state_invalid');
          transaction.abort();
          return;
        }
        result = existing.retainUntilMs >= nowMs;
      };
      transaction.oncomplete = () => {
        if (result === null) {
          reject(failure ?? new E2eReplayStoreError('nonce_replay_store_read_failed'));
          return;
        }
        resolve(result);
      };
      transaction.onerror = () => {
        failure ??= new E2eReplayStoreError('nonce_replay_store_read_failed');
      };
      transaction.onabort = () => {
        reject(failure ?? new E2eReplayStoreError('nonce_replay_store_read_failed'));
      };
    });
  }

  async claimNonce(
    domain: InboundNonceReplayDomain,
    nonceB64: string,
    retainUntilMs: number,
    nowMs: number,
  ): Promise<InboundNonceClaimResult> {
    validateNonceClaim(domain, nonceB64, retainUntilMs, nowMs);
    const database = await this.open();
    const id = nonceClaimId(domain, nonceB64);
    return new Promise((resolve, reject) => {
      let result: InboundNonceClaimResult | null = null;
      let failure: E2eReplayStoreError | null = null;
      let transaction: IDBTransaction;
      try {
        transaction = database.transaction(NONCE_STORE_NAME, 'readwrite');
      } catch {
        reject(new E2eReplayStoreError('nonce_replay_store_write_failed'));
        return;
      }
      const store = transaction.objectStore(NONCE_STORE_NAME);
      this.pruneExpired(store, nowMs, transaction, error => { failure = error; }, () => {
        const read = store.get(id);
        read.onerror = () => { failure = new E2eReplayStoreError('nonce_replay_store_read_failed'); };
        read.onsuccess = () => {
          const existing = read.result as unknown;
          if (existing !== undefined) {
            if (!isStoredNonceClaim(existing, id, domain, nonceB64)) {
              failure = new E2eReplayStoreError('nonce_replay_state_invalid');
              transaction.abort();
              return;
            }
            result = 'duplicate';
            return;
          }
          const count = store.count();
          count.onerror = () => { failure = new E2eReplayStoreError('nonce_replay_store_read_failed'); };
          count.onsuccess = () => {
            if (count.result >= MAX_ACTIVE_NONCE_CLAIMS) {
              result = 'capacity_exceeded';
              return;
            }
            const write = store.put(nonceClaimRecord(id, domain, nonceB64, retainUntilMs));
            write.onerror = () => { failure = new E2eReplayStoreError('nonce_replay_store_write_failed'); };
            write.onsuccess = () => { result = 'claimed'; };
          };
        };
      });
      transaction.oncomplete = () => {
        if (result === null) {
          reject(failure ?? new E2eReplayStoreError('nonce_replay_store_write_failed'));
          return;
        }
        resolve(result);
      };
      transaction.onerror = () => {
        failure ??= new E2eReplayStoreError('nonce_replay_store_write_failed');
      };
      transaction.onabort = () => {
        reject(failure ?? new E2eReplayStoreError('nonce_replay_store_write_failed'));
      };
    });
  }

  async claimSequence(
    domain: PairReplayWindowDomain,
    sequence: number,
    nowMs: number,
  ): Promise<PairReplayWindowClaimResult> {
    validateSequenceClaim(domain, sequence, nowMs);
    const database = await this.open();
    const id = replayWindowId(domain);
    return new Promise((resolve, reject) => {
      let result: PairReplayWindowClaimResult | null = null;
      let failure: E2eReplayStoreError | null = null;
      let transaction: IDBTransaction;
      try {
        transaction = database.transaction(WINDOW_STORE_NAME, 'readwrite');
      } catch {
        reject(new E2eReplayStoreError('pair_replay_store_write_failed'));
        return;
      }
      const store = transaction.objectStore(WINDOW_STORE_NAME);
      this.pruneExpired(store, nowMs, transaction, error => { failure = error; }, () => {
        const read = store.get(id);
        read.onerror = () => { failure = new E2eReplayStoreError('pair_replay_store_read_failed'); };
        read.onsuccess = () => {
          const existing = read.result as unknown;
          if (existing === undefined) {
            const count = store.count();
            count.onerror = () => { failure = new E2eReplayStoreError('pair_replay_store_read_failed'); };
            count.onsuccess = () => {
              if (count.result >= MAX_REPLAY_WINDOWS) {
                result = 'capacity_exceeded';
                return;
              }
              const record = replayWindowRecord(id, domain, sequence, nowMs, [sequence]);
              const write = store.put(record);
              write.onerror = () => { failure = new E2eReplayStoreError('pair_replay_store_write_failed'); };
              write.onsuccess = () => { result = 'accepted'; };
            };
            return;
          }
          if (!isStoredReplayWindow(existing, id, domain)) {
            failure = new E2eReplayStoreError('pair_replay_state_invalid');
            transaction.abort();
            return;
          }
          this.updateExistingWindow(store, existing, domain, sequence, nowMs, value => {
            result = value;
          }, error => { failure = error; });
        };
      });
      transaction.oncomplete = () => {
        if (result === null) {
          reject(failure ?? new E2eReplayStoreError('pair_replay_store_write_failed'));
          return;
        }
        resolve(result);
      };
      transaction.onerror = () => {
        failure ??= new E2eReplayStoreError('pair_replay_store_write_failed');
      };
      transaction.onabort = () => {
        reject(failure ?? new E2eReplayStoreError('pair_replay_store_write_failed'));
      };
    });
  }

  private updateExistingWindow(
    store: IDBObjectStore,
    existing: StoredReplayWindow,
    domain: PairReplayWindowDomain,
    sequence: number,
    nowMs: number,
    onResult: (result: PairReplayWindowClaimResult) => void,
    onError: (error: E2eReplayStoreError) => void,
  ): void {
    if (existing.accepted.includes(sequence)) {
      onResult('duplicate');
      return;
    }
    if (sequence <= existing.highest - REPLAY_WINDOW_SIZE) {
      onResult('too_old');
      return;
    }
    if (sequence > existing.highest + MAX_SEQUENCE_ADVANCE) {
      onResult('too_far_ahead');
      return;
    }
    const highest = Math.max(existing.highest, sequence);
    const floor = Math.max(1, highest - REPLAY_WINDOW_SIZE + 1);
    const accepted = [...existing.accepted, sequence]
      .filter(value => value >= floor)
      .sort((left, right) => left - right);
    const write = store.put(replayWindowRecord(existing.id, domain, highest, nowMs, accepted));
    write.onerror = () => { onError(new E2eReplayStoreError('pair_replay_store_write_failed')); };
    write.onsuccess = () => { onResult('accepted'); };
  }

  private pruneExpired(
    store: IDBObjectStore,
    nowMs: number,
    transaction: IDBTransaction,
    onError: (error: E2eReplayStoreError) => void,
    onComplete: () => void,
  ): void {
    let cursorRequest: IDBRequest<IDBCursorWithValue | null>;
    try {
      // Equality remains live. Secure envelopes are accepted through their
      // expires_at_ms + 30s boundary, so only strictly older claims may go.
      const expired = IDBKeyRange.upperBound(nowMs, true);
      cursorRequest = store.index(RETAIN_UNTIL_INDEX).openCursor(expired);
    } catch {
      onError(new E2eReplayStoreError('e2e_replay_store_schema_invalid'));
      transaction.abort();
      return;
    }
    cursorRequest.onerror = () => {
      onError(new E2eReplayStoreError('e2e_replay_store_cleanup_failed'));
    };
    cursorRequest.onsuccess = () => {
      const cursor = cursorRequest.result;
      if (!cursor) {
        onComplete();
        return;
      }
      const deletion = cursor.delete();
      deletion.onerror = () => {
        onError(new E2eReplayStoreError('e2e_replay_store_cleanup_failed'));
      };
      cursor.continue();
    };
  }

  private open(): Promise<IDBDatabase> {
    if (this.databasePromise) return this.databasePromise;
    let factory: IDBFactory;
    try {
      factory = globalThis.indexedDB;
      if (!factory) throw new Error('indexeddb_missing');
    } catch {
      return Promise.reject(new E2eReplayStoreError('e2e_replay_store_unavailable'));
    }
    this.databasePromise = new Promise((resolve, reject) => {
      const opening = factory.open(DB_NAME, DB_VERSION);
      let settled = false;
      opening.onupgradeneeded = () => this.createSchema(opening.result);
      opening.onsuccess = () => {
        const database = opening.result;
        if (settled) {
          database.close();
          return;
        }
        try {
          this.assertSchema(database);
        } catch (error) {
          settled = true;
          database.close();
          reject(error);
          return;
        }
        settled = true;
        database.onversionchange = () => {
          database.close();
          this.databasePromise = null;
        };
        resolve(database);
      };
      opening.onerror = () => {
        if (settled) return;
        settled = true;
        this.databasePromise = null;
        reject(new E2eReplayStoreError('e2e_replay_store_open_failed'));
      };
      opening.onblocked = () => {
        if (settled) return;
        settled = true;
        this.databasePromise = null;
        reject(new E2eReplayStoreError('e2e_replay_store_blocked'));
      };
    });
    return this.databasePromise;
  }

  private createSchema(database: IDBDatabase): void {
    if (!database.objectStoreNames.contains(NONCE_STORE_NAME)) {
      const nonceStore = database.createObjectStore(NONCE_STORE_NAME, { keyPath: 'id' });
      nonceStore.createIndex(RETAIN_UNTIL_INDEX, 'retainUntilMs', { unique: false });
    }
    if (!database.objectStoreNames.contains(WINDOW_STORE_NAME)) {
      const windowStore = database.createObjectStore(WINDOW_STORE_NAME, { keyPath: 'id' });
      windowStore.createIndex(RETAIN_UNTIL_INDEX, 'retainUntilMs', { unique: false });
    }
  }

  private assertSchema(database: IDBDatabase): void {
    if (
      !database.objectStoreNames.contains(NONCE_STORE_NAME)
      || !database.objectStoreNames.contains(WINDOW_STORE_NAME)
    ) throw new E2eReplayStoreError('e2e_replay_store_schema_invalid');
    let transaction: IDBTransaction;
    try {
      transaction = database.transaction([NONCE_STORE_NAME, WINDOW_STORE_NAME], 'readonly');
      if (
        !transaction.objectStore(NONCE_STORE_NAME).indexNames.contains(RETAIN_UNTIL_INDEX)
        || !transaction.objectStore(WINDOW_STORE_NAME).indexNames.contains(RETAIN_UNTIL_INDEX)
      ) throw new Error('index_missing');
    } catch {
      throw new E2eReplayStoreError('e2e_replay_store_schema_invalid');
    }
  }
}

export const INBOUND_NONCE_REPLAY_STORE = new InjectionToken<InboundNonceReplayStorePort>(
  'INBOUND_NONCE_REPLAY_STORE',
  { providedIn: 'root', factory: () => inject(IndexedDbE2eReplayStore) },
);

export const PAIR_REPLAY_WINDOW_STORE = new InjectionToken<PairReplayWindowStorePort>(
  'PAIR_REPLAY_WINDOW_STORE',
  { providedIn: 'root', factory: () => inject(IndexedDbE2eReplayStore) },
);

function validateNonceClaim(
  domain: InboundNonceReplayDomain,
  nonceB64: string,
  retainUntilMs: number,
  nowMs: number,
): void {
  if (
    !isInboundNonceDomain(domain)
    || !NONCE_B64_RE.test(nonceB64)
    || !isTimestamp(nowMs)
    || !isTimestamp(retainUntilMs)
    || retainUntilMs < nowMs
  ) throw new E2eReplayStoreError('nonce_replay_claim_invalid');
}

function validateNonceLookup(
  domain: InboundNonceReplayDomain,
  nonceB64: string,
  nowMs: number,
): void {
  if (
    !isInboundNonceDomain(domain)
    || !NONCE_B64_RE.test(nonceB64)
    || !isTimestamp(nowMs)
  ) throw new E2eReplayStoreError('nonce_replay_claim_invalid');
}

function validateSequenceClaim(
  domain: PairReplayWindowDomain,
  sequence: number,
  nowMs: number,
): void {
  if (
    !isPairReplayWindowDomain(domain)
    || !Number.isSafeInteger(sequence)
    || sequence < 1
    || sequence > MAX_SECURE_SEQUENCE
    || !isTimestamp(nowMs)
    || nowMs + REPLAY_WINDOW_TTL_MS > MAX_SECURE_SEQUENCE
  ) throw new E2eReplayStoreError('pair_replay_claim_invalid');
}

function isInboundNonceDomain(value: unknown): value is InboundNonceReplayDomain {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const domain = value as Record<string, unknown>;
  return isScope(domain)
    && isIdentifier(domain['keyId'])
    && isIdentifier(domain['senderId'])
    && isIdentifier(domain['recipientId']);
}

function isPairReplayWindowDomain(value: unknown): value is PairReplayWindowDomain {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const domain = value as Record<string, unknown>;
  return isScope(domain)
    && isIdentifier(domain['senderId'])
    && typeof domain['trafficClass'] === 'string'
    && ['control', 'media', 'semantic', 'bulk'].includes(domain['trafficClass']);
}

function isScope(domain: Record<string, unknown>): boolean {
  return (domain['scopeKind'] === 'session' || domain['scopeKind'] === 'room')
    && isIdentifier(domain['scopeId'])
    && Number.isSafeInteger(domain['epoch'])
    && (domain['epoch'] as number) >= 1
    && (domain['epoch'] as number) <= 2 ** 31 - 1;
}

function isIdentifier(value: unknown): value is string {
  return typeof value === 'string' && ID_RE.test(value);
}

function isTimestamp(value: number): boolean {
  return Number.isSafeInteger(value) && value >= 1 && value <= MAX_SECURE_SEQUENCE;
}

function nonceClaimId(domain: InboundNonceReplayDomain, nonceB64: string): string {
  return JSON.stringify([
    domain.scopeKind, domain.scopeId, domain.epoch, domain.keyId,
    domain.senderId, domain.recipientId, nonceB64,
  ]);
}

function replayWindowId(domain: PairReplayWindowDomain): string {
  return JSON.stringify([
    domain.scopeKind, domain.scopeId, domain.epoch, domain.senderId, domain.trafficClass,
  ]);
}

function nonceClaimRecord(
  id: string,
  domain: InboundNonceReplayDomain,
  nonceB64: string,
  retainUntilMs: number,
): StoredNonceClaim {
  return { id, version: 1, ...domain, nonceB64, retainUntilMs };
}

function replayWindowRecord(
  id: string,
  domain: PairReplayWindowDomain,
  highest: number,
  touchedAtMs: number,
  accepted: readonly number[],
): StoredReplayWindow {
  return {
    id, version: 1, ...domain, highest, accepted,
    touchedAtMs, retainUntilMs: touchedAtMs + REPLAY_WINDOW_TTL_MS,
  };
}

function isStoredNonceClaim(
  value: unknown,
  id: string,
  domain: InboundNonceReplayDomain,
  nonceB64: string,
): value is StoredNonceClaim {
  if (!closedRecord(value, NONCE_RECORD_FIELDS)) return false;
  const record = value as unknown as StoredNonceClaim;
  return record.id === id
    && record.version === 1
    && sameNonceDomain(record, domain)
    && record.nonceB64 === nonceB64
    && isTimestamp(record.retainUntilMs);
}

function isStoredReplayWindow(
  value: unknown,
  id: string,
  domain: PairReplayWindowDomain,
): value is StoredReplayWindow {
  if (!closedRecord(value, WINDOW_RECORD_FIELDS)) return false;
  const record = value as unknown as StoredReplayWindow;
  if (
    record.id !== id
    || record.version !== 1
    || !sameWindowDomain(record, domain)
    || !Number.isSafeInteger(record.highest)
    || record.highest < 1
    || record.highest > MAX_SECURE_SEQUENCE
    || !isTimestamp(record.touchedAtMs)
    || !isTimestamp(record.retainUntilMs)
    || record.retainUntilMs !== record.touchedAtMs + REPLAY_WINDOW_TTL_MS
    || !Array.isArray(record.accepted)
    || record.accepted.length < 1
    || record.accepted.length > REPLAY_WINDOW_SIZE
  ) return false;
  const floor = Math.max(1, record.highest - REPLAY_WINDOW_SIZE + 1);
  return record.accepted.every((sequence, index) => (
    Number.isSafeInteger(sequence)
    && sequence >= floor
    && sequence <= record.highest
    && (index === 0 || sequence > record.accepted[index - 1])
  )) && record.accepted.at(-1) === record.highest;
}

function sameNonceDomain(left: InboundNonceReplayDomain, right: InboundNonceReplayDomain): boolean {
  return left.scopeKind === right.scopeKind
    && left.scopeId === right.scopeId
    && left.epoch === right.epoch
    && left.keyId === right.keyId
    && left.senderId === right.senderId
    && left.recipientId === right.recipientId;
}

function sameWindowDomain(left: PairReplayWindowDomain, right: PairReplayWindowDomain): boolean {
  return left.scopeKind === right.scopeKind
    && left.scopeId === right.scopeId
    && left.epoch === right.epoch
    && left.senderId === right.senderId
    && left.trafficClass === right.trafficClass;
}

function closedRecord(value: unknown, fields: readonly string[]): value is Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const actual = Object.keys(value as Record<string, unknown>).sort();
  return actual.length === fields.length
    && actual.every((field, index) => field === fields[index]);
}
