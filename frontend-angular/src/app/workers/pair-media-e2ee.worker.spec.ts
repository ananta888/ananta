import { PUBLIC_PAIR_MEDIA_SLOTS } from '../services/public-pair-media-security-contract';

interface WorkerHarness {
  readonly posted: any[];
  readonly controllers: Map<string, ReadableStreamDefaultController<any>>;
  readonly output: Map<string, any[]>;
  transform(id: string, operation: 'encrypt' | 'decrypt'): void;
  message(value: unknown): void;
  restore(): void;
}

describe('pair media E2EE worker', () => {
  afterEach(() => vi.resetModules());

  it('keeps every transform DROP-first when the final install entry is invalid', async () => {
    const harness = await createHarness();
    try {
      const ids = installTransforms(harness);
      const entries = await installEntries(ids);
      entries[5] = {
        ...entries[5],
        context: { ...entries[5].context, recipientId: entries[5].context.senderId },
      };

      harness.message({ version: 1, type: 'install-keys', sessionId: 'session-a', entries });
      await settle();
      harness.controllers.get(ids[0])?.enqueue({ data: Uint8Array.of(1, 2, 3).buffer, type: 'audio' });
      await settle();

      expect(harness.posted).toContainEqual(expect.objectContaining({
        type: 'fatal', reasonCode: 'media_e2ee_context_invalid',
      }));
      expect(harness.posted.some(message => message.type === 'keys-installed')).toBe(false);
      expect(harness.output.get(ids[0])).toEqual([]);
    } finally {
      harness.restore();
    }
  });

  it('never permits a second installation in one worker generation', async () => {
    const harness = await createHarness();
    try {
      const ids = installTransforms(harness);
      const entries = await installEntries(ids);
      const message = { version: 1, type: 'install-keys', sessionId: 'session-a', entries };
      harness.message(message);
      await settle();
      expect(harness.posted).toContainEqual(expect.objectContaining({ type: 'keys-installed' }));

      harness.message(message);
      await settle();
      expect(harness.posted).toContainEqual(expect.objectContaining({
        type: 'fatal', reasonCode: 'media_e2ee_key_reinstall_forbidden',
      }));
    } finally {
      harness.restore();
    }
  });

  it('poisons every keyed slot before reporting one transform authentication fatal', async () => {
    const harness = await createHarness();
    const encryptGate = deferred<void>();
    const originalEncrypt = crypto.subtle.encrypt.bind(crypto.subtle);
    const encryptSpy = vi.spyOn(crypto.subtle, 'encrypt').mockImplementationOnce(async (...args) => {
      await encryptGate.promise;
      return originalEncrypt(...args);
    });
    try {
      const ids = installTransforms(harness);
      harness.message({
        version: 1, type: 'install-keys', sessionId: 'session-a',
        entries: await installEntries(ids),
      });
      await settle();

      harness.controllers.get(ids[0])?.enqueue({
        data: Uint8Array.of(1, 2, 3).buffer, type: 'audio',
      });
      await settle(5);
      expect(encryptSpy).toHaveBeenCalledOnce();

      harness.controllers.get(ids[1])?.enqueue({
        data: new Uint8Array(64).buffer, type: 'audio',
      });
      await settle(10);
      expect(harness.posted).toContainEqual(expect.objectContaining({
        type: 'fatal', transformId: ids[1], reasonCode: 'media_e2ee_header_invalid',
      }));

      encryptGate.resolve(undefined);
      harness.controllers.get(ids[2])?.enqueue({
        data: Uint8Array.of(7, 8, 9).buffer, type: 'delta',
      });
      await settle(10);
      expect(harness.output.get(ids[0])).toEqual([]);
      expect(harness.output.get(ids[2])).toEqual([]);
      expect(harness.posted.filter(message => message.type === 'fatal')).toHaveLength(1);
    } finally {
      encryptSpy.mockRestore();
      harness.restore();
    }
  });

  it('drops an empty codec callback without poisoning any keyed slot', async () => {
    const harness = await createHarness();
    try {
      const ids = installTransforms(harness);
      harness.message({
        version: 1, type: 'install-keys', sessionId: 'session-a',
        entries: await installEntries(ids),
      });
      await settle(6);

      harness.controllers.get(ids[0])?.enqueue({ data: new ArrayBuffer(0), type: 'audio' });
      await settle(8);

      expect(harness.output.get(ids[0])).toEqual([]);
      expect(harness.posted.some(message => message.type === 'fatal')).toBe(false);
    } finally {
      harness.restore();
    }
  });

  it('decrypts VP8 from its authenticated prefix when the receiver metadata is reclassified', async () => {
    const harness = await createHarness();
    try {
      const ids = installTransforms(harness);
      const entries = await installEntries(ids);
      // Camera send/receive use the same exact direction key only in this
      // loopback harness. Production installs inverse peer directions.
      entries[3] = { ...entries[3], key: entries[2].key, context: entries[2].context };
      harness.message({ version: 1, type: 'install-keys', sessionId: 'session-a', entries });
      await settle(6);

      const plaintext = Uint8Array.of(
        0x10, 0x00, 0x00, 0x9d, 0x01, 0x2a, 0x80, 0x02, 0xe0, 0x01, 0xaa, 0xbb,
      ).buffer;
      harness.controllers.get(ids[2])?.enqueue({ data: plaintext.slice(0), type: 'key' });
      await waitForOutput(harness, ids[2]);
      const encrypted = harness.output.get(ids[2])?.[0];
      expect(encrypted).toBeDefined();

      harness.controllers.get(ids[3])?.enqueue({
        data: encrypted.data.slice(0),
        // Firefox can report delta here even when Chromium sent a key frame.
        type: 'delta',
      });
      await waitForOutput(harness, ids[3]);

      expect(harness.output.get(ids[3])?.[0]?.data).toEqual(plaintext);
      expect(harness.posted.some(message => message.type === 'fatal')).toBe(false);
    } finally {
      harness.restore();
    }
  });
});

async function createHarness(): Promise<WorkerHarness> {
  vi.resetModules();
  const scope = globalThis as any;
  const previous = {
    postMessage: scope.postMessage,
    onmessage: scope.onmessage,
    onrtctransform: scope.onrtctransform,
  };
  const posted: any[] = [];
  const controllers = new Map<string, ReadableStreamDefaultController<any>>();
  const output = new Map<string, any[]>();
  scope.postMessage = (message: unknown) => posted.push(message);
  await import('./pair-media-e2ee.worker');
  return {
    posted,
    controllers,
    output,
    transform(id, operation) {
      const frames: any[] = [];
      output.set(id, frames);
      const readable = new ReadableStream({ start(controller) { controllers.set(id, controller); } });
      const writable = new WritableStream({ write(frame) { frames.push(frame); } });
      scope.onrtctransform({ transformer: {
        options: { version: 1, transformId: id, operation }, readable, writable,
      } });
    },
    message(value) { scope.onmessage({ data: value }); },
    restore() {
      scope.postMessage = previous.postMessage;
      scope.onmessage = previous.onmessage;
      scope.onrtctransform = previous.onrtctransform;
    },
  };
}

function installTransforms(harness: WorkerHarness): string[] {
  const ids = PUBLIC_PAIR_MEDIA_SLOTS.flatMap(definition => [
    `session-a:${definition.slot}:send`, `session-a:${definition.slot}:receive`,
  ]);
  ids.forEach((id, index) => harness.transform(id, index % 2 === 0 ? 'encrypt' : 'decrypt'));
  return ids;
}

async function installEntries(ids: readonly string[]): Promise<any[]> {
  const entries: any[] = [];
  for (const [index, id] of ids.entries()) {
    const definition = PUBLIC_PAIR_MEDIA_SLOTS[Math.floor(index / 2)];
    const sending = index % 2 === 0;
    entries.push({
      transformId: id,
      key: await crypto.subtle.generateKey(
        { name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt'],
      ),
      context: {
        sessionId: 'session-a', mediaContractDigest: 'a'.repeat(64), connectionId: 'b'.repeat(64),
        senderId: sending ? 'peer:local' : 'peer:remote',
        recipientId: sending ? 'peer:remote' : 'peer:local',
        slot: definition.slot, codec: definition.codec, kind: definition.kind, keyEpoch: 7,
        contractExpiresAtMs: 2_000_000_000_000,
      },
    });
  }
  return entries;
}

async function settle(turns = 3): Promise<void> {
  for (let index = 0; index < turns; index += 1) await Promise.resolve();
}

async function waitForOutput(harness: WorkerHarness, transformId: string): Promise<void> {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    if (harness.output.get(transformId)?.length) return;
    await new Promise(resolve => setTimeout(resolve, 0));
  }
}

function deferred<T>(): { promise: Promise<T>; resolve(value: T): void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(accept => { resolve = accept; });
  return { promise, resolve };
}
