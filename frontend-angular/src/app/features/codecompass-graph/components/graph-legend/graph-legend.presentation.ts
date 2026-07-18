import {
  DomainLegendEntry,
  GraphMetricCapability,
  GraphMetricScoreBreakdown,
  RelationLegendEntry,
} from '../../models/graph-visual-metrics.model';
import { GraphFilterSelection, graphSelectionContains } from '../../models/graph-filter.model';
import { GraphVisualProfile, WeightedMetricConfig } from '../../models/graph-visual-profile.model';
import {
  GraphDomainLegendEntry,
  GraphEdgeLegendEntry,
  GraphEdgeWidthLegendModel,
  GraphMetricLegendLine,
  GraphNodeSizeLegendModel,
} from './graph-legend.models';

const MARKER_SYMBOL: Readonly<Record<string, string>> = Object.freeze({
  circle: '●', square: '■', triangle: '▲', diamond: '◆',
  hexagon: '⬢', ring: '○', cross: '×', star: '★',
});

export function markerSymbol(marker: string): string {
  return MARKER_SYMBOL[marker] ?? '●';
}

function median(values: readonly number[], fallback: number): number {
  if (!values.length) return fallback;
  const upper = Math.floor(values.length / 2);
  return values.length % 2 ? values[upper] : (values[upper - 1] + values[upper]) / 2;
}

/** Pure presentation mapping. All counts and scores remain projection-owned. */
export function presentDomainLegend(
  entries: readonly Readonly<DomainLegendEntry>[],
  selection: GraphFilterSelection<string>,
): readonly GraphDomainLegendEntry[] {
  return entries.map(entry => ({
    domainId: entry.canonicalId,
    label: entry.label,
    color: entry.color,
    marker: markerSymbol(entry.marker),
    totalNodes: entry.totalCount,
    visibleNodes: entry.visibleCount,
    internalEdges: entry.internalEdges,
    outgoingExternalEdges: entry.outgoingExternalEdges,
    incomingExternalEdges: entry.incomingExternalEdges,
    sumNodeScore: entry.sumNodeScore,
    visible: graphSelectionContains(selection, entry.canonicalId),
  }));
}

export function presentRelationLegend(
  entries: readonly Readonly<RelationLegendEntry>[],
  selection: GraphFilterSelection<string>,
): readonly GraphEdgeLegendEntry[] {
  return entries.map(entry => ({
    rawEdgeType: entry.rawEdgeType,
    label: entry.label,
    color: entry.color,
    marker: markerSymbol(entry.marker),
    semanticState: entry.semanticallyKnown ? 'known' : 'semantically_unknown',
    totalEdges: entry.totalCount,
    visibleEdges: entry.visibleCount,
    multiplicitySum: entry.multiplicitySum,
    visible: graphSelectionContains(selection, entry.rawEdgeType),
  }));
}

function metricLines(
  metrics: readonly WeightedMetricConfig[],
  capabilities: readonly GraphMetricCapability[],
  breakdown: readonly GraphMetricScoreBreakdown[] = [],
): readonly GraphMetricLegendLine[] {
  return metrics.filter(metric => metric.enabled).map(metric => {
    const capability = capabilities.find(item => item.metricId === metric.metricId);
    const scorePart = breakdown.find(item => item.metricId === metric.metricId);
    return {
      metricId: metric.metricId,
      label: metric.metricId,
      weight: metric.weight,
      availability: scorePart?.availability ?? capability?.availability ?? 'unavailable',
      reasonCode: scorePart?.reasonCode ?? capability?.reasonCode ?? (capability ? undefined : 'capability_missing'),
      source: scorePart?.provenance?.source ?? capability?.source,
      partialScore: scorePart?.partialScore,
    };
  });
}

function visibleMetricLines(
  profile: GraphVisualProfile,
  metrics: readonly WeightedMetricConfig[],
  capabilities: readonly GraphMetricCapability[],
  breakdown: readonly GraphMetricScoreBreakdown[] = [],
): readonly GraphMetricLegendLine[] {
  if (!profile.legend.showMetrics) return [];
  const lines = metricLines(metrics, capabilities, breakdown);
  return profile.legend.showUnavailable
    ? lines
    : lines.filter(line => line.availability === 'available' || line.availability === 'approximate');
}

export function presentNodeSizeLegend(
  profile: GraphVisualProfile,
  capabilities: readonly GraphMetricCapability[],
  projectedValues: readonly number[] = [],
): GraphNodeSizeLegendModel {
  const { min, max } = profile.nodeSizeRange;
  const values = projectedValues.filter(Number.isFinite).sort((left, right) => left - right);
  const small = values[0] ?? min;
  const middle = median(values, min + (max - min) / 2);
  const large = values.at(-1) ?? max;
  return {
    profileName: profile.name,
    references: [
      { label: 'klein', value: small },
      { label: 'mittel', value: middle },
      { label: 'groß', value: large },
    ],
    metrics: visibleMetricLines(profile, profile.nodeMetrics, capabilities),
    metricsVisible: profile.legend.showMetrics,
  };
}

export function presentEdgeWidthLegend(
  profile: GraphVisualProfile,
  capabilities: readonly GraphMetricCapability[],
  representativeBreakdown: readonly GraphMetricScoreBreakdown[] = [],
  projectedValues: readonly number[] = [],
): GraphEdgeWidthLegendModel {
  const { min, max } = profile.edgeThicknessRange;
  const values = projectedValues.filter(Number.isFinite).sort((left, right) => left - right);
  const minimum = values[0] ?? min;
  const medianProjection = median(values, min + (max - min) / 2);
  const maximum = values.at(-1) ?? max;
  return {
    references: [
      { label: 'Minimum', value: minimum },
      { label: 'Median', value: medianProjection },
      { label: 'Maximum', value: maximum },
    ],
    metrics: visibleMetricLines(profile, profile.edgeMetrics, capabilities, representativeBreakdown),
    metricsVisible: profile.legend.showMetrics,
  };
}
