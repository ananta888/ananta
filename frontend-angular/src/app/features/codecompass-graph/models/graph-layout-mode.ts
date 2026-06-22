export type GraphLayoutMode = 'tier' | 'domain' | 'radial';

export const GRAPH_LAYOUT_MODES: GraphLayoutMode[] = ['tier', 'domain', 'radial'];

export const GRAPH_LAYOUT_MODE_LABELS: Record<GraphLayoutMode, string> = {
  tier: 'Tier',
  domain: 'Domain',
  radial: 'Radial',
};
