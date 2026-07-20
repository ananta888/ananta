import { TestBed } from '@angular/core/testing';

import { ReceiverSpeechAdapterCacheService } from './receiver-speech-adapter-cache.service';

async function digest(value: Uint8Array): Promise<string> {
  const raw = await crypto.subtle.digest('SHA-256', value);
  return Array.from(new Uint8Array(raw), byte => byte.toString(16).padStart(2, '0')).join('');
}

describe('ReceiverSpeechAdapterCacheService', () => {
  let cache: ReceiverSpeechAdapterCacheService;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [ReceiverSpeechAdapterCacheService] });
    cache = TestBed.inject(ReceiverSpeechAdapterCacheService);
  });

  afterEach(() => cache.clear());

  it('retains only authenticated ciphertext behind a non-plaintext snapshot', async () => {
    const bytes = new Uint8Array([1, 2, 3, 4]);
    const sha256 = await digest(bytes);
    await cache.put(sha256, bytes, Date.now() + 10_000);

    expect(cache.snapshot()).toMatchObject({ entries: 1, plaintextBytes: 0 });
    expect(cache.snapshot().ciphertextBytes).toBeGreaterThan(bytes.byteLength);
    expect(Array.from(await cache.read(sha256) ?? [])).toEqual([1, 2, 3, 4]);
  });

  it('bounds the ring to two adapters and deletes expired entries', async () => {
    const values = [new Uint8Array([1]), new Uint8Array([2]), new Uint8Array([3])];
    const digests = await Promise.all(values.map(value => digest(value)));
    const expiry = Date.now() + 10_000;
    for (let index = 0; index < values.length; index += 1) {
      await cache.put(digests[index], values[index], expiry);
    }

    expect(cache.snapshot().entries).toBe(2);
    expect(await cache.read(digests[0])).toBeNull();
    expect(await cache.read(digests[1], expiry)).toBeNull();
    expect(cache.snapshot().entries).toBe(1);
  });

  it('rejects bytes whose digest is not the approved artifact digest', async () => {
    await expect(cache.put('a'.repeat(64), new Uint8Array([9]), Date.now() + 10_000))
      .rejects.toThrow('speech_adapter_cache_digest_mismatch');
    expect(cache.snapshot()).toEqual({ entries: 0, ciphertextBytes: 0, plaintextBytes: 0 });
  });
});
