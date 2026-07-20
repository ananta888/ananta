import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it } from 'vitest';

import { E2eEncryptionService } from './e2e-encryption.service';
import { SemanticSpeechCryptoService } from './semantic-speech-crypto.service';
import { WebrtcPeerKeyService } from './webrtc-peer-key.service';
import { SecureEnvelopeV1, decodeB64, encodeB64 } from './webrtc-secure-envelope';

const binding = {
  scopeKind: 'session' as const,
  scopeId: 'session-a',
  localPeerId: 'alice',
  remotePeerId: 'bob',
  peerPublicKeySpkiB64: 'unused',
  epoch: 2,
  keyId: 'key-a',
  contractDigest: 'a'.repeat(64),
};

describe('SemanticSpeechCryptoService', () => {
  let service: SemanticSpeechCryptoService;
  const plaintextBySequence = new Map<number, Uint8Array>();
  const encryption = {
    seal: async (_binding: unknown, plaintext: Uint8Array, options: Record<string, unknown>): Promise<SecureEnvelopeV1> => {
      const sequence = Number(options['sequence']);
      plaintextBySequence.set(sequence, Uint8Array.from(plaintext));
      return {
        version: 1, scope: { kind: 'session', id: 'session-a' }, sender_id: 'alice',
        recipient: { kind: 'peer', id: 'bob' }, epoch: 2, sequence, key_id: 'key-a',
        payload_type: 'semantic_speech', expires_at_ms: Number(options['expiresAtMs']),
        nonce_b64: encodeB64(new Uint8Array(12).fill(sequence)),
        aad: { traffic_class: 'semantic', content_encoding: 'json', contract_digest: 'a'.repeat(64) },
        ciphertext_b64: encodeB64(new Uint8Array(16).fill(sequence)),
      };
    },
    open: async (_binding: unknown, envelope: SecureEnvelopeV1) => ({
      envelope: { ...envelope, sender_id: 'bob', recipient: { kind: 'peer' as const, id: 'alice' } },
      plaintext: plaintextBySequence.get(envelope.sequence)!,
    }),
  };
  const peerKeys = { requireBinding: () => binding };

  beforeEach(() => {
    plaintextBySequence.clear();
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ providers: [
      SemanticSpeechCryptoService,
      { provide: E2eEncryptionService, useValue: encryption },
      { provide: WebrtcPeerKeyService, useValue: peerKeys },
    ] });
    service = TestBed.inject(SemanticSpeechCryptoService);
  });

  it('seals speech plaintext inside a validated opaque secure envelope', async () => {
    const message = await service.seal(new TextEncoder().encode('geheimer Text'), 'transcript');
    expect(message.sender_id).toBe('alice');
    expect(message.audience_id).toBe('bob');
    expect(message.security).toEqual({ algorithm: 'AES-GCM-256', key_id: 'key-a' });
    const outer = new TextDecoder().decode(decodeB64(message.ciphertext));
    expect(outer).not.toContain('geheimer Text');
    expect(outer).toContain('ciphertext_b64');
  });

  it('opens only the confirmed reverse-direction binding and rejects outer tamper', async () => {
    const sent = await service.seal(new TextEncoder().encode('Hallo'), 'transcript');
    const inbound = { ...sent, sender_id: 'bob', audience_id: 'alice' };
    expect(new TextDecoder().decode(await service.open(inbound))).toBe('Hallo');
    await expect(service.open({ ...inbound, payload_digest: 'f'.repeat(64) })).rejects.toThrow();
  });
});
