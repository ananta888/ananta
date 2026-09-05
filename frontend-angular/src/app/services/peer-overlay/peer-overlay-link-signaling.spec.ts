import {
  AcceptedPeerLinkSignalingTicket,
  PeerLinkSignalingTransportPort,
  PeerOverlayLinkSignaling,
} from './peer-overlay-link-signaling';

describe('PeerOverlayLinkSignaling', () => {
  it('uses a ready DataChannel and consumes the Hub ticket once for the offer', async () => {
    const transport = fakeTransport(true);
    const signaling = new PeerOverlayLinkSignaling(transport, () => 1_000);
    const result = await signaling.send(ticket(), 'offer', 'v=0\r\na=ice-ufrag:bounded');
    expect(result.transport).toBe('in_band');
    expect(transport.sendInBand).toHaveBeenCalledOnce();
    expect(transport.sendViaHub).not.toHaveBeenCalled();
    await expect(signaling.send(ticket(), 'offer', 'v=0')).rejects.toThrow('peer_link_ticket_already_consumed');
  });

  it('falls back automatically to the Hub across bootstrap and raced partitions', async () => {
    const bootstrap = fakeTransport(false);
    expect((await new PeerOverlayLinkSignaling(bootstrap, () => 1_000)
      .send(ticket(), 'offer', 'v=0')).transport).toBe('hub_fallback');
    const partition = fakeTransport(true);
    partition.sendInBand.mockRejectedValueOnce(new Error('partitioned'));
    expect((await new PeerOverlayLinkSignaling(partition, () => 1_000)
      .send(ticket(), 'offer', 'v=0')).transport).toBe('hub_fallback');
  });

  it('enforces deterministic offer roles, expiry, bounds, and secret denial', async () => {
    const signaling = new PeerOverlayLinkSignaling(fakeTransport(false), () => 1_000);
    await expect(signaling.send(ticket({ offererPeerId: 'peer-b' }), 'offer', 'v=0'))
      .rejects.toThrow('peer_link_offer_role_denied');
    await expect(signaling.send(ticket({ expiresAtMs: 1_000 }), 'answer', 'v=0'))
      .rejects.toThrow('peer_link_ticket_invalid');
    await expect(signaling.send(ticket(), 'answer', 'Authorization: Bearer secret'))
      .rejects.toThrow('peer_link_signal_payload_invalid');
    await expect(signaling.send(ticket(), 'ice_candidate', 'x'.repeat(64 * 1024 + 1)))
      .rejects.toThrow('peer_link_signal_payload_invalid');
  });

  it('bounds candidate signaling per ticket', async () => {
    const signaling = new PeerOverlayLinkSignaling(fakeTransport(false), () => 1_000);
    for (let index = 0; index < 65; index += 1) {
      await signaling.send(ticket(), index === 0 ? 'offer' : 'ice_candidate', `candidate:${index}`);
    }
    await expect(signaling.send(ticket(), 'ice_candidate', 'candidate:overflow'))
      .rejects.toThrow('peer_link_signal_budget_exceeded');
  });
});

function ticket(changes: Partial<AcceptedPeerLinkSignalingTicket> = {}): AcceptedPeerLinkSignalingTicket {
  return Object.freeze({
    validation: 'hub-link-ticket-accepted-v1', ticketId: 'ticket-1', localPeerId: 'peer-a',
    remotePeerId: 'peer-b', publicationId: 'publication-1', routeEpoch: 2,
    offererPeerId: 'peer-a', expiresAtMs: 2_000, ...changes,
  });
}

function fakeTransport(inBand: boolean) {
  return {
    inBandReady: vi.fn(() => inBand),
    sendInBand: vi.fn(async () => undefined),
    sendViaHub: vi.fn(async () => undefined),
  } satisfies PeerLinkSignalingTransportPort;
}
