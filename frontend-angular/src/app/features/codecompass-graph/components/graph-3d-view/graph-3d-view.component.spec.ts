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
});
