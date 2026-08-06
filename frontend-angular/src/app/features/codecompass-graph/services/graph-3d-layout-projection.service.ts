import { Injectable, InjectionToken, inject } from '@angular/core';

import { Graph3dLayoutMode } from '../models/graph-3d-layout-mode';
import { RenderGraph } from '../../graph-rendering/models/render-graph.models';
import { ForceGraph3dLayoutStrategy } from './force-graph-3d-layout.strategy';
import { Graph3dLayoutHierarchyBuilder } from './graph-3d-layout-hierarchy';
import { Graph3dLayoutStrategy } from './graph-3d-layout.strategy';
import { HierarchicalGraph3dLayoutStrategy } from './hierarchical-graph-3d-layout.strategy';
import { RadialGraph3dLayoutStrategy } from './radial-graph-3d-layout.strategy';

export const GRAPH_3D_LAYOUT_STRATEGIES = new InjectionToken<readonly Graph3dLayoutStrategy[]>(
  'GRAPH_3D_LAYOUT_STRATEGIES',
  {
    providedIn: 'root',
    factory: () => {
      const hierarchy = new Graph3dLayoutHierarchyBuilder();
      return Object.freeze([
        new ForceGraph3dLayoutStrategy(),
        new HierarchicalGraph3dLayoutStrategy(hierarchy),
        new RadialGraph3dLayoutStrategy(hierarchy),
      ]);
    },
  },
);

/** Selects a layout strategy without coupling layout algorithms to WebGL. */
@Injectable({ providedIn: 'root' })
export class Graph3dLayoutProjectionService {
  private readonly strategies: ReadonlyMap<Graph3dLayoutMode, Graph3dLayoutStrategy> = new Map(
    inject(GRAPH_3D_LAYOUT_STRATEGIES).map(strategy => [strategy.mode, strategy]),
  );

  project(graph: RenderGraph, mode: Graph3dLayoutMode): RenderGraph {
    return (this.strategies.get(mode) ?? this.strategies.get('force'))?.project(graph) ?? graph;
  }
}
