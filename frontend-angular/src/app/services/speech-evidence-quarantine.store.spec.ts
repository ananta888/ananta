import { IDBFactory } from 'fake-indexeddb';

import { IndexedDbSpeechEvidenceQuarantineStore } from './speech-evidence-quarantine.store';
import { SpeechEvidenceMessage } from './speech-evidence-sync.validators';

describe('IndexedDbSpeechEvidenceQuarantineStore', () => {
  let originalIndexedDb: IDBFactory;
  let store: IndexedDbSpeechEvidenceQuarantineStore;

  beforeEach(() => {
    originalIndexedDb = globalThis.indexedDB as IDBFactory;
    globalThis.indexedDB = new IDBFactory() as unknown as IDBFactory;
    store = new IndexedDbSpeechEvidenceQuarantineStore();
  });

  afterEach(() => { globalThis.indexedDB = originalIndexedDb; });

  it('persists only encrypted chunks and treats exact resume delivery as idempotent', async () => {
    const first = chunk(1, 0, 'a'.repeat(64), 'b'.repeat(64));
    expect((await store.put(first)).disposition).toBe('stored');
    const duplicate = { ...first, message_id: 'chunk-retry', sequence: 2 };
    const resumed = await store.put(duplicate);

    expect(resumed.disposition).toBe('duplicate');
    expect(resumed.snapshot).toMatchObject({
      receivedChunks: 1, firstMissingIndex: 1, receivedBytes: 3, complete: true, conflictCount: 0,
    });
    const persisted = await store.group('session-a', 'session-a', 3, 'offer-a', 'group-a');
    expect(persisted).toHaveLength(1);
    expect(JSON.stringify(persisted[0])).not.toContain('plain transcript');
    expect(persisted[0].payload['ciphertext_b64']).toBeTruthy();
  });

  it('never overwrites a conflicting duplicate index and deletes scoped groups on revoke', async () => {
    await store.put(chunk(1, 0, 'a'.repeat(64), 'b'.repeat(64)));
    const conflict = await store.put(chunk(2, 0, 'c'.repeat(64), 'd'.repeat(64)));
    expect(conflict.disposition).toBe('conflict');
    expect(conflict.snapshot.conflictCount).toBe(1);

    expect(await store.removeGroups('session-a', 'session-a', 3, 'offer-a', ['group-a'])).toBe(1);
    expect(await store.summaries('session-a', 'session-a', 3, 'offer-a')).toEqual([]);
  });
});

function chunk(
  sequence: number,
  index: number,
  plaintextDigest: string,
  ciphertextDigest: string,
): SpeechEvidenceMessage {
  const ciphertext = new Uint8Array([1, 2, 3, ...new Uint8Array(16)]);
  return {
    protocol_version: 'ananta.speech-evidence-sync.v1',
    message_type: 'chunk',
    message_id: `chunk-${sequence}`,
    session_id: 'session-a',
    pair_id: 'session-a',
    sender_id: 'alice',
    audience_id: 'bob',
    epoch: 3,
    sequence,
    consent_version: 4,
    key_id: 'speech-key',
    issued_at_ms: Date.now(),
    expires_at_ms: Date.now() + 60_000,
    payload_digest: 'e'.repeat(64),
    payload: {
      traffic_class: 'evidence_bulk', offer_id: 'offer-a', group_id: 'group-a',
      chunk_index: index, chunk_count: 1, plaintext_bytes: 3,
      plaintext_digest: plaintextDigest, ciphertext_digest: ciphertextDigest,
      nonce_b64: bytesToB64(new Uint8Array(12)), ciphertext_b64: bytesToB64(ciphertext),
    },
    signature_algorithm: 'Ed25519',
    signature_b64: bytesToB64(new Uint8Array(64)),
  };
}

function bytesToB64(value: Uint8Array): string {
  let binary = '';
  for (const byte of value) binary += String.fromCharCode(byte);
  return btoa(binary);
}
