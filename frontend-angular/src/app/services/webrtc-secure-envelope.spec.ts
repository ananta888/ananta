import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import {
  MAX_SECURE_CIPHERTEXT_BYTES,
  SecureEnvelopeError,
  decodeB64,
  encodeB64,
  parseSecureEnvelope,
  secureEnvelopeAad,
} from './webrtc-secure-envelope';

const fixture = JSON.parse(readFileSync(
  resolve(process.cwd(), '../tests/fixtures/webrtc/crypto_vectors/secure_envelope_vectors.v1.json'),
  'utf8',
));

describe('secure envelope cross-language vectors', () => {
  it('parses and decrypts the Python golden envelope with identical AAD', async () => {
    const envelope = parseSecureEnvelope(fixture.envelope, { nowMs: fixture.now_ms });
    const key = await crypto.subtle.importKey(
      'raw', arrayBuffer(decodeB64(fixture.key_b64)), { name: 'AES-GCM' }, false, ['decrypt'],
    );
    const plaintext = await crypto.subtle.decrypt(
      {
        name: 'AES-GCM', iv: arrayBuffer(decodeB64(envelope.nonce_b64)),
        additionalData: arrayBuffer(secureEnvelopeAad(envelope)), tagLength: 128,
      },
      key,
      arrayBuffer(decodeB64(envelope.ciphertext_b64)),
    );
    expect(encodeB64(plaintext)).toBe(fixture.plaintext_b64);
  });

  it.each([
    ['unknown_field', (raw: any) => { raw.unknown = true; }, 'unknown_field'],
    ['oversize', (raw: any) => { raw.ciphertext_b64 = encodeB64(new Uint8Array(MAX_SECURE_CIPHERTEXT_BYTES + 1)); }, 'ciphertext_oversize'],
    ['non_finite', (raw: any) => { raw.sequence = Number.NaN; }, 'sequence_invalid'],
    ['expired', (raw: any) => raw, 'expired'],
  ])('%s returns the shared public code', (_name, mutation, code) => {
    const raw = structuredClone(fixture.envelope);
    mutation(raw);
    const nowMs = code === 'expired' ? raw.expires_at_ms + 30_001 : fixture.now_ms;
    try {
      parseSecureEnvelope(raw, { nowMs });
      throw new Error('vector unexpectedly accepted');
    } catch (error) {
      expect((error as SecureEnvelopeError).reasonCode).toBe(code);
    }
  });
});

function arrayBuffer(value: Uint8Array): ArrayBuffer {
  const copy = new Uint8Array(value.byteLength); copy.set(value); return copy.buffer;
}
