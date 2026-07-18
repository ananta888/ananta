export const GRAPH_VISUALIZATION_PERFORMANCE_FIXTURE_VERSION =
  'codecompass-graph-visualization-performance.v1';

export const GRAPH_VISUALIZATION_PERFORMANCE_COUNTS = Object.freeze({
  nodes: 5_000,
  edges: 15_000,
  domains: 30,
  hoverEvents: 100,
});

export interface GraphVisualizationPerformanceArtifact {
  schema: 'domain_graph_artifact.v1';
  source_kind: 'performance_fixture';
  source_ref: 'unverified';
  metadata: {
    source_ref?: string;
    source_kind: 'performance_fixture';
    graph_revision: string;
    node_count: number;
    edge_count: number;
    metric_capabilities: Record<string, Record<string, string>>;
  };
  nodes: Array<{
    node_id: string;
    node_type: string;
    attributes: Record<string, unknown>;
  }>;
  edges: Array<{
    source_id: string;
    target_id: string;
    relation: string;
    attributes: Record<string, unknown>;
  }>;
  warnings: string[];
}

const REVISION = '8e8c8acac19e67ca4bf269f26b6cdffba97e0de564a15d984f1473be92d23a18';
const RELATIONS = Object.freeze([
  'calls_probable_target',
  'imports_symbol',
  'child_of_file',
  'field_type_uses',
  'fixture_unknown_relation',
]);
const NODE_TYPES = Object.freeze([
  'python_function',
  'python_class',
  'java_method',
  'config',
  'fixture_unknown_kind',
]);

/**
 * Build a large, deterministic raw graph without repository content or source IDs.
 * The fixture deliberately contains unknown types and partial metrics so fallback
 * paths are exercised by the same gate as the hot-path projection.
 */
export function createGraphVisualizationPerformanceArtifact(): GraphVisualizationPerformanceArtifact {
  const { nodes: nodeCount, edges: edgeCount, domains } = GRAPH_VISUALIZATION_PERFORMANCE_COUNTS;
  const nodes: GraphVisualizationPerformanceArtifact['nodes'] = [];
  const edges: GraphVisualizationPerformanceArtifact['edges'] = [];

  for (let index = 0; index < nodeCount; index += 1) {
    const domainIndex = index % domains;
    const metrics: Record<string, number> = {
      in_degree: (index * 7) % 43,
      out_degree: (index * 11) % 37,
      total_degree: (index * 13) % 71,
      direct_containment_children: index % 17,
      code_extent: 10 + ((index * 97) % 3_000),
    };
    if (index % 11 !== 0) metrics['usage_frequency'] = (index * 19) % 101;
    if (index % 23 !== 0) metrics['blast_radius'] = (index * 29) % 251;
    nodes.push({
      node_id: `fixture-node-${index.toString().padStart(5, '0')}`,
      node_type: NODE_TYPES[index % NODE_TYPES.length],
      attributes: {
        name: `Fixture node ${index}`,
        file: `src/domain-${domainIndex}/file-${index}.ts`,
        domain_id: `domain-${domainIndex.toString().padStart(2, '0')}`,
        domain_path: `domain-${domainIndex.toString().padStart(2, '0')}`,
        metrics,
      },
    });
  }

  const offsets = [1, 17, 113];
  for (let source = 0; source < nodeCount; source += 1) {
    for (let lane = 0; lane < offsets.length; lane += 1) {
      const target = (source + offsets[lane]) % nodeCount;
      const ordinal = source * offsets.length + lane;
      edges.push({
        source_id: `fixture-node-${source.toString().padStart(5, '0')}`,
        target_id: `fixture-node-${target.toString().padStart(5, '0')}`,
        relation: RELATIONS[ordinal % RELATIONS.length],
        attributes: {
          confidence: ordinal % 101 === 0 ? 0 : 1 - ((ordinal % 10) / 20),
          multiplicity: 1 + (ordinal % 3),
          directed: true,
          metrics: ordinal % 13 === 0
            ? { confidence: ordinal % 101 === 0 ? 0 : 0.5 }
            : {
                confidence: ordinal % 101 === 0 ? 0 : 0.5,
                dependency_weight: ordinal % 97,
              },
        },
      });
    }
  }

  if (edges.length !== edgeCount) {
    throw new Error(`graph_visualization_fixture_edge_count:${edges.length}`);
  }

  return {
    schema: 'domain_graph_artifact.v1',
    source_kind: 'performance_fixture',
    source_ref: 'unverified',
    metadata: {
      source_kind: 'performance_fixture',
      graph_revision: REVISION,
      node_count: nodes.length,
      edge_count: edges.length,
      metric_capabilities: {
        degree: { status: 'available', source: 'fixture', algorithm_version: 'fixture.v1' },
        code_extent: { status: 'available', source: 'fixture', algorithm_version: 'fixture.v1' },
        usage_frequency: { status: 'approximate', source: 'fixture', algorithm_version: 'fixture.v1' },
        blast_radius: { status: 'approximate', source: 'fixture', algorithm_version: 'fixture.v1' },
      },
    },
    nodes,
    edges,
    warnings: [],
  };
}
