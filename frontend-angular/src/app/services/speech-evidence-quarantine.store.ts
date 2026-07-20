import { Injectable, InjectionToken, inject } from '@angular/core';

import {
  SpeechEvidenceMessage,
  SpeechEvidenceValidationError,
  validateSpeechEvidenceMessage,
} from './speech-evidence-sync.validators';

export interface SpeechEvidenceQuarantineGroupSnapshot {
  readonly offerId: string;
  readonly groupId: string;
  readonly chunkCount: number;
  readonly receivedChunks: number;
  readonly firstMissingIndex: number;
  readonly receivedBytes: number;
  readonly complete: boolean;
  readonly conflictCount: number;
  readonly lineageDigests: readonly string[];
}

export interface SpeechEvidenceQuarantinePutResult {
  readonly disposition: 'stored' | 'duplicate' | 'conflict';
  readonly snapshot: SpeechEvidenceQuarantineGroupSnapshot;
}

export interface SpeechEvidenceQuarantineStorePort {
  put(message: SpeechEvidenceMessage): Promise<SpeechEvidenceQuarantinePutResult>;
  group(
    sessionId: string,
    pairId: string,
    epoch: number,
    offerId: string,
    groupId: string,
  ): Promise<readonly SpeechEvidenceMessage[]>;
  summaries(
    sessionId: string,
    pairId: string,
    epoch: number,
    offerId?: string,
  ): Promise<readonly SpeechEvidenceQuarantineGroupSnapshot[]>;
  removeGroups(
    sessionId: string,
    pairId: string,
    epoch: number,
    offerId: string,
    groupIds: readonly string[],
  ): Promise<number>;
  pruneExpired(nowMs?: number): Promise<number>;
}

export const SPEECH_EVIDENCE_QUARANTINE_STORE = new InjectionToken<SpeechEvidenceQuarantineStorePort>(
  'SPEECH_EVIDENCE_QUARANTINE_STORE',
);

interface StoredQuarantineChunk {
  readonly key: string;
  readonly sessionId: string;
  readonly pairId: string;
  readonly epoch: number;
  readonly offerId: string;
  readonly groupId: string;
  readonly chunkIndex: number;
  readonly chunkCount: number;
  readonly plaintextBytes: number;
  readonly plaintextDigest: string;
  readonly ciphertextDigest: string;
  readonly nonceB64: string;
  readonly ciphertextBytes: number;
  readonly expiresAtMs: number;
  readonly message: SpeechEvidenceMessage;
  readonly conflicted: boolean;
}

const DB_NAME = 'ananta-speech-evidence-quarantine-v1';
const STORE_NAME = 'encrypted_chunks';
const DB_VERSION = 1;
const MAX_RECORDS = 4096;
const MAX_CIPHERTEXT_BYTES = 64 * 1024 * 1024;

/**
 * Recipient-owned encrypted quarantine.
 *
 * Only the already AES-GCM-encrypted inner chunk and content-free bindings are
 * durable. Decrypted transcript data never enters IndexedDB and cannot be
 * addressed by training or inference code through this port.
 */
@Injectable()
export class IndexedDbSpeechEvidenceQuarantineStore implements SpeechEvidenceQuarantineStorePort {
  async put(raw: SpeechEvidenceMessage): Promise<SpeechEvidenceQuarantinePutResult> {
    const message = validateSpeechEvidenceMessage(raw);
    if (message.message_type !== 'chunk') {
      throw new SpeechEvidenceValidationError('speech_evidence_chunk_required');
    }
    const candidate = stored(message);
    const database = await this.open();
    try {
      const transaction = database.transaction(STORE_NAME, 'readwrite');
      const done = complete(transaction);
      const store = transaction.objectStore(STORE_NAME);
      const allRows = await request<StoredQuarantineChunk[]>(store.getAll());
      const rows = allRows.filter(row => row.expiresAtMs > Date.now());
      const expired = allRows.filter(row => row.expiresAtMs <= Date.now());
      for (const row of expired) store.delete(row.key);
      const existing = rows.find(row => row.key === candidate.key);
      if (existing) {
        if (sameCiphertext(existing, candidate)) {
          await done;
          return Object.freeze({
            disposition: 'duplicate' as const,
            snapshot: summarize(rows, candidate),
          });
        }
        store.put({ ...existing, conflicted: true } satisfies StoredQuarantineChunk);
        await done;
        const conflicted = rows.map(row => row.key === existing.key ? { ...row, conflicted: true } : row);
        return Object.freeze({
          disposition: 'conflict' as const,
          snapshot: summarize(conflicted, candidate),
        });
      }
      const byteCount = rows.reduce((total, row) => total + row.ciphertextBytes, 0);
      if (rows.length >= MAX_RECORDS || byteCount + candidate.ciphertextBytes > MAX_CIPHERTEXT_BYTES) {
        transaction.abort();
        await done.catch(() => undefined);
        throw new SpeechEvidenceValidationError('speech_evidence_quarantine_quota_exceeded');
      }
      store.add(candidate);
      await done;
      return Object.freeze({
        disposition: 'stored' as const,
        snapshot: summarize([...rows, candidate], candidate),
      });
    } finally {
      database.close();
    }
  }

  async group(
    sessionId: string,
    pairId: string,
    epoch: number,
    offerId: string,
    groupId: string,
  ): Promise<readonly SpeechEvidenceMessage[]> {
    const rows = await this.readAll();
    return Object.freeze(rows
      .filter(row => matches(row, sessionId, pairId, epoch, offerId) && row.groupId === groupId)
      .sort((left, right) => left.chunkIndex - right.chunkIndex)
      .map(row => validateSpeechEvidenceMessage(row.message)));
  }

  async summaries(
    sessionId: string,
    pairId: string,
    epoch: number,
    offerId?: string,
  ): Promise<readonly SpeechEvidenceQuarantineGroupSnapshot[]> {
    const rows = (await this.readAll())
      .filter(row => matches(row, sessionId, pairId, epoch, offerId));
    const groups = new Map<string, StoredQuarantineChunk[]>();
    for (const row of rows) {
      const key = `${row.offerId}\0${row.groupId}`;
      groups.set(key, [...(groups.get(key) ?? []), row]);
    }
    return Object.freeze([...groups.values()]
      .map(groupRows => summarize(groupRows, groupRows[0]))
      .sort((left, right) => `${left.offerId}\0${left.groupId}`.localeCompare(`${right.offerId}\0${right.groupId}`)));
  }

  async removeGroups(
    sessionId: string,
    pairId: string,
    epoch: number,
    offerId: string,
    groupIds: readonly string[],
  ): Promise<number> {
    const selected = new Set(groupIds);
    if (selected.size > 4096) throw new SpeechEvidenceValidationError('speech_evidence_groups_invalid');
    const database = await this.open();
    try {
      const transaction = database.transaction(STORE_NAME, 'readwrite');
      const done = complete(transaction);
      const store = transaction.objectStore(STORE_NAME);
      const rows = await request<StoredQuarantineChunk[]>(store.getAll());
      const removed = rows.filter(row =>
        matches(row, sessionId, pairId, epoch, offerId) && selected.has(row.groupId));
      for (const row of removed) store.delete(row.key);
      await done;
      return removed.length;
    } finally {
      database.close();
    }
  }

  async pruneExpired(nowMs = Date.now()): Promise<number> {
    const database = await this.open();
    try {
      const transaction = database.transaction(STORE_NAME, 'readwrite');
      const done = complete(transaction);
      const store = transaction.objectStore(STORE_NAME);
      const rows = await request<StoredQuarantineChunk[]>(store.getAll());
      const expired = rows.filter(row => row.expiresAtMs <= nowMs);
      for (const row of expired) store.delete(row.key);
      await done;
      return expired.length;
    } finally {
      database.close();
    }
  }

  private async readAll(): Promise<StoredQuarantineChunk[]> {
    const database = await this.open();
    try {
      const transaction = database.transaction(STORE_NAME, 'readonly');
      const done = complete(transaction);
      const rows = await request<StoredQuarantineChunk[]>(transaction.objectStore(STORE_NAME).getAll());
      await done;
      return rows.filter(row => row.expiresAtMs > Date.now());
    } finally {
      database.close();
    }
  }

  private open(): Promise<IDBDatabase> {
    if (!globalThis.indexedDB) {
      return Promise.reject(new SpeechEvidenceValidationError('speech_evidence_quarantine_unavailable'));
    }
    return new Promise((resolve, reject) => {
      const opening = indexedDB.open(DB_NAME, DB_VERSION);
      opening.onupgradeneeded = () => {
        if (!opening.result.objectStoreNames.contains(STORE_NAME)) {
          opening.result.createObjectStore(STORE_NAME, { keyPath: 'key' });
        }
      };
      opening.onsuccess = () => resolve(opening.result);
      opening.onerror = () => reject(new SpeechEvidenceValidationError('speech_evidence_quarantine_unavailable'));
      opening.onblocked = () => reject(new SpeechEvidenceValidationError('speech_evidence_quarantine_blocked'));
    });
  }
}

@Injectable()
export class SpeechEvidenceQuarantineStore {
  private readonly store = inject(SPEECH_EVIDENCE_QUARANTINE_STORE);

  put(message: SpeechEvidenceMessage): Promise<SpeechEvidenceQuarantinePutResult> { return this.store.put(message); }
  group(...scope: Parameters<SpeechEvidenceQuarantineStorePort['group']>) { return this.store.group(...scope); }
  summaries(...scope: Parameters<SpeechEvidenceQuarantineStorePort['summaries']>) { return this.store.summaries(...scope); }
  removeGroups(...scope: Parameters<SpeechEvidenceQuarantineStorePort['removeGroups']>) {
    return this.store.removeGroups(...scope);
  }
  pruneExpired(nowMs?: number): Promise<number> { return this.store.pruneExpired(nowMs); }
}

function stored(message: SpeechEvidenceMessage): StoredQuarantineChunk {
  const payload = message.payload;
  const offerId = String(payload['offer_id']);
  const groupId = String(payload['group_id']);
  const chunkIndex = Number(payload['chunk_index']);
  const ciphertextB64 = String(payload['ciphertext_b64']);
  return Object.freeze({
    key: [message.session_id, message.pair_id, message.epoch, offerId, groupId, chunkIndex].join('\0'),
    sessionId: message.session_id,
    pairId: message.pair_id,
    epoch: message.epoch,
    offerId,
    groupId,
    chunkIndex,
    chunkCount: Number(payload['chunk_count']),
    plaintextBytes: Number(payload['plaintext_bytes']),
    plaintextDigest: String(payload['plaintext_digest']),
    ciphertextDigest: String(payload['ciphertext_digest']),
    nonceB64: String(payload['nonce_b64']),
    ciphertextBytes: Math.floor(ciphertextB64.length * 3 / 4),
    expiresAtMs: message.expires_at_ms,
    message,
    conflicted: false,
  });
}

function sameCiphertext(left: StoredQuarantineChunk, right: StoredQuarantineChunk): boolean {
  return left.chunkCount === right.chunkCount
    && left.plaintextBytes === right.plaintextBytes
    && left.plaintextDigest === right.plaintextDigest
    && left.ciphertextDigest === right.ciphertextDigest
    && left.nonceB64 === right.nonceB64;
}

function matches(
  row: StoredQuarantineChunk,
  sessionId: string,
  pairId: string,
  epoch: number,
  offerId?: string,
): boolean {
  return row.sessionId === sessionId
    && row.pairId === pairId
    && row.epoch === epoch
    && (offerId === undefined || row.offerId === offerId);
}

function summarize(
  rows: readonly StoredQuarantineChunk[],
  scope: StoredQuarantineChunk,
): SpeechEvidenceQuarantineGroupSnapshot {
  const selected = rows
    .filter(row => matches(row, scope.sessionId, scope.pairId, scope.epoch, scope.offerId)
      && row.groupId === scope.groupId)
    .sort((left, right) => left.chunkIndex - right.chunkIndex);
  const indices = new Set(selected.map(row => row.chunkIndex));
  let firstMissingIndex = 0;
  while (indices.has(firstMissingIndex)) firstMissingIndex += 1;
  return Object.freeze({
    offerId: scope.offerId,
    groupId: scope.groupId,
    chunkCount: scope.chunkCount,
    receivedChunks: indices.size,
    firstMissingIndex,
    receivedBytes: selected.reduce((total, row) => total + row.plaintextBytes, 0),
    complete: firstMissingIndex === scope.chunkCount,
    conflictCount: selected.filter(row => row.conflicted).length,
    lineageDigests: Object.freeze(selected.map(row => row.plaintextDigest)),
  });
}

function request<T>(value: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    value.onsuccess = () => resolve(value.result);
    value.onerror = () => reject(new SpeechEvidenceValidationError('speech_evidence_quarantine_io_failed'));
  });
}

function complete(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(new SpeechEvidenceValidationError('speech_evidence_quarantine_io_failed'));
    transaction.onabort = () => reject(new SpeechEvidenceValidationError('speech_evidence_quarantine_io_failed'));
  });
}
