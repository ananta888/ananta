export interface ValidatedMembershipEvent {
  readonly validation: 'hub-membership-event-accepted-v1';
  readonly eventId: string;
  readonly sequence: number;
  readonly previousDigest: string | null;
  readonly eventDigest: string;
  readonly expiresAtMs: number;
  readonly payload: Readonly<Record<string, unknown>>;
}

export interface PeerOverlayMembershipVerifierPort {
  verify(raw: unknown): Promise<ValidatedMembershipEvent>;
}

export interface PeerOverlayControlChannelPort {
  readonly ready: boolean;
  sendControl(message: Readonly<Record<string, unknown>>): Promise<void>;
  onControl(callback: (message: unknown) => void): () => void;
}

export interface PeerOverlayHubControlFallbackPort {
  send(message: Readonly<Record<string, unknown>>): Promise<void>;
  requestSnapshot(cursor: number, digest: string | null): Promise<void>;
}

export interface PeerOverlayMembershipProjectionPort {
  apply(event: ValidatedMembershipEvent): Promise<void>;
}

const MAX_CONTROL_BYTES = 64 * 1024;

/** Replicates Hub authority; it never creates or changes membership events. */
export class PeerOverlayControlReplication {
  private cursor = 0;
  private digest: string | null = null;
  private release: (() => void) | null = null;

  constructor(
    private readonly verifier: PeerOverlayMembershipVerifierPort,
    private readonly channel: PeerOverlayControlChannelPort,
    private readonly fallback: PeerOverlayHubControlFallbackPort,
    private readonly projection: PeerOverlayMembershipProjectionPort,
    private readonly clock: () => number = () => Date.now(),
  ) {}

  start(): void {
    if (this.release) return;
    this.release = this.channel.onControl(raw => { void this.accept(raw); });
  }

  close(): void {
    this.release?.();
    this.release = null;
  }

  async replicate(event: ValidatedMembershipEvent): Promise<void> {
    this.assertEvent(event);
    await this.send({ kind: 'membership_event', event: event.payload });
  }

  async accept(raw: unknown): Promise<void> {
    assertControlSize(raw);
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
      throw new Error('peer_overlay_control_invalid');
    }
    const value = raw as Record<string, unknown>;
    if (value['kind'] === 'membership_ack') {
      assertExactFields(value, ['kind', 'sequence', 'event_digest']);
      return;
    }
    if (value['kind'] !== 'membership_event') throw new Error('peer_overlay_control_kind_invalid');
    assertExactFields(value, ['kind', 'event']);
    const event = await this.verifier.verify(value['event']);
    this.assertEvent(event);
    if (event.sequence <= this.cursor) {
      if (event.sequence === this.cursor && event.eventDigest === this.digest) await this.ack(event);
      else throw new Error('peer_overlay_membership_replay');
      return;
    }
    if (event.sequence !== this.cursor + 1 || event.previousDigest !== this.digest) {
      await this.fallback.requestSnapshot(this.cursor, this.digest);
      throw new Error('peer_overlay_membership_gap');
    }
    await this.projection.apply(event);
    this.cursor = event.sequence;
    this.digest = event.eventDigest;
    await this.ack(event);
  }

  snapshot(): Readonly<{ cursor: number; digest: string | null }> {
    return Object.freeze({ cursor: this.cursor, digest: this.digest });
  }

  private async ack(event: ValidatedMembershipEvent): Promise<void> {
    await this.send({ kind: 'membership_ack', sequence: event.sequence, event_digest: event.eventDigest });
  }

  private async send(message: Readonly<Record<string, unknown>>): Promise<void> {
    assertControlSize(message);
    if (this.channel.ready) {
      try {
        await this.channel.sendControl(message);
        return;
      } catch {
        // The bounded Hub path is the authoritative fallback.
      }
    }
    await this.fallback.send(message);
  }

  private assertEvent(event: ValidatedMembershipEvent): void {
    if (event.validation !== 'hub-membership-event-accepted-v1'
        || !Number.isSafeInteger(event.sequence) || event.sequence < 1
        || !/^[a-f0-9]{64}$/.test(event.eventDigest)
        || (event.previousDigest !== null && !/^[a-f0-9]{64}$/.test(event.previousDigest))
        || event.expiresAtMs <= this.clock()) throw new Error('peer_overlay_membership_event_invalid');
  }
}

function assertControlSize(value: unknown): void {
  const bytes = new TextEncoder().encode(JSON.stringify(value)).byteLength;
  if (bytes < 1 || bytes > MAX_CONTROL_BYTES) throw new Error('peer_overlay_control_size_invalid');
}

function assertExactFields(value: Record<string, unknown>, expected: readonly string[]): void {
  if (Object.keys(value).length !== expected.length || expected.some(field => !(field in value))) {
    throw new Error('peer_overlay_control_fields_invalid');
  }
}
