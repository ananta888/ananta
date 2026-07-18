export type MetricAvailability = 'available' | 'approximate' | 'unavailable' | 'not_applicable';

export type GraphMetricEntity = 'node' | 'edge' | 'graph';

export interface GraphMetricProvenance {
  source: string;
  algorithmVersion: string;
  graphRevision?: string;
}

export interface GraphMetricCapability {
  metricId: string;
  entity: GraphMetricEntity;
  availability: MetricAvailability;
  source: string;
  algorithmVersion: string;
  reasonCode?: string;
  scope?: string;
  graphRevision?: string;
  limits?: Readonly<Record<string, string | number | boolean | null>>;
}

export interface GraphMetricDatum {
  value?: number;
  availability: MetricAvailability;
  provenance?: GraphMetricProvenance;
  reasonCode?: string;
}

/**
 * Number values are accepted for backwards-compatible graph payloads. The
 * adapter expands them to available GraphMetricDatum values before scoring.
 */
export type GraphMetricVector = Readonly<Record<string, GraphMetricDatum | number>>;

export type MetricNormalizationState =
  | 'normalized'
  | 'constant'
  | 'missing'
  | 'invalid'
  | 'unavailable'
  | 'not_applicable';

export interface GraphMetricScoreBreakdown {
  metricId: string;
  rawValue: number | null;
  normalizedValue: number | null;
  normalizationState: MetricNormalizationState;
  weight: number;
  direction: 'normal' | 'inverse';
  partialScore: number;
  availability: MetricAvailability;
  provenance: GraphMetricProvenance | null;
  reasonCode: string | null;
}

export type GraphMetricScoreState = 'scored' | 'degraded_no_active_metric';

export interface GraphMetricScoreResult {
  normalizedScore: number;
  unclampedScore: number;
  renderValue: number;
  state: GraphMetricScoreState;
  availability: MetricAvailability;
  breakdown: readonly GraphMetricScoreBreakdown[];
}

export type GraphVisualMarker =
  | 'circle'
  | 'square'
  | 'triangle'
  | 'diamond'
  | 'hexagon'
  | 'ring'
  | 'cross'
  | 'star';

export interface GraphHighlightFactors {
  hover: number;
  selected: number;
  connected: number;
}

export interface NodeVisualStyle {
  nodeId: string;
  baseColor: string;
  marker: GraphVisualMarker;
  baseSize: number;
  score: number;
  scoreState: GraphMetricScoreState;
  availability: MetricAvailability;
  breakdown: readonly GraphMetricScoreBreakdown[];
  highlightFactors: Readonly<GraphHighlightFactors>;
}

export interface EdgeVisualStyle {
  edgeId: string;
  baseColor: string;
  marker: GraphVisualMarker;
  baseThickness: number;
  score: number;
  scoreState: GraphMetricScoreState;
  availability: MetricAvailability;
  breakdown: readonly GraphMetricScoreBreakdown[];
  highlightFactors: Readonly<GraphHighlightFactors>;
}

export interface DomainLegendEntry {
  canonicalId: string;
  label: string;
  color: string;
  marker: GraphVisualMarker;
  totalCount: number;
  visibleCount: number;
  internalEdges: number;
  outgoingExternalEdges: number;
  incomingExternalEdges: number;
  sumNodeScore: number;
}

export interface RelationLegendEntry {
  rawEdgeType: string;
  label: string;
  color: string;
  marker: GraphVisualMarker;
  semanticallyKnown: boolean;
  totalCount: number;
  visibleCount: number;
  multiplicitySum: number;
}

export interface GraphVisualProjection {
  graphRevision: string;
  profileHash: string;
  nodeStyles: Readonly<Record<string, Readonly<NodeVisualStyle>>>;
  edgeStyles: Readonly<Record<string, Readonly<EdgeVisualStyle>>>;
  domainLegend: readonly Readonly<DomainLegendEntry>[];
  relationLegend: readonly Readonly<RelationLegendEntry>[];
}
