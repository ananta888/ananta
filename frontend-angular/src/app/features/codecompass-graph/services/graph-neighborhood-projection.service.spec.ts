import { NONE_SELECTION } from '../models/graph-filter.model';
import { GenericGraphModel } from '../models/graph.model';
import { GraphNeighborhoodProjectionService } from './graph-neighborhood-projection.service';

function graph(): GenericGraphModel {
  const nodes = ['a', 'b', 'c', 'd'].map(id => ({
    id,
    kind: 'python_function' as const,
    label: id,
    file: `${id}.py`,
    content: '',
    recordId: id,
    metadata: {},
  }));
  return {
    nodes,
    edges: [
      {
        id: 'ab', source: 'a', target: 'b', edgeType: 'parent_child',
        rawEdgeType: 'parent_child', confidence: 1, metadata: {},
      },
      {
        id: 'bc', source: 'b', target: 'c', edgeType: 'related',
        rawEdgeType: 'custom_link', confidence: 1, metadata: {},
      },
      {
        id: 'cd', source: 'c', target: 'd', edgeType: 'parent_child',
        rawEdgeType: 'parent_child', confidence: 1, metadata: {},
      },
    ],
    metadata: { sourceRef: 'test', sourceKind: 'test', nodeCount: 4, edgeCount: 3 },
    warnings: [],
  };
}

describe('GraphNeighborhoodProjectionService', () => {
  const service = new GraphNeighborhoodProjectionService();
  const allNodes = new Set(['a', 'b', 'c', 'd']);

  it('treats one connection step as one visible edge in either direction', () => {
    const visible = service.project({
      graph: graph(),
      anchorNodeId: 'b',
      edgeDepth: 1,
      allowedNodeIds: allNodes,
      allowedEdgeTypes: { mode: 'all', values: [] },
    });

    expect([...visible].sort()).toEqual(['a', 'b', 'c']);
  });

  it('does not traverse a hidden edge type', () => {
    const visible = service.project({
      graph: graph(),
      anchorNodeId: 'a',
      edgeDepth: 3,
      allowedNodeIds: allNodes,
      allowedEdgeTypes: { mode: 'subset', values: ['parent_child'] },
    });

    expect([...visible].sort()).toEqual(['a', 'b']);
  });

  it('does not use a hidden node as an invisible bridge', () => {
    const visible = service.project({
      graph: graph(),
      anchorNodeId: 'a',
      edgeDepth: 3,
      allowedNodeIds: new Set(['a', 'c', 'd']),
      allowedEdgeTypes: { mode: 'all', values: [] },
    });

    expect([...visible]).toEqual(['a']);
  });

  it('returns the allowed loaded window for depth zero', () => {
    expect(service.project({
      graph: graph(),
      anchorNodeId: 'a',
      edgeDepth: 0,
      allowedNodeIds: allNodes,
      allowedEdgeTypes: NONE_SELECTION,
    })).toBe(allNodes);
  });
});
