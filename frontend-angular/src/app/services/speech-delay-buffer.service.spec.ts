import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SpeechDelayBufferService } from './speech-delay-buffer.service';

const NOW = 1_000;

async function context(segmentId: string, plaintext: Uint8Array, expiresAtMs = NOW + 60_000) {
  const digest = await crypto.subtle.digest('SHA-256', plaintext);
  const sourceDigest = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
  return { sessionId: 'session-a', epoch: 2, segmentId, sourceDigest, expiresAtMs };
}

describe('SpeechDelayBufferService', () => {
  let service: SpeechDelayBufferService;

  beforeEach(() => {
    TestBed.resetTestingModule();
    service = TestBed.inject(SpeechDelayBufferService);
  });

  it('stores only AES-GCM ciphertext and deletes immediately after correction acknowledgement', async () => {
    const plaintext = new Uint8Array([1, 2, 3]);
    await service.put(await context('segment-1', plaintext), plaintext, NOW);
    expect(service.snapshot()).toMatchObject({ segments: 1, plaintextBytes: 0, timers: 0, keyReady: true });
    expect(await service.use('segment-1', async value => Array.from(value), NOW))
      .toEqual({ available: true, value: [1, 2, 3] });
    service.correctionComplete('segment-1');
    expect(service.snapshot().segments).toBe(0);
  });

  it('evicts deterministically at five segments and purges TTL, revoke and key loss', async () => {
    for (let index = 0; index < 6; index += 1) {
      const plaintext = new Uint8Array([index]);
      await service.put(await context(`segment-${index}`, plaintext, NOW + 10_000 + index), plaintext, NOW);
    }
    expect(service.snapshot().segments).toBe(5);
    expect(await service.use('segment-0', async value => Array.from(value), NOW))
      .toEqual({ available: false, value: null });
    expect(service.purgeExpired(NOW + 20_000)).toBe(5);
    const keyloss = new Uint8Array([1]);
    await service.put(await context('segment-keyloss', keyloss), keyloss, NOW);
    service.discardKey();
    expect(service.snapshot()).toMatchObject({ segments: 0, encryptedBytes: 0, keyReady: false });
  });

  it('contains 404, 409 and 413 adjacent failures without plaintext or timers', async () => {
    const plaintext = new Uint8Array([1]);
    await service.put(await context('segment-413', plaintext), plaintext, NOW);
    service.containTransportFailure(413, 'segment-413');
    await service.put(await context('segment-404', plaintext), plaintext, NOW);
    service.containTransportFailure(404);
    await service.put(await context('segment-409', plaintext), plaintext, NOW);
    service.containTransportFailure(409);
    expect(service.snapshot()).toEqual({ segments: 0, encryptedBytes: 0, plaintextBytes: 0, timers: 0, keyReady: false });
  });

  it('uses one non-extractable key for concurrent puts and cannot resurrect data after revoke', async () => {
    const first = new Uint8Array([1]);
    const second = new Uint8Array([2]);
    await Promise.all([
      service.put(await context('segment-a', first), first, NOW),
      service.put(await context('segment-b', second), second, NOW),
    ]);
    expect(await service.use('segment-a', async value => Array.from(value), NOW))
      .toEqual({ available: true, value: [1] });
    expect(await service.use('segment-b', async value => Array.from(value), NOW))
      .toEqual({ available: true, value: [2] });

    const originalGenerateKey = crypto.subtle.generateKey.bind(crypto.subtle);
    const generatedKey = originalGenerateKey(
      { name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt'],
    ) as Promise<CryptoKey>;
    let releaseGeneration: (() => void) | null = null;
    const delayed = new Promise<void>(resolve => { releaseGeneration = resolve; });
    const spy = vi.spyOn(crypto.subtle, 'generateKey').mockImplementation(async () => {
      await delayed;
      return generatedKey;
    });
    service.revoke();
    const late = new Uint8Array([3]);
    const pending = service.put(await context('segment-late', late), late, NOW);
    service.revoke();
    releaseGeneration?.();
    await expect(pending).rejects.toThrow('speech_delay_key_generation_invalidated');
    expect(service.snapshot()).toEqual({ segments: 0, encryptedBytes: 0, plaintextBytes: 0, timers: 0, keyReady: false });
    spy.mockRestore();
  });

  it('does not discard a generated key while a concurrent put is still pending', async () => {
    const first = new Uint8Array([1]);
    const second = new Uint8Array([2]);
    const originalEncrypt = crypto.subtle.encrypt.bind(crypto.subtle);
    let releaseFirstEncryption: (() => void) | null = null;
    const firstEncryptionBlocked = new Promise<void>(resolve => { releaseFirstEncryption = resolve; });
    let encryptCalls = 0;
    const spy = vi.spyOn(crypto.subtle, 'encrypt').mockImplementation(async (...args) => {
      encryptCalls += 1;
      if (encryptCalls === 1) await firstEncryptionBlocked;
      return originalEncrypt(...args);
    });

    const firstPut = service.put(await context('segment-a', first), first, NOW);
    await vi.waitFor(() => expect(encryptCalls).toBe(1));
    const secondPut = service.put(await context('segment-b', second), second, NOW);
    releaseFirstEncryption?.();

    await expect(Promise.all([firstPut, secondPut])).resolves.toEqual([undefined, undefined]);
    expect(service.snapshot()).toMatchObject({ segments: 2, keyReady: true });
    spy.mockRestore();
  });

  it('rejects a digest mismatch and always zeroes the scoped decrypted copy', async () => {
    const plaintext = new Uint8Array([4, 5, 6]);
    await expect(service.put({
      ...(await context('segment-mismatch', plaintext)),
      sourceDigest: 'f'.repeat(64),
    }, plaintext, NOW)).rejects.toThrow('speech_delay_source_digest_mismatch');
    expect(service.snapshot().segments).toBe(0);

    await service.put(await context('segment-use', plaintext), plaintext, NOW);
    let scoped: Uint8Array | null = null;
    const result = await service.use('segment-use', async value => {
      scoped = value;
      return value.byteLength;
    }, NOW);
    expect(result).toEqual({ available: true, value: 3 });
    expect(Array.from(scoped!)).toEqual([0, 0, 0]);
  });
});
