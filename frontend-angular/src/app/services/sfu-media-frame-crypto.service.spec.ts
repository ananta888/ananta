import { describe, expect, it } from 'vitest';
import {
  SfuMediaCryptoError,
  SfuMediaFrameCryptoService,
  type SfuMediaFrameContext,
} from './sfu-media-frame-crypto.service';
import { decodeB64, encodeB64 } from './webrtc-secure-envelope';

const context: SfuMediaFrameContext = {
  roomId: 'sfu-0123456789abcdef0123456789abcdef', publicationId: 'camera-alice',
  senderId: 'alice', receiverScope: 'room-members', codec: 'vp8', keyEpoch: 7,
};
const key = Uint8Array.from({ length: 32 }, (_, index) => index + 1);

describe('SfuMediaFrameCryptoService', () => {
  it('round-trips while binding codec/publication/sender/receiver/epoch/counter', async () => {
    const sender = new SfuMediaFrameCryptoService();
    const receiver = new SfuMediaFrameCryptoService();
    await sender.activateKey(context.roomId, 7, key);
    await receiver.activateKey(context.roomId, 7, key);
    const encrypted = await sender.seal(context, new TextEncoder().encode('frame'));
    expect(new TextDecoder().decode(await receiver.open(context, encrypted))).toBe('frame');
    await expect(receiver.open(context, encrypted)).rejects.toMatchObject({ reasonCode: 'sfu_media_frame_replayed' });
  });

  it.each([
    ['publicationId', 'camera-eve'], ['senderId', 'eve'], ['receiverScope', 'private-eve'], ['codec', 'h264'],
  ] as const)('rejects a changed %s context', async (field, changed) => {
    const sender = new SfuMediaFrameCryptoService(); const receiver = new SfuMediaFrameCryptoService();
    await sender.activateKey(context.roomId, 7, key); await receiver.activateKey(context.roomId, 7, key);
    const encrypted = await sender.seal(context, new Uint8Array([1, 2, 3]));
    await expect(receiver.open({ ...context, [field]: changed }, encrypted))
      .rejects.toMatchObject({ reasonCode: 'sfu_media_frame_context_mismatch' });
  });

  it('rejects tampering and does not burn the counter before authentication succeeds', async () => {
    const sender = new SfuMediaFrameCryptoService(); const receiver = new SfuMediaFrameCryptoService();
    await sender.activateKey(context.roomId, 7, key); await receiver.activateKey(context.roomId, 7, key);
    const encrypted = await sender.seal(context, new Uint8Array([9, 8, 7]));
    const bytes = decodeB64(encrypted.ciphertext_b64); bytes[0] ^= 1;
    await expect(receiver.open(context, { ...encrypted, ciphertext_b64: encodeB64(bytes) }))
      .rejects.toMatchObject({ reasonCode: 'sfu_media_authentication_failed' });
    expect(await receiver.open(context, encrypted)).toEqual(new Uint8Array([9, 8, 7]));
  });

  it('blocks all frames during rotation and rejects old epochs after activation', async () => {
    const service = new SfuMediaFrameCryptoService();
    await service.activateKey(context.roomId, 7, key);
    service.beginRotation(context.roomId);
    await expect(service.seal(context, new Uint8Array([1])))
      .rejects.toMatchObject({ reasonCode: 'sfu_media_rekey_pending' });
    await service.activateKey(context.roomId, 8, Uint8Array.from(key, value => value ^ 0xff));
    await expect(service.seal(context, new Uint8Array([1])))
      .rejects.toMatchObject({ reasonCode: 'sfu_media_key_epoch_stale' });
    await expect(service.activateKey(context.roomId, 7, key))
      .rejects.toMatchObject({ reasonCode: 'sfu_media_key_epoch_stale' });
  });

  it('deletes active keys immediately on revocation', async () => {
    const service = new SfuMediaFrameCryptoService(); await service.activateKey(context.roomId, 7, key);
    service.revokeRoom(context.roomId);
    await expect(service.seal(context, new Uint8Array([1])))
      .rejects.toSatisfy((error: unknown) => error instanceof SfuMediaCryptoError);
  });
});
