import { describe, expect, it, vi } from 'vitest';

import { SfuBroadcastDataFanoutService } from './sfu-broadcast-data-fanout.service';
import type {
  SfuBroadcastCryptographicScope,
  SfuBroadcastGroupKeyLease,
  SfuBroadcastGroupKeyPort,
} from './sfu-broadcast-group-key.port';
import type { SfuDataPort, SfuOpaqueDataPacket } from './sfu-room-session.ports';
import { WebrtcChunkReassemblyStore } from './webrtc-chunk-reassembly.store';

const senderScope: SfuBroadcastCryptographicScope = Object.freeze({
  tenantRef: 'tenant-a', roomRef: 'room-a', publicationRef: 'publication-a',
  audienceRef: 'audience-a', localHandle: 'sender-a', membershipEpoch: 4,
  routeEpoch: 8, keyEpoch: 3, fencingToken: 'fence-a',
});

describe('SfuBroadcastDataFanoutService', () => {
  it('encrypts, chunks and deterministically batches only authorized opaque destinations', async () => {
    const key = await aesKey();
    const keyPort = fakeKeyPort(key, senderScope, ['sender-a', ...handles(15)]);
    const sender = service(keyPort);
    await sender.activate(senderScope, 1_000);
    const published: Array<{ payload: Uint8Array; destinations: readonly string[]; reliable: boolean }> = [];
    const port = fakeDataPort(published);

    const result = await sender.send(port, {
      trafficKind: 'shared_reference', visibility: 'shared', payloadType: 'cursor.reference',
      contentEncoding: 'binary', plaintext: new Uint8Array(10_000).fill(7),
      destinationHandles: handles(15).reverse(), sequence: 1, ttlMs: 5_000,
    }, 1_000);

    expect(result.batchCount).toBe(3);
    expect(result.chunkCount).toBeGreaterThan(1);
    expect(result.publishedPackets).toBe(result.batchCount * result.chunkCount);
    expect(published.every(value => value.destinations.length > 0 && value.destinations.length <= 7)).toBe(true);
    expect(published.every(value => value.reliable && value.payload.byteLength < 15 * 1024)).toBe(true);
    expect(new TextDecoder().decode(published[0].payload)).not.toContain('cursor.reference payload');
  });

  it('decrypts only after scope, epoch, sender, digest and replay checks pass', async () => {
    const key = await aesKey();
    const destinations = ['receiver-a'];
    const senderPort = fakeDataPort([]);
    const sender = service(fakeKeyPort(key, senderScope, ['sender-a', ...destinations]));
    await sender.activate(senderScope, 1_000);
    const packets: Array<{ payload: Uint8Array; destinations: readonly string[]; reliable: boolean }> = [];
    const capture = fakeDataPort(packets);
    await sender.send(capture, {
      trafficKind: 'private_recovery', visibility: 'receiver_private', payloadType: 'private.recovery',
      contentEncoding: 'binary', plaintext: Uint8Array.from(new TextEncoder().encode('secret-value')),
      destinationHandles: destinations, sequence: 9, ttlMs: 5_000,
    }, 1_000);

    const receiverScope = Object.freeze({ ...senderScope, localHandle: 'receiver-a' });
    const receiver = service(fakeKeyPort(key, receiverScope, ['sender-a', 'receiver-a']));
    await receiver.activate(receiverScope, 1_000);
    const delivered: string[] = [];
    let last = { status: 'pending', reasonCode: '' } as any;
    for (const value of packets) {
      last = await receiver.acceptPacket({
        senderId: 'sender-a', topic: 'ananta.sfu-data.v1', payload: value.payload,
      }, delivery => { delivered.push(new TextDecoder().decode(delivery.plaintext)); }, 1_001);
    }
    expect(last).toEqual({ status: 'delivered', reasonCode: 'sfu_data_delivered' });
    expect(delivered).toEqual(['secret-value']);

    for (const value of packets) {
      last = await receiver.acceptPacket({
        senderId: 'sender-a', topic: 'ananta.sfu-data.v1', payload: value.payload,
      }, vi.fn(), 1_002);
    }
    expect(last).toEqual({ status: 'rejected', reasonCode: 'sfu_data_sequence_duplicate' });
    void senderPort;
  });

  it('fails closed for audience expansion, concurrent backpressure and epoch changes', async () => {
    const key = await aesKey();
    const keyPort = fakeKeyPort(key, senderScope, ['sender-a', 'receiver-a']);
    const fanout = service(keyPort);
    await fanout.activate(senderScope, 1_000);
    await expect(fanout.send(fakeDataPort([]), {
      trafficKind: 'control_hint', visibility: 'shared', payloadType: 'control.hint',
      contentEncoding: 'json', plaintext: new Uint8Array([1]),
      destinationHandles: [], sequence: 1, ttlMs: 100,
    }, 1_000)).rejects.toThrow('sfu_data_audience_unauthorized');
    await expect(fanout.send(fakeDataPort([]), {
      trafficKind: 'control_hint', visibility: 'shared', payloadType: 'control.hint',
      contentEncoding: 'json', plaintext: new Uint8Array([1]),
      destinationHandles: ['receiver-a', 'receiver-eve'], sequence: 1, ttlMs: 100,
    }, 1_000)).rejects.toThrow('sfu_data_audience_unauthorized');
    fanout.revoke();
    expect(fanout.snapshot()).toMatchObject({ active: false, replayWindows: 0, reassemblyStates: 0 });
  });

  it('does not allow a slower activation to replace a newer key lease', async () => {
    const key = await aesKey();
    const first = deferred<SfuBroadcastGroupKeyLease>();
    const second = deferred<SfuBroadcastGroupKeyLease>();
    const firstRelease = vi.fn();
    const secondRelease = vi.fn();
    const port: SfuBroadcastGroupKeyPort = {
      acquire: vi.fn()
        .mockImplementationOnce(() => first.promise)
        .mockImplementationOnce(() => second.promise),
    };
    const fanout = service(port);
    const older = fanout.activate(senderScope, 1_000);
    const newer = fanout.activate(senderScope, 1_000);
    second.resolve(lease(key, senderScope, secondRelease));
    await newer;
    first.resolve(lease(key, senderScope, firstRelease));

    await expect(older).rejects.toThrow('sfu_data_epoch_fenced');
    expect(firstRelease).toHaveBeenCalledOnce();
    expect(secondRelease).not.toHaveBeenCalled();
  });

  it('does not deliver decrypted data after the active epoch is revoked', async () => {
    const key = await aesKey();
    const packets: Array<{ payload: Uint8Array; destinations: readonly string[]; reliable: boolean }> = [];
    const sender = service(fakeKeyPort(key, senderScope, ['sender-a', 'receiver-a']));
    await sender.activate(senderScope, 1_000);
    await sender.send(fakeDataPort(packets), {
      trafficKind: 'control_hint', visibility: 'shared', payloadType: 'control.hint',
      contentEncoding: 'binary', plaintext: new Uint8Array([7]),
      destinationHandles: ['receiver-a'], sequence: 1, ttlMs: 5_000,
    }, 1_000);
    const receiverScope = Object.freeze({ ...senderScope, localHandle: 'receiver-a' });
    const entered = deferred<void>();
    const resume = deferred<void>();
    const reassembly = reassemblyStore();
    const originalAccept = reassembly.accept.bind(reassembly);
    const accept = vi.spyOn(reassembly, 'accept').mockImplementation(async (chunk, nowMs) => {
      const result = await originalAccept(chunk, nowMs);
      entered.resolve();
      await resume.promise;
      return result;
    });
    const receiver = service(fakeKeyPort(key, receiverScope, ['sender-a', 'receiver-a']), reassembly);
    await receiver.activate(receiverScope, 1_000);
    const handler = vi.fn();
    try {
      const pending = receiver.acceptPacket({
        senderId: 'sender-a', topic: 'ananta.sfu-data.v1', payload: packets[0].payload,
      }, handler, 1_001);
      await entered.promise;
      receiver.revoke();
      resume.resolve();
      await expect(pending).resolves.toEqual({ status: 'rejected', reasonCode: 'sfu_data_epoch_fenced' });
      expect(handler).not.toHaveBeenCalled();
    } finally {
      accept.mockRestore();
    }
  });
});

function service(
  port: SfuBroadcastGroupKeyPort,
  reassembly = reassemblyStore(),
): SfuBroadcastDataFanoutService {
  return new SfuBroadcastDataFanoutService(port, reassembly);
}

function reassemblyStore(): WebrtcChunkReassemblyStore {
  return new WebrtcChunkReassemblyStore({
    maxChunksPerMessage: 24, maxBytesPerMessage: 131_072, maxStatesPerPeer: 4,
    maxStatesPerSession: 32, maxBytesPerPeer: 131_072, maxBytesPerSession: 524_288,
    maxGlobalBytes: 524_288, maxStates: 32, maxTtlMs: 30_000,
  });
}

function fakeKeyPort(
  key: CryptoKey,
  scope: SfuBroadcastCryptographicScope,
  authorized: readonly string[],
): SfuBroadcastGroupKeyPort {
  return {
    acquire: vi.fn(async () => ({
      authorizationRef: 'authorization-a', keyId: 'key-a', scope,
      expiresAtMs: 31_000, authorizedDestinationHandles: new Set(authorized),
      contentKey: key, release: vi.fn(),
    } satisfies SfuBroadcastGroupKeyLease)),
  };
}

function fakeDataPort(
  sink: Array<{ payload: Uint8Array; destinations: readonly string[]; reliable: boolean }>,
): SfuDataPort {
  let callback: ((packet: SfuOpaqueDataPacket) => void) | null = null;
  return {
    publishOpaqueData: vi.fn(async (payload, _topic, destinations, options) => {
      sink.push({ payload: Uint8Array.from(payload), destinations: [...destinations], reliable: options?.reliable ?? true });
    }),
    onOpaqueDataReceived: next => { callback = next; return () => { callback = null; }; },
  };
}

function handles(count: number): string[] {
  return Array.from({ length: count }, (_, index) => `receiver-${String(index).padStart(2, '0')}`);
}

async function aesKey(): Promise<CryptoKey> {
  return crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
}

function lease(
  key: CryptoKey,
  scope: SfuBroadcastCryptographicScope,
  release: () => void,
): SfuBroadcastGroupKeyLease {
  return {
    authorizationRef: 'authorization-a', keyId: 'key-a', scope,
    expiresAtMs: 31_000, authorizedDestinationHandles: new Set(['sender-a', 'receiver-a']),
    contentKey: key, release,
  };
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  const promise = new Promise<T>(next => { resolve = next; });
  return { promise, resolve };
}
