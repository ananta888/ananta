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

function vp8KeyFrame(...suffix: number[]): ArrayBuffer {
  return Uint8Array.of(
    0x10, 0x00, 0x00, 0x9d, 0x01, 0x2a, 0x80, 0x02, 0xe0, 0x01,
    ...suffix,
  ).buffer;
}

function vp8DeltaFrame(...suffix: number[]): ArrayBuffer {
  return Uint8Array.of(0x11, 0x00, 0x00, ...suffix).buffer;
}

function counter(frame: ArrayBuffer, prefixBytes = 3): bigint {
  return new DataView(frame).getBigUint64(prefixBytes + 12);
}

describe('PairMediaE2eeFrameCipher', () => {
  it('round-trips while binding exact direction, slot, contract and connection', async () => {
    const aes = await key();
    const sender = new PairMediaE2eeFrameCipher(context());
    const receiver = new PairMediaE2eeFrameCipher(context());
    const plaintext = vp8KeyFrame(0x73, 0x65, 0x63, 0x72, 0x65, 0x74);
    const encoded = await sender.seal(aes, plaintext);
    await expect(receiver.open(aes, encoded)).resolves.toEqual(plaintext);
    await expect(new PairMediaE2eeFrameCipher(context({ slot: 'screen-vp8' })).open(aes, encoded))
      .rejects.toThrow('media_e2ee_authentication_failed');
    await expect(new PairMediaE2eeFrameCipher(context({ connectionId: 'c'.repeat(64) })).open(aes, encoded))
      .rejects.toThrow('media_e2ee_authentication_failed');
  });

  it('preserves and authenticates the complete VP8 uncompressed prefix', async () => {
    const aes = await key();
    const sender = new PairMediaE2eeFrameCipher(context());
    const keySuffix = Array.from({ length: 32 }, (_, index) => 0x80 + index);
    const keyFrame = vp8KeyFrame(...keySuffix);
    const deltaFrame = vp8DeltaFrame(0x44, 0x55, 0x66, 0x77);

    const encodedKey = await sender.seal(aes, keyFrame);
    const encodedDelta = await sender.seal(aes, deltaFrame);
    expect(new Uint8Array(encodedKey).slice(0, 10)).toEqual(new Uint8Array(keyFrame).slice(0, 10));
    expect(new Uint8Array(encodedDelta).slice(0, 3)).toEqual(new Uint8Array(deltaFrame).slice(0, 3));
    expect(new Uint8Array(encodedKey).slice(30, 30 + keySuffix.length))
      .not.toEqual(new Uint8Array(keyFrame).slice(10));

    const changed = encodedKey.slice(0);
    new Uint8Array(changed)[1] ^= 0x01;
    await expect(new PairMediaE2eeFrameCipher(context()).open(aes, changed))
      .rejects.toThrow('media_e2ee_authentication_failed');
  });

  it('preserves and authenticates only the Opus TOC byte', async () => {
    const aes = await key();
    const audioContext = context({ slot: 'microphone-opus', codec: 'opus', kind: 'audio' });
    const plaintext = Uint8Array.of(0xf8, 0x11, 0x22, 0x33, 0x44).buffer;
    const encoded = await new PairMediaE2eeFrameCipher(audioContext).seal(aes, plaintext);
    expect(new Uint8Array(encoded)[0]).toBe(0xf8);
    expect(new Uint8Array(encoded).slice(21, 25)).not.toEqual(new Uint8Array(plaintext).slice(1));
    await expect(new PairMediaE2eeFrameCipher(audioContext).open(aes, encoded))
      .resolves.toEqual(plaintext);

    const changed = encoded.slice(0);
    new Uint8Array(changed)[0] ^= 0x04;
    await expect(new PairMediaE2eeFrameCipher(audioContext).open(aes, changed))
      .rejects.toThrow('media_e2ee_authentication_failed');
  });

  it('rejects legacy layout and every authenticated v2 header mutation', async () => {
    const aes = await key();
    const encoded = await new PairMediaE2eeFrameCipher(context())
      .seal(aes, vp8DeltaFrame(0x44, 0x55));

    const legacy = new Uint8Array(encoded.byteLength);
    legacy.set(Uint8Array.of(0x41, 0x4e, 0x4d, 0x46, 0x01));
    await expect(new PairMediaE2eeFrameCipher(context()).open(aes, legacy.buffer))
      .rejects.toThrow('media_e2ee_header_invalid');

    for (const [offset, value] of [[7, 1], [8, 1], [9, 10]] as const) {
      const changed = encoded.slice(0);
      new Uint8Array(changed)[offset] = value;
      await expect(new PairMediaE2eeFrameCipher(context()).open(aes, changed))
        .rejects.toThrow('media_e2ee_header_invalid');
    }
  });

  it('rejects codec-prefix reclassification, ciphertext tampering and the wrong key', async () => {
    const aes = await key();
    const encoded = await new PairMediaE2eeFrameCipher(context())
      .seal(aes, vp8DeltaFrame(0x44, 0x55));

    const reclassified = encoded.slice(0);
    new Uint8Array(reclassified)[0] &= 0xfe;
    await expect(new PairMediaE2eeFrameCipher(context()).open(aes, reclassified))
      .rejects.toThrow('media_e2ee_header_invalid');

    const changedCiphertext = encoded.slice(0);
    new Uint8Array(changedCiphertext)[changedCiphertext.byteLength - 1] ^= 0x01;
    await expect(new PairMediaE2eeFrameCipher(context()).open(aes, changedCiphertext))
      .rejects.toThrow('media_e2ee_authentication_failed');
    await expect(new PairMediaE2eeFrameCipher(context()).open(await key(), encoded))
      .rejects.toThrow('media_e2ee_authentication_failed');
  });

  it('rejects malformed VP8 codec prefixes and mismatched codec contexts', async () => {
    const aes = await key();
    await expect(new PairMediaE2eeFrameCipher(context()).seal(
      aes, Uint8Array.of(0x10, 0, 0, 1, 2, 3, 4, 5, 6, 7).buffer,
    )).rejects.toThrow('media_e2ee_codec_frame_invalid');
    await expect(new PairMediaE2eeFrameCipher(context()).seal(
      aes, Uint8Array.of(0x10, 0, 0).buffer,
    )).rejects.toThrow('media_e2ee_codec_frame_invalid');

    const encoded = await new PairMediaE2eeFrameCipher(context()).seal(aes, vp8KeyFrame(0xaa));
    const audioContext = context({ slot: 'microphone-opus', codec: 'opus', kind: 'audio' });
    await expect(new PairMediaE2eeFrameCipher(audioContext).open(aes, encoded))
      .rejects.toThrow('media_e2ee_header_invalid');
  });

  it('reserves distinct counters before concurrent encryption awaits', async () => {
    const aes = await key();
    const sender = new PairMediaE2eeFrameCipher(context());
    const frames = await Promise.all([
      sender.seal(aes, vp8DeltaFrame(1)),
      sender.seal(aes, vp8DeltaFrame(2)),
      sender.seal(aes, vp8DeltaFrame(3)),
    ]);
    expect(new Set(frames.map(frame => counter(frame)))).toEqual(new Set([1n, 2n, 3n]));
  });

  it('rejects an empty frame before reserving encrypted output', async () => {
    const aes = await key();
    await expect(new PairMediaE2eeFrameCipher(context()).seal(aes, new ArrayBuffer(0)))
      .rejects.toThrow('media_e2ee_frame_empty');
  });

  it('rejects every frame at the signed contract expiry boundary', async () => {
    const aes = await key();
    let now = 99;
    const expiringContext = context({ contractExpiresAtMs: 100 });
    const sender = new PairMediaE2eeFrameCipher(expiringContext, () => now);
    const receiver = new PairMediaE2eeFrameCipher(expiringContext, () => now);
    const encoded = await sender.seal(aes, vp8DeltaFrame(1));
    now = 100;

    await expect(sender.seal(aes, vp8DeltaFrame(2)))
      .rejects.toThrow('media_e2ee_contract_expired');
    await expect(receiver.open(aes, encoded))
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
      ).seal(aes, vp8DeltaFrame(1));
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
      .seal(aes, vp8DeltaFrame(2));
    const decryptGate = deferred<void>();
    const originalDecrypt = crypto.subtle.decrypt.bind(crypto.subtle);
    const decryptSpy = vi.spyOn(crypto.subtle, 'decrypt').mockImplementationOnce(async (...args) => {
      await decryptGate.promise;
      return originalDecrypt(...args);
    });
    try {
      const opening = new PairMediaE2eeFrameCipher(expiringContext, () => now)
        .open(aes, encoded);
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
      .seal(aes, vp8DeltaFrame(1, 2, 3));
    const receiver = new PairMediaE2eeFrameCipher(context());
    const results = await Promise.allSettled([
      receiver.open(aes, encoded),
      receiver.open(aes, encoded),
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
    const first = await sender.seal(aes, vp8DeltaFrame(1));
    await receiver.open(aes, first);
    for (let index = 0; index < 2_049; index += 1) {
      const encoded = await sender.seal(aes, vp8DeltaFrame(index & 0xff));
      await receiver.open(aes, encoded);
    }
    await expect(receiver.open(aes, first)).rejects.toThrow('media_e2ee_replay_too_old');
  });
});

function deferred<T>(): { promise: Promise<T>; resolve(value: T): void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(accept => { resolve = accept; });
  return { promise, resolve };
}
