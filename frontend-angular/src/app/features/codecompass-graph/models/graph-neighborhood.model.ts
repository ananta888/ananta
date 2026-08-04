export const MAX_GRAPH_NEIGHBORHOOD_DEPTH = 6;

export const GRAPH_NEIGHBORHOOD_DEPTH_OPTIONS: readonly number[] = Object.freeze(
  Array.from(
    { length: MAX_GRAPH_NEIGHBORHOOD_DEPTH },
    (_value, index) => index + 1,
  ),
);
