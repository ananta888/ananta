import { RenderGraph } from '../../graph-rendering/models/render-graph.models';
import { Graph3dLayoutStrategy } from './graph-3d-layout.strategy';

/** Keeps the existing unconstrained 3D force simulation. */
export class ForceGraph3dLayoutStrategy implements Graph3dLayoutStrategy {
  readonly mode = 'force' as const;

  project(graph: RenderGraph): RenderGraph {
    if (graph.nodes.every(node => node.position === undefined)) return graph;
    return {
      nodes: graph.nodes.map(({ position: _position, ...node }) => node),
      edges: graph.edges,
    };
  }
}
