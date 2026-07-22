import { describe, expect, it, vi } from 'vitest';

import facadeSource from '../features/voice/semantic-media-program.facade.ts?raw';
import transportSource from './livekit-sfu-transport.service.ts?raw';
import factorySource from './sfu-room-session.factory.ts?raw';
import {
  LivekitSfuRoomFactory,
  legacySfuRoomFacade,
} from './sfu-room-session.factory';
import type {
  SfuPublishedTrack,
  SfuRemotePublication,
  SfuRoomSession,
  SfuStatsPort,
} from './sfu-room-session.ports';

describe('SfuRoomSession compatibility boundary', () => {
  it('delegates the legacy surface to the same focused port instances', async () => {
    const { session, spies, publication } = fakeSession();
    const legacy = legacySfuRoomFacade(session);
    const track = publication.track;

    await legacy.connect('wss://sfu.test', 'token');
    await legacy.rotateKey(new Uint8Array(32));
    expect(await legacy.publish('camera-a', 'camera', track)).toBe(publication);
    await legacy.unpublish(publication);
    await legacy.publishOpaqueData(new Uint8Array([1]), 'ananta.control.v1', ['bob']);
    legacy.denySubscriptionsByDefault();
    legacy.setTrackAudience(new Map([['TR_1', ['bob']]]));
    legacy.applyRemoteSubscriptions(new Set(['camera-b']));

    const callback = vi.fn();
    const release = legacy.onRemotePublication(callback);
    const eventCallback = spies.onRemotePublication.mock.calls[0][0];
    eventCallback({ publicationId: 'camera-b', publisherId: 'bob' });
    callback.mock.calls[0][0].setSubscribed(true);
    release();
    release();

    await legacy.disconnect();
    await legacy.destroy();

    expect(spies.connect).toHaveBeenCalledOnce();
    expect(spies.rotateKey).toHaveBeenCalledOnce();
    expect(spies.publish).toHaveBeenCalledOnce();
    expect(spies.unpublish).toHaveBeenCalledOnce();
    expect(spies.publishOpaqueData).toHaveBeenCalledOnce();
    expect(spies.setRemotePublicationSubscribed).toHaveBeenCalledWith('camera-b', true);
    expect(spies.eventRelease).toHaveBeenCalledOnce();
    expect(spies.disconnect).toHaveBeenCalledOnce();
    expect(spies.destroy).toHaveBeenCalledOnce();
    expect(legacySfuRoomFacade(session)).toBe(legacy);
  });

  it('keeps the old factory as a state-free adapter over one created session', async () => {
    const { session } = fakeSession();
    const sessions = { create: vi.fn(async () => session) };
    const factory = new LivekitSfuRoomFactory(sessions);

    const legacy = await factory.create(new Uint8Array(32));

    expect(sessions.create).toHaveBeenCalledOnce();
    expect(legacy).toBe(legacySfuRoomFacade(session));
  });

  it.each(['available', 'unsupported'] as const)(
    'accepts a structural %s stats fake without fabricating RTC reports',
    capability => {
      const fake: SfuStatsPort = Object.freeze({ capability });
      const consumer = (port: SfuStatsPort) => port.capability;
      expect(consumer(fake)).toBe(capability);
      expect(Object.keys(fake)).toEqual(['capability']);
    },
  );

  it('guards migrated consumers from the deprecated catch-all import', () => {
    for (const source of [transportSource, facadeSource]) {
      expect(source).not.toMatch(/\bSfuRoomPort\b/);
      expect(source).not.toMatch(/\bSFU_ROOM_FACTORY\b/);
      expect(source).not.toMatch(/\bLivekitSfuRoomFactory\b/);
      expect(source).not.toMatch(/\b(Room|RemoteTrackPublication|RTCPeerConnection|RTCRtpSender)\b/);
    }
    expect(factorySource).toContain('@deprecated');
    expect(factorySource).toContain('LegacySfuRoomFacade');
  });
});

function fakeSession() {
  const eventRelease = vi.fn();
  const publication: SfuPublishedTrack = {
    publicationId: 'camera-a', trackSid: 'TR_1', track: fakeTrack(),
  };
  const spies = {
    connect: vi.fn(async () => undefined),
    disconnect: vi.fn(async () => undefined),
    destroy: vi.fn(async () => undefined),
    rotateKey: vi.fn(async () => undefined),
    publish: vi.fn(async () => publication),
    unpublish: vi.fn(async () => undefined),
    publishOpaqueData: vi.fn(async () => undefined),
    denySubscriptionsByDefault: vi.fn(),
    setTrackAudience: vi.fn(),
    applyRemoteSubscriptions: vi.fn(),
    setRemotePublicationSubscribed: vi.fn(),
    attachRemoteTrack: vi.fn(() => vi.fn()),
    clear: vi.fn(),
    onRemotePublication: vi.fn((_callback: (value: SfuRemotePublication) => void) => eventRelease),
    eventRelease,
  };
  const noEvent = vi.fn(() => vi.fn());
  const session: SfuRoomSession = Object.freeze({
    lifecycle: Object.freeze({
      e2eeSupported: true,
      connect: spies.connect,
      disconnect: spies.disconnect,
      destroy: spies.destroy,
    }),
    key: Object.freeze({ rotateKey: spies.rotateKey }),
    publications: Object.freeze({
      publish: spies.publish,
      unpublish: spies.unpublish,
      denySubscriptionsByDefault: spies.denySubscriptionsByDefault,
      setTrackAudience: spies.setTrackAudience,
    }),
    data: Object.freeze({ publishOpaqueData: spies.publishOpaqueData }),
    subscriptions: Object.freeze({
      applyRemoteSubscriptions: spies.applyRemoteSubscriptions,
      setRemotePublicationSubscribed: spies.setRemotePublicationSubscribed,
    }),
    stats: Object.freeze({ capability: 'available' as const }),
    videoRender: Object.freeze({ attachRemoteTrack: spies.attachRemoteTrack, clear: spies.clear }),
    events: Object.freeze({
      onRemotePublication: spies.onRemotePublication,
      onRemoteTrackSubscribed: noEvent,
      onLocalTrackSubscribed: noEvent,
      onRemoteParticipantDisconnected: noEvent,
      onOpaqueDataReceived: noEvent,
      onDisconnected: noEvent,
    }),
  });
  return { session, spies, publication };
}

function fakeTrack(): MediaStreamTrack {
  return { kind: 'video', stop: vi.fn() } as unknown as MediaStreamTrack;
}
