import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  SFU_ROOM_FACTORY,
  type SfuPublishedTrack,
  type SfuRemotePublication,
  type SfuRemoteTrack,
  type SfuRoomFactory,
  type SfuRoomPort,
} from './livekit-sfu-room.adapter';
import { LivekitSfuTransportService, type SfuClientAdmission } from './livekit-sfu-transport.service';

class FakeRoom implements SfuRoomPort {
  e2eeSupported = true;
  connect = vi.fn(async () => undefined);
  disconnect = vi.fn(async () => undefined);
  rotateKey = vi.fn(async () => undefined);
  denySubscriptionsByDefault = vi.fn();
  setTrackAudience = vi.fn();
  applyRemoteSubscriptions = vi.fn();
  remoteCallback: ((publication: SfuRemotePublication) => void) | null = null;
  disconnectCallback: (() => void) | null = null;
  localSubscribedCallback: ((publicationId: string) => void) | null = null;
  remoteTrackCallback: ((value: SfuRemoteTrack) => void) | null = null;
  participantDisconnectedCallback: ((participantId: string) => void) | null = null;
  private index = 0;
  async publish(publicationId: string, _source: 'microphone' | 'camera' | 'screen', track: MediaStreamTrack) {
    this.index += 1; return { publicationId, trackSid: `TR_${this.index}`, track };
  }
  async unpublish(_publication: SfuPublishedTrack): Promise<void> {}
  async publishOpaqueData(
    _payload: Uint8Array,
    _topic: string,
    _destinationIds: readonly string[],
  ): Promise<void> {}
  onRemotePublication(callback: (publication: SfuRemotePublication) => void): () => void {
    this.remoteCallback = callback; return () => { this.remoteCallback = null; };
  }
  onLocalTrackSubscribed(callback: (publicationId: string) => void): () => void {
    this.localSubscribedCallback = callback; return () => { this.localSubscribedCallback = null; };
  }
  onRemoteTrackSubscribed(callback: (value: SfuRemoteTrack) => void): () => void {
    this.remoteTrackCallback = callback; return () => { this.remoteTrackCallback = null; };
  }
  onRemoteParticipantDisconnected(callback: (participantId: string) => void): () => void {
    this.participantDisconnectedCallback = callback; return () => { this.participantDisconnectedCallback = null; };
  }
  onOpaqueDataReceived(): () => void { return () => undefined; }
  onDisconnected(callback: () => void): () => void {
    this.disconnectCallback = callback; return () => { this.disconnectCallback = null; };
  }
}

const track = (kind: 'audio' | 'video') => ({ kind, enabled: true, stop: vi.fn(), onended: null } as unknown as MediaStreamTrack);
const admission = (expiresAt = 1_045): SfuClientAdmission => ({
  server_url: 'wss://sfu.example.test', access_token: 'jwt', room_id: 'sfu-0123456789abcdef0123456789abcdef',
  membership_epoch: 7, expires_at: expiresAt, strict_e2ee: true,
  publications: [{
    publication_id: 'camera-alice', source: 'camera', kind: 'video', privacy: 'ordinary',
    authorized_subscriber_ids: ['bob', 'carol'],
  }, {
    publication_id: 'private-a-b', source: 'camera', kind: 'video', privacy: 'private_recovery',
    authorized_subscriber_ids: ['bob'],
  }],
  subscription_publication_ids: ['camera-bob'],
});

describe('LivekitSfuTransportService', () => {
  let room: FakeRoom; let service: LivekitSfuTransportService;
  beforeEach(() => {
    room = new FakeRoom();
    const factory: SfuRoomFactory = { create: vi.fn(async () => room) };
    TestBed.configureTestingModule({
      providers: [LivekitSfuTransportService, { provide: SFU_ROOM_FACTORY, useValue: factory }],
    });
    service = TestBed.inject(LivekitSfuTransportService);
  });

  it('uses one room, default-deny subscriptions and explicit remote publication IDs', async () => {
    expect(await service.connect(admission(), new Uint8Array(32), 'supported', 1_000)).toBe('sfu');
    expect(room.denySubscriptionsByDefault).toHaveBeenCalledOnce();
    expect(room.applyRemoteSubscriptions).toHaveBeenCalledWith(new Set(['camera-bob']));
    const allowed = { publicationId: 'camera-bob', publisherId: 'bob', setSubscribed: vi.fn() };
    const denied = { publicationId: 'camera-eve', publisherId: 'eve', setSubscribed: vi.fn() };
    room.remoteCallback?.(allowed); room.remoteCallback?.(denied);
    expect(allowed.setSubscribed).toHaveBeenCalledWith(true);
    expect(denied.setSubscribed).toHaveBeenCalledWith(false);
  });

  it('forwards only tracks present in the Hub subscription grant', async () => {
    await service.connect(admission(), new Uint8Array(32), 'supported', 1_000);
    const received: SfuRemoteTrack[] = [];
    service.remoteTrack$.subscribe(value => received.push(value));
    room.remoteTrackCallback?.({ publicationId: 'camera-eve', publisherId: 'eve', track: track('video') });
    room.remoteTrackCallback?.({ publicationId: 'camera-bob', publisherId: 'bob', track: track('video') });
    expect(received).toHaveLength(1);
    expect(received[0].publicationId).toBe('camera-bob');
  });

  it('publishes a common track once and applies receiver IDs as SFU permissions', async () => {
    await service.connect(admission(), new Uint8Array(32), 'supported', 1_000);
    const camera = track('video');
    await service.publish('camera-alice', camera);
    const audience = room.setTrackAudience.mock.calls.at(-1)?.[0] as Map<string, readonly string[]>;
    expect(audience.get('TR_1')).toEqual(['bob', 'carol']);
    expect(service.state$.value.publicationCount).toBe(1);
    expect(service.authorizedSubscriberIds()).toEqual(new Set());
    room.localSubscribedCallback?.('camera-alice');
    // A two-receiver audience cannot be projected from LiveKit's anonymous
    // first-subscriber event.
    expect(service.authorizedSubscriberIds()).toEqual(new Set());
  });

  it('confirms a singleton receiver only after LiveKit reports a real subscription', async () => {
    const pairAdmission: SfuClientAdmission = {
      ...admission(), publications: [{
        publication_id: 'microphone-alice', source: 'microphone', kind: 'audio', privacy: 'ordinary',
        authorized_subscriber_ids: ['bob'],
      }],
    };
    await service.connect(pairAdmission, new Uint8Array(32), 'supported', 1_000);
    await service.publish('microphone-alice', track('audio'));
    const confirmation = service.waitForSubscriber('microphone-alice', 'bob', 1_000);
    room.localSubscribedCallback?.('microphone-alice');
    await expect(confirmation).resolves.toBe(true);
    expect(service.authorizedSubscriberIds()).toEqual(new Set(['bob']));
    const lost: string[] = [];
    service.subscriberLost$.subscribe(value => lost.push(value.receiverId));
    room.participantDisconnectedCallback?.('bob');
    expect(service.authorizedSubscriberIds()).toEqual(new Set());
    expect(lost).toEqual(['bob']);
  });

  it('never routes private recovery through the common SFU publication', async () => {
    await service.connect(admission(), new Uint8Array(32), 'supported', 1_000);
    await expect(service.publish('private-a-b', track('video'))).rejects.toThrow('sfu_publication_not_authorized');
  });

  it.each(['unknown', 'unsupported'] as const)('falls back for %s capability without creating a room', async capability => {
    expect(await service.connect(admission(), new Uint8Array(32), capability, 1_000)).toBe('ordinary');
    expect(room.connect).not.toHaveBeenCalled();
    expect(service.state$.value.reasonCode).toBe('sfu_e2ee_capability_unknown');
  });

  it('stops local media and falls back on disconnect', async () => {
    await service.connect(admission(), new Uint8Array(32), 'supported', 1_000);
    const camera = track('video'); await service.publish('camera-alice', camera);
    room.disconnectCallback?.();
    expect(camera.stop).toHaveBeenCalledOnce();
    expect(service.state$.value.status).toBe('fallback');
  });

  it('pauses tracks and requires a fresh admission after key rotation', async () => {
    await service.connect(admission(), new Uint8Array(32), 'supported', 1_000);
    const camera = track('video'); await service.publish('camera-alice', camera);
    await service.rotateKey(8, Uint8Array.from({ length: 32 }, () => 1));
    expect(camera.enabled).toBe(false);
    expect(room.rotateKey).toHaveBeenCalledOnce();
    expect(service.state$.value.status).toBe('idle');
  });
});
