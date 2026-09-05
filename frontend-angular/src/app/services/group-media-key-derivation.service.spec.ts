import { GroupMediaKeyContext, GroupMediaKeyDerivationService } from './group-media-key-derivation.service';

describe('GroupMediaKeyDerivationService', () => {
  const base: GroupMediaKeyContext = {
    tenantId: 'tenant-1', roomId: 'room-1', publicationId: 'publication-1',
    senderId: 'sender-1', mediaDomain: 'camera', keyEpoch: 7,
  };
  const root = Uint8Array.from({ length: 32 }, (_, index) => index);
  const iv = new Uint8Array(12);
  const plaintext = new TextEncoder().encode('known-answer');

  it('derives a stable non-extractable known-answer key', async () => {
    const service = new GroupMediaKeyDerivationService();
    const first = await service.derive(root, base);
    const second = await service.derive(root, base);
    expect(first.extractable).toBe(false);
    const firstCiphertext = await encrypt(first);
    const secondCiphertext = await encrypt(second);
    expect(hex(firstCiphertext)).toBe('f2d09dc89c3b02e8c6611d0ef83dc175020fb27c3b145d692f50943a');
    expect(hex(firstCiphertext)).toBe(hex(secondCiphertext));
  });

  it.each([
    { senderId: 'sender-2' },
    { publicationId: 'publication-2' },
    { mediaDomain: 'screenshare' as const },
    { keyEpoch: 8 },
  ])('separates the derivation context %o', async changes => {
    const service = new GroupMediaKeyDerivationService();
    const expected = await service.derive(root, base);
    const crossed = await service.derive(root, { ...base, ...changes });
    await expect(crypto.subtle.decrypt(
      { name: 'AES-GCM', iv }, crossed, await encrypt(expected),
    )).rejects.toThrow();
  });

  async function encrypt(key: CryptoKey): Promise<ArrayBuffer> {
    return crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, plaintext);
  }
});

function hex(value: ArrayBuffer): string {
  return [...new Uint8Array(value)].map(byte => byte.toString(16).padStart(2, '0')).join('');
}
