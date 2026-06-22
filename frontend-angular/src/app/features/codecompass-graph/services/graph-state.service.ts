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

  readonly focusNodeId = signal<string | null>(null);
  readonly focusHopDepth = signal(0);

  readonly filteredNodes = computed(() => {
    const g = this.graph();
    if (!g) return [];
    const f = this.filter();
    let nodes = g.nodes.filter(n => this._matchesFilter(n, f));
    const fid = this.focusNodeId();
    if (fid) {
      const inFocus = this._bfsIds(g, fid, this.focusHopDepth());
      nodes = nodes.filter(n => inFocus.has(n.id));
    }
    return nodes;
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
    this.focusNodeId.set(null);
    this.focusHopDepth.set(0);
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

  private _bfsIds(g: GenericGraphModel, startId: string, hops: number): Set<string> {
    const adj = new Map<string, string[]>();
    for (const e of g.edges) {
      if (!adj.has(e.source)) adj.set(e.source, []);
      if (!adj.has(e.target)) adj.set(e.target, []);
      adj.get(e.source)!.push(e.target);
      adj.get(e.target)!.push(e.source);
    }
    const visited = new Set<string>([startId]);
    let frontier = [startId];
    for (let h = 0; h < hops; h++) {
      const next: string[] = [];
      for (const id of frontier) {
        for (const nb of adj.get(id) ?? []) {
          if (!visited.has(nb)) { visited.add(nb); next.push(nb); }
        }
      }
      frontier = next;
      if (!frontier.length) break;
    }
    return visited;
  }
}
