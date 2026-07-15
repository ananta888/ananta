import { IDBFactory } from 'fake-indexeddb';

import {
  IndexedDbVoiceLongRunSpool,
  VOICE_PROFILE_DELETION_EVENT,
} from './voice-long-run-spool';

describe('IndexedDbVoiceLongRunSpool', () => {
  beforeEach(() => {
    globalThis.indexedDB = new IDBFactory() as unknown as IDBFactory;
  });

  it('round-trips binary audio with a persistent non-extractable AES-GCM key', async () => {
    const dbName = `voice-spool-${crypto.randomUUID()}`;
    const spool = new IndexedDbVoiceLongRunSpool(3, 1_024, dbName);
    const audio = wavBytes(96, 7);
    await spool.initialize();
    await spool.put(segment('run-a', 0, audio));

    const restored = await new IndexedDbVoiceLongRunSpool(3, 1_024, dbName).read('run-a', 0);
    const key = await readRaw<CryptoKey>(dbName, 'keys', 'voice-live-run-audio-v1');
    const stored = await readRaw<{ ciphertext: ArrayBuffer }>(dbName, 'segments', 'run-a:0');

    expect(new Uint8Array(restored!.audio)).toEqual(new Uint8Array(audio));
    expect(key.extractable).toBe(false);
    expect(new Uint8Array(stored.ciphertext)).not.toEqual(new Uint8Array(audio));
  });

  it('evicts the oldest ciphertext records at hard segment and byte limits', async () => {
    const spool = new IndexedDbVoiceLongRunSpool(2, 240, `voice-spool-${crypto.randomUUID()}`);
    await spool.initialize();
    await spool.put(segment('run-a', 0, wavBytes(100, 1)));
    await spool.put(segment('run-a', 1, wavBytes(100, 2)));
    const result = await spool.put(segment('run-a', 2, wavBytes(100, 3)));

    expect(result.evicted.map((item) => item.sequence)).toEqual([0]);
    expect((await spool.list('run-a')).map((item) => item.sequence)).toEqual([1, 2]);
    expect(await spool.stats('run-a')).toEqual({
      segments: 2, bytes: 200, maxSegments: 2, maxBytes: 240,
    });
  });

  it('deletes acknowledged segments and clears all run audio on stop', async () => {
    const spool = new IndexedDbVoiceLongRunSpool(3, 1_024, `voice-spool-${crypto.randomUUID()}`);
    await spool.put(segment('run-a', 0, wavBytes(80, 1)));
    await spool.put(segment('run-a', 1, wavBytes(80, 2)));
    await spool.delete('run-a', 0);
    expect((await spool.list('run-a')).map((item) => item.sequence)).toEqual([1]);

    await spool.clearRun('run-a');
    expect(await spool.list('run-a')).toEqual([]);
  });

  it('never silently evicts a different recoverable run', async () => {
    const spool = new IndexedDbVoiceLongRunSpool(2, 220, `voice-spool-${crypto.randomUUID()}`);
    await spool.put(segment('old-run', 0, wavBytes(100, 1)));
    await spool.put(segment('old-run', 1, wavBytes(100, 2)));

    await expect(spool.put(segment('new-run', 0, wavBytes(100, 3))))
      .rejects.toThrow('voice.long_run.other_run_pending');
    expect((await spool.list('old-run')).map((item) => item.sequence)).toEqual([0, 1]);
  });

  it('converges concurrent tabs on one durable encryption key', async () => {
    const dbName = `voice-spool-${crypto.randomUUID()}`;
    const left = new IndexedDbVoiceLongRunSpool(3, 1_024, dbName);
    const right = new IndexedDbVoiceLongRunSpool(3, 1_024, dbName);
    await Promise.all([left.initialize(), right.initialize()]);
    await left.put(segment('run-a', 0, wavBytes(80, 1)));
    await right.put(segment('run-a', 1, wavBytes(80, 2)));

    const reloaded = new IndexedDbVoiceLongRunSpool(3, 1_024, dbName);
    await expect(reloaded.read('run-a', 0)).resolves.toEqual(expect.objectContaining({ sequence: 0 }));
    await expect(reloaded.read('run-a', 1)).resolves.toEqual(expect.objectContaining({ sequence: 1 }));
  });

  it('clears ciphertext for one deleted profile without touching another', async () => {
    const spool = new IndexedDbVoiceLongRunSpool(3, 1_024, `voice-spool-${crypto.randomUUID()}`);
    const oldGeneration = await spool.allowProfile('profile-a');
    await spool.put(segment('run-a', 0, wavBytes(80, 1), 'profile-a', oldGeneration));
    await spool.put(segment('run-b', 0, wavBytes(80, 2), 'profile-b'));

    await spool.clearProfile('profile-a');

    expect(await spool.list('run-a')).toEqual([]);
    expect(await spool.list('run-b')).toHaveLength(1);
    await expect(spool.put(segment('run-c', 0, wavBytes(80, 3), 'profile-a', oldGeneration)))
      .rejects.toThrow('voice.long_run.profile_deleted');

    const newGeneration = await spool.allowProfile('profile-a');
    await expect(spool.put(segment('run-c', 0, wavBytes(80, 3), 'profile-a', newGeneration)))
      .resolves.toBeTruthy();
  });

  it('serializes a cross-tab profile clear against a concurrent segment put', async () => {
    const dbName = `voice-spool-${crypto.randomUUID()}`;
    const writer = new IndexedDbVoiceLongRunSpool(3, 1_024, dbName);
    const privacy = new IndexedDbVoiceLongRunSpool(3, 1_024, dbName);
    const generation = await writer.allowProfile('profile-a');

    await Promise.allSettled([
      writer.put(segment('run-a', 0, wavBytes(80, 1), 'profile-a', generation)),
      privacy.clearProfile('profile-a'),
    ]);

    expect(await writer.list('run-a')).toEqual([]);
    await expect(writer.put(segment('run-a', 1, wavBytes(80, 2), 'profile-a', generation)))
      .rejects.toThrow('voice.long_run.profile_deleted');
  });

  it('enforces global segment and byte bounds across concurrent tabs', async () => {
    const segmentDb = `voice-spool-${crypto.randomUUID()}`;
    const segmentLeft = new IndexedDbVoiceLongRunSpool(3, 1_024, segmentDb);
    const segmentRight = new IndexedDbVoiceLongRunSpool(3, 1_024, segmentDb);
    await Promise.all([segmentLeft.initialize(), segmentRight.initialize()]);
    await segmentLeft.put(segment('run-a', 0, wavBytes(100, 1)));
    await segmentLeft.put(segment('run-a', 1, wavBytes(100, 2)));
    await segmentLeft.put(segment('run-a', 2, wavBytes(100, 3)));

    await Promise.all([
      segmentLeft.put(segment('run-a', 3, wavBytes(100, 4))),
      segmentRight.put(segment('run-a', 4, wavBytes(100, 5))),
    ]);

    const segmentStats = await segmentLeft.stats();
    expect(segmentStats.segments).toBeLessThanOrEqual(segmentStats.maxSegments);
    expect(segmentStats.bytes).toBeLessThanOrEqual(segmentStats.maxBytes);

    const byteDb = `voice-spool-${crypto.randomUUID()}`;
    const byteLeft = new IndexedDbVoiceLongRunSpool(3, 250, byteDb);
    const byteRight = new IndexedDbVoiceLongRunSpool(3, 250, byteDb);
    await Promise.all([byteLeft.initialize(), byteRight.initialize()]);
    await byteLeft.put(segment('run-b', 0, wavBytes(100, 1)));
    await byteLeft.put(segment('run-b', 1, wavBytes(100, 2)));

    await Promise.all([
      byteLeft.put(segment('run-b', 2, wavBytes(100, 3))),
      byteRight.put(segment('run-b', 3, wavBytes(100, 4))),
    ]);

    const byteStats = await byteLeft.stats();
    expect(byteStats.segments).toBeLessThanOrEqual(byteStats.maxSegments);
    expect(byteStats.bytes).toBeLessThanOrEqual(byteStats.maxBytes);
  });

  it('signals same-document capture before reporting a local privacy-cleanup failure', async () => {
    const dbName = `voice-spool-${crypto.randomUUID()}`;
    await createBrokenDatabase(dbName);
    const spool = new IndexedDbVoiceLongRunSpool(3, 1_024, dbName);
    const deletedProfiles: string[] = [];
    const listener = (event: Event) => {
      deletedProfiles.push(String((event as CustomEvent).detail?.profileId || ''));
    };
    globalThis.addEventListener(VOICE_PROFILE_DELETION_EVENT, listener);
    try {
      await expect(spool.clearProfile('profile-a')).rejects.toBeTruthy();
      expect(deletedProfiles).toEqual(['profile-a']);
    } finally {
      globalThis.removeEventListener(VOICE_PROFILE_DELETION_EVENT, listener);
    }
  });

  it('publishes profile deletion immediately even behind a stalled spool write', async () => {
    const spool = new IndexedDbVoiceLongRunSpool(
      3,
      1_024,
      `voice-spool-${crypto.randomUUID()}`,
    );
    await spool.initialize();
    let resolveEncryption!: (value: ArrayBuffer) => void;
    const encryption = new Promise<ArrayBuffer>((resolve) => { resolveEncryption = resolve; });
    const encrypt = vi.spyOn(crypto.subtle, 'encrypt').mockReturnValueOnce(encryption);
    const deletedProfiles: string[] = [];
    const listener = (event: Event) => {
      deletedProfiles.push(String((event as CustomEvent).detail?.profileId || ''));
    };
    globalThis.addEventListener(VOICE_PROFILE_DELETION_EVENT, listener);
    const pendingPut = spool.put(segment('run-a', 0, wavBytes(80, 1), 'profile-a'));
    let clearing: Promise<void> | null = null;
    try {
      await vi.waitFor(() => expect(encrypt).toHaveBeenCalledTimes(1));
      clearing = spool.clearProfile('profile-a');

      expect(deletedProfiles).toEqual(['profile-a']);
      await expect(clearing).resolves.toBeUndefined();
    } finally {
      resolveEncryption(new ArrayBuffer(96));
      await Promise.allSettled([pendingPut, ...(clearing ? [clearing] : [])]);
      encrypt.mockRestore();
      globalThis.removeEventListener(VOICE_PROFILE_DELETION_EVENT, listener);
    }
    expect(await spool.list('run-a')).toEqual([]);
  });

  it('physically expires ciphertext during read, list, stats and put without reinitializing', async () => {
    let now = 1_700_000_000_000;
    const clock = vi.spyOn(Date, 'now').mockImplementation(() => now);
    try {
      const readDb = `voice-spool-${crypto.randomUUID()}`;
      const listDb = `voice-spool-${crypto.randomUUID()}`;
      const statsDb = `voice-spool-${crypto.randomUUID()}`;
      const capacityDb = `voice-spool-${crypto.randomUUID()}`;
      const readSpool = new IndexedDbVoiceLongRunSpool(3, 1_024, readDb);
      const listSpool = new IndexedDbVoiceLongRunSpool(3, 1_024, listDb);
      const statsSpool = new IndexedDbVoiceLongRunSpool(3, 1_024, statsDb);
      const capacitySpool = new IndexedDbVoiceLongRunSpool(3, 1_024, capacityDb);
      await Promise.all([
        readSpool.put(segment('run-read', 0, wavBytes(80, 1))),
        listSpool.put(segment('run-list', 0, wavBytes(80, 2))),
        statsSpool.put(segment('run-stats', 0, wavBytes(80, 3))),
      ]);
      await capacitySpool.put(segment('old-run', 0, wavBytes(80, 1)));
      await capacitySpool.put(segment('old-run', 1, wavBytes(80, 2)));
      await capacitySpool.put(segment('old-run', 2, wavBytes(80, 3)));

      now += 24 * 60 * 60 * 1_000 + 1;

      await expect(readSpool.read('run-read', 0)).resolves.toBeNull();
      expect(await readRaw<any>(readDb, 'segments', 'run-read:0')).toBeUndefined();
      await expect(listSpool.list('run-list')).resolves.toEqual([]);
      expect(await readRaw<any>(listDb, 'segments', 'run-list:0')).toBeUndefined();
      await expect(statsSpool.stats('run-stats')).resolves.toEqual({
        segments: 0, bytes: 0, maxSegments: 3, maxBytes: 1_024,
      });
      expect(await readRaw<any>(statsDb, 'segments', 'run-stats:0')).toBeUndefined();
      await expect(capacitySpool.put(segment('new-run', 0, wavBytes(80, 4))))
        .resolves.toBeTruthy();
      expect(await capacitySpool.stats()).toEqual({
        segments: 1, bytes: 80, maxSegments: 3, maxBytes: 1_024,
      });
      expect(await readRaw<any>(capacityDb, 'segments', 'old-run:0')).toBeUndefined();
    } finally {
      clock.mockRestore();
    }
  });
});

function segment(
  runId: string,
  sequence: number,
  audio: ArrayBuffer,
  profileId = 'default',
  profileGeneration = Date.now() || 1,
) {
  return {
    runId,
    profileId,
    profileGeneration,
    sequence,
    startedAtMs: sequence * 60_000,
    endedAtMs: (sequence + 1) * 60_000,
    durationMs: 60_000,
    overlapMilliseconds: 0,
    idempotencyKey: `segment-${runId}-${sequence}`,
    audio,
  };
}

function wavBytes(length: number, fill: number): ArrayBuffer {
  const bytes = new Uint8Array(length);
  bytes.set([0x52, 0x49, 0x46, 0x46], 0);
  bytes.fill(fill, 44);
  return bytes.buffer;
}

async function readRaw<T>(dbName: string, storeName: string, key: string): Promise<T> {
  const db = await new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open(dbName, 1);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  try {
    return await new Promise<T>((resolve, reject) => {
      const request = db.transaction(storeName, 'readonly').objectStore(storeName).get(key);
      request.onsuccess = () => resolve(request.result as T);
      request.onerror = () => reject(request.error);
    });
  } finally {
    db.close();
  }
}

async function createBrokenDatabase(dbName: string): Promise<void> {
  const db = await new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open(dbName, 1);
    request.onupgradeneeded = () => request.result.createObjectStore('segments', { keyPath: 'storageId' });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  db.close();
}
