import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';

import { E2eEncryptionService, PeerCipherContext } from './e2e-encryption.service';
import {
  IndexedDbE2eReplayStore,
  PAIR_REPLAY_WINDOW_STORE,
} from './e2e-replay.store';
import { SECURE_KEY_STORE, SecureKeyStorePort, StoredDeviceKeyPair } from './secure-key-store.service';
import { WebrtcGroupKeyService } from './webrtc-group-key.service';
import { WebrtcReplayWindowService } from './webrtc-replay-window.service';
import {
  MAX_SECURE_CIPHERTEXT_BYTES,
  SecureEnvelopeV1,
  decodeB64,
  encodeB64,
  parseSecureEnvelope,
  secureEnvelopeAad,
} from './webrtc-secure-envelope';

const fixture = JSON.parse(readFileSync(
  resolve(process.cwd(), '../tests/fixtures/webrtc/crypto_vectors/secure_envelope_vectors.v1.json'),
  'utf8',
));

describe('shared M1 crypto conformance matrix', () => {
  for (const vector of fixture.vectors as Array<{ name: string; mutation: string; expected_code: string }>) {
    it(`${vector.name} => ${vector.expected_code}`, async () => {
      expect(await evaluate(vector.mutation)).toBe(vector.expected_code);
    });
  }
});

async function evaluate(mutation: string): Promise<string> {
  const raw = structuredClone(fixture.envelope);
  let nowMs = fixture.now_ms;
  if (mutation === 'tamper_ciphertext') {
    const ciphertext = decodeB64(raw.ciphertext_b64); ciphertext[0] ^= 1;
    raw.ciphertext_b64 = encodeB64(ciphertext);
  } else if (mutation === 'swap_payload_type') raw.payload_type = 'pair.control';
  else if (mutation === 'unknown_field') raw.plaintext = 'must-not-be-accepted';
  else if (mutation === 'oversize_ciphertext') {
    raw.ciphertext_b64 = encodeB64(new Uint8Array(MAX_SECURE_CIPHERTEXT_BYTES + 1));
  } else if (mutation === 'expired') nowMs = raw.expires_at_ms + 30_001;
  try {
    const envelope = parseSecureEnvelope(raw, { nowMs });
    if (mutation === 'tamper_ciphertext' || mutation === 'swap_payload_type') {
      return await decryptCode(envelope);
    }
    if (mutation === 'wrong_peer' || mutation === 'wrong_epoch') {
      const replay = replayService();
      return await replay.accept(envelope, {
        scopeId: envelope.scope.id,
        epoch: mutation === 'wrong_epoch' ? envelope.epoch + 1 : envelope.epoch,
        authenticatedSenderId: envelope.sender_id,
        localPeerId: mutation === 'wrong_peer' ? 'mallory' : envelope.recipient.id,
      }, nowMs);
    }
    if (mutation === 'replay') {
      const replay = replayService();
      const context = { scopeId: envelope.scope.id, epoch: envelope.epoch,
        authenticatedSenderId: envelope.sender_id, localPeerId: envelope.recipient.id };
      await replay.accept(envelope, context, nowMs);
      return await replay.accept(envelope, context, nowMs);
    }
    if (mutation === 'nonce_reuse') return nonceReuseCode();
    if (mutation === 'join' || mutation === 'revoke') {
      try { new WebrtcGroupKeyService().getKey('room', 'publication', 1, nowMs); }
      catch (error) { return (error as { reasonCode?: string }).reasonCode ?? 'unexpected_error'; }
    }
    return 'ok';
  } catch (error) {
    return (error as { reasonCode?: string }).reasonCode ?? 'unexpected_error';
  }
}

function replayService(): WebrtcReplayWindowService {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({ providers: [
    WebrtcReplayWindowService,
    { provide: PAIR_REPLAY_WINDOW_STORE, useValue: new IndexedDbE2eReplayStore() },
  ] });
  return TestBed.inject(WebrtcReplayWindowService);
}

class ConformanceKeyStore implements SecureKeyStorePort {
  current: StoredDeviceKeyPair | null = null;
  loadCurrent = async () => this.current;
  replaceCurrent = async (record: StoredDeviceKeyPair) => { this.current = record; };
  clear = async () => { this.current = null; };
  discardLegacyLocalStorageKey = () => false;
}

function encryptionService(store: ConformanceKeyStore): E2eEncryptionService {
  TestBed.resetTestingModule();
  TestBed.configureTestingModule({ providers: [
    E2eEncryptionService,
    { provide: SECURE_KEY_STORE, useValue: store },
  ] });
  return TestBed.inject(E2eEncryptionService);
}

async function nonceReuseCode(): Promise<string> {
  const alice = encryptionService(new ConformanceKeyStore());
  const alicePublic = await alice.ensureLocalKeyPair();
  const bob = encryptionService(new ConformanceKeyStore());
  const bobPublic = await bob.ensureLocalKeyPair();
  const common = {
    scopeKind: 'session' as const,
    scopeId: 'nonce-conformance',
    epoch: 1,
    keyId: 'nonce-key',
    contractDigest: '0'.repeat(64),
  };
  const aliceContext: PeerCipherContext = {
    ...common,
    localPeerId: 'alice', remotePeerId: 'bob', peerPublicKeySpkiB64: bobPublic.publicKeySpkiB64,
  };
  const bobContext: PeerCipherContext = {
    ...common,
    localPeerId: 'bob', remotePeerId: 'alice', peerPublicKeySpkiB64: alicePublic.publicKeySpkiB64,
  };
  const sealed = await alice.seal(
    aliceContext,
    new TextEncoder().encode('nonce-conformance'),
    { sequence: 1, payloadType: 'pair.view_delta', trafficClass: 'semantic' },
  );
  await bob.open(bobContext, sealed);
  try {
    await bob.open(bobContext, { ...sealed, sequence: 2 });
  } catch (error) {
    return (error as { reasonCode?: string }).reasonCode ?? 'unexpected_error';
  }
  return 'ok';
}

async function decryptCode(envelope: SecureEnvelopeV1): Promise<string> {
  const key = await crypto.subtle.importKey(
    'raw', arrayBuffer(decodeB64(fixture.key_b64)), { name: 'AES-GCM' }, false, ['decrypt'],
  );
  try {
    await crypto.subtle.decrypt(
      {
        name: 'AES-GCM', iv: arrayBuffer(decodeB64(envelope.nonce_b64)),
        additionalData: arrayBuffer(secureEnvelopeAad(envelope)), tagLength: 128,
      },
      key,
      arrayBuffer(decodeB64(envelope.ciphertext_b64)),
    );
    return 'ok';
  } catch {
    return 'authentication_failed';
  }
}

function arrayBuffer(value: Uint8Array): ArrayBuffer {
  const copy = new Uint8Array(value.byteLength); copy.set(value); return copy.buffer;
}
