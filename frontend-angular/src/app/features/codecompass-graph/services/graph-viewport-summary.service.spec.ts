import { TestBed } from '@angular/core/testing';

import { GenericGraphModel } from '../models/graph.model';
import { GraphViewportSummaryService } from './graph-viewport-summary.service';

function graphWithEvidence(): GenericGraphModel {
  return {
    nodes: [
      {
        id: 'a', kind: 'python_file', label: 'A', file: 'orders/a.py', content: '',
        recordId: 'a', domainId: 'orders', metadata: {},
      },
      {
        id: 'b', kind: 'python_function', label: 'B', file: 'orders/b.py', content: '',
        recordId: 'b', domainId: 'orders', metadata: {},
      },
      {
        id: 'c', kind: 'python_file', label: 'C', file: 'billing/c.py', content: '',
        recordId: 'c', domainPath: 'billing', metadata: {},
      },
    ],
    edges: [
      {
        id: 'ab', source: 'a', target: 'b', edgeType: 'calls_probable_target',
        rawEdgeType: 'calls_probable_target', confidence: 1, metadata: {},
      },
      {
        id: 'bc', source: 'b', target: 'c', edgeType: 'depends_on',
        rawEdgeType: 'depends_on', confidence: 1, metadata: {},
      },
      {
        id: 'ac', source: 'a', target: 'c', edgeType: 'depends_on',
        rawEdgeType: 'depends_on', confidence: 1, metadata: {},
      },
    ],
    metadata: {
      sourceRef: 'index-1', sourceKind: 'codecompass_graph', nodeCount: 3, edgeCount: 3,
    },
    warnings: ['Raw backend warning remains unchanged.'],
    evidence: {
      window: {
        view: 'topology',
        nextCursor: null,
        totalNodes: 10,
        totalEdges: 20,
        sourceEdges: 22,
        unresolvedEdges: 2,
        internalEdges: 5,
        edgeCapped: true,
        maxEdges: 3,
      },
      semanticTranslation: {
        schema: 'codecompass_semantic_translation_graph.v1',
        status: 'degraded',
        reasonCode: 'semantic_graph_partial',
        semanticNodeCount: 8,
        semanticEdgeCount: 6,
        equivalenceRuleCount: 0,
        translationContractCount: 0,
        transformArtifactCount: 0,
        budget: {
          configuredMaxRecordsPerPartition: 5_000,
          maxRecordsPerPartition: 5_000,
          maxBytesPerPartition: 4_194_304,
          configurationClamped: false,
          truncated: true,
          truncatedNodeCount: 3,
          truncatedEdgeCount: 1,
          unresolvedEdgeCount: 4,
          semanticNodeBytes: 100,
          semanticEdgeBytes: 50,
          candidateEdgeRecordLimit: 20_000,
          candidateEdgeByteLimit: 16_777_216,
          candidateEdgeCount: 12,
          candidateEdgeBytes: 800,
          truncatedCandidateEdgeCount: 0,
        },
      },
      artifactStatus: {
        state: 'unavailable',
        reasonCode: 'artifact_not_materialized',
        knowledgeIndexId: 'index-1',
        manifestPresent: false,
      },
    },
  };
}

describe('GraphViewportSummaryService', () => {
  let service: GraphViewportSummaryService;

  beforeEach(() => {
    TestBed.configureTestingModule({});
    service = TestBed.inject(GraphViewportSummaryService);
  });

  it('projects immutable visible, loaded, window and total counts', () => {
    const summary = service.project(
      graphWithEvidence(),
      new Set(['a', 'b', 'not-loaded']),
      new Set(['ab', 'not-loaded']),
    );

    expect(summary.nodes).toEqual({ visible: 2, loaded: 3, total: 10 });
    expect(summary.edges).toEqual({
      visible: 1,
      loaded: 3,
      internalWindow: 5,
      total: 20,
    });
    expect(summary.domains).toEqual({ visible: 1, loaded: 2 });
    expect(summary.rawRelationTypes).toEqual({ visible: 1, loaded: 2 });
    expect(summary.nodeWindowBounded).toBe(true);
    expect(summary.edgeWindowCapped).toBe(true);
    expect(summary.projectionIssues).toEqual([
      {
        code: 'graph_node_window_bounded',
        affectedCount: 7,
        reasonCode: null,
      },
      {
        code: 'graph_edge_window_capped',
        affectedCount: 2,
        reasonCode: null,
      },
      {
        code: 'graph_relations_unresolved',
        affectedCount: 2,
        reasonCode: null,
      },
    ]);
    expect(summary.rawWarnings).toEqual(['Raw backend warning remains unchanged.']);
    expect(Object.isFrozen(summary)).toBe(true);
    expect(Object.isFrozen(summary.nodes)).toBe(true);
    expect(Object.isFrozen(summary.projectionIssues)).toBe(true);
    expect(Object.isFrozen(summary.rawWarnings)).toBe(true);
  });

  it('classifies only structured semantic and artifact evidence', () => {
    const summary = service.project(
      graphWithEvidence(),
      new Set(['a', 'b', 'c']),
      new Set(['ab', 'bc', 'ac']),
    );

    expect(summary.semanticState).toBe('partial');
    expect(summary.semanticIssues).toEqual([
      {
        code: 'semantic_graph_truncated',
        affectedCount: 4,
        reasonCode: 'semantic_graph_partial',
      },
      {
        code: 'semantic_graph_relations_unresolved',
        affectedCount: 4,
        reasonCode: 'semantic_graph_partial',
      },
    ]);
    expect(summary.artifactState).toBe('unavailable');
    expect(summary.artifactIssues).toEqual([{
      code: 'graph_artifact_unavailable',
      affectedCount: null,
      reasonCode: 'artifact_not_materialized',
    }]);
  });

  it('keeps missing evidence unknown even when warning prose claims a state', () => {
    const graph = graphWithEvidence();
    delete graph.evidence;
    graph.warnings = [
      'The semantic graph is complete.',
      'The semantic graph is a partial view.',
    ];

    const summary = service.project(
      graph,
      new Set(graph.nodes.map(node => node.id)),
      new Set(graph.edges.map(edge => edge.id)),
    );

    expect(summary.nodes).toEqual({ visible: 3, loaded: 3, total: null });
    expect(summary.edges).toEqual({
      visible: 3,
      loaded: 3,
      internalWindow: null,
      total: null,
    });
    expect(summary.nodeWindowBounded).toBeNull();
    expect(summary.edgeWindowCapped).toBeNull();
    expect(summary.semanticState).toBe('unknown');
    expect(summary.artifactState).toBe('unknown');
    expect(summary.projectionIssues).toEqual([]);
    expect(summary.semanticIssues).toEqual([]);
    expect(summary.artifactIssues).toEqual([]);
    expect(summary.rawWarnings).toEqual(graph.warnings);
  });

  it('recognizes explicit ready evidence as complete without inferring a cap', () => {
    const graph = graphWithEvidence();
    graph.evidence = {
      window: {
        ...graph.evidence!.window!,
        totalNodes: 3,
        totalEdges: 3,
        sourceEdges: 3,
        unresolvedEdges: 0,
        internalEdges: 3,
        edgeCapped: false,
      },
      semanticTranslation: {
        ...graph.evidence!.semanticTranslation!,
        status: 'ready',
        reasonCode: null,
        budget: {
          ...graph.evidence!.semanticTranslation!.budget!,
          truncated: false,
          truncatedNodeCount: 0,
          truncatedEdgeCount: 0,
          unresolvedEdgeCount: 0,
          truncatedCandidateEdgeCount: 0,
        },
      },
      artifactStatus: {
        ...graph.evidence!.artifactStatus!,
        state: 'available',
        reasonCode: null,
        manifestPresent: true,
      },
    };

    const summary = service.project(
      graph,
      new Set(graph.nodes.map(node => node.id)),
      new Set(graph.edges.map(edge => edge.id)),
    );

    expect(summary.nodeWindowBounded).toBe(false);
    expect(summary.edgeWindowCapped).toBe(false);
    expect(summary.semanticState).toBe('complete');
    expect(summary.artifactState).toBe('complete');
    expect(summary.projectionIssues).toEqual([]);
    expect(summary.semanticIssues).toEqual([]);
    expect(summary.artifactIssues).toEqual([]);
  });

  it('does not present contradictory structured window counts as exact totals', () => {
    const graph = graphWithEvidence();
    graph.evidence = {
      ...graph.evidence!,
      window: {
        ...graph.evidence!.window!,
        totalNodes: 1,
        totalEdges: 1,
        internalEdges: 2,
        edgeCapped: false,
      },
    };

    const summary = service.project(graph, new Set(), new Set());

    expect(summary.nodes.total).toBeNull();
    expect(summary.edges.internalWindow).toBeNull();
    expect(summary.edges.total).toBeNull();
    expect(summary.nodeWindowBounded).toBeNull();
    expect(summary.edgeWindowCapped).toBeNull();
    expect(summary.projectionIssues[0]?.code).toBe('graph_window_evidence_inconsistent');
  });

  it('reports candidate-edge truncation as a subset instead of double-counting it', () => {
    const graph = graphWithEvidence();
    graph.evidence = {
      ...graph.evidence!,
      semanticTranslation: {
        ...graph.evidence!.semanticTranslation!,
        budget: {
          ...graph.evidence!.semanticTranslation!.budget!,
          truncated: true,
          truncatedNodeCount: 3,
          truncatedEdgeCount: 5,
          truncatedCandidateEdgeCount: 5,
          unresolvedEdgeCount: 0,
        },
      },
    };

    const summary = service.project(graph, new Set(), new Set());

    expect(summary.semanticIssues).toContainEqual({
      code: 'semantic_graph_truncated',
      affectedCount: 8,
      reasonCode: 'semantic_graph_partial',
    });
    expect(summary.semanticIssues).toContainEqual({
      code: 'semantic_graph_candidate_relations_truncated',
      affectedCount: 5,
      reasonCode: 'semantic_graph_partial',
    });
  });
});
