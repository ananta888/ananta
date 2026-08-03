import {
  AfterViewInit,
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  ElementRef,
  EventEmitter,
  inject,
  Input,
  OnChanges,
  OnDestroy,
  Output,
  SimpleChanges,
  ViewChild,
} from '@angular/core';

import type { ForceGraph3DInstance } from '3d-force-graph';

import {
  DEFAULT_RENDER_HIGHLIGHT_FACTORS,
  RenderEdgeStyle,
  RenderGraph,
  RenderGraphEdge,
  RenderGraphNode,
  RenderLimitExceeded,
  RenderLimitStrategy,
  RenderNodeStyle,
} from '../models/render-graph.models';
import { FORCE_GRAPH_3D_FACTORY } from '../ports/force-graph-3d-factory.port';

interface ForceGraphNodeData {
  readonly id: string;
  readonly label: string;
  readonly kind: string;
}

interface ForceGraphLinkData {
  readonly id: string;
  readonly source: string;
  readonly target: string;
  readonly label: string;
}

export function renderGraphTooltipElement(text: string): HTMLElement {
  const element = document.createElement('div');
  element.textContent = text;
  return element;
}

@Component({
  selector: 'app-force-graph-3d-renderer',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (limitExceeded; as exceeded) {
      <section class="fallback-msg limit-msg" role="status">
        <strong>3D render limit exceeded.</strong>
        <span>
          {{ exceeded.nodeCount }} nodes / {{ exceeded.edgeCount }} edges;
          allowed {{ exceeded.nodeLimit ?? 'unlimited' }} / {{ exceeded.edgeLimit ?? 'unlimited' }}.
          Filter or focus a subgraph to continue.
        </span>
      </section>
    } @else if (webglUnavailable) {
      <section class="fallback-msg" role="status">
        <strong>WebGL is not available.</strong>
        <span>Use the synchronized list, hierarchy, or 2D graph instead.</span>
      </section>
    } @else if (reducedMotion) {
      <section class="fallback-msg" role="status">
        <strong>Reduced motion is enabled.</strong>
        <span>The moving 3D simulation is replaced by the synchronized static list, hierarchy, or 2D graph.</span>
      </section>
    } @else if (error) {
      <p class="error-msg" role="alert">{{ error }}</p>
    } @else if (!graph || graph.nodes.length === 0) {
      <p class="status-msg">No nodes to display.</p>
    }
    @if (loading && graph && graph.nodes.length > 0) {
      <p class="status-msg overlay-msg" role="status">Loading 3D renderer…</p>
    }
    <div
      #container
      class="fg3d-container"
      aria-hidden="true"
      [style.visibility]="showCanvas ? 'visible' : 'hidden'"
    ></div>
  `,
  styles: [`
    :host { display: flex; flex: 1; width: 100%; height: 100%; min-height: 0; position: relative; overflow: hidden; }
    .fg3d-container { position: absolute; inset: 0; width: 100%; height: 100%; min-height: 0; overflow: hidden; background: #0f172a; }
    .fallback-msg { display: grid; gap: .35rem; padding: 1.5rem; color: #cbd5e1; line-height: 1.5; position: relative; z-index: 2; }
    .limit-msg { color: #fde68a; }
    .error-msg { color: #fecaca; padding: .75rem; position: relative; z-index: 2; }
    .status-msg { color: #94a3b8; padding: .75rem; font-style: italic; position: relative; z-index: 2; }
    .overlay-msg { position: absolute; top: .5rem; left: .5rem; z-index: 3; margin: 0; background: rgba(15, 23, 42, .78); color: #e2e8f0; border-radius: 4px; padding: .35rem .5rem; font-size: .8rem; }
  `],
})
export class ForceGraph3dRendererComponent implements OnChanges, AfterViewInit, OnDestroy {
  @ViewChild('container', { static: true }) containerRef!: ElementRef<HTMLElement>;

  @Input() graph: RenderGraph | null = null;
  @Input() nodeStyles: Readonly<Record<string, Readonly<RenderNodeStyle>>> = {};
  @Input() edgeStyles: Readonly<Record<string, Readonly<RenderEdgeStyle>>> = {};
  @Input() selectedNodeId: string | null = null;
  @Input() selectedEdgeId: string | null = null;
  @Input() visibleNodeIds: ReadonlySet<string> | null = null;
  @Input() visibleEdgeIds: ReadonlySet<string> | null = null;
  @Input() highlightedNodeIds: ReadonlySet<string> = new Set();
  @Input() highlightedEdgeIds: ReadonlySet<string> = new Set();
  @Input() nodeRenderLimit: number | null = null;
  @Input() edgeRenderLimit: number | null = null;
  @Input() limitStrategy: RenderLimitStrategy = 'none';

  @Output() nodeSelected = new EventEmitter<string>();
  @Output() edgeSelected = new EventEmitter<string>();
  @Output() selectionCleared = new EventEmitter<void>();
  @Output() availabilityChange = new EventEmitter<boolean>();

  loading = false;
  error = '';
  webglUnavailable = false;
  reducedMotion = false;
  limitExceeded: RenderLimitExceeded | null = null;

  get showCanvas(): boolean {
    return !this.webglUnavailable
      && !this.reducedMotion
      && !this.error
      && !this.limitExceeded
      && Boolean(this.graph?.nodes.length);
  }

  private readonly factory = inject(FORCE_GRAPH_3D_FACTORY);
  private readonly cdr = inject(ChangeDetectorRef);
  private renderer: ForceGraph3DInstance | null = null;
  private readonly nodeMap = new Map<string, RenderGraphNode>();
  private readonly edgeMap = new Map<string, RenderGraphEdge>();
  private focalNodeId: string | null = null;
  private readonly neighbourIds = new Set<string>();
  private resizeObserver: ResizeObserver | null = null;
  private renderSequence = 0;
  private topologyIdentity = '';
  private destroyed = false;

  ngOnChanges(changes: SimpleChanges): void {
    const graphChanged = Boolean(changes['graph']);
    let topologyChanged = false;
    if (graphChanged) {
      this.rebuildLookups();
      const nextIdentity = this.graphTopologyIdentity();
      topologyChanged = nextIdentity !== this.topologyIdentity;
      this.topologyIdentity = nextIdentity;
    }
    const limitsChanged = Boolean(
      changes['nodeRenderLimit'] || changes['edgeRenderLimit'] || changes['limitStrategy'],
    );
    const selectedNodeChanged = Boolean(changes['selectedNodeId']);
    const focusLimitNeedsRebuild = selectedNodeChanged
      && this.limitStrategy === 'focus'
      && this.exceedsConfiguredLimit();

    if (topologyChanged || limitsChanged || focusLimitNeedsRebuild) {
      void this.render();
      return;
    }

    if (
      graphChanged
      || changes['nodeStyles']
      || changes['edgeStyles']
      || changes['visibleNodeIds']
      || changes['visibleEdgeIds']
      || changes['highlightedNodeIds']
      || changes['highlightedEdgeIds']
      || selectedNodeChanged
      || changes['selectedEdgeId']
    ) {
      this.setHighlightState(this.selectedNodeId);
      this.applyVisualProjection();
    }
  }

  ngAfterViewInit(): void {
    if (typeof ResizeObserver === 'undefined') return;
    this.resizeObserver = new ResizeObserver(() => this.resizeToContainer());
    this.resizeObserver.observe(this.containerRef.nativeElement);
  }

  ngOnDestroy(): void {
    this.destroyed = true;
    this.renderSequence += 1;
    this.resizeObserver?.disconnect();
    this.resizeObserver = null;
    this.destroyRenderer();
  }

  private rebuildLookups(): void {
    this.nodeMap.clear();
    this.edgeMap.clear();
    this.graph?.nodes.forEach(node => this.nodeMap.set(node.id, node));
    this.graph?.edges.forEach(edge => this.edgeMap.set(edge.id, edge));
    this.setHighlightState(this.selectedNodeId);
  }

  private graphTopologyIdentity(): string {
    if (!this.graph) return '';
    const nodeIds = this.graph.nodes.map(node => node.id).sort();
    const edges = this.graph.edges
      .map(edge => `${edge.id}\u0000${edge.sourceId}\u0000${edge.targetId}`)
      .sort();
    return JSON.stringify([nodeIds, edges]);
  }

  private resizeToContainer(): void {
    if (!this.renderer) return;
    const element = this.containerRef.nativeElement;
    const width = Math.max(1, element.clientWidth || element.getBoundingClientRect().width || 800);
    const height = Math.max(1, element.clientHeight || element.getBoundingClientRect().height || 500);
    this.renderer.width(width).height(height);
  }

  private setHighlightState(nodeId: string | null): void {
    this.focalNodeId = nodeId && this.nodeMap.has(nodeId) ? nodeId : null;
    this.neighbourIds.clear();
    if (!this.focalNodeId || !this.graph) return;
    for (const edge of this.graph.edges) {
      if (edge.sourceId === this.focalNodeId) this.neighbourIds.add(edge.targetId);
      if (edge.targetId === this.focalNodeId) this.neighbourIds.add(edge.sourceId);
    }
  }

  private nodeStyle(id: string): Readonly<RenderNodeStyle> {
    return this.nodeStyles[id] ?? { color: '#64748b', size: 5 };
  }

  private edgeStyle(id: string): Readonly<RenderEdgeStyle> {
    return this.edgeStyles[id] ?? { color: '#94a3b8', width: 1 };
  }

  private nodeColor(id: string): string {
    const base = this.nodeStyle(id).color;
    if (this.highlightedNodeIds.size) {
      return this.highlightedNodeIds.has(id) ? base : 'rgba(100,116,139,0.2)';
    }
    if (!this.focalNodeId) return base;
    if (id === this.focalNodeId) return '#f59e0b';
    if (this.neighbourIds.has(id)) return '#38bdf8';
    return 'rgba(100,116,139,0.25)';
  }

  private nodeValue(id: string): number {
    const style = this.nodeStyle(id);
    const factors = style.highlightFactors ?? DEFAULT_RENDER_HIGHLIGHT_FACTORS;
    if (this.highlightedNodeIds.has(id)) return style.size * factors.hover;
    if (id === this.focalNodeId) return style.size * factors.selected;
    if (this.neighbourIds.has(id)) return style.size * factors.connected;
    return style.size;
  }

  private edgeWidth(link: Record<string, unknown>): number {
    const id = String(link['id'] ?? '');
    const style = this.edgeStyle(id);
    const factors = style.highlightFactors ?? DEFAULT_RENDER_HIGHLIGHT_FACTORS;
    if (this.highlightedEdgeIds.has(id)) return style.width * factors.hover;
    if (id === this.selectedEdgeId) return style.width * factors.selected;
    if (!this.focalNodeId) return style.width;
    const { source, target } = this.linkEndpoints(link);
    return source === this.focalNodeId || target === this.focalNodeId
      ? style.width * factors.connected
      : style.width;
  }

  private edgeColor(link: Record<string, unknown>): string {
    const id = String(link['id'] ?? '');
    const base = this.edgeStyle(id).color;
    if (id === this.selectedEdgeId) return '#f59e0b';
    if (this.highlightedEdgeIds.size) {
      return this.highlightedEdgeIds.has(id) ? base : 'rgba(148,163,184,0.12)';
    }
    if (!this.focalNodeId) return base;
    const { source, target } = this.linkEndpoints(link);
    return source === this.focalNodeId || target === this.focalNodeId
      ? base
      : 'rgba(148,163,184,0.12)';
  }

  private linkEndpoints(link: Record<string, unknown>): { source: unknown; target: unknown } {
    const endpoint = (value: unknown) => (
      typeof value === 'object' && value !== null
        ? (value as { id?: unknown }).id
        : value
    );
    return { source: endpoint(link['source']), target: endpoint(link['target']) };
  }

  private applyVisualProjection(): void {
    if (!this.renderer) return;
    this.renderer
      .nodeColor((node: any) => this.nodeColor(String(node['id'])))
      .nodeVal((node: any) => this.nodeValue(String(node['id'])))
      .nodeVisibility((node: any) => !this.visibleNodeIds || this.visibleNodeIds.has(String(node['id'])))
      .nodeOpacity(this.focalNodeId ? 0.9 : 0.78)
      .nodeLabel((node: any) => {
        const id = String(node['id']);
        return renderGraphTooltipElement(this.nodeMap.get(id)?.tooltip ?? id);
      })
      .linkColor((link: any) => this.edgeColor(link))
      .linkWidth((link: any) => this.edgeWidth(link))
      .linkVisibility((link: any) => !this.visibleEdgeIds || this.visibleEdgeIds.has(String(link['id'])))
      .linkLabel((link: any) => {
        const id = String(link['id']);
        return renderGraphTooltipElement(this.edgeMap.get(id)?.tooltip ?? id);
      });
    this.renderer.refresh();
  }

  private normalisedLimit(value: number | null): number | null {
    if (value === null || value === undefined || !Number.isFinite(value) || value <= 0) return null;
    return Math.floor(value);
  }

  private exceedsConfiguredLimit(): boolean {
    if (!this.graph) return false;
    const nodeLimit = this.normalisedLimit(this.nodeRenderLimit);
    const edgeLimit = this.normalisedLimit(this.edgeRenderLimit);
    return Boolean(
      (nodeLimit && this.graph.nodes.length > nodeLimit)
      || (edgeLimit && this.graph.edges.length > edgeLimit),
    );
  }

  private renderGraph(): RenderGraph | null {
    this.limitExceeded = null;
    if (!this.graph) return null;
    const nodeLimit = this.normalisedLimit(this.nodeRenderLimit);
    const edgeLimit = this.normalisedLimit(this.edgeRenderLimit);
    if (!this.exceedsConfiguredLimit() || this.limitStrategy === 'none') return this.graph;

    if (this.limitStrategy === 'reject') {
      this.limitExceeded = {
        nodeCount: this.graph.nodes.length,
        edgeCount: this.graph.edges.length,
        nodeLimit,
        edgeLimit,
      };
      return null;
    }

    const degree = new Map<string, number>();
    const neighbours = new Map<string, Set<string>>();
    for (const edge of this.graph.edges) {
      degree.set(edge.sourceId, (degree.get(edge.sourceId) ?? 0) + 1);
      degree.set(edge.targetId, (degree.get(edge.targetId) ?? 0) + 1);
      if (!neighbours.has(edge.sourceId)) neighbours.set(edge.sourceId, new Set());
      if (!neighbours.has(edge.targetId)) neighbours.set(edge.targetId, new Set());
      neighbours.get(edge.sourceId)!.add(edge.targetId);
      neighbours.get(edge.targetId)!.add(edge.sourceId);
    }

    let nodes = [...this.graph.nodes];
    if (nodeLimit && nodes.length > nodeLimit) {
      const selected = new Map<string, RenderGraphNode>();
      if (this.selectedNodeId) {
        const anchor = this.nodeMap.get(this.selectedNodeId);
        if (anchor) selected.set(anchor.id, anchor);
        for (const neighbourId of neighbours.get(this.selectedNodeId) ?? []) {
          const neighbour = this.nodeMap.get(neighbourId);
          if (neighbour && selected.size < nodeLimit) selected.set(neighbour.id, neighbour);
        }
      }
      for (const node of [...nodes].sort((left, right) => (
        (degree.get(right.id) ?? 0) - (degree.get(left.id) ?? 0)
        || left.id.localeCompare(right.id)
      ))) {
        if (selected.size >= nodeLimit) break;
        selected.set(node.id, node);
      }
      nodes = [...selected.values()];
    }

    const kept = new Set(nodes.map(node => node.id));
    let edges = this.graph.edges.filter(edge => kept.has(edge.sourceId) && kept.has(edge.targetId));
    if (edgeLimit && edges.length > edgeLimit) {
      edges = [...edges].sort((left, right) => {
        const leftSelected = this.selectedNodeId
          && (left.sourceId === this.selectedNodeId || left.targetId === this.selectedNodeId) ? 1 : 0;
        const rightSelected = this.selectedNodeId
          && (right.sourceId === this.selectedNodeId || right.targetId === this.selectedNodeId) ? 1 : 0;
        return rightSelected - leftSelected || left.id.localeCompare(right.id);
      }).slice(0, edgeLimit);
    }
    return { nodes, edges };
  }

  private destroyRenderer(): void {
    this.renderer?._destructor();
    this.renderer = null;
  }

  private async render(): Promise<void> {
    const sequence = ++this.renderSequence;
    this.destroyRenderer();
    this.error = '';
    this.webglUnavailable = false;
    this.reducedMotion = this.prefersReducedMotion();
    this.loading = false;

    const graph = this.renderGraph();
    if (!graph || graph.nodes.length === 0) {
      this.markForCheck();
      return;
    }

    if (this.reducedMotion) {
      this.availabilityChange.emit(false);
      this.markForCheck();
      return;
    }

    if (!this.factory.webglAvailable()) {
      this.webglUnavailable = true;
      this.availabilityChange.emit(false);
      this.markForCheck();
      return;
    }

    this.loading = true;
    this.markForCheck();
    try {
      const renderer = await this.factory.create(this.containerRef.nativeElement);
      if (this.destroyed || sequence !== this.renderSequence) {
        renderer._destructor();
        return;
      }
      this.renderer = renderer;
      const element = this.containerRef.nativeElement;
      const width = Math.max(1, element.clientWidth || element.getBoundingClientRect().width || 800);
      const height = Math.max(1, element.clientHeight || element.getBoundingClientRect().height || 500);
      const nodes: ForceGraphNodeData[] = graph.nodes.map(node => ({
        id: node.id,
        label: node.label,
        kind: node.kind,
      }));
      const links: ForceGraphLinkData[] = graph.edges.map(edge => ({
        id: edge.id,
        source: edge.sourceId,
        target: edge.targetId,
        label: edge.label,
      }));

      renderer
        .width(width)
        .height(height)
        .backgroundColor('#0f172a')
        .nodeRelSize(4.2)
        .linkOpacity(0.85)
        .warmupTicks(60)
        .cooldownTime(6_000)
        .d3AlphaDecay(0.05)
        .d3VelocityDecay(0.4)
        .onNodeClick((node: any) => {
          const id = String(node['id']);
          if (this.nodeMap.has(id)) this.nodeSelected.emit(id);
        })
        .onLinkClick((link: any) => {
          const id = String(link['id']);
          if (this.edgeMap.has(id)) this.edgeSelected.emit(id);
        })
        .onBackgroundClick(() => {
          this.selectionCleared.emit();
        })
        .graphData({ nodes, links });

      (renderer.d3Force('charge') as any)?.strength(-20);
      (renderer.d3Force('link') as any)?.distance(25);
      this.applyVisualProjection();
      this.resizeToContainer();
      this.availabilityChange.emit(true);
    } catch (error) {
      if (sequence !== this.renderSequence || this.destroyed) return;
      this.error = `Failed to load 3D renderer: ${error instanceof Error ? error.message : String(error)}`;
      this.availabilityChange.emit(false);
    } finally {
      if (sequence === this.renderSequence && !this.destroyed) {
        this.loading = false;
        this.markForCheck();
      }
    }
  }

  private prefersReducedMotion(): boolean {
    return typeof globalThis.matchMedia === 'function'
      && globalThis.matchMedia('(prefers-reduced-motion: reduce)').matches;
  }

  private markForCheck(): void {
    if (!this.destroyed) this.cdr.detectChanges();
  }
}
