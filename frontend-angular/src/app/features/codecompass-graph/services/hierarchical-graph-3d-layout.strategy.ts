import {
  RenderGraph,
  RenderNodePosition,
} from '../../graph-rendering/models/render-graph.models';
import {
  Graph3dHierarchyNode,
  Graph3dLayoutHierarchyBuilder,
} from './graph-3d-layout-hierarchy';
import { Graph3dLayoutStrategy } from './graph-3d-layout.strategy';

const HIERARCHY_LEVEL_GAP = 110;
const HIERARCHY_NODE_GAP = 58;

/** Projects hierarchy depth onto Y and distributes each complete level on X/Z. */
export class HierarchicalGraph3dLayoutStrategy implements Graph3dLayoutStrategy {
  readonly mode = 'hierarchical' as const;

  constructor(private readonly hierarchy = new Graph3dLayoutHierarchyBuilder()) {}

  project(graph: RenderGraph): RenderGraph {
    const model = this.hierarchy.build(graph);
    const byDepth = new Map<number, Graph3dHierarchyNode[]>();
    for (const node of model.nodes) {
      const level = byDepth.get(node.depth) ?? [];
      level.push(node);
      byDepth.set(node.depth, level);
    }
    const positions = new Map<string, Readonly<RenderNodePosition>>();
    for (const [depth, values] of [...byDepth.entries()].sort(([left], [right]) => left - right)) {
      const ordered = [...values].sort((left, right) => (
        left.order - right.order || left.id.localeCompare(right.id)
      ));
      const columns = Math.max(1, Math.ceil(Math.sqrt(ordered.length)));
      const rows = Math.ceil(ordered.length / columns);
      ordered.forEach((node, index) => {
        const column = index % columns;
        const row = Math.floor(index / columns);
        positions.set(node.id, Object.freeze({
          x: (column - (columns - 1) / 2) * HIERARCHY_NODE_GAP,
          y: depth === 0 ? 0 : -depth * HIERARCHY_LEVEL_GAP,
          z: (row - (rows - 1) / 2) * HIERARCHY_NODE_GAP,
          fixed: true,
        }));
      });
    }
    return {
      nodes: graph.nodes.map(node => ({ ...node, position: positions.get(node.id) })),
      edges: graph.edges,
    };
  }
}
