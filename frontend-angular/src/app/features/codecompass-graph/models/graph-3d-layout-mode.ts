export const GRAPH_3D_LAYOUT_MODES = [
  'force',
  'hierarchical',
  'radial',
] as const;

export type Graph3dLayoutMode = typeof GRAPH_3D_LAYOUT_MODES[number];

export const GRAPH_3D_LAYOUT_MODE_LABELS: Readonly<Record<Graph3dLayoutMode, string>> = {
  force: 'Kraft',
  hierarchical: 'Hierarchisch',
  radial: 'Radial',
};
