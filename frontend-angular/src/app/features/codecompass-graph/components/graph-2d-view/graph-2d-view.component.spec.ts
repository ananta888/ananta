import { SimpleChange } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { vi } from 'vitest';
import { Graph2dViewComponent } from './graph-2d-view.component';
import { GenericGraphModel } from '../../models/graph.model';
import { GraphVisualProjection } from '../../models/graph-visual-metrics.model';

function graphWith(count: number): GenericGraphModel {
  const nodes = Array.from({ length: count }, (_, i) => ({
    id: `node-${i}`,
    kind: 'python_function' as const,
    label: `node-${i}`,
    file: `agent/routes/file_${i}.py`,
    content: '',
    recordId: `node-${i}`,
    metadata: {},
  }));
  const edges = nodes.slice(1).map((node, i) => ({
    id: `edge-${i}`,
    source: nodes[0].id,
    target: node.id,
    edgeType: 'parent_child' as const,
    confidence: 1,
    metadata: {},
  }));
  return {
    nodes,
    edges,
    metadata: { sourceRef: 'test', sourceKind: 'test', nodeCount: nodes.length, edgeCount: edges.length },
    warnings: [],
  };
}

function visualProjection(graph: GenericGraphModel): GraphVisualProjection {
  return {
    graphRevision: 'rev', profileHash: 'profile', domainLegend: [], relationLegend: [],
    nodeStyles: Object.fromEntries(graph.nodes.map((node, index) => [node.id, {
      nodeId: node.id, baseColor: '#112233', marker: 'circle' as const, baseSize: 7 + index,
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

describe('Graph2dViewComponent', () => {
  let fixture: ComponentFixture<Graph2dViewComponent>;
  let component: Graph2dViewComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [Graph2dViewComponent],
    }).compileComponents();
    fixture = TestBed.createComponent(Graph2dViewComponent);
    component = fixture.componentInstance;
  });

  it('does not apply an implicit node render limit by default', () => {
    const graph = graphWith(900);
    component.graph = graph;

    const limited = (component as any)._limitedGraph(graph.nodes, graph.edges);

    expect(limited.nodes.length).toBe(graph.nodes.length);
    expect(limited.edges.length).toBe(graph.edges.length);
    expect(component.renderWarning).toBe('');
  });

  it('applies explicit edge render limits independently from nodes', () => {
    const graph = graphWith(5);
    component.graph = graph;
    component.edgeRenderLimit = 2;

    const limited = (component as any)._limitedGraph(graph.nodes, graph.edges);

    expect(limited.nodes.length).toBe(graph.nodes.length);
    expect(limited.edges.length).toBe(2);
  });

  it('applies a style-only projection change without rebuilding Cytoscape', () => {
    const graph = graphWith(3);
    component.graph = graph;
    const projection = visualProjection(graph);
    const apply = vi.spyOn(component as any, '_applyVisualProjection');
    const render = vi.spyOn(component as any, '_render');

    component.visualProjection = projection;
    component.ngOnChanges({
      visualProjection: new SimpleChange(null, projection, false),
    });

    expect(apply).toHaveBeenCalledTimes(1);
    expect(render).not.toHaveBeenCalled();
  });

  it('applies filter visibility without rebuilding Cytoscape', () => {
    const graph = graphWith(3);
    component.graph = graph;
    component.visibleNodeIds = new Set([graph.nodes[0].id]);
    const visibility = vi.spyOn(component as any, '_applyVisibility');
    const render = vi.spyOn(component as any, '_render');
    component.ngOnChanges({
      visibleNodeIds: new SimpleChange(null, component.visibleNodeIds, false),
    });
    expect(visibility).toHaveBeenCalledTimes(1);
    expect(render).not.toHaveBeenCalled();
  });

  it('consumes canonical size, color and thickness rankings without local formulas', () => {
    const graph = graphWith(3);
    component.visualProjection = visualProjection(graph);
    const first = (component as any)._nodeVisual(graph.nodes[0], 99);
    const second = (component as any)._nodeVisual(graph.nodes[1], 99);
    const edge = (component as any)._edgeVisual(graph.edges[0]);
    expect(first.baseColor).toBe('#112233');
    expect(second.baseSize).toBeGreaterThan(first.baseSize);
    expect(edge.baseColor).toBe('#445566');
  });

  it('writes tooltip content through textContent so hostile labels cannot create DOM', () => {
    fixture.detectChanges();
    (component as any)._showTooltip('<img src=x onerror=alert(1)>', { renderedPosition: { x: 0, y: 0 } });
    const tooltip = fixture.nativeElement.querySelector('.graph-tooltip') as HTMLElement;
    expect(tooltip.querySelector('img')).toBeNull();
    expect(tooltip.textContent).toBe('<img src=x onerror=alert(1)>');
  });
});
