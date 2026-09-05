import { PeerRelayConsentPolicy } from './peer-relay-consent-policy';

describe('PeerRelayConsentPolicy', () => {
  it('requires separate, scoped, expiring, and revocable relay participation consent', () => {
    const policy = new PeerRelayConsentPolicy(() => 1_000);
    const scope = { tenantId: 'tenant-1', roomId: 'room-1', localPeerId: 'peer-a' };
    const consent = {
      validation: 'peer-relay-consent-v1' as const, ...scope,
      grantedAtMs: 900, expiresAtMs: 2_000, revokedAtMs: null,
    };
    expect(() => policy.require(scope, consent)).not.toThrow();
    expect(() => policy.require(scope, { ...consent, revokedAtMs: 950 }))
      .toThrow('peer_relay_consent_required');
    expect(() => policy.require(scope, { ...consent, roomId: 'room-2' }))
      .toThrow('peer_relay_consent_required');
    expect(() => policy.require(scope, { ...consent, expiresAtMs: 1_000 }))
      .toThrow('peer_relay_consent_required');
  });
});
