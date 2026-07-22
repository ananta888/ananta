import { RoomEvent, type Room } from 'livekit-client';
import { describe, expect, it, vi } from 'vitest';

import { LivekitSfuRoomAdapter } from './livekit-sfu-room.adapter';

describe('LivekitSfuRoomAdapter', () => {
  it('maps public SDK behavior and releases listeners, publications, room and worker once in order', async () => {
    const log: string[] = [];
    const room = new FakeRoom(log);
    const keyProvider = { rotate: vi.fn(async () => undefined) };
    const worker = { terminate: vi.fn(() => log.push('worker')) };
    const adapter = new LivekitSfuRoomAdapter(
      room as unknown as Room,
      keyProvider,
      worker,
    );
    const session = adapter.createSession();
    const sameSession = adapter.createSession();
    const remotePublications: string[] = [];
    const packets: Uint8Array[] = [];
    session.events.onRemotePublication(value => remotePublications.push(value.publicationId));
    session.events.onOpaqueDataReceived(value => packets.push(value.payload));

    session.publications.denySubscriptionsByDefault();
    await session.lifecycle.connect('wss://sfu.test', 'token');
    await session.lifecycle.connect('wss://sfu.test', 'token');
    const localTrack = fakeTrack('video');
    const published = await session.publications.publish('camera-a', 'camera', localTrack);
    await session.data.publishOpaqueData(
      new Uint8Array([7]),
      'ananta.control.v1',
      ['carol', 'bob', 'bob'],
    );

    const remote = { trackName: 'camera-b', setSubscribed: vi.fn() };
    room.remoteParticipants.set('bob', {
      trackPublications: new Map([['TR_REMOTE', remote]]),
    });
    room.emit(RoomEvent.TrackPublished, remote, { identity: 'bob' });
    session.subscriptions.setRemotePublicationSubscribed('camera-b', true);
    session.subscriptions.applyRemoteSubscriptions(new Set(['camera-b']));
    const incoming = new Uint8Array([9]);
    room.emit(RoomEvent.DataReceived, incoming, { identity: 'bob' }, 0, 'ananta.control.v1');
    incoming[0] = 0;
    await session.key.rotateKey(new Uint8Array(32));

    await session.lifecycle.disconnect();
    await session.lifecycle.destroy();

    expect(sameSession).toBe(session);
    expect(session.stats.capability).toBe('unsupported');
    expect(room.connect).toHaveBeenCalledOnce();
    expect(room.setE2EEEnabled).toHaveBeenCalledWith(true);
    expect(published).toMatchObject({ publicationId: 'camera-a', trackSid: 'TR_1' });
    expect(room.localParticipant.publishData).toHaveBeenCalledWith(
      new Uint8Array([7]),
      expect.objectContaining({ destinationIdentities: ['bob', 'carol'] }),
    );
    expect(remotePublications).toEqual(['camera-b']);
    expect(remote.setSubscribed).toHaveBeenCalledWith(true);
    expect(packets[0]).toEqual(new Uint8Array([9]));
    expect(keyProvider.rotate).toHaveBeenCalledOnce();
    expect(room.listenerCount()).toBe(0);
    expect(room.localParticipant.unpublishTrack).toHaveBeenCalledOnce();
    expect(localTrack.stop).toHaveBeenCalledOnce();
    expect(room.disconnect).toHaveBeenCalledOnce();
    expect(worker.terminate).toHaveBeenCalledOnce();
    expect(log.indexOf('off')).toBeLessThan(log.indexOf('unpublish'));
    expect(log.indexOf('unpublish')).toBeLessThan(log.indexOf('disconnect'));
    expect(log.indexOf('disconnect')).toBeLessThan(log.indexOf('worker'));
  });

  it('rejects ungrounded data and post-destroy operations fail closed', async () => {
    const room = new FakeRoom([]);
    const adapter = new LivekitSfuRoomAdapter(
      room as unknown as Room,
      { rotate: vi.fn(async () => undefined) },
      { terminate: vi.fn() },
    );
    const session = adapter.createSession();
    await session.lifecycle.connect('wss://sfu.test', 'token');

    await expect(session.data.publishOpaqueData(
      new Uint8Array([1]), 'invented.topic', ['bob'],
    )).rejects.toThrow('sfu_data_topic_invalid');
    await session.lifecycle.destroy();
    await expect(session.publications.publish(
      'camera-a', 'camera', fakeTrack('video'),
    )).rejects.toThrow('sfu_session_destroyed');
  });
});

class FakeRoom {
  readonly remoteParticipants = new Map<string, { trackPublications: Map<string, any> }>();
  readonly connect = vi.fn(async () => undefined);
  readonly setE2EEEnabled = vi.fn(async () => undefined);
  readonly disconnect = vi.fn(async () => { this.log.push('disconnect'); });
  readonly localParticipant = {
    publishTrack: vi.fn(async (track: MediaStreamTrack, options: { name: string }) => ({
      trackSid: 'TR_1', track, trackName: options.name,
    })),
    unpublishTrack: vi.fn(async () => { this.log.push('unpublish'); }),
    publishData: vi.fn(async () => undefined),
    setTrackSubscriptionPermissions: vi.fn(),
  };
  private readonly listeners = new Map<RoomEvent, Set<(...args: any[]) => void>>();

  constructor(private readonly log: string[]) {}

  on(event: RoomEvent, callback: (...args: any[]) => void): void {
    const callbacks = this.listeners.get(event) ?? new Set();
    callbacks.add(callback);
    this.listeners.set(event, callbacks);
  }

  off(event: RoomEvent, callback: (...args: any[]) => void): void {
    this.listeners.get(event)?.delete(callback);
    this.log.push('off');
  }

  emit(event: RoomEvent, ...args: any[]): void {
    for (const callback of this.listeners.get(event) ?? []) callback(...args);
  }

  listenerCount(): number {
    return [...this.listeners.values()].reduce((total, callbacks) => total + callbacks.size, 0);
  }
}

function fakeTrack(kind: 'audio' | 'video'): MediaStreamTrack {
  return { kind, enabled: true, stop: vi.fn() } as unknown as MediaStreamTrack;
}
