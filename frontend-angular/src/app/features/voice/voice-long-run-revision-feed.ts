import { firstValueFrom } from 'rxjs';

import { VoiceApiService } from './voice-api.service';
import { VoiceLongRunTimeline } from './voice-long-run-timeline';
import {
  REVISION_POLL_LIMIT,
  REVISION_POLL_MILLISECONDS,
  REVISION_RETRY_DELAYS_MILLISECONDS,
  VoiceLongRunObserver,
  ensureOperation,
  isRetriable,
} from './voice-long-run.policy';
import { VoiceLongRunResponse } from './voice.models';

/** Run context the revision feed reads from the long-run controller. */
export interface VoiceLongRunRevisionFeedHost {
  readonly hubUrl: () => string;
  /** Id of the active run, null while no run is bound. */
  readonly runId: () => string | null;
  readonly generation: () => number;
  readonly stopping: () => boolean;
  readonly observer: () => VoiceLongRunObserver;
  /** Applies the run/gap projection; true when the response is the current run version. */
  readonly acceptRunProjection: (response: VoiceLongRunResponse) => boolean;
}

/**
 * Timeline projection of Hub transcript revisions plus the visibility-aware
 * polling that fetches pending ASR/correction revisions page by page.
 */
export class VoiceLongRunRevisionFeed {
  private readonly timeline = new VoiceLongRunTimeline();
  private pollTimer: ReturnType<typeof setTimeout> | null = null;
  private pollInFlight = false;
  private pollingNeeded = false;
  private pollingBlocked = false;
  private pollFailure = 0;
  private revisionCursor = 0;

  constructor(
    private readonly api: VoiceApiService,
    private readonly host: VoiceLongRunRevisionFeedHost,
  ) {}

  publish(response: VoiceLongRunResponse): void {
    const acceptedRunProjection = this.host.acceptRunProjection(response);
    const snapshot = this.timeline.apply(response);
    this.revisionCursor = Math.max(
      this.revisionCursor,
      snapshot.highestTimelineRevision,
      Number(response.page?.next_after_revision || 0),
    );
    const hasMoreRevisionPages = response.page?.after_revision != null
      && Boolean(response.page.has_more);
    this.pollingNeeded = (snapshot.hasPendingRevisions || hasMoreRevisionPages)
      && !this.pollingBlocked;
    if (!this.pollingNeeded) this.clearTimer();
    const observer = this.host.observer();
    observer.timelineUpdated?.(snapshot);
    if (acceptedRunProjection) observer.runUpdated?.(response);
    if (this.pollingNeeded) this.schedule();
  }

  schedule(delayMilliseconds = REVISION_POLL_MILLISECONDS): void {
    if (!this.pollingNeeded || this.host.runId() === null || this.host.stopping() || this.pollTimer
      || this.pollInFlight || globalThis.document?.visibilityState === 'hidden') return;
    this.pollTimer = setTimeout(() => {
      this.pollTimer = null;
      void this.poll();
    }, Math.max(0, delayMilliseconds));
  }

  /** One revision refresh while stopping; only non-retriable errors propagate. */
  async refreshWhileStopping(generation: number): Promise<void> {
    const runId = this.host.runId();
    if (this.pollInFlight || runId === null) return;
    this.pollInFlight = true;
    try {
      const response = await firstValueFrom(this.api.getLongRun(this.host.hubUrl(), runId, {
        afterRevision: this.revisionCursor,
        limit: REVISION_POLL_LIMIT,
      }));
      ensureOperation(generation, this.host.generation());
      if (this.host.runId() !== runId) return;
      this.publish(response);
      this.host.observer().connection?.('online');
    } catch (error) {
      ensureOperation(generation, this.host.generation());
      this.host.observer().connection?.('retrying');
      if (!isRetriable(error)) throw error;
    } finally {
      this.pollInFlight = false;
    }
  }

  reset(): void {
    this.clearTimer();
    this.timeline.reset();
    this.pollingNeeded = false;
    this.pollingBlocked = false;
    this.pollFailure = 0;
    this.revisionCursor = 0;
  }

  clearTimer(): void {
    if (this.pollTimer) clearTimeout(this.pollTimer);
    this.pollTimer = null;
  }

  private async poll(): Promise<void> {
    const runId = this.host.runId();
    if (this.pollInFlight || !this.pollingNeeded || runId === null) return;
    const generation = this.host.generation();
    this.pollInFlight = true;
    let nextDelay = REVISION_POLL_MILLISECONDS;
    try {
      const response = await firstValueFrom(this.api.getLongRun(this.host.hubUrl(), runId, {
        afterRevision: this.revisionCursor,
        limit: REVISION_POLL_LIMIT,
      }));
      if (generation !== this.host.generation() || this.host.runId() !== runId
        || globalThis.document?.visibilityState === 'hidden') return;
      const hasMore = Boolean(response.page?.has_more);
      this.publish(response);
      this.pollFailure = 0;
      this.host.observer().connection?.('online');
      nextDelay = hasMore ? 0 : REVISION_POLL_MILLISECONDS;
    } catch (error) {
      if (generation !== this.host.generation() || this.host.runId() !== runId) return;
      if (!isRetriable(error)) {
        this.pollingBlocked = true;
        this.pollingNeeded = false;
        this.host.observer().error?.(error);
        return;
      }
      this.host.observer().connection?.('retrying');
      nextDelay = REVISION_RETRY_DELAYS_MILLISECONDS[
        Math.min(this.pollFailure, REVISION_RETRY_DELAYS_MILLISECONDS.length - 1)
      ];
      this.pollFailure += 1;
    } finally {
      this.pollInFlight = false;
      if (generation === this.host.generation() && this.host.runId() === runId && !this.host.stopping()) {
        this.schedule(nextDelay);
      }
    }
  }
}
