import { Injectable } from '@angular/core';
import { Subject } from 'rxjs';

export interface GuideStep {
  waypoint: string;
  bubble: string;
  delay_ms?: number;
}

@Injectable({ providedIn: 'root' })
export class SnakeGuideService {
  readonly play$ = new Subject<GuideStep[]>();

  play(steps: GuideStep[]): void {
    this.play$.next(steps);
  }
}
