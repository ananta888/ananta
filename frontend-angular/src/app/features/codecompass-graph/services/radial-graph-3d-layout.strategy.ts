import {
  RenderGraph,
  RenderNodePosition,
} from '../../graph-rendering/models/render-graph.models';
import {
  Graph3dHierarchyNode,
  Graph3dLayoutHierarchyBuilder,
} from './graph-3d-layout-hierarchy';
import { Graph3dLayoutStrategy } from './graph-3d-layout.strategy';

const RADIAL_LEVEL_GAP = 105;
const RADIAL_SHELL_NODE_GAP = 42;
const ROOT_SHELL_RADIUS = 48;
const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));

/** Uses the shared hierarchy depth as a stable, monotonically growing radius. */
export class RadialGraph3dLayoutStrategy implements Graph3dLayoutStrategy {
  readonly mode = 'radial' as const;

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
    let previousRadius = 0;
    let previousDepth = -1;
    for (const [depth, values] of [...byDepth.entries()].sort(([left], [right]) => left - right)) {
      const ordered = [...values].sort((left, right) => (
        left.order - right.order || left.id.localeCompare(right.id)
      ));
      // A spherical shell has quadratic rather than linear capacity. This
      // keeps large complete domain levels inside a bounded, navigable space
      // instead of producing a single many-kilometre ring.
      const capacityRadius = RADIAL_SHELL_NODE_GAP * Math.sqrt(
        ordered.length / (4 * Math.PI),
      );
      const depthGap = (depth - previousDepth) * RADIAL_LEVEL_GAP;
      const radius = depth === 0 && ordered.length === 1
        ? 0
        : Math.max(
            depth * RADIAL_LEVEL_GAP,
            previousDepth < 0
              ? 0
              : previousRadius + Math.max(RADIAL_LEVEL_GAP, depthGap),
            capacityRadius,
            ROOT_SHELL_RADIUS,
          );
      const offset = (depth * GOLDEN_ANGLE) % (2 * Math.PI);
      ordered.forEach((node, index) => {
        const vertical = 1 - (2 * (index + 0.5) / ordered.length);
        const horizontal = Math.sqrt(Math.max(0, 1 - vertical * vertical));
        const angle = offset + index * GOLDEN_ANGLE;
        positions.set(node.id, Object.freeze({
          x: Math.cos(angle) * horizontal * radius,
          y: vertical * radius,
          z: Math.sin(angle) * horizontal * radius,
          fixed: true,
        }));
      });
      previousRadius = radius;
      previousDepth = depth;
    }
    return {
      nodes: graph.nodes.map(node => ({ ...node, position: positions.get(node.id) })),
      edges: graph.edges,
    };
  }
}
