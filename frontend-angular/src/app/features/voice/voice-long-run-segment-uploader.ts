import { firstValueFrom } from 'rxjs';

import { VoiceApiService } from './voice-api.service';
import { VoiceLongRunSpoolPort } from './voice-long-run-spool';
import {
  RETRY_DELAYS_MILLISECONDS,
  VoiceLongRunObserver,
  isRetriable,
} from './voice-long-run.policy';
import { VoiceLongRunResponse } from './voice.models';

/** Run context and callbacks the uploader needs from the long-run controller. */
export interface VoiceLongRunSegmentUploaderHost {
  readonly hubUrl: () => string;
  /** Id of the active run, null while no run is bound. */
  readonly runId: () => string | null;
  readonly generation: () => number;
  readonly stopping: () => boolean;
  readonly observer: () => VoiceLongRunObserver;
  readonly publishResponse: (response: VoiceLongRunResponse) => void;
  readonly reportGap: (sequence: number) => void;
  readonly clearLocalGap: (sequence: number) => void;
  readonly failed: (error: unknown) => void;
}

/**
 * Drains the encrypted segment spool to the Hub in sequence order, one
 * segment in flight, with bounded retry backoff. Non-retriable or evicted
 * segments become gaps; the spool keeps retriable ones for the next attempt.
 */
export class VoiceLongRunSegmentUploader {
  private uploadOperation: Promise<void> | null = null;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private retryAttempt = 0;
  private inFlightSequence: number | null = null;
  private readonly deferredEvictions = new Set<number>();

  constructor(
    private readonly api: VoiceApiService,
    private readonly spool: VoiceLongRunSpoolPort,
    private readonly host: VoiceLongRunSegmentUploaderHost,
  ) {}

  kick(): void {
    if (this.host.runId() === null || this.retryTimer || this.uploadOperation) return;
    void this.drain().catch((error) => this.host.failed(error));
  }

  async drain(): Promise<void> {
    if (this.uploadOperation) return this.uploadOperation;
    const operation = this.uploadUntilFailure();
    this.uploadOperation = operation;
    try {
      await operation;
    } finally {
      if (this.uploadOperation === operation) this.uploadOperation = null;
    }
  }

  /** A spool eviction of the in-flight segment is deferred until its upload settles. */
  evicted(sequence: number): void {
    if (sequence === this.inFlightSequence) {
      this.deferredEvictions.add(sequence);
    } else {
      this.host.reportGap(sequence);
    }
  }

  resetRetries(): void {
    this.retryAttempt = 0;
  }

  clearRetryTimer(): void {
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.retryTimer = null;
  }

  reset(): void {
    this.uploadOperation = null;
    this.retryAttempt = 0;
    this.inFlightSequence = null;
    this.deferredEvictions.clear();
  }

  private async uploadUntilFailure(): Promise<void> {
    const runId = this.host.runId();
    if (!runId) return;
    const generation = this.host.generation();
    const current = () => generation === this.host.generation();
    while (this.host.runId() === runId && current()) {
      const pending = await this.spool.list(runId);
      if (!current()) return;
      const metadata = pending[0];
      if (!metadata) {
        this.retryAttempt = 0;
        this.host.observer().connection?.('online');
        return;
      }
      const segment = await this.spool.read(runId, metadata.sequence);
      if (!current()) return;
      if (!segment) continue;
      this.inFlightSequence = segment.sequence;
      try {
        const response = await firstValueFrom(this.api.uploadLongRunSegment(
          this.host.hubUrl(),
          runId,
          segment.sequence,
          {
            file: new Blob([segment.audio], { type: 'audio/wav' }),
            fileName: `voice-live-${String(segment.sequence).padStart(6, '0')}.wav`,
            startedAtMs: segment.startedAtMs,
            endedAtMs: segment.endedAtMs,
            durationMs: segment.durationMs,
            overlapMilliseconds: segment.overlapMilliseconds,
          },
          segment.idempotencyKey,
        ));
        if (!current()) return;
        await this.spool.delete(runId, segment.sequence);
        if (!current()) return;
        this.deferredEvictions.delete(segment.sequence);
        this.host.clearLocalGap(segment.sequence);
        this.retryAttempt = 0;
        const stats = await this.spool.stats(runId);
        if (!current()) return;
        this.host.observer().connection?.('online');
        this.host.publishResponse(response);
        this.host.observer().segmentUploaded?.(response, stats.segments);
      } catch (error) {
        if (!current()) return;
        const stillBuffered = Boolean(await this.spool.read(runId, segment.sequence));
        if (!current()) return;
        if (!isRetriable(error) || !stillBuffered) {
          await this.spool.delete(runId, segment.sequence).catch(() => undefined);
          if (!current()) return;
          this.deferredEvictions.delete(segment.sequence);
          this.host.reportGap(segment.sequence);
          this.host.observer().segmentFailed?.(segment.sequence, error);
          continue;
        }
        this.host.observer().connection?.('retrying');
        if (!this.host.stopping()) this.scheduleRetry();
        return;
      } finally {
        this.inFlightSequence = null;
      }
    }
  }

  private scheduleRetry(): void {
    if (this.retryTimer || this.host.stopping() || this.host.runId() === null) return;
    const delay = RETRY_DELAYS_MILLISECONDS[
      Math.min(this.retryAttempt, RETRY_DELAYS_MILLISECONDS.length - 1)
    ];
    this.retryAttempt += 1;
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null;
      this.kick();
    }, delay);
  }
}
