import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { SFU_ROOM_SESSION_FACTORY } from './sfu-room-session.factory';
import { SfuBroadcastPublicationControllerService } from './sfu-broadcast-publication-controller.service';
import type {
  SfuOpaqueDataPacket,
  SfuPublishedTrack,
  SfuRemotePublication,
  SfuRemoteTrack,
  SfuRoomSession,
  SfuRoomSessionFactory,
} from './sfu-room-session.ports';
import { LivekitSfuTransportService, type SfuClientAdmission } from './livekit-sfu-transport.service';

class FakeSession implements SfuRoomSession {
  readonly cleanupOrder: string[] = [];
  readonly releaseFailures = new Set<string>();
  readonly connect = vi.fn(async () => undefined);
  readonly destroy = vi.fn(async () => { this.cleanupOrder.push('destroy'); });
  readonly rotateKey = vi.fn(async () => undefined);
  readonly denySubscriptionsByDefault = vi.fn();
  readonly setTrackAudience = vi.fn();
  readonly applyRemoteSubscriptions = vi.fn();
  readonly setRemotePublicationSubscribed = vi.fn();
  readonly publishOpaqueData = vi.fn(async () => undefined);
  readonly clearVideo = vi.fn();
  remoteCallback: ((publication: SfuRemotePublication) => void) | null = null;
  disconnectCallback: (() => void) | null = null;
  localSubscribedCallback: ((publicationId: string) => void) | null = null;
  remoteTrackCallback: ((value: SfuRemoteTrack) => void) | null = null;
  participantDisconnectedCallback: ((participantId: string) => void) | null = null;
  dataCallback: ((packet: SfuOpaqueDataPacket) => void) | null = null;
  private index = 0;

  readonly lifecycle = Object.freeze({
    e2eeSupported: true,
    connect: this.connect,
    disconnect: this.destroy,
    destroy: this.destroy,
  });
  readonly key = Object.freeze({ rotateKey: this.rotateKey });
  readonly publications = Object.freeze({
    publish: async (
      publicationId: string,
      _source: 'microphone' | 'camera' | 'screen',
      track: MediaStreamTrack,
    ): Promise<SfuPublishedTrack> => {
      this.index += 1;
      return { publicationId, trackSid: `TR_${this.index}`, track };
    },
    unpublish: async (_publication: SfuPublishedTrack): Promise<void> => undefined,
    denySubscriptionsByDefault: this.denySubscriptionsByDefault,
    setTrackAudience: this.setTrackAudience,
  });
  readonly data = Object.freeze({ publishOpaqueData: this.publishOpaqueData });
  readonly subscriptions = Object.freeze({
    applyRemoteSubscriptions: this.applyRemoteSubscriptions,
    setRemotePublicationSubscribed: this.setRemotePublicationSubscribed,
  });
  readonly stats = Object.freeze({ capability: 'unsupported' as const });
  readonly videoRender = Object.freeze({
    attachRemoteTrack: () => vi.fn(),
    clear: this.clearVideo,
  });
  readonly events = Object.freeze({
    onRemotePublication: (callback: (publication: SfuRemotePublication) => void) => {
      this.remoteCallback = callback;
      return this.release('remote-publication', () => { this.remoteCallback = null; });
    },
    onRemoteTrackSubscribed: (callback: (value: SfuRemoteTrack) => void) => {
      this.remoteTrackCallback = callback;
      return this.release('remote-track', () => { this.remoteTrackCallback = null; });
    },
    onLocalTrackSubscribed: (callback: (publicationId: string) => void) => {
      this.localSubscribedCallback = callback;
      return this.release('local-track', () => { this.localSubscribedCallback = null; });
    },
    onRemoteParticipantDisconnected: (callback: (participantId: string) => void) => {
      this.participantDisconnectedCallback = callback;
      return this.release('participant', () => { this.participantDisconnectedCallback = null; });
    },
    onOpaqueDataReceived: (callback: (packet: SfuOpaqueDataPacket) => void) => {
      this.dataCallback = callback;
      return this.release('data', () => { this.dataCallback = null; });
    },
    onDisconnected: (callback: () => void) => {
      this.disconnectCallback = callback;
      return this.release('disconnect', () => { this.disconnectCallback = null; });
    },
  });

  private release(label: string, clear: () => void): () => void {
    let active = true;
    return () => {
      if (!active) return;
      active = false;
      clear();
      this.cleanupOrder.push(`release:${label}`);
      if (this.releaseFailures.has(label)) throw new Error(`release-${label}-failed`);
    };
  }
}

const track = (kind: 'audio' | 'video') => ({
  kind, enabled: true, stop: vi.fn(), onended: null,
} as unknown as MediaStreamTrack);

const admission = (expiresAt = 1_045): SfuClientAdmission => ({
  server_url: 'wss://sfu.example.test',
  access_token: 'jwt',
  room_id: 'sfu-0123456789abcdef0123456789abcdef',
  membership_epoch: 7,
  expires_at: expiresAt,
  strict_e2ee: true,
  publications: [{
    publication_id: 'camera-alice',
    source: 'camera',
    kind: 'video',
    privacy: 'ordinary',
    authorized_subscriber_ids: ['bob', 'carol'],
  }, {
    publication_id: 'private-a-b',
    source: 'camera',
    kind: 'video',
    privacy: 'private_recovery',
    authorized_subscriber_ids: ['bob'],
  }],
  subscription_publication_ids: ['camera-bob'],
});

describe('LivekitSfuTransportService', () => {
  let session: FakeSession;
  let service: LivekitSfuTransportService;
  let factory: SfuRoomSessionFactory;
  let projectionPublications: {
    apply: ReturnType<typeof vi.fn>; stop: ReturnType<typeof vi.fn>; stopAll: ReturnType<typeof vi.fn>;
  };

  beforeEach(() => {
    session = new FakeSession();
    factory = { create: vi.fn(async () => session) };
    projectionPublications = {
      apply: vi.fn(async (_port: unknown, contract: any, mediaTrack: MediaStreamTrack) => ({
        publicationId: contract.document.publication_ref, trackSid: 'TR_PROJECTED', track: mediaTrack,
      })),
      stop: vi.fn(async () => false),
      stopAll: vi.fn(async () => undefined),
    };
    TestBed.configureTestingModule({
      providers: [
        LivekitSfuTransportService,
        { provide: SFU_ROOM_SESSION_FACTORY, useValue: factory },
        { provide: SfuBroadcastPublicationControllerService, useValue: projectionPublications },
      ],
    });
    service = TestBed.inject(LivekitSfuTransportService);
  });

  it('owns one session, default-denies and applies explicit remote publication IDs', async () => {
    expect(await service.connect(admission(), new Uint8Array(32), 'supported', 1_000)).toBe('sfu');
    expect(factory.create).toHaveBeenCalledOnce();
    expect(session.denySubscriptionsByDefault).toHaveBeenCalledOnce();
    expect(session.applyRemoteSubscriptions).toHaveBeenCalledWith(new Set(['camera-bob']));
    session.remoteCallback?.({ publicationId: 'camera-bob', publisherId: 'bob' });
    session.remoteCallback?.({ publicationId: 'camera-eve', publisherId: 'eve' });
    expect(session.setRemotePublicationSubscribed).toHaveBeenNthCalledWith(1, 'camera-bob', true);
    expect(session.setRemotePublicationSubscribed).toHaveBeenNthCalledWith(2, 'camera-eve', false);
  });

  it('forwards only tracks and data received through their focused event ports', async () => {
    await service.connect(admission(), new Uint8Array(32), 'supported', 1_000);
    const received: SfuRemoteTrack[] = [];
    const packets: SfuOpaqueDataPacket[] = [];
    service.remoteTrack$.subscribe(value => received.push(value));
    service.opaqueData$.subscribe(value => packets.push(value));
    session.remoteTrackCallback?.({ publicationId: 'camera-eve', publisherId: 'eve', track: track('video') });
    session.remoteTrackCallback?.({ publicationId: 'camera-bob', publisherId: 'bob', track: track('video') });
    session.dataCallback?.({ senderId: 'bob', topic: 'ananta.control.v1', payload: new Uint8Array([1]) });
    await service.publishOpaqueData(new Uint8Array([2]), 'ananta.control.v1', ['bob']);

    expect(received).toHaveLength(1);
    expect(received[0].publicationId).toBe('camera-bob');
    expect(packets).toHaveLength(1);
    expect(session.publishOpaqueData).toHaveBeenCalledOnce();
  });

  it('publishes a common track once and applies receiver IDs as SFU permissions', async () => {
    await service.connect(admission(), new Uint8Array(32), 'supported', 1_000);
    const camera = track('video');
    await service.publish('camera-alice', camera);
    const audience = session.setTrackAudience.mock.calls.at(-1)?.[0] as Map<string, readonly string[]>;
    expect(audience.get('TR_1')).toEqual(['bob', 'carol']);
    expect(service.state$.value.publicationCount).toBe(1);
    expect(service.authorizedSubscriberIds()).toEqual(new Set());
    session.localSubscribedCallback?.('camera-alice');
    expect(service.authorizedSubscriberIds()).toEqual(new Set());
  });

  it('indexes projected publications in the common audience and lifecycle state', async () => {
    await service.connect(admission(), new Uint8Array(32), 'supported', 1_000);
    await service.publishProjected({ document: { publication_ref: 'camera-alice' } } as never, track('video'));

    const audience = session.setTrackAudience.mock.calls.at(-1)?.[0] as Map<string, readonly string[]>;
    expect(audience.get('TR_PROJECTED')).toEqual(['bob', 'carol']);
    expect(service.state$.value.publicationCount).toBe(1);
  });

  it('confirms a singleton receiver only after a real subscription event', async () => {
    const pairAdmission: SfuClientAdmission = {
      ...admission(),
      publications: [{
        publication_id: 'microphone-alice',
        source: 'microphone',
        kind: 'audio',
        privacy: 'ordinary',
        authorized_subscriber_ids: ['bob'],
      }],
    };
    await service.connect(pairAdmission, new Uint8Array(32), 'supported', 1_000);
    await service.publish('microphone-alice', track('audio'));
    const confirmation = service.waitForSubscriber('microphone-alice', 'bob', 1_000);
    session.localSubscribedCallback?.('microphone-alice');
    await expect(confirmation).resolves.toBe(true);
    expect(service.authorizedSubscriberIds()).toEqual(new Set(['bob']));
    const lost: string[] = [];
    service.subscriberLost$.subscribe(value => lost.push(value.receiverId));
    session.participantDisconnectedCallback?.('bob');
    expect(service.authorizedSubscriberIds()).toEqual(new Set());
    expect(lost).toEqual(['bob']);
  });

  it('never routes private recovery through the common SFU publication', async () => {
    await service.connect(admission(), new Uint8Array(32), 'supported', 1_000);
    await expect(service.publish('private-a-b', track('video')))
      .rejects.toThrow('sfu_publication_not_authorized');
  });

  it.each(['unknown', 'unsupported'] as const)(
    'falls back for %s capability without creating a session',
    async capability => {
      expect(await service.connect(admission(), new Uint8Array(32), capability, 1_000)).toBe('ordinary');
      expect(factory.create).not.toHaveBeenCalled();
      expect(service.state$.value.reasonCode).toBe('sfu_e2ee_capability_unknown');
    },
  );

  it('stops local media and falls back on disconnect', async () => {
    await service.connect(admission(), new Uint8Array(32), 'supported', 1_000);
    const camera = track('video');
    await service.publish('camera-alice', camera);
    session.disconnectCallback?.();
    expect(camera.stop).toHaveBeenCalledOnce();
    expect(service.state$.value.status).toBe('fallback');
  });

  it('pauses tracks and requires a fresh admission after key rotation', async () => {
    await service.connect(admission(), new Uint8Array(32), 'supported', 1_000);
    const camera = track('video');
    await service.publish('camera-alice', camera);
    await service.rotateKey(8, Uint8Array.from({ length: 32 }, () => 1));
    expect(camera.enabled).toBe(false);
    expect(session.rotateKey).toHaveBeenCalledOnce();
    expect(service.state$.value.status).toBe('idle');
  });

  it('releases each listener before destroying the session and remains idempotent', async () => {
    await service.connect(admission(), new Uint8Array(32), 'supported', 1_000);
    await service.disconnect();
    await service.disconnect();

    const releases = session.cleanupOrder.filter(value => value.startsWith('release:'));
    expect(releases).toEqual([
      'release:disconnect',
      'release:data',
      'release:participant',
      'release:local-track',
      'release:remote-track',
      'release:remote-publication',
    ]);
    expect(session.cleanupOrder.at(-1)).toBe('destroy');
    expect(session.destroy).toHaveBeenCalledOnce();
  });

  it('expires an admission with generation-bound session cleanup', async () => {
    vi.useFakeTimers();
    try {
      await service.connect(admission(1_001), new Uint8Array(32), 'supported', 1_000);
      await vi.advanceTimersByTimeAsync(1_000);

      expect(session.destroy).toHaveBeenCalledOnce();
      expect(service.state$.value).toMatchObject({ status: 'idle', reasonCode: 'sfu_admission_expired' });
    } finally {
      vi.useRealTimers();
    }
  });

  it('does not let a slower connect replace a newer session', async () => {
    const older = new FakeSession();
    const newer = new FakeSession();
    const gate = deferred<void>();
    older.connect.mockImplementation(() => gate.promise);
    vi.mocked(factory.create)
      .mockReset()
      .mockResolvedValueOnce(older)
      .mockResolvedValueOnce(newer);

    const first = service.connect(admission(), new Uint8Array(32), 'supported', 1_000);
    await Promise.resolve();
    await Promise.resolve();
    const second = service.connect(admission(), new Uint8Array(32), 'supported', 1_000);
    await expect(second).resolves.toBe('sfu');
    gate.resolve();
    await expect(first).resolves.toBe('ordinary');

    expect(older.destroy).toHaveBeenCalledOnce();
    expect(newer.destroy).not.toHaveBeenCalled();
    expect(service.state$.value.status).toBe('connected');
  });

  it('disconnects fail-closed when key rotation fails', async () => {
    await service.connect(admission(), new Uint8Array(32), 'supported', 1_000);
    session.rotateKey.mockRejectedValueOnce(new Error('rotation failed'));

    await expect(service.rotateKey(8, new Uint8Array(32))).rejects.toThrow('sfu_rekey_failed');
    expect(session.destroy).toHaveBeenCalledOnce();
    expect(service.state$.value).toMatchObject({ status: 'idle', reasonCode: 'sfu_rekey_failed' });
  });

  it('continues session destruction when one listener release throws', async () => {
    await service.connect(admission(), new Uint8Array(32), 'supported', 1_000);
    session.releaseFailures.add('data');

    await expect(service.disconnect()).resolves.toBeUndefined();
    expect(session.destroy).toHaveBeenCalledOnce();
    expect(session.cleanupOrder).toContain('release:remote-publication');
  });
});

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  const promise = new Promise<T>(next => { resolve = next; });
  return { promise, resolve };
}
