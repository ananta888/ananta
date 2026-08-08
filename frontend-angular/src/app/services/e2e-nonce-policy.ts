import { SecureEnvelopeError, encodeB64 } from './webrtc-secure-envelope';

const AES_GCM_NONCE_BYTES = 12;
const DEFAULT_OUTBOUND_RECENT_LIMIT = 8192;
const MAX_NONCE_GENERATION_ATTEMPTS = 8;

export interface E2eOutboundNoncePolicyOptions {
  readonly outboundRecentLimit?: number;
  readonly randomFill?: (target: Uint8Array) => Uint8Array;
}

/**
 * Owns AES-GCM outbound nonce generation.
 *
 * Random nonces use a bounded recent collision guard; retaining every nonce
 * for a key epoch would turn ordinary session traffic into a hard lifetime
 * limit. Exact inbound replay claims belong exclusively to the persistent,
 * cross-tab IndexedDbE2eReplayStore.
 */
export class E2eOutboundNoncePolicy {
  private readonly outboundRecent = new Map<string, Set<string>>();
  private readonly outboundRecentLimit: number;
  private readonly randomFill: (target: Uint8Array) => Uint8Array;

  constructor(options: E2eOutboundNoncePolicyOptions = {}) {
    const limit = options.outboundRecentLimit ?? DEFAULT_OUTBOUND_RECENT_LIMIT;
    if (!Number.isSafeInteger(limit) || limit < 1) {
      throw new SecureEnvelopeError('nonce_policy_invalid');
    }
    this.outboundRecentLimit = limit;
    this.randomFill = options.randomFill ?? (target => crypto.getRandomValues(target));
  }

  nextOutbound(scope: string): Uint8Array {
    const recent = this.outboundRecent.get(scope) ?? new Set<string>();
    for (let attempt = 0; attempt < MAX_NONCE_GENERATION_ATTEMPTS; attempt += 1) {
      const nonce = this.randomFill(new Uint8Array(AES_GCM_NONCE_BYTES));
      if (nonce.byteLength !== AES_GCM_NONCE_BYTES) {
        throw new SecureEnvelopeError('nonce_generation_failed');
      }
      const encoded = encodeB64(nonce);
      if (recent.has(encoded)) continue;
      while (recent.size >= this.outboundRecentLimit) {
        const oldest = recent.values().next().value as string | undefined;
        if (oldest === undefined) break;
        recent.delete(oldest);
      }
      recent.add(encoded);
      this.outboundRecent.set(scope, recent);
      return Uint8Array.from(nonce);
    }
    throw new SecureEnvelopeError('nonce_generation_failed');
  }

  forget(scope: string): void {
    this.outboundRecent.delete(scope);
  }

  clear(): void {
    this.outboundRecent.clear();
  }
}
