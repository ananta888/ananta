import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Graph2dViewComponent } from './graph-2d-view.component';
import { GenericGraphModel } from '../../models/graph.model';

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
});
