import { MediaE2eeTransformService, type MediaE2eeFrameContext } from './media-e2ee-transform.service';

const context = (overrides: Partial<MediaE2eeFrameContext> = {}): MediaE2eeFrameContext => ({
  publicationId: 'camera-alice', senderId: 'alice', recipientScopeId: 'room-a',
  codec: 'vp8', kind: 'video', keyEpoch: 7, ...overrides,
});

async function key(): Promise<CryptoKey> {
  return crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
}

describe('MediaE2eeTransformService', () => {
  it('round-trips a frame while binding publication, codec and epoch', async () => {
    const sender = new MediaE2eeTransformService(); const receiver = new MediaE2eeTransformService();
    const aes = await key(); const input = new TextEncoder().encode('known-plaintext-probe').buffer;
    const encrypted = await sender.seal(context(), aes, input, 'key');
    expect(new TextDecoder().decode(encrypted)).not.toContain('known-plaintext-probe');
    const opened = await receiver.open(context(), aes, encrypted, 'key');
    expect(new TextDecoder().decode(opened)).toBe('known-plaintext-probe');
  });

  it.each([
    [{ publicationId: 'camera-mallory' }, 'media_e2ee_authentication_failed'],
    [{ keyEpoch: 8 }, 'media_e2ee_epoch_stale'],
    [{ codec: 'h264' }, 'media_e2ee_authentication_failed'],
  ] as const)('rejects changed binding %o', async (changes, reason) => {
    const sender = new MediaE2eeTransformService(); const receiver = new MediaE2eeTransformService();
    const aes = await key(); const encrypted = await sender.seal(context(), aes, Uint8Array.of(1, 2, 3).buffer, 'delta');
    await expect(receiver.open(context(changes), aes, encrypted, 'delta')).rejects.toThrow(reason);
  });

  it('rejects replay and tampering without admitting a frame', async () => {
    const sender = new MediaE2eeTransformService(); const receiver = new MediaE2eeTransformService();
    const aes = await key(); const encrypted = await sender.seal(context(), aes, Uint8Array.of(1).buffer, 'delta');
    await receiver.open(context(), aes, encrypted, 'delta');
    await expect(receiver.open(context(), aes, encrypted, 'delta')).rejects.toThrow('media_e2ee_replay');
    const changed = encrypted.slice(0); new Uint8Array(changed)[changed.byteLength - 1] ^= 1;
    await expect(new MediaE2eeTransformService().open(context(), aes, changed, 'delta'))
      .rejects.toThrow('media_e2ee_authentication_failed');
  });

  it('admits a ciphertext only once when concurrent opens race', async () => {
    const sender = new MediaE2eeTransformService(); const receiver = new MediaE2eeTransformService();
    const aes = await key();
    const encrypted = await sender.seal(context(), aes, Uint8Array.of(1, 2, 3).buffer, 'delta');
    const results = await Promise.allSettled([
      receiver.open(context(), aes, encrypted, 'delta'),
      receiver.open(context(), aes, encrypted, 'delta'),
    ]);
    expect(results.filter(result => result.status === 'fulfilled')).toHaveLength(1);
    expect(results.filter(result => result.status === 'rejected')).toHaveLength(1);
    expect(String((results.find(result => result.status === 'rejected') as PromiseRejectedResult).reason))
      .toContain('media_e2ee_replay');
  });

  it('permanently rejects an unseen frame older than the strict sliding window', async () => {
    const sender = new MediaE2eeTransformService(); const receiver = new MediaE2eeTransformService();
    const aes = await key();
    const oldUnseen = await sender.seal(context(), aes, Uint8Array.of(1).buffer, 'delta');
    for (let index = 0; index < 2_049; index += 1) {
      const encrypted = await sender.seal(
        context(), aes, Uint8Array.of(index & 0xff).buffer, 'delta',
      );
      await receiver.open(context(), aes, encrypted, 'delta');
    }
    await expect(receiver.open(context(), aes, oldUnseen, 'delta'))
      .rejects.toThrow('media_e2ee_replay_too_old');
  });

  it('requires a non-extractable AES-GCM key', async () => {
    const extractable = await crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, true, ['encrypt', 'decrypt']);
    await expect(new MediaE2eeTransformService().seal(
      context(), extractable, Uint8Array.of(1).buffer, 'delta',
    )).rejects.toThrow('media_e2ee_key_invalid');
  });
});
