import { describe, expect, it } from 'vitest';

import { sha256Fallback, sha256Hex } from './sha256';

function toHex(bytes: Uint8Array): string {
  return Array.from(bytes).map(byte => byte.toString(16).padStart(2, '0')).join('');
}

describe('sha256', () => {
  const expected = 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad';

  it('produces the standard SHA-256 digest', async () => {
    await expect(sha256Hex('abc')).resolves.toBe(expected);
  });

  it('provides the same digest without Web Crypto', () => {
    expect(toHex(sha256Fallback(new TextEncoder().encode('abc')))).toBe(expected);
  });
});
