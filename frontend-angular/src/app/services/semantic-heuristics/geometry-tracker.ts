export interface GeometryFeatureGrid {
  readonly columns: number;
  readonly rows: number;
  readonly activity: Uint8Array;
}

export interface TrackedGeometry {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
  readonly confidence: number;
}

export interface GeometryTracker {
  readonly algorithmVersion: string;
  track(input: GeometryFeatureGrid): readonly TrackedGeometry[];
}

/** Deterministic bounded connected-component tracker over a coarse safe grid. */
export class BoundedGridGeometryTracker implements GeometryTracker {
  readonly algorithmVersion = 'bounded-grid-geometry/1.0.0';

  constructor(private readonly threshold = 32, private readonly maxRegions = 64) {}

  track(input: GeometryFeatureGrid): readonly TrackedGeometry[] {
    if (!Number.isSafeInteger(input.columns) || !Number.isSafeInteger(input.rows)
        || input.columns < 1 || input.rows < 1 || input.columns * input.rows !== input.activity.byteLength
        || input.activity.byteLength > 4096) return [];
    const seen = new Uint8Array(input.activity.byteLength);
    const regions: TrackedGeometry[] = [];
    for (let start = 0; start < input.activity.byteLength && regions.length < this.maxRegions; start += 1) {
      if (seen[start] || input.activity[start] < this.threshold) continue;
      const queue = [start];
      seen[start] = 1;
      let head = 0;
      let minX = input.columns, minY = input.rows, maxX = 0, maxY = 0, strength = 0;
      while (head < queue.length && queue.length <= 4096) {
        const index = queue[head++];
        const x = index % input.columns;
        const y = Math.floor(index / input.columns);
        minX = Math.min(minX, x); minY = Math.min(minY, y);
        maxX = Math.max(maxX, x); maxY = Math.max(maxY, y);
        strength += input.activity[index];
        for (const [dx, dy] of [[-1, 0], [1, 0], [0, -1], [0, 1]] as const) {
          const nx = x + dx, ny = y + dy;
          if (nx < 0 || ny < 0 || nx >= input.columns || ny >= input.rows) continue;
          const next = ny * input.columns + nx;
          if (!seen[next] && input.activity[next] >= this.threshold) {
            seen[next] = 1;
            queue.push(next);
          }
        }
      }
      regions.push(Object.freeze({
        x: minX / input.columns,
        y: minY / input.rows,
        width: (maxX - minX + 1) / input.columns,
        height: (maxY - minY + 1) / input.rows,
        confidence: Math.min(1, strength / (queue.length * 255)),
      }));
    }
    return Object.freeze(regions);
  }
}
