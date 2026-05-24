export type GraphViewMode = 'simple' | '2d' | '3d';

export const GRAPH_VIEW_MODES: GraphViewMode[] = ['simple', '2d', '3d'];

export const GRAPH_VIEW_MODE_LABELS: Record<GraphViewMode, string> = {
  simple: 'Simple List',
  '2d': '2D Graph',
  '3d': '3D Graph',
};
