import { Injectable, signal, computed } from '@angular/core';
import { GenericGraphModel, GraphEdge, GraphNode } from '../models/graph.model';
import { GraphFilter, EMPTY_FILTER } from '../models/graph-filter.model';
import { GraphViewMode } from '../models/graph-view-mode';

@Injectable({ providedIn: 'root' })
export class GraphStateService {

  readonly viewMode = signal<GraphViewMode>('simple');
  readonly selectedNode = signal<GraphNode | null>(null);
  readonly selectedEdge = signal<GraphEdge | null>(null);
  readonly filter = signal<GraphFilter>({ ...EMPTY_FILTER });
  readonly graph = signal<GenericGraphModel | null>(null);

  readonly filteredNodes = computed(() => {
    const g = this.graph();
    if (!g) return [];
    const f = this.filter();
    return g.nodes.filter(n => this._matchesFilter(n, f));
  });

  readonly filteredEdges = computed(() => {
    const g = this.graph();
    if (!g) return [];
    const f = this.filter();
    const visibleIds = new Set(this.filteredNodes().map(n => n.id));
    return g.edges.filter(e =>
      visibleIds.has(e.source) && visibleIds.has(e.target) &&
      (f.edgeTypeFilter.length === 0 || f.edgeTypeFilter.includes(e.edgeType)),
    );
  });

  setGraph(graph: GenericGraphModel): void {
    this.graph.set(graph);
    this.selectedNode.set(null);
    this.selectedEdge.set(null);
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

  updateFilter(patch: Partial<GraphFilter>): void {
    this.filter.update(f => ({ ...f, ...patch }));
  }

  resetFilter(): void {
    this.filter.set({ ...EMPTY_FILTER });
  }

  clearSelection(): void {
    this.selectedNode.set(null);
    this.selectedEdge.set(null);
  }

  private _matchesFilter(node: GraphNode, f: GraphFilter): boolean {
    if (f.nodeKindFilter.length > 0 && !f.nodeKindFilter.includes(node.kind)) {
      return false;
    }
    if (f.searchText) {
      const q = f.searchText.toLowerCase();
      if (!node.label.toLowerCase().includes(q) && !node.file.toLowerCase().includes(q)) {
        return false;
      }
    }
    return true;
  }
}
