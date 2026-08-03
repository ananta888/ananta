import { ComponentFixture, TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { describe, expect, it, vi } from 'vitest';

import { ForceGraph3dRendererComponent } from '../../../graph-rendering/components/force-graph-3d-renderer.component';
import { FORCE_GRAPH_3D_FACTORY } from '../../../graph-rendering/ports/force-graph-3d-factory.port';
import { GenericGraphModel } from '../../models/graph.model';
import { GraphVisualProjection } from '../../models/graph-visual-metrics.model';
import { GraphAdapterService } from '../../services/graph-adapter.service';
import { MOCK_DOMAIN_GRAPH_ARTIFACT } from '../../testing/mock-codecompass-graph';
import { Graph3dViewComponent } from './graph-3d-view.component';

function buildGraph(): GenericGraphModel {
  return new GraphAdapterService().fromDomainArtifact(MOCK_DOMAIN_GRAPH_ARTIFACT);
}

describe('Graph3dViewComponent renderer adapter', () => {
  let fixture: ComponentFixture<Graph3dViewComponent>;
  let component: Graph3dViewComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [Graph3dViewComponent],
      providers: [{
        provide: FORCE_GRAPH_3D_FACTORY,
        useValue: { webglAvailable: () => false, create: vi.fn() },
      }],
    }).compileComponents();
    fixture = TestBed.createComponent(Graph3dViewComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('projects all CodeCompass nodes and edges into the neutral contract', () => {
    const graph = buildGraph();
    fixture.componentRef.setInput('graph', graph);
    fixture.detectChanges();

    expect(component.renderGraph?.nodes).toHaveLength(graph.nodes.length);
    expect(component.renderGraph?.edges).toHaveLength(graph.edges.length);
    expect(component.renderGraph?.edges[0]).toMatchObject({
      sourceId: graph.edges[0].source,
      targetId: graph.edges[0].target,
    });
    expect(component.webglUnavailable).toBe(true);
  });

  it('maps the canonical visual projection without changing the graph contract', () => {
    const graph = buildGraph();
    const firstNode = graph.nodes[0];
    const firstEdge = graph.edges[0];
    const projection = {
      graphRevision: 'rev', profileHash: 'profile', domainLegend: [], relationLegend: [],
      nodeStyles: {
        [firstNode.id]: {
          nodeId: firstNode.id, baseColor: '#112233', marker: 'circle', baseSize: 9,
          score: .5, scoreState: 'scored', availability: 'available', breakdown: [],
          highlightFactors: { hover: 1.25, selected: 1.5, connected: 1.1 },
        },
      },
      edgeStyles: {
        [firstEdge.id]: {
          edgeId: firstEdge.id, baseColor: '#445566', marker: 'triangle', baseThickness: 3,
          score: .5, scoreState: 'scored', availability: 'available', breakdown: [],
          highlightFactors: { hover: 1.25, selected: 1.5, connected: 1.1 },
        },
      },
    } as GraphVisualProjection;

    fixture.componentRef.setInput('graph', graph);
    fixture.componentRef.setInput('visualProjection', projection);
    fixture.detectChanges();

    expect(component.renderNodeStyles[firstNode.id]).toMatchObject({ color: '#112233', size: 9 });
    expect(component.renderEdgeStyles[firstEdge.id]).toMatchObject({ color: '#445566', width: 3 });
  });

  it('maps neutral selection ids back to CodeCompass domain objects', () => {
    const graph = buildGraph();
    fixture.componentRef.setInput('graph', graph);
    fixture.detectChanges();
    const nodeListener = vi.fn();
    const edgeListener = vi.fn();
    component.nodeSelected.subscribe(nodeListener);
    component.edgeSelected.subscribe(edgeListener);

    component.selectNode(graph.nodes[0].id);
    component.selectEdge(graph.edges[0].id);

    expect(nodeListener).toHaveBeenCalledWith(graph.nodes[0]);
    expect(edgeListener).toHaveBeenCalledWith(graph.edges[0]);
  });

  it('forwards background selection clearing through the adapter', () => {
    const listener = vi.fn();
    component.selectionCleared.subscribe(listener);
    const renderer = fixture.debugElement.query(By.directive(ForceGraph3dRendererComponent))
      .componentInstance as ForceGraph3dRendererComponent;

    renderer.selectionCleared.emit();

    expect(listener).toHaveBeenCalledOnce();
  });
});
