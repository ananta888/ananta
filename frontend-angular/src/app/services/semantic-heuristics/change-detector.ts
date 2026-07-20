import { GeometryFeatureGrid } from './geometry-tracker';

export interface ChangeSummary {
  readonly changedCells: number;
  readonly changedRatio: number;
  readonly meanDelta: number;
  readonly classification: 'static' | 'local_change' | 'global_change' | 'unknown';
}

export interface ChangeDetector {
  readonly algorithmVersion: string;
  detect(previous: GeometryFeatureGrid | undefined, current: GeometryFeatureGrid): ChangeSummary;
}

export class AbsoluteGridChangeDetector implements ChangeDetector {
  readonly algorithmVersion = 'absolute-grid-change/1.0.0';

  constructor(private readonly cellThreshold = 24) {}

  detect(previous: GeometryFeatureGrid | undefined, current: GeometryFeatureGrid): ChangeSummary {
    if (!previous || previous.columns !== current.columns || previous.rows !== current.rows
        || previous.activity.byteLength !== current.activity.byteLength || current.activity.byteLength === 0) {
      return Object.freeze({ changedCells: 0, changedRatio: 1, meanDelta: 255, classification: 'unknown' });
    }
    let changedCells = 0;
    let sum = 0;
    for (let index = 0; index < current.activity.byteLength; index += 1) {
      const delta = Math.abs(current.activity[index] - previous.activity[index]);
      sum += delta;
      if (delta >= this.cellThreshold) changedCells += 1;
    }
    const changedRatio = changedCells / current.activity.byteLength;
    return Object.freeze({
      changedCells,
      changedRatio,
      meanDelta: sum / current.activity.byteLength,
      classification: changedRatio === 0 ? 'static' : changedRatio <= 0.25 ? 'local_change' : 'global_change',
    });
  }
}
