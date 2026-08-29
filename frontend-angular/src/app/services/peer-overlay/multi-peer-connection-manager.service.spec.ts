import { TestBed } from '@angular/core/testing';

import { MultiPeerConnectionManager } from './multi-peer-connection-manager.service';
import {
  PEER_LINK_SESSION_FACTORY,
  PeerLinkSession,
  PeerLinkSessionFactory,
  ValidatedPeerLinkTicket,
} from './peer-media-fanout.ports';

describe('MultiPeerConnectionManager', () => {
  let factory: FakeFactory;
  let manager: MultiPeerConnectionManager;

  beforeEach(() => {
    factory = new FakeFactory();
    TestBed.configureTestingModule({ providers: [
      MultiPeerConnectionManager,
      { provide: PEER_LINK_SESSION_FACTORY, useValue: factory },
    ] });
    manager = TestBed.inject(MultiPeerConnectionManager);
  });

  afterEach(() => { manager.close(); TestBed.resetTestingModule(); });

  it('owns independent idempotent peer sessions and isolates one failed peer', async () => {
    factory.fail.add('peer-2');
    const first = await manager.reconcile([ticket('peer-1'), ticket('peer-2'), ticket('peer-3')]);
    expect(first.peerIds).toEqual(['peer-1', 'peer-3']);
    expect(first.failedPeerIds).toEqual(['peer-2']);

    await manager.setTrack('camera', { trackId: 'camera-1', kind: 'video', value: {} });
    factory.sessions.get('peer-1')!.publications.setMuted = vi.fn(async () => { throw new Error('failed'); });
    await manager.setMuted('camera', true);
    expect(manager.snapshot().peerIds).toEqual(['peer-1', 'peer-3']);
    expect(manager.snapshot().failedPeerIds).toContain('peer-1');

    const created = factory.create.mock.calls.length;
    await manager.reconcile([ticket('peer-1'), ticket('peer-3')]);
    expect(factory.create).toHaveBeenCalledTimes(created);
  });

  it('enforces the four-participant mesh hard maximum', async () => {
    await expect(manager.reconcile([
      ticket('peer-1'), ticket('peer-2'), ticket('peer-3'), ticket('peer-4'),
    ])).rejects.toThrow('peer_mesh_size_invalid');
  });
});

class FakeFactory implements PeerLinkSessionFactory {
  readonly sessions = new Map<string, PeerLinkSession>();
  readonly fail = new Set<string>();
  readonly create = vi.fn(async (value: ValidatedPeerLinkTicket): Promise<PeerLinkSession> => {
    if (this.fail.has(value.remotePeerId)) throw new Error('peer_failed');
    const session: PeerLinkSession = {
      lifecycle: {
        remotePeerId: value.remotePeerId, state: 'connected', close: vi.fn(), restartIce: vi.fn(),
      },
      publications: { setTrack: vi.fn(async () => undefined), setMuted: vi.fn(async () => undefined) },
      data: { bufferedAmount: 0, sendOpaque: vi.fn() },
      observations: { observe: vi.fn(async () => ({
        observedAtMs: 1, roundTripTimeMs: 10, availableOutgoingBitrate: 1_000_000,
        packetsLost: 0, framesDropped: 0, qualityLimitationReason: null,
      })) },
    };
    this.sessions.set(value.remotePeerId, session);
    return session;
  });
}

function ticket(remotePeerId: string): ValidatedPeerLinkTicket {
  return Object.freeze({
    validation: 'hub-link-ticket-accepted-v1', ticketId: `ticket-${remotePeerId}`,
    localPeerId: 'local', remotePeerId, publicationId: 'publication-1', routeEpoch: 1,
    icePolicy: 'all', expiresAtMs: Date.now() + 60_000,
  });
}
