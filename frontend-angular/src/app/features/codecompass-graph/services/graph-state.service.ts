import { Injectable, computed, inject, signal } from '@angular/core';

import { GenericGraphModel, GraphEdge, GraphNode } from '../models/graph.model';
import { MAX_GRAPH_NEIGHBORHOOD_DEPTH } from '../models/graph-neighborhood.model';
import {
  EMPTY_FILTER,
  GraphFilter,
  graphSelectionContains,
  graphSelectionToggle,
} from '../models/graph-filter.model';
import { GraphViewMode } from '../models/graph-view-mode';
import { GraphColorService } from './graph-color.service';
import { GraphNeighborhoodProjectionService } from './graph-neighborhood-projection.service';

/** Viewer-local interaction state. GraphViewer provides one instance per viewer. */
@Injectable()
export class GraphStateService {
  private readonly colors = inject(GraphColorService);
  private readonly neighborhoods = inject(GraphNeighborhoodProjectionService);
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

  private readonly baseFilteredNodes = computed<readonly GraphNode[]>(() => {
    const graph = this.graph();
    if (!graph) return [];
    const filter = this.filter();
    return graph.nodes.filter(node => this._matchesFilter(node, filter));
  });
  readonly focusNodeLabel = computed(() => {
    const nodeId = this.focusNodeId();
    return nodeId
      ? this.baseFilteredNodes().find(node => node.id === nodeId)?.label ?? ''
      : '';
  });

  readonly filteredNodes = computed<readonly GraphNode[]>(() => {
    const graph = this.graph();
    if (!graph) return [];
    const nodes = this.baseFilteredNodes();
    const focusId = this.focusNodeId();
    const depth = this.focusHopDepth();
    if (!focusId || depth === 0) return nodes;
    if (!nodes.some(node => node.id === focusId)) return nodes;
    const inFocus = this.neighborhoods.project({
      graph,
      anchorNodeId: focusId,
      edgeDepth: depth,
      allowedNodeIds: new Set(nodes.map(node => node.id)),
      allowedEdgeTypes: this.filter().edgeTypes,
    });
    return nodes.filter(node => inFocus.has(node.id));
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

  /**
   * Replace only the loaded server window while retaining viewer-local intent.
   *
   * Callers must use this only when source, evidence revision and server scope
   * are unchanged. Object references for retained selections are rebound to the
   * new canonical graph records; selections that left the window are cleared.
   */
  updateGraphWindow(graph: GenericGraphModel): void {
    const selectedNodeId = this.selectedNode()?.id ?? null;
    const selectedEdgeId = this.selectedEdge()?.id ?? null;
    const focusNodeId = this.focusNodeId();
    const hoveredDomainId = this.hoveredDomainId();
    const hoveredRelation = this.hoveredRawEdgeType();

    this.graph.set(graph);
    this.selectedNode.set(
      selectedNodeId
        ? graph.nodes.find(node => node.id === selectedNodeId) ?? null
        : null,
    );
    this.selectedEdge.set(
      selectedEdgeId
        ? graph.edges.find(edge => edge.id === selectedEdgeId) ?? null
        : null,
    );
    this.focusNodeId.set(
      focusNodeId && this._isFocusable(focusNodeId) ? focusNodeId : null,
    );
    if (
      hoveredDomainId
      && !graph.nodes.some(node => this._domainId(node) === hoveredDomainId)
    ) {
      this.hoveredDomainId.set(null);
    }
    if (
      hoveredRelation
      && !graph.edges.some(edge => (edge.rawEdgeType ?? edge.edgeType) === hoveredRelation)
    ) {
      this.hoveredRawEdgeType.set(null);
    }
  }

  setViewMode(mode: GraphViewMode): void {
    this.viewMode.set(mode);
  }

  selectNode(node: GraphNode | null): void {
    this.selectedNode.set(node);
    this.selectedEdge.set(null);
    this.focusNodeId.set(
      node && this.focusHopDepth() > 0 && this._isFocusable(node.id)
        ? node.id
        : null,
    );
  }

  selectEdge(edge: GraphEdge | null): void {
    this.selectedEdge.set(edge);
    this.selectedNode.set(null);
  }

  setFocus(nodeId: string | null, hops = 0): void {
    const depth = this._boundedDepth(hops);
    this.focusHopDepth.set(depth);
    this.focusNodeId.set(
      nodeId && depth > 0 && this._isFocusable(nodeId) ? nodeId : null,
    );
  }

  setNeighborhoodDepth(hops: number): void {
    const depth = this._boundedDepth(hops);
    const selectedNodeId = this.selectedNode()?.id ?? null;
    this.focusHopDepth.set(depth);
    this.focusNodeId.set(
      depth > 0 && selectedNodeId && this._isFocusable(selectedNodeId)
        ? selectedNodeId
        : null,
    );
  }

  updateFilter(patch: Partial<GraphFilter>): void {
    this._setFilter(Object.freeze({ ...this.filter(), ...patch }));
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
    this._setFilter(EMPTY_FILTER);
  }

  clearSelection(): void {
    this.selectedNode.set(null);
    this.selectedEdge.set(null);
    this.focusNodeId.set(null);
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
    const filter = this.filter();
    this._setFilter(Object.freeze({
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

  private _boundedDepth(value: number): number {
    return Number.isFinite(value)
      ? Math.max(0, Math.min(MAX_GRAPH_NEIGHBORHOOD_DEPTH, Math.floor(value)))
      : 0;
  }

  private _isFocusable(nodeId: string): boolean {
    const node = this.graph()?.nodes.find(candidate => candidate.id === nodeId);
    return Boolean(node && this._matchesFilter(node, this.filter()));
  }

  private _setFilter(filter: GraphFilter): void {
    this.filter.set(filter);
    const focusId = this.focusNodeId();
    if (!focusId) return;
    const focusNode = this.graph()?.nodes.find(node => node.id === focusId);
    if (!focusNode || !this._matchesFilter(focusNode, filter)) {
      this.focusNodeId.set(null);
    }
  }
}
