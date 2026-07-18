/**
 * Renderer-independent presentation contracts for graph legends.
 *
 * The visual projection facade owns the calculations. Legend components only
 * render immutable values and emit user intent, which keeps them reusable and
 * prevents a second filtering or scoring source of truth.
 */
export type GraphLegendAvailability =
  | 'available'
  | 'approximate'
  | 'unavailable'
  | 'not_applicable';

export interface GraphMetricLegendLine {
  readonly metricId: string;
  readonly label: string;
  readonly weight: number;
  readonly availability: GraphLegendAvailability;
  readonly reasonCode?: string;
  readonly source?: string;
  readonly partialScore?: number;
}

export interface GraphSizeReference {
  readonly label: string;
  readonly value: number;
}

export interface GraphNodeSizeLegendModel {
  readonly profileName: string;
  readonly references: readonly GraphSizeReference[];
  readonly metrics: readonly GraphMetricLegendLine[];
  readonly metricsVisible: boolean;
}

export interface GraphEdgeWidthLegendModel {
  readonly references: readonly GraphSizeReference[];
  readonly metrics: readonly GraphMetricLegendLine[];
  readonly metricsVisible: boolean;
}

export interface GraphDomainLegendEntry {
  readonly domainId: string;
  readonly label: string;
  readonly color: string;
  readonly marker: string;
  readonly totalNodes: number;
  readonly visibleNodes: number;
  readonly internalEdges: number;
  readonly outgoingExternalEdges: number;
  readonly incomingExternalEdges: number;
  readonly sumNodeScore: number;
  readonly visible: boolean;
}

export interface GraphEdgeLegendEntry {
  readonly rawEdgeType: string;
  readonly label: string;
  readonly color: string;
  readonly marker: string;
  readonly semanticState: 'known' | 'semantically_unknown';
  readonly totalEdges: number;
  readonly visibleEdges: number;
  readonly multiplicitySum: number;
  readonly visible: boolean;
}

export interface GraphLegendToggle<T extends string = string> {
  readonly id: T;
  readonly visible: boolean;
}
