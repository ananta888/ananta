import {
  PairMediaE2eeFrameCipher,
  PairMediaE2eeFrameContext,
} from './pair-media-e2ee-frame-codec';

const context = (overrides: Partial<PairMediaE2eeFrameContext> = {}): PairMediaE2eeFrameContext => ({
  sessionId: 'session-a',
  mediaContractDigest: 'a'.repeat(64),
  connectionId: 'b'.repeat(64),
  senderId: 'peer:sender',
  recipientId: 'peer:recipient',
  slot: 'camera-vp8',
  codec: 'vp8',
  kind: 'video',
  keyEpoch: 7,
  contractExpiresAtMs: 2_000_000_000_000,
  ...overrides,
});

async function key(): Promise<CryptoKey> {
  return crypto.subtle.generateKey(
    { name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt'],
  );
}

function counter(frame: ArrayBuffer): bigint {
  return new DataView(frame).getBigUint64(12);
}

describe('PairMediaE2eeFrameCipher', () => {
  it('round-trips while binding exact direction, slot, contract and connection', async () => {
    const aes = await key();
    const sender = new PairMediaE2eeFrameCipher(context());
    const receiver = new PairMediaE2eeFrameCipher(context());
    const encoded = await sender.seal(aes, new TextEncoder().encode('secret').buffer, 'key');
    await expect(receiver.open(aes, encoded, 'key')).resolves.toEqual(
      new TextEncoder().encode('secret').buffer,
    );
    await expect(new PairMediaE2eeFrameCipher(context({ slot: 'screen-vp8' })).open(aes, encoded, 'key'))
      .rejects.toThrow('media_e2ee_authentication_failed');
    await expect(new PairMediaE2eeFrameCipher(context({ connectionId: 'c'.repeat(64) })).open(aes, encoded, 'key'))
      .rejects.toThrow('media_e2ee_authentication_failed');
  });

  it('reserves distinct counters before concurrent encryption awaits', async () => {
    const aes = await key();
    const sender = new PairMediaE2eeFrameCipher(context());
    const frames = await Promise.all([
      sender.seal(aes, Uint8Array.of(1).buffer, 'delta'),
      sender.seal(aes, Uint8Array.of(2).buffer, 'delta'),
      sender.seal(aes, Uint8Array.of(3).buffer, 'delta'),
    ]);
    expect(new Set(frames.map(counter))).toEqual(new Set([1n, 2n, 3n]));
  });

  it('authenticates an empty encoded frame without treating DTX as fatal', async () => {
    const aes = await key();
    const encoded = await new PairMediaE2eeFrameCipher(context())
      .seal(aes, new ArrayBuffer(0), 'empty');
    await expect(new PairMediaE2eeFrameCipher(context()).open(aes, encoded, 'empty'))
      .resolves.toEqual(new ArrayBuffer(0));
  });

  it('rejects every frame at the signed contract expiry boundary', async () => {
    const aes = await key();
    let now = 99;
    const expiringContext = context({ contractExpiresAtMs: 100 });
    const sender = new PairMediaE2eeFrameCipher(expiringContext, () => now);
    const receiver = new PairMediaE2eeFrameCipher(expiringContext, () => now);
    const encoded = await sender.seal(aes, Uint8Array.of(1).buffer, 'delta');
    now = 100;

    await expect(sender.seal(aes, Uint8Array.of(2).buffer, 'delta'))
      .rejects.toThrow('media_e2ee_contract_expired');
    await expect(receiver.open(aes, encoded, 'delta'))
      .rejects.toThrow('media_e2ee_contract_expired');
  });

  it('does not release a frame when WebCrypto crosses the expiry boundary', async () => {
    const aes = await key();
    let now = 99;
    const gate = deferred<void>();
    const originalEncrypt = crypto.subtle.encrypt.bind(crypto.subtle);
    const encryptSpy = vi.spyOn(crypto.subtle, 'encrypt').mockImplementationOnce(async (...args) => {
      await gate.promise;
      return originalEncrypt(...args);
    });
    try {
      const sealing = new PairMediaE2eeFrameCipher(
        context({ contractExpiresAtMs: 100 }), () => now,
      ).seal(aes, Uint8Array.of(1).buffer, 'delta');
      const rejected = expect(sealing).rejects.toThrow('media_e2ee_contract_expired');
      await Promise.resolve();
      now = 100;
      gate.resolve(undefined);
      await rejected;
    } finally {
      encryptSpy.mockRestore();
    }

    now = 99;
    const expiringContext = context({ contractExpiresAtMs: 100 });
    const encoded = await new PairMediaE2eeFrameCipher(expiringContext, () => now)
      .seal(aes, Uint8Array.of(2).buffer, 'delta');
    const decryptGate = deferred<void>();
    const originalDecrypt = crypto.subtle.decrypt.bind(crypto.subtle);
    const decryptSpy = vi.spyOn(crypto.subtle, 'decrypt').mockImplementationOnce(async (...args) => {
      await decryptGate.promise;
      return originalDecrypt(...args);
    });
    try {
      const opening = new PairMediaE2eeFrameCipher(expiringContext, () => now)
        .open(aes, encoded, 'delta');
      const rejected = expect(opening).rejects.toThrow('media_e2ee_contract_expired');
      await Promise.resolve();
      now = 100;
      decryptGate.resolve(undefined);
      await rejected;
    } finally {
      decryptSpy.mockRestore();
    }
  });

  it('admits a ciphertext only once when concurrent opens race', async () => {
    const aes = await key();
    const encoded = await new PairMediaE2eeFrameCipher(context())
      .seal(aes, Uint8Array.of(1, 2, 3).buffer, 'delta');
    const receiver = new PairMediaE2eeFrameCipher(context());
    const results = await Promise.allSettled([
      receiver.open(aes, encoded, 'delta'),
      receiver.open(aes, encoded, 'delta'),
    ]);
    expect(results.filter(result => result.status === 'fulfilled')).toHaveLength(1);
    expect(results.filter(result => result.status === 'rejected')).toHaveLength(1);
    expect(String((results.find(result => result.status === 'rejected') as PromiseRejectedResult).reason))
      .toContain('media_e2ee_replay');
  });

  it('permanently rejects frames older than the strict sliding window', async () => {
    const aes = await key();
    const sender = new PairMediaE2eeFrameCipher(context());
    const receiver = new PairMediaE2eeFrameCipher(context());
    const first = await sender.seal(aes, Uint8Array.of(1).buffer, 'delta');
    await receiver.open(aes, first, 'delta');
    for (let index = 0; index < 2_049; index += 1) {
      const encoded = await sender.seal(aes, Uint8Array.of(index & 0xff).buffer, 'delta');
      await receiver.open(aes, encoded, 'delta');
    }
    await expect(receiver.open(aes, first, 'delta')).rejects.toThrow('media_e2ee_replay_too_old');
  });
});

function deferred<T>(): { promise: Promise<T>; resolve(value: T): void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(accept => { resolve = accept; });
  return { promise, resolve };
}
