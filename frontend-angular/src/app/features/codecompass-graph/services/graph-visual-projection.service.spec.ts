import { TestBed } from '@angular/core/testing';
import { GenericGraphModel, GraphEdge, GraphNode } from '../models/graph.model';
import {
  DEFAULT_GRAPH_VISUAL_PROFILE,
  GraphVisualProfile,
} from '../models/graph-visual-profile.model';
import { GraphColorService } from './graph-color.service';
import { GraphMetricScoreService } from './graph-metric-score.service';
import { GraphVisualProjectionService } from './graph-visual-projection.service';

function graph(revision = 'revision-1'): GenericGraphModel {
  const nodes: GraphNode[] = [
    ['a', 'orders', 1],
    ['b', 'orders', 3],
    ['c', 'billing', 5],
  ].map(([id, domainId, degree]) => ({
    id: String(id),
    kind: 'python_file',
    rawNodeType: 'python_file',
    knownKind: 'python_file',
    label: String(id),
    file: `${id}.py`,
    content: '',
    recordId: '',
    domainId: String(domainId),
    metrics: {
      total_degree: Number(degree),
      in_degree: Number(degree),
      out_degree: Number(degree),
      direct_containment_children: Number(degree),
    },
    metadata: {},
  }));
  const edges: GraphEdge[] = [
    ['ab', 'a', 'b', 'depends_on', 2],
    ['bc', 'b', 'c', 'depends_on', 3],
    ['ca', 'c', 'a', 'custom_link', 1],
  ].map(([id, source, target, rawEdgeType, multiplicity]) => ({
    id: String(id),
    source: String(source),
    target: String(target),
    edgeType: rawEdgeType === 'depends_on' ? 'depends_on' : 'related',
    rawEdgeType: String(rawEdgeType),
    knownRelation: rawEdgeType === 'depends_on' ? 'depends_on' : null,
    confidence: 1,
    multiplicity: Number(multiplicity),
    directed: true,
    selfLoop: false,
    metrics: { confidence: 1, multiplicity: Number(multiplicity), dependency_weight: Number(multiplicity) },
    metadata: {},
  } as GraphEdge));
  return {
    nodes,
    edges,
    metadata: {
      sourceRef: 'repo-a',
      sourceKind: 'repository',
      graphRevision: revision,
      nodeCount: nodes.length,
      edgeCount: edges.length,
    },
    warnings: [],
  };
}

function profileWithDomainColor(index: number): GraphVisualProfile {
  return {
    ...DEFAULT_GRAPH_VISUAL_PROFILE,
    domainColorOverrides: {
      orders: `#${(index + 1).toString(16).padStart(6, '0')}`,
    },
  } as GraphVisualProfile;
}

describe('GraphVisualProjectionService', () => {
  function service(): {
    projection: GraphVisualProjectionService;
    score: GraphMetricScoreService;
  } {
    const score = new GraphMetricScoreService();
    return {
      score,
      projection: new GraphVisualProjectionService(score, new GraphColorService()),
    };
  }

  it('resolves through Angular DI without decorator metadata', () => {
    TestBed.configureTestingModule({});
    expect(TestBed.inject(GraphVisualProjectionService)).toBeTruthy();
  });

  it('projects immutable renderer-independent node and edge styles', () => {
    const { projection } = service();
    const result = projection.project(graph(), DEFAULT_GRAPH_VISUAL_PROFILE);

    expect(Object.keys(result.nodeStyles)).toEqual(['a', 'b', 'c']);
    expect(Object.keys(result.edgeStyles)).toEqual(['ab', 'bc', 'ca']);
    expect(result.nodeStyles['a']).toEqual(expect.objectContaining({
      nodeId: 'a',
      baseColor: expect.stringMatching(/^#[0-9A-F]{6}$/),
      marker: expect.any(String),
      baseSize: expect.any(Number),
      breakdown: expect.any(Array),
      availability: expect.any(String),
      highlightFactors: DEFAULT_GRAPH_VISUAL_PROFILE.highlightFactors,
    }));
    expect(result.edgeStyles['ab']).toEqual(expect.objectContaining({
      edgeId: 'ab',
      baseThickness: expect.any(Number),
      breakdown: expect.any(Array),
    }));
    expect(Object.isFrozen(result)).toBe(true);
    expect(Object.isFrozen(result.nodeStyles)).toBe(true);
    expect(Object.isFrozen(result.nodeStyles['a'])).toBe(true);
  });

  it('is the single source for domain and relation inventory metrics', () => {
    const { projection } = service();
    const result = projection.project(graph(), DEFAULT_GRAPH_VISUAL_PROFILE);
    const orders = result.domainLegend.find(entry => entry.canonicalId === 'orders')!;
    const billing = result.domainLegend.find(entry => entry.canonicalId === 'billing')!;
    const dependency = result.relationLegend.find(entry => entry.rawEdgeType === 'depends_on')!;
    const custom = result.relationLegend.find(entry => entry.rawEdgeType === 'custom_link')!;

    expect(orders).toMatchObject({
      totalCount: 2,
      visibleCount: 2,
      internalEdges: 1,
      outgoingExternalEdges: 1,
      incomingExternalEdges: 1,
    });
    expect(billing).toMatchObject({
      totalCount: 1,
      visibleCount: 1,
      internalEdges: 0,
      outgoingExternalEdges: 1,
      incomingExternalEdges: 1,
    });
    expect(Number.isFinite(orders.sumNodeScore)).toBe(true);
    expect(dependency).toMatchObject({
      totalCount: 2,
      visibleCount: 2,
      multiplicitySum: 5,
      semanticallyKnown: true,
    });
    expect(custom).toMatchObject({
      totalCount: 1,
      multiplicitySum: 1,
      semanticallyKnown: false,
    });
  });

  it('updates visible counts without recomputing scores', () => {
    const { projection, score } = service();
    const fixture = graph();
    const base = projection.project(fixture, DEFAULT_GRAPH_VISUAL_PROFILE);
    const nodeSpy = vi.spyOn(score, 'scoreNode');
    const edgeSpy = vi.spyOn(score, 'scoreEdge');

    const visible = projection.withVisibility(
      base,
      fixture,
      new Set(['a', 'b']),
      new Set(['ab']),
    );

    expect(visible.domainLegend.find(entry => entry.canonicalId === 'orders')?.visibleCount).toBe(2);
    expect(visible.domainLegend.find(entry => entry.canonicalId === 'billing')?.visibleCount).toBe(0);
    expect(visible.relationLegend.find(entry => entry.rawEdgeType === 'depends_on')?.visibleCount).toBe(1);
    expect(visible.relationLegend.find(entry => entry.rawEdgeType === 'custom_link')?.visibleCount).toBe(0);
    expect(nodeSpy).not.toHaveBeenCalled();
    expect(edgeSpy).not.toHaveBeenCalled();
    expect(visible.nodeStyles).toBe(base.nodeStyles);
  });

  it('memoizes styles so 100 hover/selection reads perform no score work or HTTP', () => {
    const { projection, score } = service();
    const fixture = graph();
    const first = projection.project(fixture, DEFAULT_GRAPH_VISUAL_PROFILE);
    const nodeSpy = vi.spyOn(score, 'scoreNode');
    const edgeSpy = vi.spyOn(score, 'scoreEdge');

    const repeated = Array.from({ length: 100 }, () =>
      projection.project(fixture, DEFAULT_GRAPH_VISUAL_PROFILE));

    expect(repeated.every(item => item === first)).toBe(true);
    expect(nodeSpy).not.toHaveBeenCalled();
    expect(edgeSpy).not.toHaveBeenCalled();
  });

  it('rebinds highlight-only profile changes without recomputing scores', () => {
    const { projection, score } = service();
    const fixture = graph();
    projection.project(fixture, DEFAULT_GRAPH_VISUAL_PROFILE);
    const nodeSpy = vi.spyOn(score, 'scoreNode');
    const edgeSpy = vi.spyOn(score, 'scoreEdge');
    const profile = {
      ...DEFAULT_GRAPH_VISUAL_PROFILE,
      highlightFactors: {
        ...DEFAULT_GRAPH_VISUAL_PROFILE.highlightFactors,
        hover: 1.35,
      },
    } as GraphVisualProfile;

    const rebound = projection.project(fixture, profile);

    expect(nodeSpy).not.toHaveBeenCalled();
    expect(edgeSpy).not.toHaveBeenCalled();
    expect(rebound.nodeStyles['a'].highlightFactors.hover).toBe(1.35);
    expect(rebound.edgeStyles['ab'].highlightFactors.hover).toBe(1.35);
  });

  it('reuses raw normalization on profile changes and invalidates old revisions', () => {
    const { projection, score } = service();
    const contextSpy = vi.spyOn(score, 'createContext');
    const firstGraph = graph('revision-1');

    projection.project(firstGraph, profileWithDomainColor(1));
    projection.project(firstGraph, profileWithDomainColor(2));
    expect(contextSpy).toHaveBeenCalledTimes(1);
    expect(projection.cacheStats()).toEqual({ normalizationEntries: 1, projectionEntries: 2 });

    projection.project(graph('revision-2'), profileWithDomainColor(2));
    expect(contextSpy).toHaveBeenCalledTimes(2);
    expect(projection.cacheStats()).toEqual({ normalizationEntries: 1, projectionEntries: 1 });
  });

  it('invalidates cached normalization when worker metric evidence changes', () => {
    const { projection, score } = service();
    const contextSpy = vi.spyOn(score, 'createContext');
    const firstGraph = graph('revision-1');
    firstGraph.metadata['visual_metrics_content_hash'] = 'sha256:first';
    const secondGraph = graph('revision-1');
    secondGraph.metadata['visual_metrics_content_hash'] = 'sha256:second';
    secondGraph.nodes[0].metrics = { ...secondGraph.nodes[0].metrics, total_degree: 99 };

    const first = projection.project(firstGraph, DEFAULT_GRAPH_VISUAL_PROFILE);
    const second = projection.project(secondGraph, DEFAULT_GRAPH_VISUAL_PROFILE);

    expect(second).not.toBe(first);
    expect(contextSpy).toHaveBeenCalledTimes(2);
    expect(projection.cacheStats()).toEqual({ normalizationEntries: 1, projectionEntries: 1 });
  });

  it('evicts the least-recently-used projection deterministically at eight entries', () => {
    const { projection, score } = service();
    const fixture = graph();
    const profiles = Array.from({ length: 9 }, (_, index) => profileWithDomainColor(index));
    profiles.forEach(profile => projection.project(fixture, profile));
    expect(projection.cacheStats().projectionEntries).toBe(8);

    const nodeSpy = vi.spyOn(score, 'scoreNode');
    projection.project(fixture, profiles[0]);
    expect(nodeSpy).toHaveBeenCalledTimes(fixture.nodes.length);
    expect(projection.cacheStats().projectionEntries).toBe(8);
  });

  it('does not cache legacy graphs without a hub/worker supplied revision', () => {
    const { projection, score } = service();
    const fixture = graph();
    delete fixture.metadata.graphRevision;
    const contextSpy = vi.spyOn(score, 'createContext');

    const first = projection.project(fixture, DEFAULT_GRAPH_VISUAL_PROFILE);
    const second = projection.project(fixture, DEFAULT_GRAPH_VISUAL_PROFILE);

    expect(first).not.toBe(second);
    expect(contextSpy).toHaveBeenCalledTimes(2);
    expect(projection.cacheStats()).toEqual({ normalizationEntries: 0, projectionEntries: 0 });
  });
});
