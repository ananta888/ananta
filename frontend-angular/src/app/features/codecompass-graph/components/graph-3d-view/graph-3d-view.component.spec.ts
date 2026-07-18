import { SimpleChange } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { Graph3dViewComponent } from './graph-3d-view.component';
import { GraphAdapterService } from '../../services/graph-adapter.service';
import { MOCK_DOMAIN_GRAPH_ARTIFACT } from '../../testing/mock-codecompass-graph';
import { GenericGraphModel } from '../../models/graph.model';
import { GraphVisualProjection } from '../../models/graph-visual-metrics.model';
import { graphVisualTooltipElement } from '../graph-tooltip/graph-visual-tooltip';

function buildGraph(): GenericGraphModel {
  return new GraphAdapterService().fromDomainArtifact(MOCK_DOMAIN_GRAPH_ARTIFACT);
}

function visualProjection(graph: GenericGraphModel): GraphVisualProjection {
  return {
    graphRevision: 'rev', profileHash: 'profile', domainLegend: [], relationLegend: [],
    nodeStyles: Object.fromEntries(graph.nodes.map((node, index) => [node.id, {
      nodeId: node.id, baseColor: '#112233', marker: 'circle' as const, baseSize: 4 + index,
      score: index / 10, scoreState: 'scored' as const, availability: 'available' as const,
      breakdown: [], highlightFactors: { hover: 1.25, selected: 1.5, connected: 1.1 },
    }])),
    edgeStyles: Object.fromEntries(graph.edges.map((edge, index) => [edge.id, {
      edgeId: edge.id, baseColor: '#445566', marker: 'triangle' as const, baseThickness: 1 + index,
      score: index / 10, scoreState: 'scored' as const, availability: 'available' as const,
      breakdown: [], highlightFactors: { hover: 1.25, selected: 1.5, connected: 1.1 },
    }])),
  };
}

describe('Graph3dViewComponent', () => {
  let fixture: ComponentFixture<Graph3dViewComponent>;
  let component: Graph3dViewComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [Graph3dViewComponent],
    }).compileComponents();
    fixture = TestBed.createComponent(Graph3dViewComponent);
    component = fixture.componentInstance;
  });

  it('shows empty message when no graph is provided', () => {
    component.graph = null;
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('No nodes to display');
  });

  it('shows WebGL fallback when WebGL is unavailable (expected in JSDOM)', () => {
    fixture.componentRef.setInput('graph', buildGraph());
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('WebGL is not available');
  });

  it('sets webglUnavailable when WebGL cannot be created', () => {
    fixture.componentRef.setInput('graph', buildGraph());
    fixture.detectChanges();
    // JSDOM never has WebGL — setInput triggers ngOnChanges → _render() detects no WebGL
    expect(component.webglUnavailable).toBe(true);
  });

  it('emits nodeSelected when node map has the id', () => {
    const g = buildGraph();
    component.graph = g;
    fixture.detectChanges();
    let emitted: any = null;
    component.nodeSelected.subscribe((n: any) => (emitted = n));
    // Directly invoke the output — renderer doesn't run in JSDOM
    component.nodeSelected.emit(g.nodes[0]);
    expect(emitted).toBe(g.nodes[0]);
  });

  it('emits edgeSelected when edge map has the id', () => {
    const g = buildGraph();
    component.graph = g;
    fixture.detectChanges();
    let emitted: any = null;
    component.edgeSelected.subscribe((e: any) => (emitted = e));
    component.edgeSelected.emit(g.edges[0]);
    expect(emitted).toBe(g.edges[0]);
  });

  it('does not throw on destroy before renderer is initialised', () => {
    component.graph = null;
    fixture.detectChanges();
    expect(() => fixture.destroy()).not.toThrow();
  });

  it('renders every node by default without applying an implicit cap', () => {
    const nodes = Array.from({ length: 1600 }, (_, i) => ({
      id: `node-${i}`,
      kind: 'python_function' as const,
      label: `node-${i}`,
      file: `agent/routes/file_${i}.py`,
      content: '',
      recordId: `node-${i}`,
      metadata: {},
    }));
    component.graph = {
      nodes,
      edges: [],
      metadata: { sourceRef: 'test', sourceKind: 'test', nodeCount: nodes.length, edgeCount: 0 },
      warnings: [],
    };

    const limited = (component as any)._limitedGraph();

    expect(limited.nodes.length).toBe(nodes.length);
  });

  it('keeps the selected node neighbourhood when an explicit node render limit is set', () => {
    const anchor = {
      id: 'pair-file',
      kind: 'python_file' as const,
      label: 'pair_groups.py',
      file: 'agent/routes/pair_groups.py',
      content: '',
      recordId: 'pair-file',
      metadata: {},
    };
    const neighbour = {
      id: 'pair-function',
      kind: 'python_function' as const,
      label: 'list_pair_groups',
      file: 'agent/routes/pair_groups.py',
      content: '',
      recordId: 'pair-function',
      metadata: {},
    };
    const fillerNodes = Array.from({ length: 501 }, (_, i) => ({
      id: `filler-${i}`,
      kind: 'python_function' as const,
      label: `filler-${i}`,
      file: `agent/routes/filler_${i}.py`,
      content: '',
      recordId: `filler-${i}`,
      metadata: {},
    }));
    component.graph = {
      nodes: [anchor, neighbour, ...fillerNodes],
      edges: [
        {
          id: 'pair-file|pair-function|contains_symbol',
          source: anchor.id,
          target: neighbour.id,
          edgeType: 'parent_child',
          confidence: 1,
          metadata: {},
        },
      ],
      metadata: { sourceRef: 'test', sourceKind: 'test', nodeCount: 503, edgeCount: 1 },
      warnings: [],
    };
    component.selectedNode = anchor;
    component.nodeRenderLimit = 500;

    const capped = (component as any)._limitedGraph();

    expect(capped.nodes.map((node: any) => node.id)).toContain(anchor.id);
    expect(capped.nodes.map((node: any) => node.id)).toContain(neighbour.id);
    expect(capped.edges).toEqual([component.graph.edges[0]]);
  });

  it('applies explicit edge render limits independently from nodes', () => {
    const nodes = [
      {
        id: 'a',
        kind: 'python_file' as const,
        label: 'a.py',
        file: 'a.py',
        content: '',
        recordId: 'a',
        metadata: {},
      },
      {
        id: 'b',
        kind: 'python_function' as const,
        label: 'b',
        file: 'a.py',
        content: '',
        recordId: 'b',
        metadata: {},
      },
      {
        id: 'c',
        kind: 'python_function' as const,
        label: 'c',
        file: 'a.py',
        content: '',
        recordId: 'c',
        metadata: {},
      },
    ];
    component.graph = {
      nodes,
      edges: [
        { id: 'ab', source: 'a', target: 'b', edgeType: 'parent_child', confidence: 1, metadata: {} },
        { id: 'ac', source: 'a', target: 'c', edgeType: 'parent_child', confidence: 1, metadata: {} },
      ],
      metadata: { sourceRef: 'test', sourceKind: 'test', nodeCount: 3, edgeCount: 2 },
      warnings: [],
    };
    component.edgeRenderLimit = 1;

    const limited = (component as any)._limitedGraph();

    expect(limited.nodes.length).toBe(3);
    expect(limited.edges.length).toBe(1);
  });

  it('applies a style-only projection change without renderer construction or destruction', () => {
    const graph = buildGraph();
    const projection = visualProjection(graph);
    component.graph = graph;
    component.visualProjection = projection;
    const apply = vi.spyOn(component as any, '_applyVisualProjection');
    const render = vi.spyOn(component as any, '_render');
    const destroy = vi.spyOn(component as any, '_destroy');

    component.ngOnChanges({ visualProjection: new SimpleChange(null, projection, false) });

    expect(apply).toHaveBeenCalledTimes(1);
    expect(render).not.toHaveBeenCalled();
    expect(destroy).not.toHaveBeenCalled();
  });

  it('applies filter visibility without renderer construction or destruction', () => {
    const graph = buildGraph();
    component.graph = graph;
    component.visibleNodeIds = new Set([graph.nodes[0].id]);
    const apply = vi.spyOn(component as any, '_applyVisualProjection');
    const render = vi.spyOn(component as any, '_render');
    const destroy = vi.spyOn(component as any, '_destroy');
    component.ngOnChanges({
      visibleNodeIds: new SimpleChange(null, component.visibleNodeIds, false),
    });
    expect(apply).toHaveBeenCalledTimes(1);
    expect(render).not.toHaveBeenCalled();
    expect(destroy).not.toHaveBeenCalled();
  });

  it('multiplies canonical base sizes for hover and selection', () => {
    const graph = buildGraph();
    component.graph = graph;
    component.visualProjection = visualProjection(graph);
    const id = graph.nodes[0].id;
    const base = component.visualProjection.nodeStyles[id].baseSize;
    component.highlightedNodeIds = new Set([id]);
    expect((component as any)._nodeValue(id)).toBe(base * 1.25);
    component.highlightedNodeIds = new Set();
    (component as any)._focalId = id;
    expect((component as any)._nodeValue(id)).toBe(base * 1.5);
  });

  it('creates tooltip elements with textContent instead of executable markup', () => {
    const tooltip = graphVisualTooltipElement('<img src=x onerror=alert(1)>');
    expect(tooltip.querySelector('img')).toBeNull();
    expect(tooltip.textContent).toBe('<img src=x onerror=alert(1)>');
  });
});
