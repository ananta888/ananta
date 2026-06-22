import {
  Component, Input, Output, EventEmitter,
  ElementRef, ViewChild, OnChanges, SimpleChanges, OnDestroy,
  ChangeDetectionStrategy, ChangeDetectorRef, inject,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import type { ForceGraph3DInstance } from '3d-force-graph';
import { GenericGraphModel, GraphEdge, GraphNode } from '../../models/graph.model';

const KIND_COLORS: Record<string, string> = {
  java_type:   '#3b82f6',
  java_method: '#10b981',
  config:      '#f59e0b',
  xml_tag:     '#8b5cf6',
  unknown:     '#94a3b8',
};

const RENDER_CAP = 500;

function hasWebGL(): boolean {
  try {
    const canvas = document.createElement('canvas');
    return !!(canvas.getContext('webgl') || canvas.getContext('experimental-webgl'));
  } catch {
    return false;
  }
}

@Component({
  standalone: true,
  selector: 'app-graph-3d-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  template: `
    @if (webglUnavailable) {
      <div class="fallback-msg">
        <p>WebGL is not available in this browser. The 3D renderer cannot start.</p>
        <p>Switch to <strong>Simple</strong> or <strong>2D</strong> view to explore the graph.</p>
      </div>
    } @else if (error) {
      <p class="error-msg">{{ error }}</p>
    } @else if (loading) {
      <p class="status-msg">Loading 3D renderer…</p>
    } @else if (!graph || graph.nodes.length === 0) {
      <p class="status-msg">No nodes to display.</p>
    }
    <div
      #container
      class="fg3d-container"
      [style.display]="showCanvas ? 'block' : 'none'"
    ></div>
  `,
  styles: [`
    :host { display: flex; flex-direction: column; flex: 1; width: 100%; height: 100%; min-height: 0; }
    .fg3d-container { flex: 1; width: 100%; height: 100%; min-height: 0; }
    .fallback-msg { padding: 1.5rem; color: #555; line-height: 1.6; }
    .fallback-msg p { margin: 0 0 .5rem; }
    .error-msg { color: #c00; padding: .75rem; }
    .status-msg { color: #888; padding: .75rem; font-style: italic; }
  `],
})
export class Graph3dViewComponent implements OnChanges, OnDestroy {
  @ViewChild('container', { static: true }) containerRef!: ElementRef<HTMLElement>;

  @Input() graph: GenericGraphModel | null = null;
  @Input() selectedNode: GraphNode | null = null;
  @Input() selectedEdge: GraphEdge | null = null;

  @Output() nodeSelected = new EventEmitter<GraphNode>();
  @Output() edgeSelected = new EventEmitter<GraphEdge>();

  loading = false;
  error = '';
  webglUnavailable = false;

  get showCanvas(): boolean {
    return !this.webglUnavailable && !this.error && !this.loading && !!this.graph && this.graph.nodes.length > 0;
  }

  private cdr = inject(ChangeDetectorRef);
  private fg: ForceGraph3DInstance | null = null;
  private nodeMap = new Map<string, GraphNode>();
  private edgeMap = new Map<string, GraphEdge>();
  private _focalId: string | null = null;
  private _neighbourIds = new Set<string>();

  ngOnChanges(changes: SimpleChanges): void {
    const gc = changes['graph'];

    // Only selection changed — update highlight without rebuild
    if (!gc) {
      this._updateHighlight(this.selectedNode?.id ?? null);
      return;
    }
    const prev = gc.previousValue as GenericGraphModel | null;
    const curr = gc.currentValue as GenericGraphModel | null;
    if (prev && curr && prev.nodes === curr.nodes && prev.edges === curr.edges) {
      this._updateHighlight(this.selectedNode?.id ?? null);
      return;
    }

    this.nodeMap.clear();
    this.edgeMap.clear();
    this.graph?.nodes.forEach(n => this.nodeMap.set(n.id, n));
    this.graph?.edges.forEach(e => this.edgeMap.set(e.id, e));
    this._focalId = null;
    this._neighbourIds.clear();
    this._render();
  }

  private _updateHighlight(nodeId: string | null): void {
    this._focalId = nodeId;
    this._neighbourIds.clear();
    if (nodeId && this.graph) {
      for (const e of this.graph.edges) {
        if (e.source === nodeId) this._neighbourIds.add(e.target);
        if (e.target === nodeId) this._neighbourIds.add(e.source);
      }
    }
    if (!this.fg) return;
    this.fg
      .nodeColor((n: any) => this._nodeColor(n['id'] as string))
      .nodeOpacity(nodeId ? 0.9 : 0.75)
      .linkColor((l: any) => {
        if (!this._focalId) return '#94a3b8';
        const src = typeof l['source'] === 'object' ? l['source']?.id : l['source'];
        const tgt = typeof l['target'] === 'object' ? l['target']?.id : l['target'];
        return src === this._focalId || tgt === this._focalId ? '#38bdf8' : 'rgba(148,163,184,0.12)';
      })
      .linkWidth((l: any) => {
        if (!this._focalId) return 1;
        const src = typeof l['source'] === 'object' ? l['source']?.id : l['source'];
        const tgt = typeof l['target'] === 'object' ? l['target']?.id : l['target'];
        return src === this._focalId || tgt === this._focalId ? 2.5 : 0.5;
      });
  }

  private _nodeColor(id: string): string {
    const kind = this.nodeMap.get(id)?.kind ?? 'unknown';
    const base = KIND_COLORS[kind] ?? KIND_COLORS['unknown'];
    if (!this._focalId) return base;
    if (id === this._focalId) return '#f59e0b';
    if (this._neighbourIds.has(id)) return '#38bdf8';
    return 'rgba(100,116,139,0.25)';
  }

  private _cappedGraph(): { nodes: GraphNode[]; edges: GraphEdge[] } {
    if (!this.graph) return { nodes: [], edges: [] };
    if (this.graph.nodes.length <= RENDER_CAP) {
      return { nodes: this.graph.nodes, edges: this.graph.edges };
    }

    const degree = new Map<string, number>();
    for (const edge of this.graph.edges) {
      degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1);
      degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1);
    }

    const nodes = [...this.graph.nodes]
      .sort((a, b) => (degree.get(b.id) ?? 0) - (degree.get(a.id) ?? 0))
      .slice(0, RENDER_CAP);
    const kept = new Set(nodes.map(node => node.id));
    const edges = this.graph.edges.filter(edge => kept.has(edge.source) && kept.has(edge.target));
    return { nodes, edges };
  }

  ngOnDestroy(): void {
    this._destroy();
  }

  private _destroy(): void {
    if (this.fg) {
      this.fg._destructor();
      this.fg = null;
    }
  }

  private async _render(): Promise<void> {
    this._destroy();
    this.error = '';
    this.webglUnavailable = false;

    if (!this.graph || this.graph.nodes.length === 0) return;

    if (!hasWebGL()) {
      this.webglUnavailable = true;
      this.cdr.detectChanges();
      return;
    }

    this.loading = true;
    this.cdr.detectChanges();

    try {
      const { default: ForceGraph3D } = await import('3d-force-graph');

      const { nodes, edges } = this._cappedGraph();

      const gNodes = nodes.map(n => ({
        id: n.id, label: n.label, kind: n.kind,
        color: KIND_COLORS[n.kind] ?? KIND_COLORS['unknown'],
      }));
      const gLinks = edges.map(e => ({
        id: e.id, source: e.source, target: e.target, label: e.edgeType,
      }));

      const el = this.containerRef.nativeElement;
      const w = el.clientWidth  || 800;
      const h = el.clientHeight || 500;

      this.fg = new ForceGraph3D(el, { controlType: 'orbit' })
        .width(w).height(h)
        .backgroundColor('#0f172a')
        .nodeLabel((n: any) => n['label'] as string)
        .nodeColor((n: any) => this._nodeColor(n['id'] as string))
        .nodeRelSize(4)
        .linkLabel((l: any) => l['label'] as string)
        .linkColor(() => '#94a3b8')
        .linkWidth(1)
        .linkOpacity(0.6)
        .warmupTicks(60)
        .cooldownTime(6000)
        .d3AlphaDecay(0.05)
        .d3VelocityDecay(0.4)
        .onNodeClick((node: any) => {
          const id = node['id'] as string;
          this._updateHighlight(this._focalId === id ? null : id);
          const gNode = this.nodeMap.get(id);
          if (gNode) this.nodeSelected.emit(gNode);
        })
        .onLinkClick((link: any) => {
          const gEdge = this.edgeMap.get(link['id'] as string);
          if (gEdge) this.edgeSelected.emit(gEdge);
        })
        .onBackgroundClick(() => {
          this._updateHighlight(null);
        })
        .graphData({ nodes: gNodes, links: gLinks });

      // Compact layout: reduce repulsion + shorten links
      (this.fg.d3Force('charge') as any)?.strength(-20);
      (this.fg.d3Force('link') as any)?.distance(25);

      // Zoom to fit after warmup ticks have settled the layout
      setTimeout(() => this.fg?.zoomToFit(600, 40), 200);

    } catch (err) {
      this.error = `Failed to load 3D renderer: ${(err as Error).message ?? err}`;
    } finally {
      this.loading = false;
      this.cdr.detectChanges();
    }
  }
}
