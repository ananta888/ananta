import {
  Component, Input, Output, EventEmitter,
  ElementRef, ViewChild, OnChanges, SimpleChanges, AfterViewInit, OnDestroy,
  ChangeDetectionStrategy, ChangeDetectorRef, inject,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import type { ForceGraph3DInstance } from '3d-force-graph';
import { GenericGraphModel, GraphEdge, GraphNode } from '../../models/graph.model';

const KIND_COLORS: Record<string, string> = {
  java_type:   '#3b82f6',
  java_method: '#10b981',
  java_file:   '#1d4ed8',
  python_file: '#f59e0b',
  python_class: '#d97706',
  python_function: '#92400e',
  python_method: '#78350f',
  python_import: '#a16207',
  typescript_file: '#0284c7',
  typescript_class: '#0369a1',
  typescript_function: '#075985',
  typescript_import: '#0e7490',
  config:      '#f59e0b',
  xml_tag:     '#8b5cf6',
  unknown:     '#94a3b8',
};

const DOMAIN_COLORS = [
  '#60a5fa', '#34d399', '#f87171', '#c084fc', '#22d3ee',
  '#facc15', '#f472b6', '#818cf8', '#2dd4bf', '#fb923c',
];

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
    } @else if (!graph || graph.nodes.length === 0) {
      <p class="status-msg">No nodes to display.</p>
    }
    @if (loading && graph && graph.nodes.length > 0) {
      <p class="status-msg overlay-msg">Loading 3D renderer…</p>
    }
    <div
      #container
      class="fg3d-container"
      [style.visibility]="showCanvas ? 'visible' : 'hidden'"
    ></div>
  `,
  styles: [`
    :host { display: flex; flex: 1; width: 100%; height: 100%; min-height: 0; position: relative; overflow: hidden; }
    .fg3d-container { position: absolute; inset: 0; width: 100%; height: 100%; min-height: 0; overflow: hidden; background: #0f172a; }
    .fallback-msg { padding: 1.5rem; color: #555; line-height: 1.6; }
    .fallback-msg p { margin: 0 0 .5rem; }
    .error-msg { color: #c00; padding: .75rem; position: relative; z-index: 2; }
    .status-msg { color: #888; padding: .75rem; font-style: italic; position: relative; z-index: 2; }
    .overlay-msg {
      position: absolute; top: .5rem; left: .5rem; z-index: 3; margin: 0;
      background: rgba(15, 23, 42, .78); color: #e2e8f0; border-radius: 4px;
      padding: .35rem .5rem; font-size: .8rem;
    }
  `],
})
export class Graph3dViewComponent implements OnChanges, AfterViewInit, OnDestroy {
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
    return !this.webglUnavailable && !this.error && !!this.graph && this.graph.nodes.length > 0;
  }

  private cdr = inject(ChangeDetectorRef);
  private fg: ForceGraph3DInstance | null = null;
  private nodeMap = new Map<string, GraphNode>();
  private edgeMap = new Map<string, GraphEdge>();
  private _focalId: string | null = null;
  private _neighbourIds = new Set<string>();
  private _capAnchorId: string | null = null;
  private resizeObserver: ResizeObserver | null = null;

  ngOnChanges(changes: SimpleChanges): void {
    const gc = changes['graph'];

    // Only selection changed: update highlight without rebuilding the WebGL scene.
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

  ngAfterViewInit(): void {
    if (typeof ResizeObserver === 'undefined') return;
    this.resizeObserver = new ResizeObserver(() => this._resizeToContainer());
    this.resizeObserver.observe(this.containerRef.nativeElement);
  }

  private _resizeToContainer(): void {
    if (!this.fg) return;
    const el = this.containerRef.nativeElement;
    const width = Math.max(1, el.clientWidth || el.getBoundingClientRect().width || 800);
    const height = Math.max(1, el.clientHeight || el.getBoundingClientRect().height || 500);
    this.fg.width(width).height(height);
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
    const node = this.nodeMap.get(id);
    const base = node ? this._domainColor(node) : KIND_COLORS['unknown'];
    if (!this._focalId) return base;
    if (id === this._focalId) return '#f59e0b';
    if (this._neighbourIds.has(id)) return '#38bdf8';
    return 'rgba(100,116,139,0.25)';
  }

  private _hash(value: string): number {
    let hash = 0;
    for (let i = 0; i < value.length; i++) {
      hash = ((hash << 5) - hash + value.charCodeAt(i)) | 0;
    }
    return Math.abs(hash);
  }

  private _domainColor(node: GraphNode): string {
    const domain = String(node.metadata?.['domain_path'] ?? '');
    if (!domain) return KIND_COLORS[node.kind] ?? KIND_COLORS['unknown'];
    return DOMAIN_COLORS[this._hash(domain) % DOMAIN_COLORS.length];
  }

  private _domainLevel(node: GraphNode): number {
    const raw = Number(node.metadata?.['domain_level'] ?? 0);
    return Number.isFinite(raw) ? Math.max(0, raw) : 0;
  }

  private _nodeVal(node: GraphNode): number {
    const level = Math.min(this._domainLevel(node), 5);
    const tierBoost = node.kind.endsWith('_file') || node.kind.endsWith('_summary') ? 1.25 : 1;
    return Math.max(0.9, (6.2 - level * 0.95) * tierBoost);
  }

  private _cappedGraph(): { nodes: GraphNode[]; edges: GraphEdge[] } {
    if (!this.graph) return { nodes: [], edges: [] };
    if (this.graph.nodes.length <= RENDER_CAP) {
      this._capAnchorId = null;
      return { nodes: this.graph.nodes, edges: this.graph.edges };
    }

    this._capAnchorId = this.selectedNode?.id ?? null;
    const degree = new Map<string, number>();
    const neighbours = new Map<string, Set<string>>();
    for (const edge of this.graph.edges) {
      degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1);
      degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1);
      if (!neighbours.has(edge.source)) neighbours.set(edge.source, new Set());
      if (!neighbours.has(edge.target)) neighbours.set(edge.target, new Set());
      neighbours.get(edge.source)!.add(edge.target);
      neighbours.get(edge.target)!.add(edge.source);
    }

    const selected = new Map<string, GraphNode>();
    const byId = new Map(this.graph.nodes.map(node => [node.id, node]));
    if (this._capAnchorId) {
      const anchor = byId.get(this._capAnchorId);
      if (anchor) selected.set(anchor.id, anchor);
      for (const neighbourId of neighbours.get(this._capAnchorId) ?? []) {
        const neighbour = byId.get(neighbourId);
        if (neighbour && selected.size < RENDER_CAP) {
          selected.set(neighbour.id, neighbour);
        }
      }
    }

    const rankedNodes = [...this.graph.nodes]
      .sort((a, b) => (degree.get(b.id) ?? 0) - (degree.get(a.id) ?? 0))
    for (const node of rankedNodes) {
      if (selected.size >= RENDER_CAP) break;
      selected.set(node.id, node);
    }

    const nodes = [...selected.values()];
    const kept = new Set(nodes.map(node => node.id));
    const edges = this.graph.edges.filter(edge => kept.has(edge.source) && kept.has(edge.target));
    return { nodes, edges };
  }

  ngOnDestroy(): void {
    this.resizeObserver?.disconnect();
    this.resizeObserver = null;
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
        domain: String(n.metadata?.['domain_path'] ?? ''),
        domainLevel: this._domainLevel(n),
        value: this._nodeVal(n),
        color: this._domainColor(n),
      }));
      const gLinks = edges.map(e => ({
        id: e.id, source: e.source, target: e.target, label: e.edgeType,
      }));

      const el = this.containerRef.nativeElement;
      const w = Math.max(1, el.clientWidth || el.getBoundingClientRect().width || 800);
      const h = Math.max(1, el.clientHeight || el.getBoundingClientRect().height || 500);

      this.fg = new ForceGraph3D(el, { controlType: 'orbit' })
        .width(w).height(h)
        .backgroundColor('#0f172a')
        .nodeLabel((n: any) => `${n['label']}${n['domain'] ? ` · ${n['domain']}` : ''}`)
        .nodeColor((n: any) => this._nodeColor(n['id'] as string))
        .nodeVal((n: any) => n['value'] as number)
        .nodeRelSize(4.2)
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
      this._resizeToContainer();

    } catch (err) {
      this.error = `Failed to load 3D renderer: ${(err as Error).message ?? err}`;
    } finally {
      this.loading = false;
      this.cdr.detectChanges();
    }
  }
}
