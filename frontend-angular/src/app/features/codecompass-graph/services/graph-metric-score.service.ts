import { Injectable } from '@angular/core';
import { GenericGraphModel, GraphEdge, GraphNode } from '../models/graph.model';
import {
  GraphMetricCapability,
  GraphMetricDatum,
  GraphMetricEntity,
  GraphMetricProvenance,
  GraphMetricScoreBreakdown,
  GraphMetricScoreResult,
  GraphMetricVector,
  MetricAvailability,
  MetricNormalizationState,
} from '../models/graph-visual-metrics.model';
import {
  GraphMetricNormalization,
  GraphRenderRange,
  GraphVisualMetricId,
  GraphVisualProfile,
  WeightedMetricConfig,
} from '../models/graph-visual-profile.model';

export interface GraphMetricValueRange {
  min: number;
  max: number;
  count: number;
}

export interface GraphMetricNormalizationContext {
  graphRevision: string;
  nodeRanges: Readonly<Record<string, Readonly<GraphMetricValueRange>>>;
  edgeRanges: Readonly<Record<string, Readonly<GraphMetricValueRange>>>;
  capabilities: Readonly<Record<string, Readonly<GraphMetricCapability>>>;
}

interface PreparedBreakdown {
  metricId: string;
  rawValue: number | null;
  normalizedValue: number | null;
  normalizationState: MetricNormalizationState;
  weight: number;
  direction: 'normal' | 'inverse';
  availability: MetricAvailability;
  provenance: GraphMetricProvenance | null;
  reasonCode: string | null;
  activeValue: number | null;
}

@Injectable({ providedIn: 'root' })
export class GraphMetricScoreService {
  /**
   * Visual profiles are immutable by contract. Cache their deterministic
   * metric order by array identity so a 5k/15k projection does not repeat the
   * same sort once per graph entity.
   */
  private readonly orderedMetricConfigs = new WeakMap<
    readonly WeightedMetricConfig<GraphVisualMetricId>[],
    readonly WeightedMetricConfig<GraphVisualMetricId>[]
  >();

  createContext(graph: GenericGraphModel): Readonly<GraphMetricNormalizationContext> {
    const nodeRanges = this.collectRanges(graph.nodes.map(node => node.metrics ?? {}));
    const edgeRanges = this.collectRanges(graph.edges.map(edge => ({
      ...(edge.metrics ?? {}),
      confidence: edge.metrics?.['confidence'] ?? edge.confidence,
      multiplicity: edge.metrics?.['multiplicity'] ?? edge.multiplicity ?? 1,
    })));
    const capabilities: Record<string, Readonly<GraphMetricCapability>> = {};
    for (const capability of graph.metadata.metricCapabilities ?? []) {
      capabilities[`${capability.entity}:${capability.metricId}`] = Object.freeze({ ...capability });
    }
    return Object.freeze({
      graphRevision: graph.metadata.graphRevision ?? '',
      nodeRanges,
      edgeRanges,
      capabilities: Object.freeze(capabilities),
    });
  }

  scoreNode(
    node: GraphNode,
    profile: GraphVisualProfile,
    context: GraphMetricNormalizationContext,
  ): Readonly<GraphMetricScoreResult> {
    return this.score(
      'node',
      node.metrics ?? {},
      profile.nodeMetrics,
      profile.nodeSizeRange,
      context.nodeRanges,
      context,
    );
  }

  scoreEdge(
    edge: GraphEdge,
    profile: GraphVisualProfile,
    context: GraphMetricNormalizationContext,
  ): Readonly<GraphMetricScoreResult> {
    const metrics: Record<string, GraphMetricDatum | number> = {
      ...(edge.metrics ?? {}),
    };
    if (!Object.prototype.hasOwnProperty.call(metrics, 'confidence')) metrics['confidence'] = edge.confidence;
    if (!Object.prototype.hasOwnProperty.call(metrics, 'multiplicity')) metrics['multiplicity'] = edge.multiplicity ?? 1;
    return this.score(
      'edge',
      metrics,
      profile.edgeMetrics,
      profile.edgeThicknessRange,
      context.edgeRanges,
      context,
    );
  }

  private score(
    entity: GraphMetricEntity,
    vector: GraphMetricVector,
    configs: readonly WeightedMetricConfig<GraphVisualMetricId>[],
    renderRange: Readonly<GraphRenderRange>,
    ranges: Readonly<Record<string, Readonly<GraphMetricValueRange>>>,
    context: GraphMetricNormalizationContext,
  ): Readonly<GraphMetricScoreResult> {
    const prepared = this.orderedConfigs(configs)
      .map(config => this.prepareBreakdown(entity, vector, config, ranges[config.metricId], context));
    const activeWeight = prepared.reduce((sum, item) =>
      sum + (item.activeValue === null ? 0 : item.weight), 0);

    if (!Number.isFinite(activeWeight) || activeWeight <= 0) {
      const breakdown = prepared.map(item => this.finalBreakdown(item, 0));
      return Object.freeze({
        normalizedScore: 0,
        unclampedScore: 0,
        renderValue: this.finiteRangeMinimum(renderRange),
        state: 'degraded_no_active_metric',
        availability: 'unavailable',
        breakdown: Object.freeze(breakdown),
      });
    }

    const breakdown = prepared.map(item => this.finalBreakdown(
      item,
      item.activeValue === null ? 0 : item.activeValue * item.weight / activeWeight,
    ));
    const unclampedScore = breakdown.reduce((sum, item) => sum + item.partialScore, 0);
    const normalizedScore = this.clamp(unclampedScore, 0, 1);
    const min = this.finiteRangeMinimum(renderRange);
    const max = Number.isFinite(renderRange.max) && renderRange.max >= min ? renderRange.max : min;
    const renderValue = min + normalizedScore * (max - min);
    const availabilityValue: MetricAvailability = prepared.some(item =>
      item.activeValue !== null && item.availability === 'approximate')
      ? 'approximate'
      : 'available';
    return Object.freeze({
      normalizedScore,
      unclampedScore,
      renderValue: Number.isFinite(renderValue) ? renderValue : min,
      state: 'scored',
      availability: availabilityValue,
      breakdown: Object.freeze(breakdown),
    });
  }

  private orderedConfigs(
    configs: readonly WeightedMetricConfig<GraphVisualMetricId>[],
  ): readonly WeightedMetricConfig<GraphVisualMetricId>[] {
    const cached = this.orderedMetricConfigs.get(configs);
    if (cached) return cached;
    const ordered = Object.freeze(
      [...configs].sort((left, right) => left.metricId.localeCompare(right.metricId)),
    );
    this.orderedMetricConfigs.set(configs, ordered);
    return ordered;
  }

  private prepareBreakdown(
    entity: GraphMetricEntity,
    vector: GraphMetricVector,
    config: WeightedMetricConfig<GraphVisualMetricId>,
    range: Readonly<GraphMetricValueRange> | undefined,
    context: GraphMetricNormalizationContext,
  ): PreparedBreakdown {
    const datum = this.resolveDatum(entity, config.metricId, vector[config.metricId], context);
    const base = {
      metricId: config.metricId,
      rawValue: datum.value ?? null,
      weight: config.weight,
      direction: config.direction,
      availability: datum.availability,
      provenance: datum.provenance ?? null,
    };
    if (!config.enabled) {
      return {
        ...base,
        normalizedValue: null,
        normalizationState: 'unavailable',
        reasonCode: 'metric_disabled',
        activeValue: null,
      };
    }
    if (config.weight <= 0 || !Number.isFinite(config.weight)) {
      return {
        ...base,
        normalizedValue: null,
        normalizationState: 'unavailable',
        reasonCode: 'metric_weight_zero',
        activeValue: null,
      };
    }
    if (datum.availability === 'unavailable' || datum.availability === 'not_applicable') {
      return {
        ...base,
        normalizedValue: null,
        normalizationState: datum.availability === 'not_applicable' ? 'not_applicable' : 'unavailable',
        reasonCode: datum.reasonCode ?? 'metric_unavailable',
        activeValue: null,
      };
    }
    if (datum.value === undefined) {
      return {
        ...base,
        normalizedValue: null,
        normalizationState: 'missing',
        reasonCode: datum.reasonCode ?? 'metric_value_missing',
        activeValue: null,
      };
    }
    if (!Number.isFinite(datum.value) || datum.value < 0) {
      return {
        ...base,
        availability: 'unavailable',
        normalizedValue: null,
        normalizationState: 'invalid',
        reasonCode: !Number.isFinite(datum.value) ? 'metric_not_finite' : 'metric_negative',
        activeValue: null,
      };
    }
    if (!range || range.count === 0) {
      return {
        ...base,
        normalizedValue: null,
        normalizationState: 'missing',
        reasonCode: 'normalization_range_missing',
        activeValue: null,
      };
    }
    const transformedValue = this.transform(datum.value, config.normalization);
    const transformedMin = this.transform(range.min, config.normalization);
    const transformedMax = this.transform(range.max, config.normalization);
    if (![transformedValue, transformedMin, transformedMax].every(Number.isFinite)) {
      return {
        ...base,
        normalizedValue: null,
        normalizationState: 'invalid',
        reasonCode: 'normalization_not_finite',
        activeValue: null,
      };
    }
    const constant = Math.abs(transformedMax - transformedMin) <= Number.EPSILON;
    const normalized = constant
      ? 0.5
      : this.clamp((transformedValue - transformedMin) / (transformedMax - transformedMin), 0, 1);
    const directed = config.direction === 'inverse' ? 1 - normalized : normalized;
    return {
      ...base,
      normalizedValue: normalized,
      normalizationState: constant ? 'constant' : 'normalized',
      reasonCode: constant ? 'constant_metric_range' : datum.reasonCode ?? null,
      activeValue: directed,
    };
  }

  private resolveDatum(
    entity: GraphMetricEntity,
    metricIdValue: string,
    raw: GraphMetricDatum | number | undefined,
    context: GraphMetricNormalizationContext,
  ): GraphMetricDatum {
    const capability = context.capabilities[`${entity}:${metricIdValue}`]
      ?? context.capabilities[`graph:${metricIdValue}`];
    const provenance = capability ? {
      source: capability.source,
      algorithmVersion: capability.algorithmVersion,
      graphRevision: capability.graphRevision ?? (context.graphRevision || undefined),
    } : undefined;
    if (typeof raw === 'number') {
      return {
        value: raw,
        availability: capability?.availability ?? 'available',
        provenance,
        reasonCode: capability?.reasonCode,
      };
    }
    if (raw) {
      return {
        ...raw,
        provenance: raw.provenance
          ? {
            ...raw.provenance,
            graphRevision: raw.provenance.graphRevision
              ?? capability?.graphRevision
              ?? (context.graphRevision || undefined),
          }
          : provenance,
      };
    }
    return {
      availability: capability?.availability ?? 'unavailable',
      provenance,
      reasonCode: capability?.reasonCode ?? 'metric_missing',
    };
  }

  private collectRanges(vectors: readonly GraphMetricVector[]): Readonly<Record<string, Readonly<GraphMetricValueRange>>> {
    const values = new Map<string, number[]>();
    for (const vector of vectors) {
      for (const [id, raw] of Object.entries(vector)) {
        const datum = typeof raw === 'number' ? { value: raw, availability: 'available' } : raw;
        if ((datum.availability === 'available' || datum.availability === 'approximate')
          && typeof datum.value === 'number'
          && Number.isFinite(datum.value)
          && datum.value >= 0) {
          const bucket = values.get(id) ?? [];
          bucket.push(datum.value);
          values.set(id, bucket);
        }
      }
    }
    const ranges: Record<string, Readonly<GraphMetricValueRange>> = {};
    for (const [id, bucket] of [...values.entries()].sort(([left], [right]) => left.localeCompare(right))) {
      ranges[id] = Object.freeze({
        min: Math.min(...bucket),
        max: Math.max(...bucket),
        count: bucket.length,
      });
    }
    return Object.freeze(ranges);
  }

  private transform(value: number, normalization: GraphMetricNormalization): number {
    if (normalization === 'log1p') return Math.log1p(value);
    if (normalization === 'sqrt') return Math.sqrt(value);
    return value;
  }

  private finalBreakdown(item: PreparedBreakdown, partialScore: number): Readonly<GraphMetricScoreBreakdown> {
    const { activeValue: _activeValue, ...publicItem } = item;
    return Object.freeze({
      ...publicItem,
      partialScore: Number.isFinite(partialScore) ? partialScore : 0,
    });
  }

  private finiteRangeMinimum(range: Readonly<GraphRenderRange>): number {
    return Number.isFinite(range.min) ? range.min : 0;
  }

  private clamp(value: number, min: number, max: number): number {
    return Math.min(max, Math.max(min, value));
  }
}
