import { describe, expect, it } from 'vitest';

import {
  createGraphVisualizationPerformanceArtifact,
  GRAPH_VISUALIZATION_PERFORMANCE_COUNTS,
} from './graph-visualization-performance.fixture';

describe('graph visualization performance fixture', () => {
  it('is deterministic, bounded and contains the required coverage', () => {
    const first = createGraphVisualizationPerformanceArtifact();
    const second = createGraphVisualizationPerformanceArtifact();

    expect(first).toEqual(second);
    expect(first.nodes).toHaveLength(GRAPH_VISUALIZATION_PERFORMANCE_COUNTS.nodes);
    expect(first.edges).toHaveLength(GRAPH_VISUALIZATION_PERFORMANCE_COUNTS.edges);
    expect(new Set(first.nodes.map(node => node.attributes['domain_id'])).size).toBe(
      GRAPH_VISUALIZATION_PERFORMANCE_COUNTS.domains,
    );
    expect(first.edges.some(edge => edge.relation === 'fixture_unknown_relation')).toBe(true);
    expect(first.nodes.some(node => node.node_type === 'fixture_unknown_kind')).toBe(true);
    expect(first.nodes.some(node => !('usage_frequency' in (node.attributes['metrics'] as object)))).toBe(true);
    expect(first.edges.some(edge => edge.attributes['confidence'] === 0)).toBe(true);
    expect(first.source_ref).toBe('unverified');
  });
});
