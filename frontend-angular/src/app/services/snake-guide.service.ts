import { Injectable } from '@angular/core';
import { BehaviorSubject, Subject } from 'rxjs';

export interface GuideStep {
  waypoint: string;
  bubble: string;
  delay_ms?: number;
  /** Pixel position override — when set, skips UiWaypointService.resolve() */
  x?: number;
  y?: number;
}

@Injectable({ providedIn: 'root' })
export class SnakeGuideService {
  readonly play$ = new Subject<GuideStep[]>();
  /** True while a guide sequence is active (steps remain or current step running). */
  readonly active$ = new BehaviorSubject<boolean>(false);

  private _remainingSteps: GuideStep[] = [];

  play(steps: GuideStep[]): void {
    this._remainingSteps = [...steps];
    this.active$.next(steps.length > 0);
    this.play$.next(steps);
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
    this.active$.next(false);
  }
}
