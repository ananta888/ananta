import {
  Component, Input, Output, EventEmitter,
  ElementRef, ViewChild, OnChanges, SimpleChanges, AfterViewInit, OnDestroy,
  ChangeDetectionStrategy, ChangeDetectorRef, inject,
} from '@angular/core';

import type { ForceGraph3DInstance } from '3d-force-graph';
import { GenericGraphModel, GraphEdge, GraphNode } from '../../models/graph.model';
import { EdgeVisualStyle, GraphVisualProjection, NodeVisualStyle } from '../../models/graph-visual-metrics.model';
import { graphVisualTooltipElement, graphVisualTooltipText } from '../graph-tooltip/graph-visual-tooltip';

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
  imports: [],
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
  @Input() visualProjection: GraphVisualProjection | null = null;
  @Input() visibleNodeIds: ReadonlySet<string> | null = null;
  @Input() visibleEdgeIds: ReadonlySet<string> | null = null;
  @Input() highlightedNodeIds: ReadonlySet<string> = new Set();
  @Input() highlightedEdgeIds: ReadonlySet<string> = new Set();
  @Input() nodeRenderLimit: number | null = null;
  @Input() edgeRenderLimit: number | null = null;

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
  private resizeObserver: ResizeObserver | null = null;

  ngOnChanges(changes: SimpleChanges): void {
    const gc = changes['graph'];
    const limitChanged = !!changes['nodeRenderLimit'] || !!changes['edgeRenderLimit'];
    const projectionChanged = !!changes['visualProjection'];
    const visibilityChanged = !!changes['visibleNodeIds'] || !!changes['visibleEdgeIds'];

    // Only selection changed: update highlight without rebuilding the WebGL scene.
    if (!gc && !limitChanged) {
      if (projectionChanged || visibilityChanged) {
        this._setHighlightState(this.selectedNode?.id ?? null);
        this._applyVisualProjection();
      } else {
        this._updateHighlight(this.selectedNode?.id ?? null);
      }
      return;
    }
    const prev = gc?.previousValue as GenericGraphModel | null;
    const curr = gc?.currentValue as GenericGraphModel | null;
    if (!limitChanged && prev && curr && prev.nodes === curr.nodes && prev.edges === curr.edges) {
      if (projectionChanged) this._applyVisualProjection();
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
    this._setHighlightState(nodeId);
    if (!this.fg) return;
    this.fg
      .nodeColor((n: any) => this._nodeColor(n['id'] as string))
      .nodeVal((n: any) => this._nodeValue(n['id'] as string))
      .nodeOpacity(nodeId ? 0.9 : 0.75)
      .linkColor((link: any) => this._linkColor(link))
      .linkWidth((link: any) => this._linkWidth(link));
    this.fg.refresh();
  }

  private _setHighlightState(nodeId: string | null): void {
    this._focalId = nodeId;
    this._neighbourIds.clear();
    if (nodeId && this.graph) {
      for (const e of this.graph.edges) {
        if (e.source === nodeId) this._neighbourIds.add(e.target);
        if (e.target === nodeId) this._neighbourIds.add(e.source);
      }
    }
  }

  private _nodeColor(id: string): string {
    const base = this._nodeVisual(id).baseColor;
    if (this.highlightedNodeIds.size) {
      return this.highlightedNodeIds.has(id) ? base : 'rgba(100,116,139,0.2)';
    }
    if (!this._focalId) return base;
    if (id === this._focalId) return '#f59e0b';
    if (this._neighbourIds.has(id)) return '#38bdf8';
    return 'rgba(100,116,139,0.25)';
  }

  private _nodeValue(id: string): number {
    const style = this._nodeVisual(id);
    if (this.highlightedNodeIds.has(id)) return style.baseSize * style.highlightFactors.hover;
    if (id === this._focalId) return style.baseSize * style.highlightFactors.selected;
    if (this._neighbourIds.has(id)) return style.baseSize * style.highlightFactors.connected;
    return style.baseSize;
  }

  private _nodeVisual(id: string): Readonly<NodeVisualStyle> {
    return this.visualProjection?.nodeStyles[id] ?? {
      nodeId: id, baseColor: '#64748b', marker: 'circle', baseSize: 5,
      score: 0, scoreState: 'degraded_no_active_metric', availability: 'unavailable', breakdown: [],
      highlightFactors: { hover: 1.2, selected: 1.5, connected: 1.1 },
    };
  }

  private _nodeVisible(id: string): boolean {
    return !this.visibleNodeIds || this.visibleNodeIds.has(id);
  }

  private _edgeVisible(id: string): boolean {
    return !this.visibleEdgeIds || this.visibleEdgeIds.has(id);
  }

  private _edgeVisual(id: string): Readonly<EdgeVisualStyle> {
    return this.visualProjection?.edgeStyles[id] ?? {
      edgeId: id, baseColor: '#94a3b8', marker: 'triangle', baseThickness: 1,
      score: 0, scoreState: 'degraded_no_active_metric', availability: 'unavailable', breakdown: [],
      highlightFactors: { hover: 1.2, selected: 1.5, connected: 1.1 },
    };
  }

  private _linkWidth(link: Record<string, unknown>): number {
    const style = this._edgeVisual(String(link['id'] ?? ''));
    if (this.highlightedEdgeIds.has(String(link['id'] ?? ''))) {
      return style.baseThickness * style.highlightFactors.hover;
    }
    if (!this._focalId) return style.baseThickness;
    const source = typeof link['source'] === 'object' ? (link['source'] as { id?: string })?.id : link['source'];
    const target = typeof link['target'] === 'object' ? (link['target'] as { id?: string })?.id : link['target'];
    return source === this._focalId || target === this._focalId
      ? style.baseThickness * style.highlightFactors.selected
      : style.baseThickness;
  }

  private _linkColor(link: Record<string, unknown>): string {
    const baseColor = this._edgeVisual(String(link['id'] ?? '')).baseColor;
    if (this.highlightedEdgeIds.size) {
      return this.highlightedEdgeIds.has(String(link['id'] ?? ''))
        ? baseColor
        : 'rgba(148,163,184,0.12)';
    }
    if (!this._focalId) return baseColor;
    const source = typeof link['source'] === 'object' ? (link['source'] as { id?: string })?.id : link['source'];
    const target = typeof link['target'] === 'object' ? (link['target'] as { id?: string })?.id : link['target'];
    return source === this._focalId || target === this._focalId
      ? baseColor
      : 'rgba(148,163,184,0.12)';
  }

  private _applyVisualProjection(): void {
    if (!this.fg) return;
    this.fg
      .nodeColor((node: any) => this._nodeColor(String(node['id'])))
      .nodeVal((node: any) => this._nodeValue(String(node['id'])))
      .nodeVisibility((node: any) => this._nodeVisible(String(node['id'])))
      .nodeOpacity(this._focalId ? 0.9 : 0.75)
      .nodeLabel((node: any) => {
        const id = String(node['id']);
        return graphVisualTooltipElement(graphVisualTooltipText(this.nodeMap.get(id)?.label ?? id, this._nodeVisual(id)));
      })
      .linkColor((link: any) => this._linkColor(link))
      .linkWidth((link: any) => this._linkWidth(link))
      .linkVisibility((link: any) => this._edgeVisible(String(link['id'])))
      .linkLabel((link: any) => {
        const id = String(link['id']);
        const edge = this.edgeMap.get(id);
        return graphVisualTooltipElement(graphVisualTooltipText(edge?.rawEdgeType ?? edge?.edgeType ?? id, this._edgeVisual(id)));
      });
    this.fg.refresh();
  }

  private _normalisedLimit(value: number | null): number | null {
    if (value === null || value === undefined) return null;
    if (!Number.isFinite(value) || value <= 0) return null;
    return Math.floor(value);
  }

  private _limitedGraph(): { nodes: GraphNode[]; edges: GraphEdge[] } {
    if (!this.graph) return { nodes: [], edges: [] };
    const nodeLimit = this._normalisedLimit(this.nodeRenderLimit);
    const edgeLimit = this._normalisedLimit(this.edgeRenderLimit);
    if (
      (!nodeLimit || this.graph.nodes.length <= nodeLimit) &&
      (!edgeLimit || this.graph.edges.length <= edgeLimit)
    ) {
      return { nodes: this.graph.nodes, edges: this.graph.edges };
    }

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

    let nodes = this.graph.nodes;
    const byId = new Map(this.graph.nodes.map(node => [node.id, node]));

    if (nodeLimit && this.graph.nodes.length > nodeLimit) {
      const selected = new Map<string, GraphNode>();
      const anchorId = this.selectedNode?.id ?? null;
      if (anchorId) {
        const anchor = byId.get(anchorId);
        if (anchor) selected.set(anchor.id, anchor);
        for (const neighbourId of neighbours.get(anchorId) ?? []) {
          const neighbour = byId.get(neighbourId);
          if (neighbour && selected.size < nodeLimit) {
            selected.set(neighbour.id, neighbour);
          }
        }
      }

      const rankedNodes = [...this.graph.nodes]
        .sort((a, b) => (degree.get(b.id) ?? 0) - (degree.get(a.id) ?? 0));
      for (const node of rankedNodes) {
        if (selected.size >= nodeLimit) break;
        selected.set(node.id, node);
      }
      nodes = [...selected.values()];
    }

    const kept = new Set(nodes.map(node => node.id));
    let edges = this.graph.edges.filter(edge => kept.has(edge.source) && kept.has(edge.target));
    if (edgeLimit && edges.length > edgeLimit) {
      const focalId = this.selectedNode?.id ?? null;
      edges = [...edges]
        .sort((a, b) => {
          const aFocal = focalId && (a.source === focalId || a.target === focalId) ? 1 : 0;
          const bFocal = focalId && (b.source === focalId || b.target === focalId) ? 1 : 0;
          return bFocal - aFocal;
        })
        .slice(0, edgeLimit);
    }
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

      const { nodes, edges } = this._limitedGraph();

      const gNodes = nodes.map(n => ({
        id: n.id, label: n.label, kind: n.kind,
        value: this._nodeVisual(n.id).baseSize,
        color: this._nodeVisual(n.id).baseColor,
      }));
      const gLinks = edges.map(e => ({
        id: e.id, source: e.source, target: e.target, label: e.rawEdgeType ?? e.edgeType,
        color: this._edgeVisual(e.id).baseColor,
      }));

      const el = this.containerRef.nativeElement;
      const w = Math.max(1, el.clientWidth || el.getBoundingClientRect().width || 800);
      const h = Math.max(1, el.clientHeight || el.getBoundingClientRect().height || 500);

      this.fg = new ForceGraph3D(el, { controlType: 'orbit' })
        .width(w).height(h)
        .backgroundColor('#0f172a')
        .nodeLabel((n: any) => {
          const id = String(n['id']);
          return graphVisualTooltipElement(graphVisualTooltipText(this.nodeMap.get(id)?.label ?? id, this._nodeVisual(id)));
        })
        .nodeColor((n: any) => this._nodeColor(n['id'] as string))
        .nodeVal((n: any) => n['value'] as number)
        .nodeVisibility((node: any) => this._nodeVisible(String(node['id'])))
        .nodeRelSize(4.2)
        .linkLabel((l: any) => {
          const id = String(l['id']);
          return graphVisualTooltipElement(graphVisualTooltipText(String(l['label'] ?? id), this._edgeVisual(id)));
        })
        .linkColor((l: any) => l['color'] as string ?? '#94a3b8')
        .linkWidth((link: any) => this._linkWidth(link))
        .linkVisibility((link: any) => this._edgeVisible(String(link['id'])))
        .linkOpacity(0.85)
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
