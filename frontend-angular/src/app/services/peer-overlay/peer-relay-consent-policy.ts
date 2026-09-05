export interface PeerRelayScope {
  readonly tenantId: string;
  readonly roomId: string;
  readonly localPeerId: string;
}

export interface PeerRelayConsent extends PeerRelayScope {
  readonly validation: 'peer-relay-consent-v1';
  readonly grantedAtMs: number;
  readonly expiresAtMs: number;
  readonly revokedAtMs: number | null;
}

/** Enforces the user's separate, scoped and revocable relay participation grant. */
export class PeerRelayConsentPolicy {
  constructor(private readonly clock: () => number = () => Date.now()) {}

  require(scope: PeerRelayScope, consent: PeerRelayConsent | null): void {
    if (!consent || consent.validation !== 'peer-relay-consent-v1'
        || consent.tenantId !== scope.tenantId || consent.roomId !== scope.roomId
        || consent.localPeerId !== scope.localPeerId || consent.grantedAtMs > this.clock()
        || consent.expiresAtMs <= this.clock() || consent.revokedAtMs !== null) {
      throw new Error('peer_relay_consent_required');
    }
  }
}
