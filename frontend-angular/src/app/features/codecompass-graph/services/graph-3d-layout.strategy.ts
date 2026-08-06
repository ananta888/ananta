import { Graph3dLayoutMode } from '../models/graph-3d-layout-mode';
import { RenderGraph } from '../../graph-rendering/models/render-graph.models';

export interface Graph3dLayoutStrategy {
  readonly mode: Graph3dLayoutMode;
  project(graph: RenderGraph): RenderGraph;
}
