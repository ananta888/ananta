import { TestBed } from '@angular/core/testing';
import { GraphAdapterService } from './graph-adapter.service';
import { MOCK_DOMAIN_GRAPH_ARTIFACT } from '../testing/mock-codecompass-graph';
import { ALL_EDGE_TYPES, ALL_NODE_KINDS } from '../models/graph-filter.model';

describe('GraphAdapterService', () => {
  let svc: GraphAdapterService;

  beforeEach(() => {
    TestBed.configureTestingModule({});
    svc = TestBed.inject(GraphAdapterService);
  });

  it('maps mock artifact to GenericGraphModel with correct counts', () => {
    const model = svc.fromDomainArtifact(MOCK_DOMAIN_GRAPH_ARTIFACT);
    expect(model.nodes.length).toBe(20);
    expect(model.edges.length).toBe(30);
  });

  it('maps node_id to id and node_type to kind', () => {
    const model = svc.fromDomainArtifact(MOCK_DOMAIN_GRAPH_ARTIFACT);
    const os = model.nodes.find(n => n.id === 'n-OrderService')!;
    expect(os).toBeTruthy();
    expect(os.kind).toBe('java_type');
    expect(os.label).toBe('OrderService');
    expect(os.file).toBe('src/main/java/shop/OrderService.java');
  });

  it('maps edge relation to edgeType and confidence from attributes', () => {
    const model = svc.fromDomainArtifact(MOCK_DOMAIN_GRAPH_ARTIFACT);
    const e = model.edges.find(e => e.source === 'n-OrderService' && e.edgeType === 'extends')!;
    expect(e).toBeTruthy();
    expect(e.target).toBe('n-BaseService');
    expect(e.confidence).toBe(1.0);
  });

  it('preserves self-graph semantic edge types for styling and filtering', () => {
    const raw = {
      nodes: [
        { node_id: 'a', node_type: 'python_file', attributes: { name: 'quality_gates.py' } },
        { node_id: 'b', node_type: 'python_function', attributes: { name: 'evaluate_quality_gates' } },
      ],
      edges: [
        { source_id: 'a', target_id: 'b', relation: 'imports_symbol', attributes: {} },
        { source_id: 'a', target_id: 'b', relation: 'contains_symbol', attributes: {} },
      ],
    };

    const model = svc.fromDomainArtifact(raw);

    expect(model.edges.map(e => e.edgeType)).toEqual(['imports_symbol', 'contains_symbol']);
  });

  it('maps unknown node_type to "unknown"', () => {
    const raw = {
      nodes: [{ node_id: 'x1', node_type: 'not_a_real_type', attributes: { name: 'X' } }],
      edges: [],
    };
    const model = svc.fromDomainArtifact(raw);
    expect(model.nodes[0].kind).toBe('unknown');
    expect(model.nodes[0].rawNodeType).toBe('not_a_real_type');
    expect(model.nodes[0].knownKind).toBeNull();
  });

  it('maps unknown relation to "related"', () => {
    const raw = {
      nodes: [
        { node_id: 'a', node_type: 'java_type', attributes: { name: 'A' } },
        { node_id: 'b', node_type: 'java_type', attributes: { name: 'B' } },
      ],
      edges: [{ source_id: 'a', target_id: 'b', relation: 'fantasy_relation', attributes: {} }],
    };
    const model = svc.fromDomainArtifact(raw);
    expect(model.edges[0].edgeType).toBe('related');
    expect(model.edges[0].rawEdgeType).toBe('fantasy_relation');
    expect(model.edges[0].knownRelation).toBeNull();
  });

  it('classifies known values canonically without changing their raw spelling', () => {
    const model = svc.fromDomainArtifact({
      nodes: [{ node_id: 'a', node_type: '  TS_FILE  ', attributes: {} }],
      edges: [{
        source_id: 'a',
        target_id: 'a',
        relation: '  DePeNdS_On  ',
        attributes: { confidence: 1, multiplicity: 1 },
      }],
    });

    expect(model.nodes[0]).toMatchObject({
      kind: 'typescript_file',
      knownKind: 'typescript_file',
      rawNodeType: '  TS_FILE  ',
    });
    expect(model.edges[0]).toMatchObject({
      edgeType: 'depends_on',
      knownRelation: 'depends_on',
      rawEdgeType: '  DePeNdS_On  ',
    });
  });

  it('honours backend semantic status while preserving fallback values', () => {
    const model = svc.fromDomainArtifact({
      nodes: [{
        node_id: 'a',
        node_type: 'unknown',
        attributes: {
          raw_node_type: 'custom_widget',
          known_kind: 'unknown',
          semantic_status: 'semantically_unknown',
        },
      }],
      edges: [{
        edge_id: 'edge-a',
        source_id: 'a',
        target_id: 'a',
        relation: 'custom_link',
        attributes: {
          raw_edge_type: 'custom_link',
          known_relation: 'related',
          semantic_status: 'semantically_unknown',
        },
      }],
    });

    expect(model.nodes[0]).toMatchObject({
      kind: 'unknown',
      rawNodeType: 'custom_widget',
      knownKind: null,
    });
    expect(model.edges[0]).toMatchObject({
      id: 'edge-a',
      edgeType: 'related',
      rawEdgeType: 'custom_link',
      knownRelation: null,
    });
  });

  it('returns empty model for null input', () => {
    const model = svc.fromDomainArtifact(null);
    expect(model.nodes).toEqual([]);
    expect(model.edges).toEqual([]);
  });

  it('returns empty model for empty nodes/edges', () => {
    const model = svc.fromDomainArtifact({ nodes: [], edges: [] });
    expect(model.nodes.length).toBe(0);
    expect(model.edges.length).toBe(0);
  });

  it('skips edges with missing source or target', () => {
    const raw = {
      nodes: [],
      edges: [
        { source_id: '', target_id: 'b', relation: 'related', attributes: {} },
        { source_id: 'a', target_id: '', relation: 'related', attributes: {} },
      ],
    };
    const model = svc.fromDomainArtifact(raw);
    expect(model.edges.length).toBe(0);
  });

  it('skips nodes with empty node_id', () => {
    const raw = {
      nodes: [{ node_id: '', node_type: 'java_type', attributes: { name: 'X' } }],
      edges: [],
    };
    const model = svc.fromDomainArtifact(raw);
    expect(model.nodes.length).toBe(0);
  });

  it('propagates warnings from artifact', () => {
    const model = svc.fromDomainArtifact({ nodes: [], edges: [], warnings: ['degraded'] });
    expect(model.warnings).toContain('degraded');
  });

  it('maps structured window, semantic and artifact evidence without parsing warnings', () => {
    const model = svc.fromDomainArtifact({
      nodes: [],
      edges: [],
      metadata: {
        view: 'topology',
        next_cursor: null,
        total_nodes: 10_618,
        total_edges: 107_062,
        source_edge_count: 107_065,
        unresolved_edge_count: 3,
        internal_edge_count: 5_200,
        edge_capped: true,
        max_edges: 2_000,
        semantic_budget: {
          configured_max_records_per_partition: 5_000,
          max_records_per_partition: 5_000,
          max_bytes_per_partition: 4_194_304,
          configuration_clamped: false,
          truncated: true,
          truncated_node_count: 4,
          truncated_edge_count: 2,
          unresolved_edge_count: 7,
          semantic_node_bytes: 500,
          semantic_edge_bytes: 300,
          candidate_edge_record_limit: 20_000,
          candidate_edge_byte_limit: 16_777_216,
          candidate_edge_count: 40,
          candidate_edge_bytes: 4_000,
          truncated_candidate_edge_count: 1,
        },
      },
      diagnostics: {
        semantic_translation: {
          schema: 'codecompass_semantic_translation_graph.v1',
          status: 'degraded',
          reason: 'semantic_graph_partial',
          semantic_node_count: 100,
          semantic_edge_count: 80,
          equivalence_rule_count: 6,
          translation_contract_count: 5,
          transform_artifact_count: 4,
        },
      },
      artifact_status: {
        state: 'available',
        reason_code: null,
        knowledge_index_id: 'index-1',
        manifest_present: true,
      },
      warnings: ['This prose is retained, never classified.'],
    });

    expect(model.evidence?.window).toEqual({
      view: 'topology',
      nextCursor: null,
      totalNodes: 10_618,
      totalEdges: 107_062,
      sourceEdges: 107_065,
      unresolvedEdges: 3,
      internalEdges: 5_200,
      edgeCapped: true,
      maxEdges: 2_000,
    });
    expect(model.evidence?.semanticTranslation).toMatchObject({
      schema: 'codecompass_semantic_translation_graph.v1',
      status: 'degraded',
      reasonCode: 'semantic_graph_partial',
      semanticNodeCount: 100,
      semanticEdgeCount: 80,
      equivalenceRuleCount: 6,
      translationContractCount: 5,
      transformArtifactCount: 4,
      budget: {
        truncated: true,
        truncatedNodeCount: 4,
        truncatedEdgeCount: 2,
        unresolvedEdgeCount: 7,
        truncatedCandidateEdgeCount: 1,
      },
    });
    expect(model.evidence?.artifactStatus).toEqual({
      state: 'available',
      reasonCode: null,
      knowledgeIndexId: 'index-1',
      manifestPresent: true,
    });
    expect(model.warnings).toEqual(['This prose is retained, never classified.']);
    expect(Object.isFrozen(model.evidence)).toBe(true);
    expect(Object.isFrozen(model.evidence?.semanticTranslation?.budget)).toBe(true);
  });

  it('leaves absent evidence unknown and rejects invalid count and boolean values', () => {
    const legacy = svc.fromDomainArtifact({
      nodes: [],
      edges: [],
      warnings: ['The semantic graph is allegedly complete.'],
    });
    expect(legacy.evidence).toBeUndefined();

    const invalid = svc.fromDomainArtifact({
      nodes: [],
      edges: [],
      metadata: {
        view: 'topology',
        total_nodes: '12',
        total_edges: -1,
        source_edge_count: Number.POSITIVE_INFINITY,
        internal_edge_count: 1.5,
        scope_unresolved_edge_count: -2,
        edge_capped: 'false',
        semantic_budget: {
          truncated: 'true',
          truncated_node_count: Number.NaN,
          unresolved_edge_count: -2,
        },
      },
      diagnostics: {
        semantic_translation: {
          status: 5,
          semantic_node_count: Number.POSITIVE_INFINITY,
        },
      },
      artifact_status: {
        state: 7,
        manifest_present: 'yes',
      },
    });

    expect(invalid.evidence?.window).toMatchObject({
      totalNodes: null,
      totalEdges: null,
      sourceEdges: null,
      internalEdges: null,
      scopeUnresolvedEdges: null,
      edgeCapped: null,
    });
    expect(invalid.evidence?.semanticTranslation).toMatchObject({
      status: null,
      semanticNodeCount: null,
      budget: {
        truncated: null,
        truncatedNodeCount: null,
        unresolvedEdgeCount: null,
      },
    });
    expect(invalid.evidence?.artifactStatus).toMatchObject({
      state: null,
      manifestPresent: null,
    });
  });

  it('maps a string artifact status without treating it as semantic evidence', () => {
    const model = svc.fromDomainArtifact({
      nodes: [],
      edges: [],
      artifact_status: 'verified',
    });

    expect(model.evidence).toEqual({
      window: null,
      semanticTranslation: null,
      artifactStatus: {
        state: 'verified',
        reasonCode: null,
        knowledgeIndexId: null,
        manifestPresent: null,
      },
    });
  });

  it('derives edge id from source|target|relation', () => {
    const raw = {
      nodes: [],
      edges: [{ source_id: 'a', target_id: 'b', relation: 'extends', attributes: {} }],
    };
    const model = svc.fromDomainArtifact(raw);
    expect(model.edges[0].id).toBe('a|b|extends');
  });

  it('keeps optional domain hierarchy metadata on nodes', () => {
    const raw = {
      nodes: [{
        node_id: 'a',
        node_type: 'python_file',
        attributes: {
          name: 'pair_groups.py',
          file: 'agent/routes/pair_groups.py',
          domain_path: 'agent.routes.pair_groups',
          domain_level: 1,
        },
      }],
      edges: [],
    };

    const model = svc.fromDomainArtifact(raw);

    expect(model.nodes[0].metadata['domain_path']).toBe('agent.routes.pair_groups');
    expect(model.nodes[0].metadata['domain_level']).toBe(1);
    expect(model.nodes[0].domainPath).toBe('agent.routes.pair_groups');
  });

  it('preserves confidence=0, multiplicity=0, direction and self loops', () => {
    const model = svc.fromDomainArtifact({
      nodes: [{ node_id: 'a', node_type: 'python_file', attributes: {} }],
      edges: [{
        source_id: 'a',
        target_id: 'a',
        relation: 'related',
        attributes: { confidence: 0, multiplicity: 0, directed: false },
      }],
    });

    expect(model.edges[0]).toMatchObject({
      confidence: 0,
      multiplicity: 0,
      directed: false,
      selfLoop: true,
    });
    expect(model.edges[0].metrics?.['confidence']).toMatchObject({ value: 0 });
    expect(model.edges[0].metrics?.['multiplicity']).toMatchObject({ value: 0 });
  });

  it('assigns deterministic collision-free IDs to parallel edges', () => {
    const model = svc.fromDomainArtifact({
      nodes: [],
      edges: [
        { source_id: 'a', target_id: 'b', relation: 'depends_on', attributes: {} },
        { source_id: 'a', target_id: 'b', relation: 'depends_on', attributes: {} },
        { source_id: 'a', target_id: 'b', relation: 'depends_on', attributes: {} },
      ],
    });

    expect(model.edges.map(edge => edge.id)).toEqual([
      'a|b|depends_on',
      'a|b|depends_on|parallel:2',
      'a|b|depends_on|parallel:3',
    ]);
    expect(new Set(model.edges.map(edge => edge.id)).size).toBe(3);
  });

  it('maps graph revision, sorted capabilities, provenance and worker metric aliases', () => {
    const model = svc.fromDomainArtifact({
      graph_revision: 'sha256:revision',
      metric_capabilities: {
        usage_frequency: {
          status: 'approximate',
          source: 'worker-evidence',
          algorithm_version: 'metrics.v1',
          reason_code: 'partial_node_evidence',
        },
        total_degree: {
          status: 'available',
          source: 'worker-topology',
          algorithm_version: 'metrics.v1',
        },
      },
      nodes: [{
        node_id: 'a',
        node_type: 'python_file',
        attributes: { metrics: { degree: 0, usage: 3 } },
      }],
      edges: [],
    });

    expect(model.metadata.graphRevision).toBe('sha256:revision');
    expect(model.metadata.metricCapabilities
      ?.filter(capability => capability.entity === 'node')
      .map(capability => capability.metricId)).toEqual([
      'total_degree',
      'usage_frequency',
    ]);
    expect(model.nodes[0].metrics?.['total_degree']).toMatchObject({
      value: 0,
      availability: 'available',
      provenance: { source: 'worker-topology', algorithmVersion: 'metrics.v1' },
    });
    expect(model.nodes[0].metrics?.['usage_frequency']).toMatchObject({
      value: 3,
      availability: 'approximate',
      reasonCode: 'partial_node_evidence',
    });
    expect(model.metadata.metricCapabilities?.find(capability => capability.metricId === 'confidence'))
      .toMatchObject({ entity: 'edge', availability: 'not_applicable' });
  });

  it('projects intrinsic edge metric availability without topology calculations', () => {
    const model = svc.fromDomainArtifact({
      nodes: [],
      edges: [
        { source_id: 'a', target_id: 'b', relation: 'depends_on', attributes: { dependency_weight: 0 } },
        { source_id: 'b', target_id: 'c', relation: 'depends_on', attributes: {} },
      ],
    });

    expect(model.metadata.metricCapabilities?.filter(capability => capability.entity === 'edge'))
      .toEqual(expect.arrayContaining([
        expect.objectContaining({
          metricId: 'confidence',
          availability: 'approximate',
          reasonCode: 'confidence_defaulted',
        }),
        expect.objectContaining({
          metricId: 'multiplicity',
          availability: 'approximate',
          reasonCode: 'multiplicity_defaulted',
        }),
        expect.objectContaining({
          metricId: 'dependency_weight',
          availability: 'approximate',
          reasonCode: 'partial_edge_evidence',
        }),
      ]));
    expect(model.edges[0].metrics?.['dependency_weight']).toMatchObject({ value: 0 });
  });

  it('marks partial and invalid intrinsic edge evidence fail-closed', () => {
    const model = svc.fromDomainArtifact({
      nodes: [],
      edges: [
        {
          source_id: 'a', target_id: 'b', relation: 'depends_on',
          attributes: { confidence: 0, multiplicity: 0 },
        },
        {
          source_id: 'b', target_id: 'c', relation: 'depends_on',
          attributes: { confidence: 1.01, multiplicity: -1 },
        },
      ],
    });

    expect(model.metadata.metricCapabilities).toEqual(expect.arrayContaining([
      expect.objectContaining({
        entity: 'edge', metricId: 'confidence', availability: 'approximate',
        reasonCode: 'partial_edge_evidence', scope: 'subset',
        limits: { evidence_edge_count: 1, graph_edge_count: 2 },
      }),
      expect.objectContaining({
        entity: 'edge', metricId: 'multiplicity', availability: 'approximate',
        reasonCode: 'partial_edge_evidence', scope: 'subset',
        limits: { evidence_edge_count: 1, graph_edge_count: 2 },
      }),
    ]));
    expect(model.edges[1].confidence).toBe(1);
    expect(model.edges[1].multiplicity).toBe(1);
    expect(model.edges[1].metrics?.['confidence']).toMatchObject({
      value: 1,
      availability: 'approximate',
      reasonCode: 'partial_edge_evidence',
    });
  });

  it('preserves server edge capabilities, scope, limits and evidence revision', () => {
    const model = svc.fromDomainArtifact({
      metric_capabilities: {
        confidence: {
          entity: 'edge',
          scope: 'subset',
          status: 'approximate',
          source: 'hub-projection',
          algorithm_version: 'projection.v1',
          graph_revision: 'evidence-revision',
          reason_code: 'partial_edge_evidence',
          limits: { evidence_edge_count: 1, graph_edge_count: 2 },
        },
      },
      nodes: [],
      edges: [
        { source_id: 'a', target_id: 'b', relation: 'depends_on', attributes: { confidence: 0 } },
        { source_id: 'b', target_id: 'c', relation: 'depends_on', attributes: {} },
      ],
    });

    const capability = model.metadata.metricCapabilities
      ?.find(item => item.entity === 'edge' && item.metricId === 'confidence');
    expect(capability).toMatchObject({
      availability: 'approximate',
      source: 'hub-projection',
      scope: 'subset',
      graphRevision: 'evidence-revision',
      reasonCode: 'partial_edge_evidence',
      limits: { evidence_edge_count: 1, graph_edge_count: 2 },
    });
    expect(model.edges[0].metrics?.['confidence']).toMatchObject({
      value: 0,
      provenance: { graphRevision: 'evidence-revision' },
    });
  });

  it('keeps adapter semantics aligned with the dynamic filter registries', () => {
    expect(new Set(ALL_NODE_KINDS).size).toBe(ALL_NODE_KINDS.length);
    expect(new Set(ALL_EDGE_TYPES).size).toBe(ALL_EDGE_TYPES.length);
    const nodes = ALL_NODE_KINDS
      .filter(kind => kind !== 'unknown')
      .map((kind, index) => ({ node_id: `n-${index}`, node_type: kind, attributes: {} }));
    const edges = ALL_EDGE_TYPES.map((relation, index) => ({
      source_id: `n-${index}`,
      target_id: `n-${index + 1}`,
      relation,
      attributes: {},
    }));

    const model = svc.fromDomainArtifact({ nodes, edges });

    expect(model.nodes.map(node => node.knownKind)).toEqual(ALL_NODE_KINDS.filter(kind => kind !== 'unknown'));
    expect(model.edges.map(edge => edge.knownRelation)).toEqual(ALL_EDGE_TYPES);
  });

  it('maps the architecture ts_file alias without losing its raw type', () => {
    const model = svc.fromDomainArtifact({
      nodes: [{ node_id: 'ts', node_type: 'ts_file', raw_node_type: 'ts_file', attributes: {} }],
      edges: [],
    });

    expect(model.nodes[0]).toMatchObject({
      kind: 'typescript_file',
      knownKind: 'typescript_file',
      rawNodeType: 'ts_file',
    });
  });

  it('recognizes canonical worker-bridge topology vocabulary', () => {
    const model = svc.fromDomainArtifact({
      nodes: [
        { node_id: 'repo', node_type: 'repository', attributes: {} },
        { node_id: 'dir', node_type: 'directory', attributes: {} },
        { node_id: 'file', node_type: 'source_file', attributes: {} },
      ],
      edges: [
        { source_id: 'repo', target_id: 'dir', relation: 'contains_directory', attributes: {} },
        { source_id: 'dir', target_id: 'file', relation: 'contains_file', attributes: {} },
      ],
    });

    expect(model.nodes.map(node => node.knownKind)).toEqual([
      'repository',
      'directory',
      'source_file',
    ]);
    expect(model.edges.map(edge => edge.knownRelation)).toEqual([
      'contains_directory',
      'contains_file',
    ]);
  });

  it('maps global, scope, boundary and delivery evidence without inventing defaults', () => {
    const model = svc.fromDomainArtifact({
      nodes: [],
      edges: [],
      metadata: {
        view: 'topology',
        total_nodes: 40,
        total_edges: 70,
        global_total_nodes: 400,
        global_total_edges: 900,
        scope_total_nodes: 40,
        scope_boundary_edge_count: 11,
        scope_unresolved_edge_count: 4,
        remaining_nodes: 15,
        delivery_complete: false,
      },
    });

    expect(model.evidence?.window).toMatchObject({
      totalNodes: 40,
      totalEdges: 70,
      globalTotalNodes: 400,
      globalTotalEdges: 900,
      scopeTotalNodes: 40,
      scopeBoundaryEdges: 11,
      scopeUnresolvedEdges: 4,
      remainingNodes: 15,
      deliveryComplete: false,
    });
  });
});
