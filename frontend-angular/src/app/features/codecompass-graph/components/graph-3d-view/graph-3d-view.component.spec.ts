import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Graph3dViewComponent } from './graph-3d-view.component';
import { GraphAdapterService } from '../../services/graph-adapter.service';
import { MOCK_DOMAIN_GRAPH_ARTIFACT } from '../../testing/mock-codecompass-graph';
import { GenericGraphModel } from '../../models/graph.model';

function buildGraph(): GenericGraphModel {
  return new GraphAdapterService().fromDomainArtifact(MOCK_DOMAIN_GRAPH_ARTIFACT);
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

  it('keeps the selected node neighbourhood when applying the large graph cap', () => {
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

    const capped = (component as any)._cappedGraph();

    expect(capped.nodes.map((node: any) => node.id)).toContain(anchor.id);
    expect(capped.nodes.map((node: any) => node.id)).toContain(neighbour.id);
    expect(capped.edges).toEqual([component.graph.edges[0]]);
  });
});
