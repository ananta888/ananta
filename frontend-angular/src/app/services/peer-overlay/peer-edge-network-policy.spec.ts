import {
  AcceptedPeerEdgeTicket,
  AcceptedPeerTurnCredential,
  PeerEdgeNetworkPolicy,
  PeerEdgePrivacyMode,
} from './peer-edge-network-policy';

describe('PeerEdgeNetworkPolicy', () => {
  it.each(['direct_preferred', 'automatic'] as PeerEdgePrivacyMode[])(
    'discloses direct-neighbor IP visibility for %s', mode => {
      const decision = new PeerEdgeNetworkPolicy(() => 1_000)
        .decide(ticket(), mode, [{ urls: 'stun:stun.example.test' }], turn());
      expect(decision.rtcConfiguration.iceTransportPolicy).toBe('all');
      expect(decision.neighborIpVisible).toBe(true);
      expect(decision.notice).toContain('mDNS und E2EE verbergen diese nicht');
    },
  );

  it('forces TURN for relay-only without changing an unrelated edge', () => {
    const policy = new PeerEdgeNetworkPolicy(() => 1_000);
    const relayed = policy.decide(ticket({ icePolicy: 'relay' }), 'relay_only', [], turn());
    const sibling = policy.decide(
      ticket({ ticketId: 'ticket-2', remotePeerId: 'peer-c' }),
      'automatic',
      [{ urls: 'stun:stun.example.test' }],
      turn({ remotePeerId: 'peer-c' }),
    );
    expect(relayed.rtcConfiguration.iceTransportPolicy).toBe('relay');
    expect(relayed.notice).toContain('benötigt erreichbares TURN');
    expect(sibling.rtcConfiguration.iceTransportPolicy).toBe('all');
  });

  it('rejects transferable, stale, overlong, or epoch-mismatched TURN credentials', () => {
    const policy = new PeerEdgeNetworkPolicy(() => 1_000);
    for (const credential of [
      turn({ remotePeerId: 'peer-c' }),
      turn({ expiresAtMs: 1_000 }),
      turn({ issuedAtMs: 0, expiresAtMs: 700_001 }),
      turn({ epochs: { membership: 1, route: 1, key: 1 } }),
    ]) {
      expect(() => policy.decide(ticket({ icePolicy: 'relay' }), 'relay_only', [], credential))
        .toThrow('peer_edge_turn_credential_invalid');
    }
  });

  it('does not admit unscoped TURN credentials from the room-wide base profile', () => {
    const policy = new PeerEdgeNetworkPolicy(() => 1_000);
    expect(() => policy.decide(
      ticket({ icePolicy: 'relay' }),
      'relay_only',
      [{ urls: 'turn:shared.example.test', username: 'shared', credential: 'shared' }],
    )).toThrow('peer_edge_turn_credential_required');
  });

  it('preserves membership, key, route, and exact edge fencing on ICE restart', () => {
    const policy = new PeerEdgeNetworkPolicy(() => 1_000);
    expect(() => policy.authorizeIceRestart(ticket(), ticket({ ticketId: 'ticket-2', expiresAtMs: 8_000 })))
      .not.toThrow();
    expect(() => policy.authorizeIceRestart(
      ticket(), ticket({ ticketId: 'ticket-2', epochs: { membership: 1, route: 1, key: 2 } }),
    )).toThrow('peer_edge_ice_restart_fence_invalid');
  });

});

function ticket(changes: Partial<AcceptedPeerEdgeTicket> = {}): AcceptedPeerEdgeTicket {
  return Object.freeze({
    validation: 'hub-edge-ticket-accepted-v1', ticketId: 'ticket-1', tenantId: 'tenant-1', roomId: 'room-1',
    publicationId: 'publication-1', localPeerId: 'peer-a', remotePeerId: 'peer-b',
    epochs: { membership: 2, route: 3, key: 2 }, icePolicy: 'all', expiresAtMs: 10_000, ...changes,
  });
}

function turn(changes: Partial<AcceptedPeerTurnCredential> = {}): AcceptedPeerTurnCredential {
  return Object.freeze({
    validation: 'hub-turn-credential-accepted-v1', tenantId: 'tenant-1', roomId: 'room-1',
    localPeerId: 'peer-a', remotePeerId: 'peer-b', epochs: { membership: 2, route: 3, key: 2 },
    urls: ['turns:turn.example.test:5349'], username: 'short-lived', credential: 'opaque-secret',
    issuedAtMs: 900, expiresAtMs: 9_000, ...changes,
  });
}
