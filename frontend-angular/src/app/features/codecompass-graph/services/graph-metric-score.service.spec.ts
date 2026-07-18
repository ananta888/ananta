import { GenericGraphModel, GraphEdge, GraphNode } from '../models/graph.model';
import {
  GraphMetricDatum,
  GraphMetricVector,
} from '../models/graph-visual-metrics.model';
import {
  DEFAULT_GRAPH_VISUAL_PROFILE,
  GraphMetricDirection,
  GraphMetricNormalization,
  GraphVisualProfile,
} from '../models/graph-visual-profile.model';
import { GraphMetricScoreService } from './graph-metric-score.service';

function node(id: string, metrics: GraphMetricVector): GraphNode {
  return {
    id,
    kind: 'python_file',
    rawNodeType: 'python_file',
    knownKind: 'python_file',
    label: id,
    file: `${id}.py`,
    content: '',
    recordId: '',
    metrics,
    metadata: {},
  };
}

function edge(id: string, metrics: GraphMetricVector): GraphEdge {
  return {
    id,
    source: 'low',
    target: 'high',
    edgeType: 'depends_on',
    rawEdgeType: 'depends_on',
    knownRelation: 'depends_on',
    confidence: typeof metrics['confidence'] === 'number' ? metrics['confidence'] : 1,
    multiplicity: typeof metrics['multiplicity'] === 'number' ? metrics['multiplicity'] : 1,
    metrics,
    metadata: {},
  };
}

function graph(nodes: GraphNode[], edges: GraphEdge[] = []): GenericGraphModel {
  return {
    nodes,
    edges,
    metadata: {
      sourceRef: 'test',
      sourceKind: 'fixture',
      graphRevision: 'rev-1',
      nodeCount: nodes.length,
      edgeCount: edges.length,
    },
    warnings: [],
  };
}

function nodeProfile(
  metricId: string,
  normalization: GraphMetricNormalization,
  direction: GraphMetricDirection = 'normal',
): GraphVisualProfile {
  return {
    ...DEFAULT_GRAPH_VISUAL_PROFILE,
    nodeMetrics: DEFAULT_GRAPH_VISUAL_PROFILE.nodeMetrics.map(config => ({
      ...config,
      enabled: config.metricId === metricId,
      weight: config.metricId === metricId ? 1 : 0,
      normalization: config.metricId === metricId ? normalization : config.normalization,
      direction: config.metricId === metricId ? direction : config.direction,
    })),
  } as GraphVisualProfile;
}

describe('GraphMetricScoreService', () => {
  const service = new GraphMetricScoreService();

  it.each([
    ['linear', 'normal', 0.25],
    ['log1p', 'normal', Math.log1p(1) / Math.log1p(4)],
    ['sqrt', 'normal', 0.5],
    ['linear', 'inverse', 0.75],
  ] as const)('normalizes %s in %s direction', (normalization, direction, expected) => {
    const fixture = graph([
      node('target', { total_degree: 1 }),
      node('minimum', { total_degree: 0 }),
      node('maximum', { total_degree: 4 }),
    ]);
    const profile = nodeProfile('total_degree', normalization, direction);

    const result = service.scoreNode(fixture.nodes[0], profile, service.createContext(fixture));

    expect(result.normalizedScore).toBeCloseTo(expected, 12);
    expect(result.renderValue).toBeGreaterThanOrEqual(profile.nodeSizeRange.min);
    expect(result.renderValue).toBeLessThanOrEqual(profile.nodeSizeRange.max);
  });

  it('uses normalized=0.5 and an explicit state for constant ranges', () => {
    const fixture = graph([
      node('a', { total_degree: 2 }),
      node('b', { total_degree: 2 }),
    ]);
    const result = service.scoreNode(
      fixture.nodes[0],
      nodeProfile('total_degree', 'linear'),
      service.createContext(fixture),
    );
    const breakdown = result.breakdown.find(item => item.metricId === 'total_degree')!;

    expect(result.normalizedScore).toBe(0.5);
    expect(breakdown.normalizationState).toBe('constant');
    expect(breakdown.normalizedValue).toBe(0.5);
    expect(breakdown.reasonCode).toBe('constant_metric_range');
  });

  it('combines at least four node and three edge metrics with a complete sorted breakdown', () => {
    const lowNodeMetrics = {
      in_degree: 0,
      out_degree: 2,
      total_degree: 2,
      direct_containment_children: 1,
    };
    const highNodeMetrics = {
      in_degree: 10,
      out_degree: 10,
      total_degree: 20,
      direct_containment_children: 5,
    };
    const edges = [
      edge('low-edge', { confidence: 0, multiplicity: 1, dependency_weight: 2 }),
      edge('high-edge', { confidence: 1, multiplicity: 5, dependency_weight: 10 }),
    ];
    const fixture = graph([node('low', lowNodeMetrics), node('high', highNodeMetrics)], edges);
    const profile = {
      ...DEFAULT_GRAPH_VISUAL_PROFILE,
      nodeMetrics: DEFAULT_GRAPH_VISUAL_PROFILE.nodeMetrics.map(config => ({
        ...config,
        enabled: ['in_degree', 'out_degree', 'total_degree', 'direct_containment_children'].includes(config.metricId),
        weight: 1,
      })),
      edgeMetrics: DEFAULT_GRAPH_VISUAL_PROFILE.edgeMetrics.map(config => ({ ...config, enabled: true, weight: 1 })),
    } as GraphVisualProfile;
    const context = service.createContext(fixture);

    const nodeResult = service.scoreNode(fixture.nodes[0], profile, context);
    const edgeResult = service.scoreEdge(fixture.edges[0], profile, context);
    const nodeActive = nodeResult.breakdown.filter(item => item.normalizedValue !== null && item.weight > 0);
    const edgeActive = edgeResult.breakdown.filter(item => item.normalizedValue !== null && item.weight > 0);

    expect(nodeActive).toHaveLength(4);
    expect(edgeActive).toHaveLength(3);
    expect(nodeResult.breakdown.map(item => item.metricId)).toEqual(
      [...nodeResult.breakdown.map(item => item.metricId)].sort(),
    );
    expect(nodeResult.breakdown.reduce((sum, item) => sum + item.partialScore, 0))
      .toBeCloseTo(nodeResult.unclampedScore, 12);
    for (const item of [...nodeActive, ...edgeActive]) {
      expect(item).toEqual(expect.objectContaining({
        rawValue: expect.any(Number),
        normalizedValue: expect.any(Number),
        normalizationState: expect.any(String),
        weight: expect.any(Number),
        direction: expect.any(String),
        partialScore: expect.any(Number),
        availability: expect.any(String),
      }));
      expect(Object.prototype.hasOwnProperty.call(item, 'provenance')).toBe(true);
      expect(Object.prototype.hasOwnProperty.call(item, 'reasonCode')).toBe(true);
    }
  });

  it('uses the configured minimum for zero weights and missing metrics', () => {
    const fixture = graph([node('a', {})]);
    const profile = {
      ...DEFAULT_GRAPH_VISUAL_PROFILE,
      nodeMetrics: DEFAULT_GRAPH_VISUAL_PROFILE.nodeMetrics.map(config => ({ ...config, enabled: true, weight: 0 })),
    } as GraphVisualProfile;

    const result = service.scoreNode(fixture.nodes[0], profile, service.createContext(fixture));

    expect(result.state).toBe('degraded_no_active_metric');
    expect(result.availability).toBe('unavailable');
    expect(result.renderValue).toBe(profile.nodeSizeRange.min);
    expect(result.breakdown.every(item => item.reasonCode === 'metric_weight_zero')).toBe(true);
  });

  it.each([
    ['negative', -1, 'metric_negative'],
    ['NaN', Number.NaN, 'metric_not_finite'],
    ['Infinity', Number.POSITIVE_INFINITY, 'metric_not_finite'],
  ])('rejects %s raw metrics without producing a non-finite render value', (_label, value, reasonCode) => {
    const fixture = graph([
      node('invalid', { total_degree: value }),
      node('valid', { total_degree: 4 }),
    ]);
    const result = service.scoreNode(
      fixture.nodes[0],
      nodeProfile('total_degree', 'linear'),
      service.createContext(fixture),
    );
    const item = result.breakdown.find(entry => entry.metricId === 'total_degree')!;

    expect(item.reasonCode).toBe(reasonCode);
    expect(item.availability).toBe('unavailable');
    expect(result.state).toBe('degraded_no_active_metric');
    expect(Number.isFinite(result.renderValue)).toBe(true);
  });

  it('preserves approximate availability even when its active normalized value is zero', () => {
    const approximate: GraphMetricDatum = {
      value: 0,
      availability: 'approximate',
      provenance: { source: 'worker', algorithmVersion: 'v1' },
      reasonCode: 'partial_node_evidence',
    };
    const fixture = graph([
      node('target', { usage_frequency: approximate }),
      node('max', { usage_frequency: 1 }),
    ]);

    const result = service.scoreNode(
      fixture.nodes[0],
      nodeProfile('usage_frequency', 'linear'),
      service.createContext(fixture),
    );

    expect(result.normalizedScore).toBe(0);
    expect(result.availability).toBe('approximate');
    expect(result.breakdown.find(item => item.metricId === 'usage_frequency')).toMatchObject({
      availability: 'approximate',
      reasonCode: 'partial_node_evidence',
    });
  });

  it('is finite, bounded and monotone over a deterministic value sequence', () => {
    const nodes = Array.from({ length: 101 }, (_, value) => node(String(value), { total_degree: value }));
    const fixture = graph(nodes);
    const profile = nodeProfile('total_degree', 'sqrt');
    const context = service.createContext(fixture);
    const scores = nodes.map(item => service.scoreNode(item, profile, context).normalizedScore);

    expect(scores.every(score => Number.isFinite(score) && score >= 0 && score <= 1)).toBe(true);
    expect(scores.every((score, index) => index === 0 || score >= scores[index - 1])).toBe(true);
  });
});
