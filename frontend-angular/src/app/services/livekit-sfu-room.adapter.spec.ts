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
    expect(session.stats.capability).toBe('available');
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

  it('maps validated publisher options and reports only SDK-observed publication state', async () => {
    const room = new FakeRoom([]);
    const adapter = new LivekitSfuRoomAdapter(
      room as unknown as Room,
      { rotate: vi.fn(async () => undefined) },
      { terminate: vi.fn() },
    );
    const session = adapter.createSession();
    await session.lifecycle.connect('wss://sfu.test', 'token');
    const track = fakeTrack('video');
    const published = await session.publications.publishProjected!({
      validation: 'hub-contract-accepted-v1', publicationId: 'camera-projected', source: 'camera',
      mediaKind: 'video', projectionVersion: 4, routeEpoch: 8, keyEpoch: 3,
      encodings: [{
        encodingClass: 'video_baseline', codecClass: 'video_vp8', ridClass: 'low',
        scalabilityClass: 'simulcast', maxBitrateBps: 150_000, maxWidth: 320, maxHeight: 180, maxFps: 15,
      }, {
        encodingClass: 'video_enhancement', codecClass: 'video_vp8', ridClass: 'high',
        scalabilityClass: 'simulcast', maxBitrateBps: 1_200_000, maxWidth: 1280, maxHeight: 720, maxFps: 30,
      }],
    }, track);

    expect(room.localParticipant.publishTrack).toHaveBeenLastCalledWith(track, expect.objectContaining({
      videoCodec: 'vp8', simulcast: true,
      videoEncoding: { maxBitrate: 1_200_000, maxFramerate: 30 },
    }));
    expect(published).toMatchObject({
      projectionVersion: 4, routeEpoch: 8, keyEpoch: 3,
      observation: { status: 'observed', codecClass: 'video_vp8', simulcasted: true },
    });
    await session.lifecycle.destroy();
  });

  it('keeps video tracks private while using public SDK attach, detach and stats methods', async () => {
    const room = new FakeRoom([]);
    const adapter = new LivekitSfuRoomAdapter(
      room as unknown as Room,
      { rotate: vi.fn(async () => undefined) },
      { terminate: vi.fn() },
    );
    const session = adapter.createSession();
    const handles: any[] = [];
    const unavailable: any[] = [];
    session.videoRender.onRemoteVideoAvailable!(value => handles.push(value));
    session.videoRender.onRemoteVideoUnavailable!(value => unavailable.push(value));
    await session.lifecycle.connect('wss://sfu.test', 'token');
    const remoteTrack = {
      kind: 'video', mediaStreamTrack: fakeTrack('video'), attach: vi.fn(), detach: vi.fn(),
      getRTCStatsReport: vi.fn(async () => statsReport([
        { id: 'in', type: 'inbound-rtp', bytesReceived: 1000, packetsReceived: 20,
          packetsLost: 1, framesDecoded: 5, jitter: .02, totalDecodeTime: .03,
          totalFreezesDuration: 0 },
        { id: 'pair', type: 'candidate-pair', state: 'succeeded', currentRoundTripTime: .05,
          localCandidateId: 'private-address' },
      ])),
    };
    room.emit(RoomEvent.TrackSubscribed, remoteTrack, {
      trackName: 'camera-b', source: 'camera',
    }, { identity: 'bob' });
    const target = document.createElement('video');
    const release = session.videoRender.attachRemoteVideo!(handles[0], target);
    const snapshot = await session.stats.read!(handles[0]);

    expect(handles[0]).toEqual({
      handleId: 'sfu-video-1', source: 'camera',
    });
    expect(handles[0]).not.toHaveProperty('track');
    expect(remoteTrack.attach).toHaveBeenCalledWith(target);
    expect(JSON.stringify(snapshot)).not.toMatch(/candidate|address|track|device|sdp/i);
    expect(snapshot).toMatchObject({ bytesReceived: 1000, roundTripTimeSeconds: .05 });
    release();
    release();
    expect(remoteTrack.detach).toHaveBeenCalledOnce();

    room.emit(RoomEvent.TrackUnsubscribed, remoteTrack, { trackName: 'camera-b' }, { identity: 'bob' });
    room.emit(RoomEvent.TrackSubscribed, remoteTrack, {
      trackName: 'camera-b', source: 'camera',
    }, { identity: 'bob' });
    expect(unavailable).toEqual([{ handleId: 'sfu-video-1', reason: 'unsubscribed' }]);
    expect(handles[1]).toMatchObject({ handleId: 'sfu-video-2', source: 'camera' });
    await session.lifecycle.destroy();
  });
});

class FakeRoom {
  readonly remoteParticipants = new Map<string, { trackPublications: Map<string, any> }>();
  readonly connect = vi.fn(async () => undefined);
  readonly setE2EEEnabled = vi.fn(async () => undefined);
  readonly disconnect = vi.fn(async () => { this.log.push('disconnect'); });
  readonly localParticipant = {
    publishTrack: vi.fn(async (track: MediaStreamTrack, options: { name: string }) => ({
      trackSid: 'TR_1',
      track: {
        mediaStreamTrack: track,
        getRTCStatsReport: vi.fn(async () => statsReport([{ id: 'out', type: 'outbound-rtp', bytesSent: 1, rid: 'f' }])),
        replaceTrack: vi.fn(async () => undefined),
        pauseUpstream: vi.fn(async () => undefined),
        resumeUpstream: vi.fn(async () => undefined),
      },
      trackName: options.name,
      mimeType: 'video/VP8',
      simulcasted: true,
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

function statsReport(rows: readonly Record<string, unknown>[]): RTCStatsReport {
  const values = new Map(rows.map(row => [String(row['id']), row as unknown as RTCStats]));
  return values as unknown as RTCStatsReport;
}
