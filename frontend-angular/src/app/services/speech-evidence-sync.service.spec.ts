import { TestBed } from '@angular/core/testing';

import {
  SPEECH_EVIDENCE_AEAD_KEYS,
  SPEECH_EVIDENCE_SEND_QUEUE,
  SPEECH_EVIDENCE_SIGNER,
  SpeechEvidenceAeadKeyPort,
  SpeechEvidenceMessageSignerPort,
  SpeechEvidenceSendPort,
  SpeechEvidenceSyncService,
} from './speech-evidence-sync.service';
import {
  SPEECH_EVIDENCE_PROTOCOL_VERSION,
  SpeechEvidenceMessage,
  SpeechEvidenceMessageType,
  SpeechEvidenceValidationError,
  sha256Canonical,
} from './speech-evidence-sync.validators';

const NOW = Date.now();

async function aesKey(): Promise<CryptoKey> {
  return crypto.subtle.importKey('raw', new Uint8Array(32).fill(7), 'AES-GCM', false, ['encrypt', 'decrypt']);
}

class Signer implements SpeechEvidenceMessageSignerPort {
  sequence = 0;
  async sign(type: SpeechEvidenceMessageType, payload: Record<string, unknown>, expiresAtMs: number): Promise<SpeechEvidenceMessage> {
    this.sequence += 1;
    return {
      protocol_version: SPEECH_EVIDENCE_PROTOCOL_VERSION, message_type: type, message_id: `message-${this.sequence}`,
      session_id: 'session-test', pair_id: 'pair-test', sender_id: 'peer-a', audience_id: 'peer-b', epoch: 7,
      sequence: this.sequence, consent_version: 3, key_id: 'key-test', issued_at_ms: NOW, expires_at_ms: expiresAtMs,
      payload_digest: await sha256Canonical(payload), payload, signature_algorithm: 'Ed25519',
      signature_b64: btoa(String.fromCharCode(...new Uint8Array(64))),
    };
  }
}

class Keys implements SpeechEvidenceAeadKeyPort {
  constructor(private readonly key: CryptoKey) {}
  async resolve(): Promise<CryptoKey> { return this.key; }
}

function queue(sent: string[]): SpeechEvidenceSendPort {
  return { send: async (_traffic, payload) => { sent.push(payload); return true; } };
}

function service(queuePort: SpeechEvidenceSendPort, signer: Signer, keys: Keys): SpeechEvidenceSyncService {
  TestBed.configureTestingModule({ providers: [
    SpeechEvidenceSyncService,
    { provide: SPEECH_EVIDENCE_SEND_QUEUE, useValue: queuePort },
    { provide: SPEECH_EVIDENCE_SIGNER, useValue: signer },
    { provide: SPEECH_EVIDENCE_AEAD_KEYS, useValue: keys },
  ] });
  return TestBed.inject(SpeechEvidenceSyncService);
}

async function ack(signer: Signer, firstMissing: number, indices: number[]): Promise<SpeechEvidenceMessage> {
  return signer.sign('chunk_ack', {
    traffic_class: 'control', offer_id: 'offer-test', group_id: 'group-test', acknowledged_indices: indices,
    first_missing_index: firstMissing, received_bytes: indices.length === 2 ? 70_000 : indices.length * 65_536,
    complete: firstMissing === 2,
  }, NOW + 60_000);
}

describe('SpeechEvidenceSyncService', () => {
  it('chunks at 64 KiB, keeps at most 1 MiB in flight and resumes at first missing ACK', async () => {
    const sent: string[] = [];
    const signer = new Signer();
    const sync = service(queue(sent), signer, new Keys(await aesKey()));
    const snapshot = await sync.prepareTransfer(
      { offerId: 'offer-test', groupId: 'group-test', epoch: 7, keyId: 'key-test', expiresAtMs: NOW + 60_000, dataClass: 'text_corrections' },
      new Uint8Array(70_000).fill(1),
    );
    expect(snapshot.chunkCount).toBe(2);
    expect(snapshot.inFlightBytes).toBeLessThanOrEqual(1024 * 1024);
    const messages = sent.map(row => JSON.parse(row) as SpeechEvidenceMessage);
    expect(messages[0].payload['plaintext_bytes']).toBe(65_536);
    const partial = await sync.acknowledge(await ack(signer, 1, [0]));
    expect(partial.firstMissingIndex).toBe(1);
    const duplicateAck = await sync.acknowledge(await ack(signer, 1, [0]));
    expect(duplicateAck).toMatchObject({ firstMissingIndex: 1, acknowledgedChunks: 1 });
    const sentBeforeResume = sent.length;
    await sync.resume('offer-test', 'group-test');
    expect(sync.snapshot('offer-test', 'group-test').firstMissingIndex).toBe(1);
    expect(sent.length).toBeGreaterThan(sentBeforeResume);
    expect((JSON.parse(sent.at(-1)!) as SpeechEvidenceMessage).message_id)
      .not.toBe((JSON.parse(sent[1]) as SpeechEvidenceMessage).message_id);
    const done = await sync.acknowledge(await ack(signer, 2, [0, 1]));
    expect(done.state).toBe('completed');
  });

  it('rejects cursor rollback, forbidden bulk classes and reused nonces', async () => {
    const signer = new Signer();
    const sync = service(queue([]), signer, new Keys(await aesKey()));
    await expect(sync.prepareTransfer(
      { offerId: 'offer-test', groupId: 'group-test', epoch: 7, keyId: 'key-test', expiresAtMs: NOW + 60_000, dataClass: 'raw_audio' },
      new Uint8Array([1]),
    )).rejects.toMatchObject({ reasonCode: 'speech_evidence_bulk_class_forbidden' });
    await expect(sync.prepareTransfer(
      { offerId: 'offer-audio', groupId: 'group-audio', epoch: 7, keyId: 'key-test', expiresAtMs: NOW + 60_000, dataClass: 'audio' },
      new Uint8Array([1]),
    )).rejects.toMatchObject({ reasonCode: 'speech_evidence_bulk_class_forbidden' });
    await sync.prepareTransfer(
      { offerId: 'offer-test', groupId: 'group-test', epoch: 7, keyId: 'key-test', expiresAtMs: NOW + 60_000, dataClass: 'text_corrections' },
      new Uint8Array(70_000).fill(1),
    );
    await sync.acknowledge(await ack(signer, 1, [0]));
    await expect(sync.acknowledge(await ack(signer, 0, []))).rejects.toMatchObject({
      reasonCode: 'speech_evidence_ack_cursor_rollback',
    });
  });

  it('pauses bulk sync without mutating completed evidence state', async () => {
    const sync = service(queue([]), new Signer(), new Keys(await aesKey()));
    await sync.prepareTransfer(
      { offerId: 'offer-test', groupId: 'group-test', epoch: 7, keyId: 'key-test', expiresAtMs: NOW + 60_000, dataClass: 'vocabulary' },
      new Uint8Array([1, 2, 3]),
    );
    sync.pause('offer-test');
    expect(sync.snapshot('offer-test', 'group-test').state).toBe('paused');
    sync.revoke('offer-test');
    expect(sync.snapshot('offer-test', 'group-test')).toMatchObject({ state: 'revoked', inFlightBytes: 0 });
    sync.clear();
    expect(() => sync.snapshot('offer-test', 'group-test')).toThrow('speech_evidence_transfer_not_found');
    expect(SpeechEvidenceValidationError).toBeTruthy();
  });
});
