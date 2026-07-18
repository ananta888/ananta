import type { GraphHighlightFactors } from './graph-visual-metrics.model';

export const GRAPH_VISUAL_PROFILE_SCHEMA_VERSION = 1 as const;
export const GRAPH_VISUAL_PROFILE_MAX_WEIGHT = 100;
export const GRAPH_VISUAL_PROFILE_IMPORT_MAX_BYTES = 131_072;
export const GRAPH_VISUAL_PROFILE_MAX_DEPTH = 12;
export const GRAPH_VISUAL_PROFILE_CACHE_MAX_ENTRIES = 8;

export const NODE_VISUAL_METRIC_IDS = [
  'in_degree',
  'out_degree',
  'total_degree',
  'direct_containment_children',
  'descendant_count',
  'code_extent',
  'usage_frequency',
  'degree_centrality',
  'bridge_score',
  'blast_radius',
] as const;

export const EDGE_VISUAL_METRIC_IDS = [
  'confidence',
  'multiplicity',
  'dependency_weight',
] as const;

export type NodeVisualMetricId = typeof NODE_VISUAL_METRIC_IDS[number];
export type EdgeVisualMetricId = typeof EDGE_VISUAL_METRIC_IDS[number];
export type GraphVisualMetricId = NodeVisualMetricId | EdgeVisualMetricId;
export type GraphMetricNormalization = 'linear' | 'log1p' | 'sqrt';
export type GraphMetricDirection = 'normal' | 'inverse';

export interface WeightedMetricConfig<TMetricId extends GraphVisualMetricId = GraphVisualMetricId> {
  metricId: TMetricId;
  enabled: boolean;
  weight: number;
  normalization: GraphMetricNormalization;
  direction: GraphMetricDirection;
}

export interface GraphRenderRange {
  min: number;
  max: number;
}

export interface GraphVisualLegendConfig {
  showDomains: boolean;
  showRelations: boolean;
  showMetrics: boolean;
  showUnavailable: boolean;
}

export interface GraphVisualProfile {
  schemaVersion: typeof GRAPH_VISUAL_PROFILE_SCHEMA_VERSION;
  profileId: string;
  name: string;
  nodeMetrics: readonly WeightedMetricConfig<NodeVisualMetricId>[];
  edgeMetrics: readonly WeightedMetricConfig<EdgeVisualMetricId>[];
  nodeSizeRange: Readonly<GraphRenderRange>;
  edgeThicknessRange: Readonly<GraphRenderRange>;
  highlightFactors: Readonly<GraphHighlightFactors>;
  domainColorOverrides: Readonly<Record<string, string>>;
  nodeKindColorOverrides: Readonly<Record<string, string>>;
  relationColorOverrides: Readonly<Record<string, string>>;
  legend: Readonly<GraphVisualLegendConfig>;
}

export type GraphVisualProfilePresetId =
  | 'structure'
  | 'dependencies'
  | 'importance'
  | 'scope'
  | 'change-risk';

function metric<T extends GraphVisualMetricId>(
  metricId: T,
  enabled: boolean,
  weight: number,
  normalization: GraphMetricNormalization = 'linear',
  direction: GraphMetricDirection = 'normal',
): WeightedMetricConfig<T> {
  return { metricId, enabled, weight, normalization, direction };
}

function deepFreeze<T>(value: T): Readonly<T> {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    for (const nested of Object.values(value as Record<string, unknown>)) {
      deepFreeze(nested);
    }
    Object.freeze(value);
  }
  return value;
}

function baseProfile(
  profileId: string,
  name: string,
  nodeMetrics: readonly WeightedMetricConfig<NodeVisualMetricId>[],
  edgeMetrics: readonly WeightedMetricConfig<EdgeVisualMetricId>[],
): GraphVisualProfile {
  return deepFreeze({
    schemaVersion: GRAPH_VISUAL_PROFILE_SCHEMA_VERSION,
    profileId,
    name,
    nodeMetrics,
    edgeMetrics,
    nodeSizeRange: { min: 5, max: 24 },
    edgeThicknessRange: { min: 0.75, max: 6 },
    highlightFactors: { hover: 1.2, selected: 1.5, connected: 1.1 },
    domainColorOverrides: {},
    nodeKindColorOverrides: {},
    relationColorOverrides: {},
    legend: {
      showDomains: true,
      showRelations: true,
      showMetrics: true,
      showUnavailable: true,
    },
  }) as GraphVisualProfile;
}

const STRUCTURE_PROFILE = baseProfile(
  'structure',
  'Struktur',
  [
    metric('total_degree', true, 1),
    metric('in_degree', true, 0.5),
    metric('out_degree', true, 0.5),
    metric('direct_containment_children', true, 1, 'log1p'),
    metric('descendant_count', false, 1, 'log1p'),
    metric('code_extent', false, 1, 'log1p'),
    metric('usage_frequency', false, 1, 'log1p'),
    metric('degree_centrality', false, 1),
    metric('bridge_score', false, 1),
    metric('blast_radius', false, 1),
  ],
  [
    metric('confidence', true, 1),
    metric('multiplicity', true, 0.75, 'log1p'),
    metric('dependency_weight', false, 1, 'log1p'),
  ],
);

const DEPENDENCIES_PROFILE = baseProfile(
  'dependencies',
  'Abhängigkeiten',
  [
    metric('total_degree', true, 0.5),
    metric('in_degree', true, 1),
    metric('out_degree', true, 1),
    metric('direct_containment_children', false, 1, 'log1p'),
    metric('descendant_count', false, 1, 'log1p'),
    metric('code_extent', false, 1, 'log1p'),
    metric('usage_frequency', false, 1, 'log1p'),
    metric('degree_centrality', true, 1),
    metric('bridge_score', true, 1.5),
    metric('blast_radius', false, 1),
  ],
  [
    metric('confidence', true, 0.5),
    metric('multiplicity', true, 1, 'log1p'),
    metric('dependency_weight', true, 1, 'log1p'),
  ],
);

const IMPORTANCE_PROFILE = baseProfile(
  'importance',
  'Wichtigkeit',
  [
    metric('total_degree', true, 0.5),
    metric('in_degree', false, 1),
    metric('out_degree', false, 1),
    metric('direct_containment_children', false, 1, 'log1p'),
    metric('descendant_count', false, 1, 'log1p'),
    metric('code_extent', true, 0.75, 'log1p'),
    metric('usage_frequency', true, 1.5, 'log1p'),
    metric('degree_centrality', true, 1.5),
    metric('bridge_score', true, 1),
    metric('blast_radius', false, 1),
  ],
  [
    metric('confidence', true, 0.5),
    metric('multiplicity', false, 1, 'log1p'),
    metric('dependency_weight', true, 0.75, 'log1p'),
  ],
);

const SCOPE_PROFILE = baseProfile(
  'scope',
  'Umfang',
  [
    metric('total_degree', false, 1),
    metric('in_degree', false, 1),
    metric('out_degree', false, 1),
    metric('direct_containment_children', true, 0.75, 'log1p'),
    metric('descendant_count', true, 1.5, 'log1p'),
    metric('code_extent', true, 0.75, 'log1p'),
    metric('usage_frequency', false, 1, 'log1p'),
    metric('degree_centrality', false, 1),
    metric('bridge_score', true, 0.75),
    metric('blast_radius', true, 2, 'sqrt'),
  ],
  [
    metric('confidence', true, 0.5),
    metric('multiplicity', true, 0.5, 'log1p'),
    metric('dependency_weight', true, 1, 'log1p'),
  ],
);

const CHANGE_RISK_PROFILE = baseProfile(
  'change-risk',
  'Änderungsrisiko',
  [
    metric('total_degree', true, 0.5),
    metric('in_degree', false, 1),
    metric('out_degree', false, 1),
    metric('direct_containment_children', false, 1, 'log1p'),
    metric('descendant_count', true, 1, 'log1p'),
    metric('code_extent', true, 1, 'log1p'),
    metric('usage_frequency', true, 1, 'log1p'),
    metric('degree_centrality', false, 1),
    metric('bridge_score', true, 1),
    metric('blast_radius', true, 1.5, 'sqrt'),
  ],
  [
    metric('confidence', true, 0.25),
    metric('multiplicity', true, 0.5, 'log1p'),
    metric('dependency_weight', true, 1, 'log1p'),
  ],
);

export const GRAPH_VISUAL_PROFILE_PRESETS: Readonly<
  Record<GraphVisualProfilePresetId, GraphVisualProfile>
> = deepFreeze({
  structure: STRUCTURE_PROFILE,
  dependencies: DEPENDENCIES_PROFILE,
  importance: IMPORTANCE_PROFILE,
  scope: SCOPE_PROFILE,
  'change-risk': CHANGE_RISK_PROFILE,
}) as Readonly<Record<GraphVisualProfilePresetId, GraphVisualProfile>>;

export const DEFAULT_GRAPH_VISUAL_PROFILE = STRUCTURE_PROFILE;

export function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== 'object') {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map(item => canonicalJson(item)).join(',')}]`;
  }
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record)
    .sort()
    .map(key => `${JSON.stringify(key)}:${canonicalJson(record[key])}`)
    .join(',')}}`;
}

function sortedMetricConfigs<T extends GraphVisualMetricId>(
  configs: readonly WeightedMetricConfig<T>[],
): readonly WeightedMetricConfig<T>[] {
  return [...configs].sort((left, right) => left.metricId.localeCompare(right.metricId));
}

export function canonicalGraphVisualProfileJson(profile: GraphVisualProfile): string {
  return canonicalJson({
    ...profile,
    nodeMetrics: sortedMetricConfigs(profile.nodeMetrics),
    edgeMetrics: sortedMetricConfigs(profile.edgeMetrics),
  });
}

/** Stable non-cryptographic content hash used only as an in-browser cache key. */
export function graphVisualProfileHash(profile: GraphVisualProfile): string {
  const visualConfig = {
    schemaVersion: profile.schemaVersion,
    nodeMetrics: sortedMetricConfigs(profile.nodeMetrics),
    edgeMetrics: sortedMetricConfigs(profile.edgeMetrics),
    nodeSizeRange: profile.nodeSizeRange,
    edgeThicknessRange: profile.edgeThicknessRange,
    highlightFactors: profile.highlightFactors,
    domainColorOverrides: profile.domainColorOverrides,
    nodeKindColorOverrides: profile.nodeKindColorOverrides,
    relationColorOverrides: profile.relationColorOverrides,
    legend: profile.legend,
  };
  return stableProfileHash(visualConfig);
}

/** Hash of fields that require score/color recomputation rather than presentation rebinding. */
export function graphVisualProfileSemanticHash(profile: GraphVisualProfile): string {
  return stableProfileHash({
    schemaVersion: profile.schemaVersion,
    nodeMetrics: sortedMetricConfigs(profile.nodeMetrics),
    edgeMetrics: sortedMetricConfigs(profile.edgeMetrics),
    nodeSizeRange: profile.nodeSizeRange,
    edgeThicknessRange: profile.edgeThicknessRange,
    domainColorOverrides: profile.domainColorOverrides,
    nodeKindColorOverrides: profile.nodeKindColorOverrides,
    relationColorOverrides: profile.relationColorOverrides,
  });
}

function stableProfileHash(value: unknown): string {
  const input = canonicalJson(value);
  let hash = 0xcbf29ce484222325n;
  for (const char of input) {
    hash ^= BigInt(char.codePointAt(0) ?? 0);
    hash = BigInt.asUintN(64, hash * 0x100000001b3n);
  }
  return `gvp1-${hash.toString(16).padStart(16, '0')}`;
}
