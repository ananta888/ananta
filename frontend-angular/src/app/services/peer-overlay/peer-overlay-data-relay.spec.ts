import {
  AcceptedPeerRouteLease,
  OpaquePeerRelayPacketV1,
  PeerOverlayChildDataPort,
  PeerOverlayDataRelay,
  PeerOverlayPacketAuthenticityPort,
} from './peer-overlay-data-relay';

describe('PeerOverlayDataRelay', () => {
  const now = 1_000_000;

  it('forwards opaque ciphertext by a Hub-accepted lease and rejects replay and loops', async () => {
    const relay = new PeerOverlayDataRelay(lease(), () => now, verifier());
    const child = port('child-1');
    relay.bindChild(child);
    const packet = await makePacket();

    expect(await relay.accept(packet)).toEqual({
      state: 'queued', reasonCode: 'peer_overlay_queued', childPeerId: 'child-1',
    });
    expect(child.send).toHaveBeenCalledOnce();
    expect((child.send as ReturnType<typeof vi.fn>).mock.calls[0][0].hop_limit).toBe(1);
    expect(await relay.accept(packet)).toEqual({ state: 'rejected', reasonCode: 'peer_overlay_duplicate' });
    expect(await relay.accept({ ...packet, message_id: 'message-loop', path: ['local'] }))
      .toEqual({ state: 'rejected', reasonCode: 'peer_overlay_path_invalid' });
  });

  it('keeps a slow child queue isolated from its sibling', async () => {
    const relay = new PeerOverlayDataRelay(lease(), () => now, verifier());
    const slow = port('child-1', 3_000_000);
    const fast = port('child-2');
    relay.bindChild(slow); relay.bindChild(fast);

    expect((await relay.accept(await makePacket('message-slow', 'child-1'))).state).toBe('queued');
    expect((await relay.accept(await makePacket('message-fast', 'child-2'))).state).toBe('queued');
    expect(slow.send).not.toHaveBeenCalled();
    expect(fast.send).toHaveBeenCalledOnce();
    const depths = relay.snapshot()['queueDepths'] as Record<string, Record<string, number>>;
    expect(depths['child-1']['event']).toBe(1);
  });

  it('fails closed when the origin signature is not verified', async () => {
    const relay = new PeerOverlayDataRelay(lease(), () => now, verifier(false));
    relay.bindChild(port('child-1'));
    expect(await relay.accept(await makePacket())).toEqual({
      state: 'rejected', reasonCode: 'peer_overlay_signature_invalid',
    });
  });

  it('relays identical authenticated ciphertext over two bounded hops', async () => {
    const authentic = verifier();
    const first = new PeerOverlayDataRelay(lease({
      localPeerId: 'relay-1', childPeerIds: ['relay-2'], destinationRoutes: { destination: 'relay-2' },
    }), () => now, authentic);
    const second = new PeerOverlayDataRelay(lease({
      localPeerId: 'relay-2', childPeerIds: ['destination'], destinationRoutes: { destination: 'destination' },
    }), () => now, authentic);
    const link = port('relay-2');
    first.bindChild(link);
    const destination = port('destination');
    second.bindChild(destination);
    const packet = await makePacket('message-two-hop', 'destination');

    expect(await first.accept(packet)).toEqual({
      state: 'queued', reasonCode: 'peer_overlay_queued', childPeerId: 'relay-2',
    });
    const forwarded = (link.send as ReturnType<typeof vi.fn>).mock.calls[0][0] as OpaquePeerRelayPacketV1;
    expect(forwarded.ciphertext_b64).toBe(packet.ciphertext_b64);
    expect(forwarded.signature_b64).toBe(packet.signature_b64);
    expect(forwarded.path).toEqual(['origin', 'relay-1']);
    expect(await second.accept(forwarded)).toEqual({
      state: 'queued', reasonCode: 'peer_overlay_queued', childPeerId: 'destination',
    });
    const final = (destination.send as ReturnType<typeof vi.fn>).mock.calls[0][0] as OpaquePeerRelayPacketV1;
    expect(final.ciphertext_b64).toBe(packet.ciphertext_b64);
    expect(final.hop_limit).toBe(0);
    expect(final.path).toEqual(['origin', 'relay-1', 'relay-2']);
    expect(await new PeerOverlayDataRelay(lease({
      localPeerId: 'destination', childPeerIds: [], destinationRoutes: {},
    }), () => now, authentic).accept(final)).toEqual({
      state: 'delivered_local', reasonCode: 'peer_overlay_destination_reached',
    });
    const verifyCalls = (authentic.verify as ReturnType<typeof vi.fn>).mock.calls;
    expect(new TextDecoder().decode(verifyCalls[1][1])).toBe(new TextDecoder().decode(verifyCalls[0][1]));
    expect(new TextDecoder().decode(verifyCalls[2][1])).toBe(new TextDecoder().decode(verifyCalls[0][1]));
  });

  it.each([
    ['cross-publication', (packet: OpaquePeerRelayPacketV1) => ({ ...packet, publication_id: 'publication-forged' }),
      'peer_overlay_packet_scope_invalid'],
    ['stale-route', (packet: OpaquePeerRelayPacketV1) => ({ ...packet, route_epoch: 1 }),
      'peer_overlay_packet_scope_invalid'],
    ['ciphertext-substitution', (packet: OpaquePeerRelayPacketV1) => ({ ...packet, ciphertext_digest: '0'.repeat(64) }),
      'peer_overlay_ciphertext_digest_mismatch'],
    ['route-poisoning', (packet: OpaquePeerRelayPacketV1) => ({ ...packet, destination_peer_id: 'not-leased' }),
      'peer_overlay_destination_not_leased'],
    ['loop-injection', (packet: OpaquePeerRelayPacketV1) => ({ ...packet, path: ['origin', 'local'] }),
      'peer_overlay_path_invalid'],
    ['chunk-confusion', (packet: OpaquePeerRelayPacketV1) => ({ ...packet, chunk_index: 2, chunk_count: 2 }),
      'peer_overlay_chunk_invalid'],
  ] as const)('rejects the deterministic adversarial corpus: %s', async (_name, mutate, reasonCode) => {
    const relay = new PeerOverlayDataRelay(lease(), () => now, verifier());
    relay.bindChild(port('child-1'));
    expect(await relay.accept(mutate(await makePacket()))).toEqual({ state: 'rejected', reasonCode });
  });

  it('keeps adversarial load bounded and exposes no content-key or decrypt surface', async () => {
    const relay = new PeerOverlayDataRelay(lease(), () => now, verifier());
    relay.bindChild(port('child-1'));
    const prototypeSurface = Object.getOwnPropertyNames(Object.getPrototypeOf(relay));
    expect(prototypeSurface).not.toContain('decrypt');
    expect(prototypeSurface).not.toContain('open');
    expect(prototypeSurface).not.toContain('exportKey');

    for (let seed = 0; seed < 128; seed += 1) {
      const packet = await makePacket(`attack-${seed}`);
      expect((await relay.accept({ ...packet, ciphertext_digest: 'f'.repeat(64) })).state).toBe('rejected');
    }
    const snapshot = relay.snapshot();
    expect(snapshot['replayEntries']).toBe(0);
    expect((snapshot['drops'] as Record<string, number>)['peer_overlay_ciphertext_digest_mismatch']).toBe(128);
    expect(snapshot).not.toHaveProperty('ciphertext');
    expect(snapshot).not.toHaveProperty('contentKey');
    expect(snapshot).not.toHaveProperty('cleartext');
  });
});

function lease(changes: Partial<AcceptedPeerRouteLease> = {}): AcceptedPeerRouteLease {
  return Object.freeze({
    validation: 'hub-route-lease-accepted-v1', leaseId: 'lease-1', tenantId: 'tenant-1',
    roomId: 'room-1', publicationId: 'publication-1', localPeerId: 'local',
    childPeerIds: Object.freeze(['child-1', 'child-2']), routeEpoch: 2, maxHops: 3,
    destinationRoutes: Object.freeze({ 'child-1': 'child-1', 'child-2': 'child-2' }),
    expiresAtMs: 1_100_000,
    trafficClasses: Object.freeze(['control', 'rekey', 'event', 'semantic', 'bulk']),
    ...changes,
  });
}

function port(childPeerId: string, bufferedAmount = 0): PeerOverlayChildDataPort {
  return { childPeerId, bufferedAmount, readyState: 'open', send: vi.fn() };
}

async function makePacket(messageId = 'message-1', destination = 'child-1'): Promise<OpaquePeerRelayPacketV1> {
  const ciphertext = Uint8Array.of(1, 2, 3, 4);
  const digest = await crypto.subtle.digest('SHA-256', ciphertext);
  return Object.freeze({
    version: 1, message_id: messageId, tenant_id: 'tenant-1', room_id: 'room-1',
    publication_id: 'publication-1', origin_peer_id: 'origin', destination_peer_id: destination,
    route_epoch: 2, traffic_class: 'event', expires_at_ms: 1_050_000, hop_limit: 2,
    path: Object.freeze(['origin']), chunk_index: 0, chunk_count: 1,
    ciphertext_digest: [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, '0')).join(''),
    ciphertext_b64: btoa(String.fromCharCode(...ciphertext)),
    signature_b64: 'AQ==',
  });
}

function verifier(accepted = true): PeerOverlayPacketAuthenticityPort {
  return { verify: vi.fn(async () => accepted) };
}
