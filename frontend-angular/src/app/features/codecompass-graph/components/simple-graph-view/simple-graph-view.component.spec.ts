import { ComponentFixture, TestBed } from '@angular/core/testing';
import { SimpleGraphViewComponent } from './simple-graph-view.component';
import { GraphAdapterService } from '../../services/graph-adapter.service';
import { MOCK_DOMAIN_GRAPH_ARTIFACT } from '../../testing/mock-codecompass-graph';
import { GenericGraphModel } from '../../models/graph.model';

function buildGraph(): GenericGraphModel {
  return new GraphAdapterService().fromDomainArtifact(MOCK_DOMAIN_GRAPH_ARTIFACT);
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
});
