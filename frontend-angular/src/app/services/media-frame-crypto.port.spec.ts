import { TestBed } from '@angular/core/testing';

import { MediaE2eeFrameContext, MediaE2eeTransformService } from './media-e2ee-transform.service';
import { AnmeMediaFrameCryptoAdapter, ScopedMediaFrameKeyLease } from './media-frame-crypto.port';

describe('MediaFrameCryptoPort', () => {
  const context: MediaE2eeFrameContext = {
    publicationId: 'publication-1', senderId: 'sender-1', recipientScopeId: 'room-1',
    codec: 'vp8', kind: 'video', keyEpoch: 2,
  };

  beforeEach(() => TestBed.configureTestingModule({ providers: [
    MediaE2eeTransformService,
    AnmeMediaFrameCryptoAdapter,
  ] }));

  afterEach(() => TestBed.resetTestingModule());

  it('uses a non-extractable scoped lease and fails closed after release', async () => {
    const key = await crypto.subtle.generateKey(
      { name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt'],
    );
    const lease = new ScopedMediaFrameKeyLease({
      tenantId: 'tenant-1', roomId: 'room-1', publicationId: 'publication-1',
      senderId: 'sender-1', mediaKind: 'video', keyEpoch: 2,
    }, Date.now() + 10_000, key);
    const adapter = TestBed.inject(AnmeMediaFrameCryptoAdapter);
    expect(adapter.encryptStream(context, lease)).toBeInstanceOf(TransformStream);
    lease.release();
    expect(() => adapter.decryptStream(context, lease)).toThrow('media_frame_key_lease_released');
  });

  it('rejects cross-publication and cross-epoch use before touching the codec', async () => {
    const key = await crypto.subtle.generateKey(
      { name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt'],
    );
    const lease = new ScopedMediaFrameKeyLease({
      tenantId: 'tenant-1', roomId: 'room-1', publicationId: 'other-publication',
      senderId: 'sender-1', mediaKind: 'video', keyEpoch: 3,
    }, Date.now() + 10_000, key);
    expect(() => TestBed.inject(AnmeMediaFrameCryptoAdapter).encryptStream(context, lease))
      .toThrow('media_frame_key_lease_scope_mismatch');
  });
});
