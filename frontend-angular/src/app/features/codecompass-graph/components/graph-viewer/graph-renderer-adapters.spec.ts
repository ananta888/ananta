import { TestBed } from '@angular/core/testing';

import { GraphVisualProjection } from '../../models/graph-visual-metrics.model';
import { GenericGraphModel } from '../../models/graph.model';
import { Graph2dViewComponent } from '../graph-2d-view/graph-2d-view.component';
import { Graph3dViewComponent } from '../graph-3d-view/graph-3d-view.component';
import { SimpleGraphViewComponent } from '../simple-graph-view/simple-graph-view.component';

const GRAPH: GenericGraphModel = {
  nodes: [
    { id: 'small', kind: 'python_function', label: 'small', file: 'a.py', content: '', recordId: 'small', metadata: {} },
    { id: 'large', kind: 'python_function', label: 'large', file: 'b.py', content: '', recordId: 'large', metadata: {} },
  ],
  edges: [
    { id: 'edge', source: 'small', target: 'large', edgeType: 'calls_probable_target', confidence: 1, metadata: {} },
  ],
  metadata: { sourceRef: 'test', sourceKind: 'test', graphRevision: 'rev', nodeCount: 2, edgeCount: 1 },
  warnings: [],
};

const PROJECTION: GraphVisualProjection = {
  graphRevision: 'rev', profileHash: 'profile', domainLegend: [], relationLegend: [],
  nodeStyles: {
    small: {
      nodeId: 'small', baseColor: '#112233', marker: 'circle', baseSize: 5, score: 0.2,
      scoreState: 'scored', availability: 'available', breakdown: [],
      highlightFactors: { hover: 1.2, selected: 1.5, connected: 1.1 },
    },
    large: {
      nodeId: 'large', baseColor: '#445566', marker: 'diamond', baseSize: 12, score: 0.8,
      scoreState: 'scored', availability: 'available', breakdown: [],
      highlightFactors: { hover: 1.2, selected: 1.5, connected: 1.1 },
    },
  },
  edgeStyles: {
    edge: {
      edgeId: 'edge', baseColor: '#778899', marker: 'triangle', baseThickness: 3, score: 0.6,
      scoreState: 'scored', availability: 'available', breakdown: [],
      highlightFactors: { hover: 1.2, selected: 1.5, connected: 1.1 },
    },
  },
};

describe('graph renderer projection adapters', () => {
  it('consumes the same canonical styles and ranking in 2D, 3D and simple views', async () => {
    await TestBed.configureTestingModule({
      imports: [Graph2dViewComponent, Graph3dViewComponent, SimpleGraphViewComponent],
    }).compileComponents();
    const twoD = TestBed.createComponent(Graph2dViewComponent).componentInstance;
    const threeD = TestBed.createComponent(Graph3dViewComponent).componentInstance;
    const simple = TestBed.createComponent(SimpleGraphViewComponent).componentInstance;
    twoD.visualProjection = PROJECTION;
    threeD.visualProjection = PROJECTION;
    simple.visualProjection = PROJECTION;
    threeD.graph = GRAPH;
    (threeD as any).projectGraph();
    (threeD as any).projectStyles();

    for (const node of GRAPH.nodes) {
      expect((twoD as any)._nodeVisual(node, 99)).toBe(PROJECTION.nodeStyles[node.id]);
      expect(threeD.renderNodeStyles[node.id]).toEqual({
        color: PROJECTION.nodeStyles[node.id].baseColor,
        size: PROJECTION.nodeStyles[node.id].baseSize,
        highlightFactors: PROJECTION.nodeStyles[node.id].highlightFactors,
      });
      expect(simple.nodeStyle(node)).toBe(PROJECTION.nodeStyles[node.id]);
    }
    expect((twoD as any)._nodeVisual(GRAPH.nodes[1], 99).baseSize)
      .toBeGreaterThan((twoD as any)._nodeVisual(GRAPH.nodes[0], 99).baseSize);
    expect(threeD.renderNodeStyles['large'].size).toBeGreaterThan(threeD.renderNodeStyles['small'].size);
    expect(simple.edgeStyle(GRAPH.edges[0])).toBe(PROJECTION.edgeStyles['edge']);
  });

  it('keeps ranking when one highlight layer applies the same multiplicative factor', async () => {
    await TestBed.configureTestingModule({ imports: [Graph3dViewComponent] }).compileComponents();
    const component = TestBed.createComponent(Graph3dViewComponent).componentInstance;
    component.graph = GRAPH;
    component.visualProjection = PROJECTION;
    (component as any).projectGraph();
    (component as any).projectStyles();
    const small = component.renderNodeStyles['small'];
    const large = component.renderNodeStyles['large'];
    expect(small.size * small.highlightFactors!.hover).toBe(6);
    expect(large.size * large.highlightFactors!.hover).toBeCloseTo(14.4, 12);
    expect(large.size).toBeGreaterThan(small.size);
  });
});
