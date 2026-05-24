import { TestBed } from '@angular/core/testing';
import { GraphStateService } from './graph-state.service';
import { GraphAdapterService } from './graph-adapter.service';
import { MOCK_DOMAIN_GRAPH_ARTIFACT } from '../testing/mock-codecompass-graph';
import { GenericGraphModel } from '../models/graph.model';

function buildGraph(): GenericGraphModel {
  return TestBed.inject(GraphAdapterService).fromDomainArtifact(MOCK_DOMAIN_GRAPH_ARTIFACT);
}

describe('GraphStateService', () => {
  let svc: GraphStateService;

  beforeEach(() => {
    TestBed.configureTestingModule({});
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

  it('filteredNodes filters by nodeKindFilter', () => {
    svc.setGraph(buildGraph());
    svc.updateFilter({ nodeKindFilter: ['java_type'] });
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
    svc.updateFilter({ nodeKindFilter: ['config'] });
    const edgeSources = new Set(svc.filteredEdges().map(e => e.source));
    const configIds = new Set(svc.filteredNodes().map(n => n.id));
    for (const src of edgeSources) {
      expect(configIds.has(src)).toBe(true);
    }
  });

  it('resetFilter restores all nodes', () => {
    svc.setGraph(buildGraph());
    svc.updateFilter({ nodeKindFilter: ['config'] });
    svc.resetFilter();
    expect(svc.filteredNodes().length).toBe(20);
  });
});
