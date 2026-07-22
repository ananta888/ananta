import { InjectionToken } from '@angular/core';

export interface SfuBroadcastCryptographicScope {
  readonly tenantRef: string;
  readonly roomRef: string;
  readonly publicationRef: string;
  readonly audienceRef: string;
  readonly localHandle: string;
  readonly membershipEpoch: number;
  readonly routeEpoch: number;
  readonly keyEpoch: number;
  readonly fencingToken: string;
}

/**
 * A GRP-013 implementation creates this lease only after Hub authorization,
 * delivery and ACK validation. Consumers never query a key store directly.
 */
export interface SfuBroadcastGroupKeyLease {
  readonly authorizationRef: string;
  readonly keyId: string;
  readonly scope: Readonly<SfuBroadcastCryptographicScope>;
  readonly expiresAtMs: number;
  readonly authorizedDestinationHandles: ReadonlySet<string>;
  readonly contentKey: CryptoKey;
  /** Supplies a fresh owned copy and invalidates it after the callback. */
  withLivekitKeyMaterial?<T>(consumer: (owned: Uint8Array) => Promise<T>): Promise<T>;
  release(): void;
}

export interface SfuBroadcastGroupKeyPort {
  acquire(
    scope: Readonly<SfuBroadcastCryptographicScope>,
    nowMs: number,
  ): Promise<SfuBroadcastGroupKeyLease>;
}

class UnsupportedSfuBroadcastGroupKeyPort implements SfuBroadcastGroupKeyPort {
  async acquire(): Promise<SfuBroadcastGroupKeyLease> {
    throw new Error('sfu_group_key_port_unavailable');
  }
}

/** Fail-closed until the app-scoped persistent GRP-013 adapter overrides it. */
export const SFU_BROADCAST_GROUP_KEY_PORT = new InjectionToken<SfuBroadcastGroupKeyPort>(
  'SFU_BROADCAST_GROUP_KEY_PORT',
  { providedIn: 'root', factory: () => new UnsupportedSfuBroadcastGroupKeyPort() },
);
