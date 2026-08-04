import { Injectable } from '@angular/core';

import { GraphFilterSelection, graphSelectionContains } from '../models/graph-filter.model';
import { GenericGraphModel } from '../models/graph.model';
import { MAX_GRAPH_NEIGHBORHOOD_DEPTH } from '../models/graph-neighborhood.model';

export interface GraphNeighborhoodProjectionRequest {
  readonly graph: GenericGraphModel;
  readonly anchorNodeId: string;
  readonly edgeDepth: number;
  readonly allowedNodeIds: ReadonlySet<string>;
  readonly allowedEdgeTypes: GraphFilterSelection<string>;
}

/**
 * Computes a local, undirected neighbourhood inside the already loaded graph.
 *
 * Only currently visible nodes and edge types may participate in traversal, so
 * filtered elements cannot act as invisible bridges between displayed nodes.
 */
@Injectable({ providedIn: 'root' })
export class GraphNeighborhoodProjectionService {
  project(request: GraphNeighborhoodProjectionRequest): ReadonlySet<string> {
    const depth = Number.isFinite(request.edgeDepth)
      ? Math.max(
          0,
          Math.min(MAX_GRAPH_NEIGHBORHOOD_DEPTH, Math.floor(request.edgeDepth)),
        )
      : 0;
    if (depth === 0) return request.allowedNodeIds;
    if (!request.allowedNodeIds.has(request.anchorNodeId)) return new Set();

    const adjacency = new Map<string, Set<string>>();
    for (const edge of request.graph.edges) {
      if (!request.allowedNodeIds.has(edge.source) || !request.allowedNodeIds.has(edge.target)) {
        continue;
      }
      if (!graphSelectionContains(
        request.allowedEdgeTypes,
        edge.rawEdgeType ?? edge.edgeType,
      )) {
        continue;
      }
      this.addNeighbour(adjacency, edge.source, edge.target);
      this.addNeighbour(adjacency, edge.target, edge.source);
    }

    const visited = new Set<string>([request.anchorNodeId]);
    let frontier = [request.anchorNodeId];
    for (let distance = 0; distance < depth && frontier.length > 0; distance++) {
      const next: string[] = [];
      for (const nodeId of frontier) {
        for (const neighbour of adjacency.get(nodeId) ?? []) {
          if (visited.has(neighbour)) continue;
          visited.add(neighbour);
          next.push(neighbour);
        }
      }
      frontier = next;
    }
    return visited;
  }

  private addNeighbour(
    adjacency: Map<string, Set<string>>,
    nodeId: string,
    neighbourId: string,
  ): void {
    let neighbours = adjacency.get(nodeId);
    if (!neighbours) {
      neighbours = new Set<string>();
      adjacency.set(nodeId, neighbours);
    }
    neighbours.add(neighbourId);
  }
}
