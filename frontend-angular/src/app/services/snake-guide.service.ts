import { Injectable } from '@angular/core';
import { BehaviorSubject, Subject } from 'rxjs';

export interface GuideStep {
  waypoint: string;
  bubble: string;
  delay_ms?: number;
  /** Pixel position override — when set, skips UiWaypointService.resolve() */
  x?: number;
  y?: number;
  priority?: number;
  ttl_ms?: number;
  /** Epoch ms when this step was created — used for staleness checks. */
  created_at?: number;
}

export interface PlayOptions {
  requestId?: string;
  priority?: number;
  ttl_ms?: number;
}

@Injectable({ providedIn: 'root' })
export class SnakeGuideService {
  readonly play$ = new Subject<GuideStep[]>();
  /** True while a guide sequence is active (steps remain or current step running). */
  readonly active$ = new BehaviorSubject<boolean>(false);

  /** Tracks the pending request_id set by VisualGuideClientService before sending. */
  readonly pendingRequestId$ = new BehaviorSubject<string | null>(null);

  private _remainingSteps: GuideStep[] = [];
  /** Lower number = higher priority (region-explain = 2, predictive = 7). */
  currentPriority = 10;

  play(steps: GuideStep[], options?: PlayOptions): void {
    const incomingPriority = options?.priority ?? 10;

    // Region-explain (lower number) replaces predictive guides (higher number).
    // If a higher-priority guide is running, discard incoming lower-priority guide.
    if (this.active$.value && incomingPriority >= this.currentPriority) {
      return;
    }

    if (options?.requestId !== undefined) {
      const pending = this.pendingRequestId$.value;
      // Stale response: a different request is now pending — ignore unless no pending id.
      if (pending && options.requestId !== pending) return;
    }

    // Warn if steps carry creation timestamps that are too old.
    if (steps.length && steps[0].created_at) {
      const age = Date.now() - steps[0].created_at;
      if (age > 8000) {
        console.warn(`[SnakeGuide] Playing stale guide steps (${age}ms old)`);
      }
    }

    this.currentPriority = incomingPriority;
    this._remainingSteps = [...steps];
    this.active$.next(steps.length > 0);
    this.play$.next(steps);
  }

  acceptGuideForRequest(requestId: string): boolean {
    const current = this.pendingRequestId$.value;
    if (!current || requestId === current) return true;
    return false;
  }

  /** Called by overlay to update remaining steps as each step is consumed.
   *  Does NOT touch active$ — the current step is still running even when queue is empty. */
  updateRemaining(steps: GuideStep[]): void {
    this._remainingSteps = steps;
  }

  /** Re-emit the remaining steps — used when user navigates to wrong page. */
  replay(): void {
    if (this._remainingSteps.length) {
      this.play$.next([...this._remainingSteps]);
    }
  }

  markDone(): void {
    this._remainingSteps = [];
    this.currentPriority = 10;
    this.active$.next(false);
  }

  /** Abort the running guide, optionally only if it matches the given requestId. */
  cancelGuide(requestId?: string): void {
    if (requestId !== undefined && this.pendingRequestId$.value !== requestId) return;
    this._remainingSteps = [];
    this.currentPriority = 10;
    this.active$.next(false);
  }
}
