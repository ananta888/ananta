import { ComponentFixture, TestBed } from '@angular/core/testing';
import { SimpleGraphViewComponent } from './simple-graph-view.component';
import { GraphAdapterService } from '../../services/graph-adapter.service';
import { MOCK_DOMAIN_GRAPH_ARTIFACT } from '../../testing/mock-codecompass-graph';
import { GenericGraphModel } from '../../models/graph.model';
import { GraphVisualProjection } from '../../models/graph-visual-metrics.model';
import { SIMPLE_GRAPH_VIEW_CAPABILITIES } from './simple-graph-view.component';

function buildGraph(): GenericGraphModel {
  return new GraphAdapterService().fromDomainArtifact(MOCK_DOMAIN_GRAPH_ARTIFACT);
}

function projection(graph: GenericGraphModel): GraphVisualProjection {
  return {
    graphRevision: 'test', profileHash: 'profile', domainLegend: [], relationLegend: [],
    nodeStyles: Object.fromEntries(graph.nodes.map((node, index) => [node.id, {
      nodeId: node.id, baseColor: index ? '#123456' : '#ABCDEF', marker: 'diamond' as const,
      baseSize: 5 + index, score: index / 100, scoreState: 'scored' as const,
      availability: 'available' as const,
      breakdown: index ? [] : [{
        metricId: 'total_degree', rawValue: 4, normalizedValue: 0.5, normalizationState: 'normalized' as const,
        weight: 2, direction: 'normal' as const, partialScore: 1,
        availability: 'available' as const, provenance: null, reasonCode: null,
      }],
      highlightFactors: { hover: 1.2, selected: 1.5, connected: 1.1 },
    }])),
    edgeStyles: Object.fromEntries(graph.edges.map(edge => [edge.id, {
      edgeId: edge.id, baseColor: '#654321', marker: 'triangle' as const,
      baseThickness: 2, score: 0.4, scoreState: 'scored' as const, availability: 'approximate' as const,
      breakdown: [], highlightFactors: { hover: 1.2, selected: 1.5, connected: 1.1 },
    }])),
  };
}

describe('SimpleGraphViewComponent', () => {
  let fixture: ComponentFixture<SimpleGraphViewComponent>;
  let component: SimpleGraphViewComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [SimpleGraphViewComponent],
    }).compileComponents();
    fixture = TestBed.createComponent(SimpleGraphViewComponent);
    component = fixture.componentInstance;
  });

  it('shows empty message when no graph', () => {
    component.graph = null;
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('No nodes to display');
  });

  it('renders all nodes', () => {
    component.graph = buildGraph();
    fixture.detectChanges();
    const items = fixture.nativeElement.querySelectorAll('.sgv-node');
    expect(items.length).toBe(20);
  });

  it('renders all edges', () => {
    component.graph = buildGraph();
    fixture.detectChanges();
    const items = fixture.nativeElement.querySelectorAll('.sgv-edge');
    expect(items.length).toBe(30);
  });

  it('emits nodeSelected when a node is clicked', () => {
    const g = buildGraph();
    component.graph = g;
    fixture.detectChanges();
    let emitted: any = null;
    component.nodeSelected.subscribe((n: any) => (emitted = n));
    const first = fixture.nativeElement.querySelector('.sgv-node') as HTMLElement;
    first.click();
    expect(emitted).toBeTruthy();
    expect(emitted.id).toBeTruthy();
  });

  it('emits edgeSelected when an edge is clicked', () => {
    const g = buildGraph();
    component.graph = g;
    fixture.detectChanges();
    let emitted: any = null;
    component.edgeSelected.subscribe((e: any) => (emitted = e));
    const first = fixture.nativeElement.querySelector('.sgv-edge') as HTMLElement;
    first.click();
    expect(emitted).toBeTruthy();
    expect(emitted.edgeType).toBeTruthy();
  });

  it('marks selected node with .selected class', () => {
    const g = buildGraph();
    component.graph = g;
    component.selectedNode = g.nodes[0];
    fixture.detectChanges();
    const selected = fixture.nativeElement.querySelectorAll('.sgv-node.selected');
    expect(selected.length).toBe(1);
  });

  it('uses the canonical projection for color, marker, score and availability only', () => {
    const graph = buildGraph();
    component.graph = graph;
    component.visualProjection = projection(graph);
    fixture.detectChanges();
    const first = fixture.nativeElement.querySelector('.sgv-node') as HTMLElement;
    expect((first.querySelector('.visual-marker') as HTMLElement).style.backgroundColor).toBe('rgb(171, 205, 239)');
    expect(first.textContent).toContain('Score 0.000 · available');
    expect(SIMPLE_GRAPH_VIEW_CAPABILITIES.nodeSize).toBe(false);
    expect(SIMPLE_GRAPH_VIEW_CAPABILITIES.edgeThickness).toBe(false);
  });

  it('renders hostile tooltip labels as escaped text and includes the full breakdown', () => {
    const graph = buildGraph();
    graph.nodes[0] = { ...graph.nodes[0], label: '<img src=x onerror=alert(1)>' };
    component.graph = graph;
    component.visualProjection = projection(graph);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('img')).toBeNull();
    const first = fixture.nativeElement.querySelector('.sgv-node') as HTMLElement;
    expect(first.title).toContain('raw=4');
    expect(first.title).toContain('normalized=0.5');
    expect(first.title).toContain('weight=2');
    expect(first.title).toContain('partial=1');
  });
});
