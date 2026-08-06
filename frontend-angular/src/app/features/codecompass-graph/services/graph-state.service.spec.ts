import { TestBed } from '@angular/core/testing';
import { GraphStateService } from './graph-state.service';
import { GraphAdapterService } from './graph-adapter.service';
import { MOCK_DOMAIN_GRAPH_ARTIFACT } from '../testing/mock-codecompass-graph';
import { GenericGraphModel } from '../models/graph.model';

function buildGraph(): GenericGraphModel {
  return TestBed.inject(GraphAdapterService).fromDomainArtifact(MOCK_DOMAIN_GRAPH_ARTIFACT);
}

function chainGraph(): GenericGraphModel {
  const nodes = ['a', 'b', 'c', 'd'].map(id => ({
    id,
    kind: 'python_function' as const,
    label: id,
    file: `${id}.py`,
    content: '',
    recordId: id,
    metadata: {},
  }));
  return {
    nodes,
    edges: [
      { id: 'ab', source: 'a', target: 'b', edgeType: 'parent_child', confidence: 1, metadata: {} },
      { id: 'bc', source: 'b', target: 'c', edgeType: 'parent_child', confidence: 1, metadata: {} },
      { id: 'cd', source: 'c', target: 'd', edgeType: 'parent_child', confidence: 1, metadata: {} },
    ],
    metadata: { sourceRef: 'test', sourceKind: 'test', nodeCount: nodes.length, edgeCount: 3 },
    warnings: [],
  };
}

describe('GraphStateService', () => {
  let svc: GraphStateService;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [GraphStateService] });
    svc = TestBed.inject(GraphStateService);
  });

  it('starts in simple view mode', () => {
    expect(svc.viewMode()).toBe('simple');
  });

  it('setViewMode updates viewMode signal', () => {
    svc.setViewMode('2d');
    expect(svc.viewMode()).toBe('2d');
  });

  it('setGraph sets graph and resets selection', () => {
    const g = buildGraph();
    svc.selectNode(g.nodes[0]);
    svc.setGraph(g);
    expect(svc.graph()).toBe(g);
    expect(svc.selectedNode()).toBeNull();
    expect(svc.selectedEdge()).toBeNull();
  });

  it('updates a larger window without losing filter, selection, or hop depth', () => {
    const initial = chainGraph();
    svc.setGraph(initial);
    svc.selectNode(initial.nodes[0]);
    svc.updateFilter({ edgeTypes: { mode: 'subset', values: ['parent_child'] } });
    svc.setNeighborhoodDepth(2);
    const expanded: GenericGraphModel = {
      ...chainGraph(),
      nodes: [
        ...chainGraph().nodes,
        {
          id: 'e', kind: 'python_function', label: 'e', file: 'e.py', content: '',
          recordId: 'e', metadata: {},
        },
      ],
    };

    svc.updateGraphWindow(expanded);

    expect(svc.graph()).toBe(expanded);
    expect(svc.selectedNode()).toBe(expanded.nodes[0]);
    expect(svc.selectedNode()).not.toBe(initial.nodes[0]);
    expect(svc.filter().edgeTypes).toEqual({ mode: 'subset', values: ['parent_child'] });
    expect(svc.focusNodeId()).toBe('a');
    expect(svc.focusHopDepth()).toBe(2);
  });

  it('clears retained selections that are absent from the replacement window', () => {
    const initial = chainGraph();
    svc.setGraph(initial);
    svc.selectNode(initial.nodes[3]);
    svc.setNeighborhoodDepth(2);

    svc.updateGraphWindow({
      ...initial,
      nodes: initial.nodes.slice(0, 2),
      edges: initial.edges.slice(0, 1),
    });

    expect(svc.selectedNode()).toBeNull();
    expect(svc.focusNodeId()).toBeNull();
    expect(svc.focusHopDepth()).toBe(2);
  });

  it('selectNode sets selectedNode and clears selectedEdge', () => {
    const g = buildGraph();
    svc.setGraph(g);
    svc.selectEdge(g.edges[0]);
    svc.selectNode(g.nodes[0]);
    expect(svc.selectedNode()).toBe(g.nodes[0]);
    expect(svc.selectedEdge()).toBeNull();
  });

  it('selectEdge sets selectedEdge and clears selectedNode', () => {
    const g = buildGraph();
    svc.setGraph(g);
    svc.selectNode(g.nodes[0]);
    svc.selectEdge(g.edges[0]);
    expect(svc.selectedEdge()).toBe(g.edges[0]);
    expect(svc.selectedNode()).toBeNull();
  });

  it('clearSelection clears both', () => {
    const g = buildGraph();
    svc.setGraph(g);
    svc.selectNode(g.nodes[0]);
    svc.clearSelection();
    expect(svc.selectedNode()).toBeNull();
    expect(svc.selectedEdge()).toBeNull();
  });

  it('filteredNodes returns all nodes when no filter set', () => {
    svc.setGraph(buildGraph());
    expect(svc.filteredNodes().length).toBe(20);
  });

  it('filteredNodes filters by explicit node kind subset', () => {
    svc.setGraph(buildGraph());
    svc.updateFilter({ nodeKinds: { mode: 'subset', values: ['java_type'] } });
    const kinds = new Set(svc.filteredNodes().map(n => n.kind));
    expect(kinds.has('java_type')).toBe(true);
    expect(kinds.has('java_method')).toBe(false);
  });

  it('filteredNodes filters by searchText on label or file', () => {
    svc.setGraph(buildGraph());
    svc.updateFilter({ searchText: 'OrderService' });
    const nodes = svc.filteredNodes();
    // Every result must match either label or file
    for (const n of nodes) {
      const matchesLabel = n.label.toLowerCase().includes('orderservice');
      const matchesFile  = n.file.toLowerCase().includes('orderservice');
      expect(matchesLabel || matchesFile).toBe(true);
    }
    // At least the OrderService type node itself should be present
    expect(nodes.some(n => n.id === 'n-OrderService')).toBe(true);
  });

  it('filteredEdges excludes edges when endpoints are filtered out', () => {
    svc.setGraph(buildGraph());
    svc.updateFilter({ nodeKinds: { mode: 'subset', values: ['config'] } });
    const edgeSources = new Set(svc.filteredEdges().map(e => e.source));
    const configIds = new Set(svc.filteredNodes().map(n => n.id));
    for (const src of edgeSources) {
      expect(configIds.has(src)).toBe(true);
    }
  });

  it('resetFilter restores all nodes', () => {
    svc.setGraph(buildGraph());
    svc.updateFilter({ nodeKinds: { mode: 'subset', values: ['config'] } });
    svc.resetFilter();
    expect(svc.filteredNodes().length).toBe(20);
  });

  it('focus depth 0 clears focus and keeps the full graph visible', () => {
    const g = chainGraph();
    svc.setGraph(g);
    svc.setFocus('a', 2);
    expect(svc.filteredNodes().map(n => n.id)).toEqual(['a', 'b', 'c']);

    svc.setFocus('a', 0);

    expect(svc.focusNodeId()).toBeNull();
    expect(svc.focusHopDepth()).toBe(0);
    expect(svc.filteredNodes().map(n => n.id)).toEqual(['a', 'b', 'c', 'd']);
  });

  it('focus depth follows graph hops from the selected node', () => {
    const g = chainGraph();
    svc.setGraph(g);

    svc.setFocus('a', 1);
    expect(svc.filteredNodes().map(n => n.id)).toEqual(['a', 'b']);

    svc.setFocus('a', 2);
    expect(svc.filteredNodes().map(n => n.id)).toEqual(['a', 'b', 'c']);
  });

  it('applies connection depth immediately and follows a newly selected anchor', () => {
    const g = chainGraph();
    svc.setGraph(g);
    svc.selectNode(g.nodes[0]);

    svc.setNeighborhoodDepth(1);
    expect(svc.focusNodeId()).toBe('a');
    expect(svc.filteredNodes().map(node => node.id)).toEqual(['a', 'b']);

    svc.selectNode(g.nodes[2]);
    expect(svc.focusNodeId()).toBe('c');
    expect(svc.filteredNodes().map(node => node.id)).toEqual(['b', 'c', 'd']);
  });

  it('expands the graph when selection closes but preserves the chosen depth', () => {
    const g = chainGraph();
    svc.setGraph(g);
    svc.selectNode(g.nodes[0]);
    svc.setNeighborhoodDepth(2);

    svc.clearSelection();

    expect(svc.focusNodeId()).toBeNull();
    expect(svc.focusHopDepth()).toBe(2);
    expect(svc.filteredNodes().map(node => node.id)).toEqual(['a', 'b', 'c', 'd']);
  });

  it('does not traverse edge types hidden by the active filter', () => {
    const g = chainGraph();
    g.edges[1] = {
      ...g.edges[1],
      edgeType: 'related',
      rawEdgeType: 'hidden_link',
    };
    svc.setGraph(g);
    svc.selectNode(g.nodes[0]);
    svc.updateFilter({ edgeTypes: { mode: 'subset', values: ['parent_child'] } });
    svc.setNeighborhoodDepth(3);

    expect(svc.filteredNodes().map(node => node.id)).toEqual(['a', 'b']);
    expect(svc.filteredEdges().map(edge => edge.id)).toEqual(['ab']);
  });

  it('does not traverse nodes hidden by the active node filter', () => {
    const g = chainGraph();
    g.nodes[1] = { ...g.nodes[1], kind: 'java_type', rawNodeType: 'java_type' };
    svc.setGraph(g);
    svc.selectNode(g.nodes[0]);
    svc.updateFilter({ nodeKinds: { mode: 'subset', values: ['python_function'] } });
    svc.setNeighborhoodDepth(3);

    expect(svc.filteredNodes().map(node => node.id)).toEqual(['a']);
    expect(svc.filteredEdges()).toEqual([]);
  });

  it('clears a depth anchor when a node filter hides that anchor', () => {
    const g = chainGraph();
    svc.setGraph(g);
    svc.selectNode(g.nodes[0]);
    svc.setNeighborhoodDepth(2);

    svc.updateFilter({ searchText: 'b' });

    expect(svc.focusNodeId()).toBeNull();
    expect(svc.focusHopDepth()).toBe(2);
    expect(svc.focusNodeLabel()).toBe('');
    expect(svc.filteredNodes().map(node => node.id)).toEqual(['b']);
  });

  it('does not restore a depth anchor while the selected node is filtered out', () => {
    const g = chainGraph();
    svc.setGraph(g);
    svc.selectNode(g.nodes[0]);
    svc.updateFilter({ searchText: 'b' });

    svc.setNeighborhoodDepth(2);
    svc.setFocus('a', 2);

    expect(svc.selectedNode()).toBe(g.nodes[0]);
    expect(svc.focusNodeId()).toBeNull();
    expect(svc.focusHopDepth()).toBe(2);
    expect(svc.filteredNodes().map(node => node.id)).toEqual(['b']);
  });

  it('represents all, none, and subset without sentinel casts', () => {
    svc.setGraph(chainGraph());
    svc.updateFilter({ edgeTypes: { mode: 'none', values: [] } });
    expect(svc.filteredEdges()).toEqual([]);
    svc.updateFilter({ edgeTypes: { mode: 'subset', values: ['parent_child'] } });
    expect(svc.filteredEdges().length).toBe(3);
    expect(JSON.stringify(svc.filter())).not.toContain('__none__');
  });

  it('keeps filtered graph wrapper memoized until state changes', () => {
    svc.setGraph(chainGraph());
    const first = svc.filteredGraph();
    expect(svc.filteredGraph()).toBe(first);
    svc.updateFilter({ searchText: 'a' });
    expect(svc.filteredGraph()).not.toBe(first);
  });
});
