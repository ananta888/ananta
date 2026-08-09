import { TestBed } from '@angular/core/testing';

import {
  PAIR_MEDIA_E2EE_WORKER_FACTORY,
  PairMediaE2eeTransformAdapter,
  PublicPairMediaSlotKeySet,
} from './pair-media-e2ee-transform.adapter';
import {
  PUBLIC_PAIR_MEDIA_SLOTS,
  PublicPairMediaSecurityContractV1,
} from './public-pair-media-security-contract';

class FakeWorker {
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: ErrorEvent) => void) | null = null;
  onmessageerror: ((event: MessageEvent) => void) | null = null;
  readonly messages: unknown[] = [];
  terminated = false;
  acknowledgeInstall = true;

  postMessage(message: any): void {
    this.messages.push(message);
    if (message?.type === 'install-keys' && this.acknowledgeInstall) {
      queueMicrotask(() => this.emit({
        version: 1,
        type: 'keys-installed',
        sessionId: message.sessionId,
        transformIds: message.entries.map((entry: any) => entry.transformId),
      }));
    }
  }

  terminate(): void { this.terminated = true; }

  emit(data: unknown): void {
    this.onmessage?.({ data } as MessageEvent);
  }
}

class FakeScriptTransform {
  constructor(worker: FakeWorker, options: { transformId: string }) {
    queueMicrotask(() => worker.emit({
      version: 1, type: 'transform-ready', transformId: options.transformId,
    }));
  }
}

interface FakeTransceiver {
  mid: string | null;
  direction: RTCRtpTransceiverDirection;
  currentDirection: RTCRtpTransceiverDirection | null;
  sender: RTCRtpSender & { transform?: unknown };
  receiver: RTCRtpReceiver & { transform?: unknown };
  setCodecPreferences: ReturnType<typeof vi.fn>;
}

class FakePeerConnection {
  readonly transceivers: FakeTransceiver[] = [];
  addTransceiverCalls = 0;

  addTransceiver(kind: string, init: RTCRtpTransceiverInit): RTCRtpTransceiver {
    this.addTransceiverCalls += 1;
    const index = this.transceivers.length;
    const value: FakeTransceiver = {
      mid: String(index),
      direction: init.direction ?? 'sendrecv',
      currentDirection: 'sendrecv',
      sender: { replaceTrack: vi.fn(async () => undefined), transform: null } as unknown as FakeTransceiver['sender'],
      receiver: { transform: null } as unknown as FakeTransceiver['receiver'],
      setCodecPreferences: vi.fn(),
    };
    expect(kind).toBe(index === 0 ? 'audio' : 'video');
    this.transceivers.push(value);
    return value as unknown as RTCRtpTransceiver;
  }

  getTransceivers(): RTCRtpTransceiver[] {
    return this.transceivers as unknown as RTCRtpTransceiver[];
  }

  receiveExactMediaOffer(): void {
    for (const [index, definition] of PUBLIC_PAIR_MEDIA_SLOTS.entries()) {
      this.transceivers.push({
        mid: String(index), direction: 'recvonly', currentDirection: null,
        sender: {
          replaceTrack: vi.fn(async () => undefined), transform: null,
        } as unknown as FakeTransceiver['sender'],
        receiver: {
          track: { kind: definition.kind }, transform: null,
        } as unknown as FakeTransceiver['receiver'],
        setCodecPreferences: vi.fn(),
      });
    }
  }
}

describe('PairMediaE2eeTransformAdapter', () => {
  const runtime = globalThis as typeof globalThis & {
    RTCRtpScriptTransform: typeof RTCRtpScriptTransform;
    RTCRtpSender: typeof RTCRtpSender;
  };
  const originalTransform = runtime.RTCRtpScriptTransform;
  const originalSender = runtime.RTCRtpSender;
  let workers: FakeWorker[];
  let adapter: PairMediaE2eeTransformAdapter;

  beforeAll(() => {
    runtime.RTCRtpScriptTransform = FakeScriptTransform as unknown as typeof RTCRtpScriptTransform;
    runtime.RTCRtpSender = class {
      static getCapabilities(kind: string): RTCRtpCapabilities {
        return {
          codecs: [{
            mimeType: kind === 'audio' ? 'audio/opus' : 'video/VP8',
            clockRate: kind === 'audio' ? 48_000 : 90_000,
            ...(kind === 'audio' ? { channels: 2 } : {}),
          }],
          headerExtensions: [],
        };
      }
    } as unknown as typeof RTCRtpSender;
  });

  afterAll(() => {
    runtime.RTCRtpScriptTransform = originalTransform;
    runtime.RTCRtpSender = originalSender;
  });

  beforeEach(() => {
    workers = [];
    TestBed.configureTestingModule({ providers: [
      PairMediaE2eeTransformAdapter,
      {
        provide: PAIR_MEDIA_E2EE_WORKER_FACTORY,
        useValue: () => {
          const worker = new FakeWorker();
          workers.push(worker);
          return worker as unknown as Worker;
        },
      },
    ] });
    adapter = TestBed.inject(PairMediaE2eeTransformAdapter);
  });

  afterEach(() => {
    adapter.releaseSession();
    vi.useRealTimers();
    TestBed.resetTestingModule();
  });

  it('prepares exactly three fixed sendrecv Opus/VP8 slots and validates bilateral topology', async () => {
    const peer = new FakePeerConnection();
    const generation = await adapter.prepareSession(
      peer as unknown as RTCPeerConnection, 'session-a', contract(), vi.fn(),
    );

    expect(generation).toBeGreaterThan(0);
    expect(peer.transceivers).toHaveLength(3);
    expect(peer.transceivers.map(item => item.direction)).toEqual(['sendrecv', 'sendrecv', 'sendrecv']);
    expect(peer.transceivers.map(item => item.setCodecPreferences.mock.calls[0][0][0].mimeType))
      .toEqual(['audio/opus', 'video/VP8', 'video/VP8']);
    for (const [index, definition] of PUBLIC_PAIR_MEDIA_SLOTS.entries()) {
      expect(adapter.senderForSlot('session-a', definition.slot)).toBe(peer.transceivers[index].sender);
      expect(adapter.slotForReceiver('session-a', peer.transceivers[index].receiver))
        .toBe(definition.slot);
    }
    expect(() => adapter.validateFinalTopology('session-a')).not.toThrow();

    peer.transceivers[2].currentDirection = 'sendonly';
    expect(() => adapter.validateFinalTopology('session-a')).toThrow('public_media_topology_invalid');
  });

  it('stays unkeyed until the exact worker ACK and terminates all slots when the ACK is lost', async () => {
    vi.useFakeTimers();
    const fatal = vi.fn();
    const generation = await adapter.prepareSession(
      new FakePeerConnection() as unknown as RTCPeerConnection, 'session-a', contract(), fatal,
    );
    workers[0].acknowledgeInstall = false;
    const installing = adapter.installKeys('session-a', await keySets(), generation);
    const rejectedInstall = expect(installing).rejects.toThrow('media_e2ee_worker_ack_timeout');

    expect(adapter.isKeyed('session-a')).toBe(false);
    await vi.advanceTimersByTimeAsync(5_000);
    await rejectedInstall;
    expect(workers[0].terminated).toBe(true);
    expect(adapter.isPrepared('session-a')).toBe(false);
    expect(fatal).toHaveBeenCalledWith('media_e2ee_worker_ack_timeout');
  });

  it('does not precreate answerer slots and DROP-binds the exact remote-offer transceivers', async () => {
    const peer = new FakePeerConnection();
    const generation = await adapter.prepareSession(
      peer as unknown as RTCPeerConnection,
      'session-a',
      contract(),
      vi.fn(),
      'answerer',
    );
    expect(peer.addTransceiverCalls).toBe(0);
    expect(adapter.isAwaitingRemoteTopology('session-a', generation)).toBe(true);

    peer.receiveExactMediaOffer();
    const stagedSlot = adapter.stageRemoteOfferTrack(
      'session-a',
      peer.transceivers[0] as unknown as RTCRtpTransceiver,
      peer.transceivers[0].receiver,
      generation,
    );
    expect(stagedSlot).toBe('microphone-opus');
    expect(peer.transceivers[0].sender.transform).toBeInstanceOf(FakeScriptTransform);
    expect(peer.transceivers[0].receiver.transform).toBeInstanceOf(FakeScriptTransform);
    await adapter.bindRemoteOfferTopology('session-a', generation);

    expect(peer.addTransceiverCalls).toBe(0);
    expect(peer.transceivers.map(value => value.direction))
      .toEqual(['sendrecv', 'sendrecv', 'sendrecv']);
    expect(adapter.isAwaitingRemoteTopology('session-a', generation)).toBe(false);
    for (const [index, definition] of PUBLIC_PAIR_MEDIA_SLOTS.entries()) {
      expect(adapter.senderForSlot('session-a', definition.slot)).toBe(peer.transceivers[index].sender);
      expect(adapter.slotForReceiver('session-a', peer.transceivers[index].receiver))
        .toBe(definition.slot);
    }
  });

  it('sets every offered answerer transceiver inactive on partial topology fallback', async () => {
    const peer = new FakePeerConnection();
    const generation = await adapter.prepareSession(
      peer as unknown as RTCPeerConnection, 'session-a', contract(), vi.fn(), 'answerer',
    );
    peer.receiveExactMediaOffer();
    (peer.transceivers[2].receiver.track as { kind: string }).kind = 'audio';

    await expect(adapter.bindRemoteOfferTopology('session-a', generation))
      .rejects.toThrow('public_media_topology_invalid');
    adapter.releaseSession('session-a', generation);

    expect(peer.transceivers.map(value => value.direction))
      .toEqual(['inactive', 'inactive', 'inactive']);
    for (const transceiver of peer.transceivers) {
      expect(transceiver.sender.replaceTrack).toHaveBeenCalledWith(null);
    }
  });

  it('drops unexpected extra transceivers as well as contracted offerer slots on release', async () => {
    const peer = new FakePeerConnection();
    const generation = await adapter.prepareSession(
      peer as unknown as RTCPeerConnection, 'session-a', contract(), vi.fn(), 'offerer',
    );
    peer.receiveExactMediaOffer();

    adapter.releaseSession('session-a', generation);

    expect(peer.transceivers).toHaveLength(6);
    expect(peer.transceivers.every(value => value.direction === 'inactive')).toBe(true);
    expect(peer.transceivers.every(value => {
      const replaceTrack = value.sender.replaceTrack as ReturnType<typeof vi.fn>;
      return replaceTrack.mock.calls.some(([track]) => track === null);
    })).toBe(true);
  });

  it('allows one key installation only and globally terminates on a single transform fatal', async () => {
    const fatal = vi.fn();
    const generation = await adapter.prepareSession(
      new FakePeerConnection() as unknown as RTCPeerConnection, 'session-a', contract(), fatal,
    );
    const keys = await keySets();
    await adapter.installKeys('session-a', keys, generation);
    expect(adapter.isKeyed('session-a')).toBe(true);
    await expect(adapter.installKeys('session-a', keys, generation))
      .rejects.toThrow('media_e2ee_key_reinstall_forbidden');

    workers[0].emit({
      version: 1, type: 'fatal', transformId: 'session-a:camera-vp8:receive',
      reasonCode: 'media_e2ee_authentication_failed',
    });
    expect(workers[0].terminated).toBe(true);
    expect(adapter.isPrepared('session-a')).toBe(false);
    expect(fatal).toHaveBeenCalledWith('media_e2ee_authentication_failed');
    expect(() => adapter.senderForSlot('session-a', 'camera-vp8'))
      .toThrow('public_media_transform_not_prepared');
  });
});

function contract(): PublicPairMediaSecurityContractV1 {
  return {
    session_id: 'session-a', epoch: 7, digest: 'a'.repeat(64),
    expires_at_ms: 2_000_000_000_000, transform: 'RTCRtpScriptTransform',
  } as PublicPairMediaSecurityContractV1;
}

async function keySets(): Promise<readonly PublicPairMediaSlotKeySet[]> {
  const connectionId = 'b'.repeat(64);
  const values: PublicPairMediaSlotKeySet[] = [];
  for (const definition of PUBLIC_PAIR_MEDIA_SLOTS) {
    const [sendKey, receiveKey] = await Promise.all([key(), key()]);
    values.push({
      slot: definition.slot,
      sendKey,
      receiveKey,
      sendContext: {
        sessionId: 'session-a', mediaContractDigest: 'a'.repeat(64), connectionId,
        senderId: 'peer:local', recipientId: 'peer:remote', slot: definition.slot,
        kind: definition.kind, codec: definition.codec, keyEpoch: 7,
        contractExpiresAtMs: 2_000_000_000_000,
      },
      receiveContext: {
        sessionId: 'session-a', mediaContractDigest: 'a'.repeat(64), connectionId,
        senderId: 'peer:remote', recipientId: 'peer:local', slot: definition.slot,
        kind: definition.kind, codec: definition.codec, keyEpoch: 7,
        contractExpiresAtMs: 2_000_000_000_000,
      },
    });
  }
  return values;
}

function key(): Promise<CryptoKey> {
  return crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
}
