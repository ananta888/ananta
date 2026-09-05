import { assertMediaFanoutPortSet, MediaFanoutUnsupportedError } from './media-fanout-contract';
import type { PeerLinkSession } from './peer-overlay/peer-media-fanout.ports';
import type { SfuRoomSession } from './sfu-room-session.ports';

describe('Media fanout focused-port contract', () => {
  it.each([
    ['peer', peerSession()],
    ['livekit', sfuSession()],
  ])('accepts the same focused boundaries for the %s adapter', (_name, session) => {
    expect(() => assertMediaFanoutPortSet(session)).not.toThrow();
  });

  it('fails closed when an adapter omits a capability', () => {
    expect(() => assertMediaFanoutPortSet({ lifecycle: {} }))
      .toThrowError(new MediaFanoutUnsupportedError('media_fanout_publications_unsupported'));
  });
});

function peerSession(): PeerLinkSession {
  return {
    lifecycle: { remotePeerId: 'peer-1', state: 'connected', close: vi.fn(), restartIce: vi.fn() },
    publications: { setTrack: vi.fn(), setMuted: vi.fn() },
    data: { bufferedAmount: 0, sendOpaque: vi.fn() },
    subscriptions: { setPublicationSubscribed: vi.fn() },
    stats: { observe: vi.fn() },
    events: { onStateChanged: vi.fn() },
  };
}

function sfuSession(): SfuRoomSession {
  return {
    lifecycle: {} as SfuRoomSession['lifecycle'],
    key: {} as SfuRoomSession['key'],
    publications: {} as SfuRoomSession['publications'],
    data: {} as SfuRoomSession['data'],
    subscriptions: {} as SfuRoomSession['subscriptions'],
    stats: {} as SfuRoomSession['stats'],
    videoRender: {} as SfuRoomSession['videoRender'],
    events: {} as SfuRoomSession['events'],
  };
}
