export type MediaRotationReason = 'create' | 'join' | 'leave' | 'revoke' | 'hub_failover' | 'refresh';

export interface RotatingMediaKeyLease {
  readonly publicationId: string;
  readonly keyEpoch: number;
  readonly expiresAtMs: number;
  release(): void;
}

export interface MediaKeyRotationPort {
  activate(lease: RotatingMediaKeyLease): Promise<void>;
  retireSender(publicationId: string, keyEpoch: number): void;
  retireReceiver(publicationId: string, keyEpoch: number): void;
  requestKeyFrame(publicationId: string): void;
}

export interface MediaRotationScheduler {
  now(): number;
  schedule(operation: () => void, delayMs: number): { cancel(): void };
}

const SYSTEM_SCHEDULER: MediaRotationScheduler = Object.freeze({
  now: () => Date.now(),
  schedule: (operation, delayMs) => {
    const timer = setTimeout(operation, delayMs);
    return { cancel: () => clearTimeout(timer) };
  },
});

/** Coordinates sender cutover and bounded receive-only grace without approval waits. */
export class MediaKeyRotationCoordinator {
  private grace: { cancel(): void } | null = null;

  constructor(
    private readonly port: MediaKeyRotationPort,
    private readonly scheduler: MediaRotationScheduler = SYSTEM_SCHEDULER,
    private readonly maximumGraceMs = 5_000,
  ) {
    if (!Number.isSafeInteger(maximumGraceMs) || maximumGraceMs < 0 || maximumGraceMs > 30_000) {
      throw new Error('media_key_rotation_grace_invalid');
    }
  }

  async rotate(
    previous: RotatingMediaKeyLease | null,
    next: RotatingMediaKeyLease,
    reason: MediaRotationReason,
    mediaKind: 'audio' | 'video' | 'data',
  ): Promise<void> {
    validateTransition(previous, next, this.scheduler.now());
    this.grace?.cancel();
    this.grace = null;
    try {
      await this.port.activate(next);
    } catch (error) {
      next.release();
      throw error;
    }
    if (mediaKind === 'video') this.port.requestKeyFrame(next.publicationId);
    if (!previous) return;
    this.port.retireSender(previous.publicationId, previous.keyEpoch);
    const remainingLifetime = Math.max(0, previous.expiresAtMs - this.scheduler.now());
    const graceMs = reason === 'revoke' ? 0 : Math.min(this.maximumGraceMs, remainingLifetime);
    if (graceMs === 0) {
      this.retirePrevious(previous);
      return;
    }
    this.grace = this.scheduler.schedule(() => this.retirePrevious(previous), graceMs);
  }

  close(): void {
    this.grace?.cancel();
    this.grace = null;
  }

  private retirePrevious(previous: RotatingMediaKeyLease): void {
    this.port.retireReceiver(previous.publicationId, previous.keyEpoch);
    previous.release();
    this.grace = null;
  }
}

function validateTransition(
  previous: RotatingMediaKeyLease | null,
  next: RotatingMediaKeyLease,
  nowMs: number,
): void {
  if (!Number.isSafeInteger(next.keyEpoch) || next.keyEpoch < 1 || next.expiresAtMs <= nowMs) {
    throw new Error('media_key_rotation_lease_invalid');
  }
  if (previous && (previous.publicationId !== next.publicationId || previous.keyEpoch >= next.keyEpoch)) {
    throw new Error('media_key_rotation_transition_invalid');
  }
}
