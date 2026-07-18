import { Injectable, computed, inject, signal } from '@angular/core';

import { GenericGraphModel, GraphEdge, GraphNode } from '../models/graph.model';
import {
  EMPTY_FILTER,
  GraphFilter,
  graphSelectionContains,
  graphSelectionToggle,
} from '../models/graph-filter.model';
import { GraphViewMode } from '../models/graph-view-mode';
import { GraphColorService } from './graph-color.service';

/** Viewer-local interaction state. GraphViewer provides one instance per viewer. */
@Injectable()
export class GraphStateService {
  private readonly colors = inject(GraphColorService);
  readonly viewMode = signal<GraphViewMode>('simple');
  readonly selectedNode = signal<GraphNode | null>(null);
  readonly selectedEdge = signal<GraphEdge | null>(null);
  readonly filter = signal<GraphFilter>(EMPTY_FILTER);
  readonly graph = signal<GenericGraphModel | null>(null);
  readonly hoveredDomainId = signal<string | null>(null);
  readonly hoveredRawEdgeType = signal<string | null>(null);

  readonly focusNodeId = signal<string | null>(null);
  readonly focusHopDepth = signal(0);

  readonly nodeKindInventory = computed<readonly string[]>(() => this._inventory(
    this.graph()?.nodes.map(node => node.rawNodeType ?? node.kind) ?? [],
  ));

  readonly edgeTypeInventory = computed<readonly string[]>(() => this._inventory(
    this.graph()?.edges.map(edge => edge.rawEdgeType ?? edge.edgeType) ?? [],
  ));

  readonly domainInventory = computed<readonly string[]>(() => this._inventory(
    this.graph()?.nodes.map(node => this._domainId(node)) ?? [],
  ));

  readonly filteredNodes = computed<readonly GraphNode[]>(() => {
    const graph = this.graph();
    if (!graph) return [];
    const filter = this.filter();
    let nodes = graph.nodes.filter(node => this._matchesFilter(node, filter));
    const focusId = this.focusNodeId();
    if (focusId) {
      const inFocus = this._bfsIds(graph, focusId, this.focusHopDepth());
      nodes = nodes.filter(node => inFocus.has(node.id));
    }
    return nodes;
  });

  readonly filteredEdges = computed<readonly GraphEdge[]>(() => {
    const graph = this.graph();
    if (!graph) return [];
    const filter = this.filter();
    const visibleIds = new Set(this.filteredNodes().map(node => node.id));
    return graph.edges.filter(edge =>
      visibleIds.has(edge.source) &&
      visibleIds.has(edge.target) &&
      graphSelectionContains(filter.edgeTypes, edge.rawEdgeType ?? edge.edgeType),
    );
  });

  /** Memoized wrapper: change detection alone never allocates a new graph. */
  readonly filteredGraph = computed<GenericGraphModel | null>(() => {
    const graph = this.graph();
    if (!graph) return null;
    const nodes = this.filteredNodes();
    const edges = this.filteredEdges();
    if (nodes === graph.nodes && edges === graph.edges) return graph;
    return { ...graph, nodes: [...nodes], edges: [...edges] };
  });

  setGraph(graph: GenericGraphModel): void {
    this.graph.set(graph);
    this.selectedNode.set(null);
    this.selectedEdge.set(null);
    this.focusNodeId.set(null);
    this.focusHopDepth.set(0);
    this.hoveredDomainId.set(null);
    this.hoveredRawEdgeType.set(null);
    this.filter.set(EMPTY_FILTER);
  }

  setViewMode(mode: GraphViewMode): void {
    this.viewMode.set(mode);
  }

  selectNode(node: GraphNode | null): void {
    this.selectedNode.set(node);
    this.selectedEdge.set(null);
  }

  selectEdge(edge: GraphEdge | null): void {
    this.selectedEdge.set(edge);
    this.selectedNode.set(null);
  }

  setFocus(nodeId: string | null, hops = 0): void {
    const depth = Math.max(0, Math.floor(hops));
    this.focusHopDepth.set(depth);
    this.focusNodeId.set(nodeId && depth > 0 ? nodeId : null);
  }

  updateFilter(patch: Partial<GraphFilter>): void {
    this.filter.update(filter => Object.freeze({ ...filter, ...patch }));
  }

  setNodeKindVisible(rawNodeType: string, visible: boolean): void {
    this._toggleSelection('nodeKinds', rawNodeType, visible, this.nodeKindInventory());
  }

  setEdgeTypeVisible(rawEdgeType: string, visible: boolean): void {
    this._toggleSelection('edgeTypes', rawEdgeType, visible, this.edgeTypeInventory());
  }

  setDomainVisible(domainId: string, visible: boolean): void {
    this._toggleSelection('domains', domainId, visible, this.domainInventory());
  }

  resetFilter(): void {
    this.filter.set(EMPTY_FILTER);
  }

  clearSelection(): void {
    this.selectedNode.set(null);
    this.selectedEdge.set(null);
  }

  clearHover(): void {
    this.hoveredDomainId.set(null);
    this.hoveredRawEdgeType.set(null);
  }

  private _toggleSelection(
    key: 'nodeKinds' | 'edgeTypes' | 'domains',
    value: string,
    visible: boolean,
    inventory: readonly string[],
  ): void {
    this.filter.update(filter => ({
      ...filter,
      [key]: graphSelectionToggle(filter[key], value, visible, inventory),
    }));
  }

  private _matchesFilter(node: GraphNode, filter: GraphFilter): boolean {
    if (!graphSelectionContains(filter.nodeKinds, node.rawNodeType ?? node.kind)) return false;
    if (!graphSelectionContains(filter.domains, this._domainId(node))) return false;
    if (filter.searchText) {
      const query = filter.searchText.toLowerCase();
      if (!node.label.toLowerCase().includes(query) && !node.file.toLowerCase().includes(query)) {
        return false;
      }
    }
    return true;
  }

  private _domainId(node: GraphNode): string {
    return this.colors.resolveCanonicalDomain(node).canonicalId;
  }

  private _inventory(values: readonly string[]): readonly string[] {
    return Object.freeze([...new Set(values)].sort((left, right) => left.localeCompare(right)));
  }

  private _bfsIds(graph: GenericGraphModel, startId: string, hops: number): Set<string> {
    const adjacent = new Map<string, string[]>();
    for (const edge of graph.edges) {
      if (!adjacent.has(edge.source)) adjacent.set(edge.source, []);
      if (!adjacent.has(edge.target)) adjacent.set(edge.target, []);
      adjacent.get(edge.source)!.push(edge.target);
      adjacent.get(edge.target)!.push(edge.source);
    }
    const visited = new Set<string>([startId]);
    let frontier = [startId];
    for (let hop = 0; hop < hops; hop++) {
      const next: string[] = [];
      for (const id of frontier) {
        for (const neighbour of adjacent.get(id) ?? []) {
          if (!visited.has(neighbour)) {
            visited.add(neighbour);
            next.push(neighbour);
          }
        }
      }
      frontier = next;
      if (!frontier.length) break;
    }
    return visited;
  }
}
