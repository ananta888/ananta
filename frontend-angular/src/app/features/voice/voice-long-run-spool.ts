import { InjectionToken } from '@angular/core';

const DEFAULT_DB_NAME = 'ananta-voice-live-run-spool';
const DB_VERSION = 1;
const KEY_STORE = 'keys';
const SEGMENT_STORE = 'segments';
const KEY_ID = 'voice-live-run-audio-v1';
const CIPHERTEXT_TTL_MILLISECONDS = 24 * 60 * 60 * 1_000;
export const VOICE_PROFILE_DELETION_STORAGE_PREFIX = 'ananta.voice.profile-deleted.';
export const VOICE_PROFILE_DELETION_EVENT = 'ananta:voice-profile-deleted';

export interface VoiceLongRunSpoolSegment {
  runId: string;
  profileId: string;
  profileGeneration: number;
  sequence: number;
  startedAtMs: number;
  endedAtMs: number;
  durationMs: number;
  overlapMilliseconds: number;
  idempotencyKey: string;
  audio: ArrayBuffer;
}

export interface VoiceLongRunSpoolMetadata extends Omit<VoiceLongRunSpoolSegment, 'audio'> {
  byteLength: number;
  createdAt: number;
  expiresAt: number;
}

export interface VoiceLongRunSpoolPutResult {
  stored: VoiceLongRunSpoolMetadata;
  evicted: VoiceLongRunSpoolMetadata[];
}

export interface VoiceLongRunSpoolStats {
  segments: number;
  bytes: number;
  maxSegments: number;
  maxBytes: number;
}

export interface VoiceLongRunSpoolPort {
  initialize(): Promise<void>;
  put(segment: VoiceLongRunSpoolSegment): Promise<VoiceLongRunSpoolPutResult>;
  read(runId: string, sequence: number): Promise<VoiceLongRunSpoolSegment | null>;
  list(runId: string): Promise<VoiceLongRunSpoolMetadata[]>;
  delete(runId: string, sequence: number): Promise<void>;
  clearRun(runId: string): Promise<void>;
  signalProfileDeletion(profileId: string): void;
  clearProfile(profileId: string): Promise<void>;
  allowProfile(profileId: string): Promise<number>;
  stats(runId?: string): Promise<VoiceLongRunSpoolStats>;
}

interface StoredVoiceLongRunSegment extends VoiceLongRunSpoolMetadata {
  storageId: string;
  iv: ArrayBuffer;
  ciphertext: ArrayBuffer;
}

/**
 * Bounded, encrypted IndexedDB ring buffer for complete rolling segments.
 * Audio is always stored as binary AES-GCM ciphertext. The non-extractable key
 * remains a structured-cloned CryptoKey in IndexedDB and is never exported.
 */
export class IndexedDbVoiceLongRunSpool implements VoiceLongRunSpoolPort {
  private key: CryptoKey | null = null;
  private keyPromise: Promise<CryptoKey> | null = null;
  private operationQueue: Promise<void> = Promise.resolve();

  constructor(
    private readonly maxSegments = 5,
    private readonly maxBytes = 24 * 1024 * 1024,
    private readonly dbName = DEFAULT_DB_NAME,
  ) {
    if (!Number.isInteger(maxSegments) || maxSegments < 1) {
      throw new Error('voice.long_run.spool_segment_limit_invalid');
    }
    if (!Number.isInteger(maxBytes) || maxBytes < 1) {
      throw new Error('voice.long_run.spool_byte_limit_invalid');
    }
  }

  async initialize(): Promise<void> {
    this.ensurePlatform();
    await this.withDatabase(async () => undefined);
    await this.getOrCreateKey();
    await this.purgeExpired();
  }

  put(segment: VoiceLongRunSpoolSegment): Promise<VoiceLongRunSpoolPutResult> {
    return this.serialized(async () => {
      this.validateSegment(segment);
      if (segment.audio.byteLength > this.maxBytes) {
        throw new Error('voice.long_run.segment_exceeds_spool');
      }
      const metadata: VoiceLongRunSpoolMetadata = {
        runId: segment.runId,
        profileId: segment.profileId,
        profileGeneration: segment.profileGeneration,
        sequence: segment.sequence,
        startedAtMs: segment.startedAtMs,
        endedAtMs: segment.endedAtMs,
        durationMs: segment.durationMs,
        overlapMilliseconds: segment.overlapMilliseconds,
        idempotencyKey: segment.idempotencyKey,
        byteLength: segment.audio.byteLength,
        createdAt: Date.now(),
        expiresAt: Date.now() + CIPHERTEXT_TTL_MILLISECONDS,
      };
      const key = await this.getOrCreateKey();
      const iv = crypto.getRandomValues(new Uint8Array(new ArrayBuffer(12)));
      const ciphertext = await crypto.subtle.encrypt(
        { name: 'AES-GCM', iv, additionalData: this.additionalData(metadata) },
        key,
        new Uint8Array(segment.audio),
      );
      return this.withDatabase(async (db) => {
        const storageId = this.storageId(segment.runId, segment.sequence);
        const stored: StoredVoiceLongRunSegment = {
          ...metadata,
          storageId,
          iv: iv.slice().buffer,
          ciphertext,
        };
        return new Promise<VoiceLongRunSpoolPutResult>((resolve, reject) => {
          const transaction = db.transaction([SEGMENT_STORE, KEY_STORE], 'readwrite');
          const store = transaction.objectStore(SEGMENT_STORE);
          const keys = transaction.objectStore(KEY_STORE);
          const currentRequest = store.getAll();
          const markerRequest = keys.get(this.profileDeletionKey(segment.profileId));
          let current: StoredVoiceLongRunSegment[] | null = null;
          let deletedAt: number | null = null;
          let evicted: StoredVoiceLongRunSegment[] = [];
          let failure: unknown = null;
          let result: VoiceLongRunSpoolPutResult | null = null;

          const writeWhenReady = () => {
            if (!current || deletedAt == null || result || failure) return;
            try {
              if (deletedAt && segment.profileGeneration <= deletedAt) {
                throw new Error('voice.long_run.profile_deleted');
              }
              const otherRuns = current.filter((record) => (
                record.storageId !== storageId && record.runId !== segment.runId
              ));
              const retained = current
                .filter((record) => record.storageId !== storageId)
                .filter((record) => record.runId === segment.runId)
                .sort((left, right) => left.createdAt - right.createdAt || left.sequence - right.sequence);
              const otherBytes = otherRuns.reduce((total, record) => total + record.byteLength, 0);
              let retainedBytes = retained.reduce((total, record) => total + record.byteLength, 0);
              while (retained.length >= this.maxSegments
                || otherRuns.length + retained.length >= this.maxSegments
                || otherBytes + retainedBytes + metadata.byteLength > this.maxBytes) {
                const removed = retained.shift();
                if (!removed) throw new Error('voice.long_run.other_run_pending');
                evicted.push(removed);
                retainedBytes -= removed.byteLength;
              }
              for (const record of evicted) store.delete(record.storageId);
              store.put(stored);
              result = {
                stored: metadata,
                evicted: evicted.map((record) => this.metadata(record)),
              };
            } catch (error) {
              failure = error;
              transaction.abort();
            }
          };

          currentRequest.onsuccess = () => {
            const now = Date.now();
            const records = currentRequest.result as StoredVoiceLongRunSegment[];
            for (const record of records) {
              if (this.expired(record, now)) store.delete(record.storageId);
            }
            current = records.filter((record) => !this.expired(record, now));
            writeWhenReady();
          };
          markerRequest.onsuccess = () => {
            deletedAt = Number(markerRequest.result || 0);
            writeWhenReady();
          };
          currentRequest.onerror = () => { failure = currentRequest.error; };
          markerRequest.onerror = () => { failure = markerRequest.error; };
          transaction.oncomplete = () => result
            ? resolve(result)
            : reject(failure || new Error('voice.long_run.spool_write_incomplete'));
          transaction.onerror = () => reject(transaction.error);
          transaction.onabort = () => reject(failure || transaction.error);
        });
      });
    });
  }

  read(runId: string, sequence: number): Promise<VoiceLongRunSpoolSegment | null> {
    return this.serialized(async () => this.withDatabase(async (db) => {
      const record = await this.readActiveRecord(db, this.storageId(runId, sequence));
      if (!record) return null;
      const plaintext = await crypto.subtle.decrypt(
        {
          name: 'AES-GCM',
          iv: new Uint8Array(record.iv),
          additionalData: this.additionalData(record),
        },
        await this.getOrCreateKey(),
        new Uint8Array(record.ciphertext),
      );
      return {
        runId: record.runId,
        profileId: record.profileId,
        profileGeneration: record.profileGeneration,
        sequence: record.sequence,
        startedAtMs: record.startedAtMs,
        endedAtMs: record.endedAtMs,
        durationMs: record.durationMs,
        overlapMilliseconds: record.overlapMilliseconds,
        idempotencyKey: record.idempotencyKey,
        audio: plaintext,
      };
    }));
  }

  list(runId: string): Promise<VoiceLongRunSpoolMetadata[]> {
    return this.serialized(async () => this.withDatabase(async (db) => (
      (await this.activeRecords(db))
        .filter((record) => record.runId === runId)
        .sort((left, right) => left.sequence - right.sequence)
        .map((record) => this.metadata(record))
    )));
  }

  delete(runId: string, sequence: number): Promise<void> {
    return this.serialized(async () => this.withDatabase(async (db) => {
      await this.deleteIds(db, [this.storageId(runId, sequence)]);
    }));
  }

  clearRun(runId: string): Promise<void> {
    return this.serialized(async () => this.withDatabase(async (db) => {
      const ids = (await this.getAll(db))
        .filter((record) => record.runId === runId)
        .map((record) => record.storageId);
      await this.deleteIds(db, ids);
    }));
  }

  signalProfileDeletion(profileId: string): void {
    // Publish synchronously, before entering the spool's operation queue. A
    // stalled encryption/write or HTTP request must never delay capture
    // cancellation once the user has confirmed the privacy deletion.
    try {
      localStorage.setItem(`${VOICE_PROFILE_DELETION_STORAGE_PREFIX}${profileId}`, String(Date.now()));
    } catch {
      // IndexedDB tombstone below remains authoritative when localStorage is blocked.
    }
    globalThis.dispatchEvent?.(new CustomEvent(VOICE_PROFILE_DELETION_EVENT, {
      detail: { profileId },
    }));
  }

  clearProfile(profileId: string): Promise<void> {
    this.signalProfileDeletion(profileId);
    // Privacy deletion bypasses the ordinary per-instance queue. A put may be
    // stalled in WebCrypto before opening its transaction; the tombstone must
    // become durable now so that late put observes it and aborts. IndexedDB
    // still serializes the actual readwrite transactions across tabs.
    return this.withDatabase(async (db) => {
      await new Promise<void>((resolve, reject) => {
        const transaction = db.transaction([SEGMENT_STORE, KEY_STORE], 'readwrite');
        transaction.objectStore(KEY_STORE).put(Date.now(), this.profileDeletionKey(profileId));
        const segments = transaction.objectStore(SEGMENT_STORE);
        // Scan and delete inside the marker transaction. IndexedDB serializes
        // this against puts from other tabs: an earlier put is observed here;
        // a later put observes the tombstone and aborts.
        const existing = segments.getAll();
        existing.onsuccess = () => {
          for (const record of existing.result as StoredVoiceLongRunSegment[]) {
            if (record.profileId === profileId) segments.delete(record.storageId);
          }
        };
        transaction.oncomplete = () => resolve();
        transaction.onerror = () => reject(transaction.error);
        transaction.onabort = () => reject(transaction.error);
      });
    });
  }

  allowProfile(profileId: string): Promise<number> {
    return this.serialized(async () => this.withDatabase(async (db) => {
      const deletedAt = await new Promise<number>((resolve, reject) => {
        const request = db.transaction(KEY_STORE, 'readonly')
          .objectStore(KEY_STORE)
          .get(this.profileDeletionKey(profileId));
        request.onsuccess = () => resolve(Number(request.result || 0));
        request.onerror = () => reject(request.error);
      });
      // The tombstone deliberately remains. A fresh, explicit run receives a
      // later generation, while tabs holding a pre-delete generation stay fenced.
      return Math.max(Date.now(), deletedAt + 1);
    }));
  }

  stats(runId?: string): Promise<VoiceLongRunSpoolStats> {
    return this.serialized(async () => this.withDatabase(async (db) => {
      const records = (await this.activeRecords(db))
        .filter((record) => !runId || record.runId === runId);
      return {
        segments: records.length,
        bytes: records.reduce((total, record) => total + record.byteLength, 0),
        maxSegments: this.maxSegments,
        maxBytes: this.maxBytes,
      };
    }));
  }

  private async getOrCreateKey(): Promise<CryptoKey> {
    if (this.key) return this.key;
    if (this.keyPromise) return this.keyPromise;
    this.keyPromise = this.withDatabase(async (db) => {
      const existing = await this.readKey(db);
      if (existing) return existing;
      const generated = await crypto.subtle.generateKey(
        { name: 'AES-GCM', length: 256 },
        false,
        ['encrypt', 'decrypt'],
      );
      try {
        await new Promise<void>((resolve, reject) => {
          const transaction = db.transaction(KEY_STORE, 'readwrite');
          // add(), rather than put(), makes concurrent tabs converge on the
          // first durable key instead of replacing it with an incompatible key.
          transaction.objectStore(KEY_STORE).add(generated, KEY_ID);
          transaction.oncomplete = () => resolve();
          transaction.onerror = () => reject(transaction.error);
          transaction.onabort = () => reject(transaction.error);
        });
        return generated;
      } catch (error) {
        const winner = await this.readKey(db);
        if (winner) return winner;
        throw error;
      }
    });
    try {
      this.key = await this.keyPromise;
      return this.key;
    } finally {
      this.keyPromise = null;
    }
  }

  private async withDatabase<T>(operation: (db: IDBDatabase) => Promise<T>): Promise<T> {
    this.ensurePlatform();
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open(this.dbName, DB_VERSION);
      request.onupgradeneeded = () => {
        const database = request.result;
        if (!database.objectStoreNames.contains(KEY_STORE)) database.createObjectStore(KEY_STORE);
        if (!database.objectStoreNames.contains(SEGMENT_STORE)) {
          database.createObjectStore(SEGMENT_STORE, { keyPath: 'storageId' });
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
      request.onblocked = () => reject(new Error('voice.long_run.spool_blocked'));
    });
    try {
      return await operation(db);
    } finally {
      db.close();
    }
  }

  private getAll(db: IDBDatabase): Promise<StoredVoiceLongRunSegment[]> {
    return new Promise((resolve, reject) => {
      const request = db.transaction(SEGMENT_STORE, 'readonly').objectStore(SEGMENT_STORE).getAll();
      request.onsuccess = () => resolve(request.result as StoredVoiceLongRunSegment[]);
      request.onerror = () => reject(request.error);
    });
  }

  private activeRecords(db: IDBDatabase): Promise<StoredVoiceLongRunSegment[]> {
    return new Promise((resolve, reject) => {
      const transaction = db.transaction(SEGMENT_STORE, 'readwrite');
      const store = transaction.objectStore(SEGMENT_STORE);
      const request = store.getAll();
      let active: StoredVoiceLongRunSegment[] = [];
      request.onsuccess = () => {
        const now = Date.now();
        const records = request.result as StoredVoiceLongRunSegment[];
        active = records.filter((record) => !this.expired(record, now));
        for (const record of records) {
          if (this.expired(record, now)) store.delete(record.storageId);
        }
      };
      transaction.oncomplete = () => resolve(active);
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error);
    });
  }

  private readActiveRecord(
    db: IDBDatabase,
    storageId: string,
  ): Promise<StoredVoiceLongRunSegment | undefined> {
    return new Promise((resolve, reject) => {
      const transaction = db.transaction(SEGMENT_STORE, 'readwrite');
      const store = transaction.objectStore(SEGMENT_STORE);
      const request = store.get(storageId);
      let active: StoredVoiceLongRunSegment | undefined;
      request.onsuccess = () => {
        const record = request.result as StoredVoiceLongRunSegment | undefined;
        if (record && this.expired(record)) store.delete(storageId);
        else active = record;
      };
      transaction.oncomplete = () => resolve(active);
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error);
    });
  }

  private expired(record: StoredVoiceLongRunSegment, now = Date.now()): boolean {
    return !Number.isFinite(record.expiresAt) || record.expiresAt <= now;
  }

  private readKey(db: IDBDatabase): Promise<CryptoKey | undefined> {
    return new Promise((resolve, reject) => {
      const request = db.transaction(KEY_STORE, 'readonly').objectStore(KEY_STORE).get(KEY_ID);
      request.onsuccess = () => resolve(request.result as CryptoKey | undefined);
      request.onerror = () => reject(request.error);
    });
  }

  private purgeExpired(): Promise<void> {
    return this.serialized(async () => this.withDatabase(async (db) => {
      const now = Date.now();
      const ids = (await this.getAll(db))
        .filter((record) => !record.expiresAt || record.expiresAt <= now)
        .map((record) => record.storageId);
      await this.deleteIds(db, ids);
    }));
  }

  private deleteIds(db: IDBDatabase, ids: string[]): Promise<void> {
    if (!ids.length) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const transaction = db.transaction(SEGMENT_STORE, 'readwrite');
      const store = transaction.objectStore(SEGMENT_STORE);
      for (const id of ids) store.delete(id);
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error);
    });
  }

  private additionalData(metadata: VoiceLongRunSpoolMetadata): ArrayBuffer {
    const encoded = new TextEncoder().encode(JSON.stringify([
      metadata.runId,
      metadata.profileId,
      metadata.profileGeneration,
      metadata.sequence,
      metadata.startedAtMs,
      metadata.endedAtMs,
      metadata.durationMs,
      metadata.overlapMilliseconds,
      metadata.idempotencyKey,
      metadata.byteLength,
    ]));
    const data = new Uint8Array(new ArrayBuffer(encoded.byteLength));
    data.set(encoded);
    return data.buffer;
  }

  private validateSegment(segment: VoiceLongRunSpoolSegment): void {
    if (!segment.runId.trim() || !segment.profileId.trim() || !segment.idempotencyKey.trim()) {
      throw new Error('voice.long_run.spool_metadata_invalid');
    }
    if (!Number.isInteger(segment.sequence) || segment.sequence < 0
      || !Number.isInteger(segment.profileGeneration) || segment.profileGeneration <= 0
      || !Number.isInteger(segment.startedAtMs) || segment.startedAtMs < 0
      || !Number.isInteger(segment.endedAtMs) || segment.endedAtMs <= segment.startedAtMs
      || !Number.isInteger(segment.durationMs) || segment.durationMs <= 0
      || !Number.isInteger(segment.overlapMilliseconds) || segment.overlapMilliseconds < 0
      || segment.audio.byteLength <= 44) {
      throw new Error('voice.long_run.spool_metadata_invalid');
    }
  }

  private metadata(record: StoredVoiceLongRunSegment): VoiceLongRunSpoolMetadata {
    return {
      runId: record.runId,
      profileId: record.profileId,
      profileGeneration: record.profileGeneration,
      sequence: record.sequence,
      startedAtMs: record.startedAtMs,
      endedAtMs: record.endedAtMs,
      durationMs: record.durationMs,
      overlapMilliseconds: record.overlapMilliseconds,
      idempotencyKey: record.idempotencyKey,
      byteLength: record.byteLength,
      createdAt: record.createdAt,
      expiresAt: record.expiresAt,
    };
  }

  private storageId(runId: string, sequence: number): string {
    return `${runId}:${sequence}`;
  }

  private profileDeletionKey(profileId: string): string {
    return `deleted-profile:${profileId}`;
  }

  private ensurePlatform(): void {
    if (!globalThis.indexedDB || !globalThis.crypto?.subtle) {
      throw new Error('voice.long_run.secure_spool_unavailable');
    }
  }

  private serialized<T>(operation: () => Promise<T>): Promise<T> {
    const result = this.operationQueue.then(operation, operation);
    this.operationQueue = result.then(() => undefined, () => undefined);
    return result;
  }
}

export const VOICE_LONG_RUN_SPOOL = new InjectionToken<VoiceLongRunSpoolPort>('VOICE_LONG_RUN_SPOOL', {
  providedIn: 'root',
  factory: () => new IndexedDbVoiceLongRunSpool(),
});
