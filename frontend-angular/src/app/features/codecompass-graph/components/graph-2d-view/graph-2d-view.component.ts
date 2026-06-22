import {
  Component, Input, Output, EventEmitter,
  ElementRef, ViewChild, OnChanges, SimpleChanges, OnDestroy,
  ChangeDetectionStrategy, ChangeDetectorRef, inject,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import type { Core, NodeSingular, EdgeSingular } from 'cytoscape';
import { GenericGraphModel, GraphEdge, GraphNode } from '../../models/graph.model';
import { GraphLayoutMode } from '../../models/graph-layout-mode';
import { graphEdgeColor } from '../../models/graph-edge-style';

const KIND_COLORS: Record<string, string> = {
  java_type: '#3b82f6', java_method: '#10b981', java_file: '#1d4ed8',
  python_file: '#f59e0b', python_class: '#d97706', python_function: '#92400e', python_method: '#78350f',
  typescript_file: '#0284c7', typescript_class: '#0369a1', typescript_function: '#075985',
  python_module_summary: '#7c3aed', typescript_folder_summary: '#0891b2', java_module_summary: '#1e40af',
  config: '#a16207', xml_tag: '#8b5cf6', md_file: '#6b7280', unknown: '#94a3b8',
};

const DOMAIN_COLORS = [
  '#2563eb', '#16a34a', '#dc2626', '#9333ea', '#0891b2',
  '#ca8a04', '#db2777', '#4f46e5', '#059669', '#ea580c',
];

const KIND_TIER: Record<string, number> = {
  python_module_summary: 0, typescript_folder_summary: 0, java_module_summary: 0,
  python_file: 1, typescript_file: 1, java_file: 1, md_file: 1, yaml_file: 1, xml_file: 1,
  python_class: 2, typescript_class: 2, typescript_interface: 2, typescript_enum: 2, java_type: 2,
  python_function: 3, python_method: 3, typescript_function: 3, typescript_method: 3,
  typescript_const: 3, typescript_constructor: 3, java_method: 3, java_constructor: 3,
};

const CHUNK = 300;
const GAP_X = 110;
const GAP_Y = 160;

function yieldFrame(): Promise<void> {
  return new Promise(r => setTimeout(r, 0));
}

@Component({
  standalone: true,
  selector: 'app-graph-2d-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  template: `
    @if (renderWarning) {
      <div class="render-warn">{{ renderWarning }}</div>
    }
    @if (loading) {
      <div class="cy-progress-wrap">
        <div class="cy-phase">{{ phase }}</div>
        <div class="cy-bar-track">
          <div class="cy-bar-fill" [style.width.%]="progress"></div>
        </div>
        <div class="cy-pct">{{ progress }}%</div>
      </div>
    }
    @if (error) {
      <p class="status-msg error-msg">{{ error }}</p>
    }
    <div #cyContainer class="cy-container" [style.visibility]="showGraph ? 'visible' : 'hidden'"></div>
  `,
  styles: [`
    :host { display: flex; flex-direction: column; flex: 1; width: 100%; height: 100%; min-height: 0; position: relative; }
    .cy-container { flex: 1; width: 100%; height: 100%; min-height: 0; overflow: hidden; }
    .status-msg { color: #888; padding: .75rem; font-style: italic; margin: 0; flex-shrink: 0; }
    .error-msg { color: #c00; }
    .render-warn {
      flex-shrink: 0; padding: 4px 10px; font-size: 11px; font-weight: 600;
      background: #fef9c3; border-bottom: 1px solid #fde68a; color: #92400e;
    }
    .cy-progress-wrap {
      position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
      display: flex; flex-direction: column; align-items: center; gap: 8px;
      background: var(--card-bg, #fff); border: 1px solid #e2e8f0; border-radius: 10px;
      padding: 20px 32px; z-index: 10; min-width: 260px; box-shadow: 0 4px 16px rgba(0,0,0,.1);
    }
    .cy-phase { font-size: 12px; color: #555; font-weight: 500; }
    .cy-bar-track { width: 220px; height: 8px; background: #e2e8f0; border-radius: 4px; overflow: hidden; }
    .cy-bar-fill { height: 100%; background: #3b82f6; border-radius: 4px; transition: width 80ms linear; }
    .cy-pct { font-size: 11px; color: #888; }
  `],
})
export class Graph2dViewComponent implements OnChanges, OnDestroy {
  @ViewChild('cyContainer', { static: true }) cyContainer!: ElementRef<HTMLElement>;

  @Input() graph: GenericGraphModel | null = null;
  @Input() layoutMode: GraphLayoutMode = 'tier';
  @Input() selectedNode: GraphNode | null = null;
  @Input() selectedEdge: GraphEdge | null = null;
  @Input() nodeRenderLimit: number | null = null;
  @Input() edgeRenderLimit: number | null = null;

  @Output() nodeSelected = new EventEmitter<GraphNode>();
  @Output() edgeSelected = new EventEmitter<GraphEdge>();

  loading = false;
  error = '';
  renderWarning = '';
  showGraph = false;
  phase = '';
  progress = 0;

  private cy: Core | null = null;
  private nodeMap = new Map<string, GraphNode>();
  private edgeMap = new Map<string, GraphEdge>();
  private cyNodeIdByOriginal = new Map<string, string>();
  private _cancelled = false;
  private readonly cdr = inject(ChangeDetectorRef);

  ngOnChanges(changes: SimpleChanges): void {
    const gc = changes['graph'];
    const lc = changes['layoutMode'];
    const limitChanged = !!changes['nodeRenderLimit'] || !!changes['edgeRenderLimit'];
    // If only selectedNode/selectedEdge changed, just update highlight — don't re-render
    if (!gc) {
      if (lc || limitChanged) {
        // Layout mode changed; rebuild positions with the current graph.
      } else {
        if (this.cy) {
          if (this.selectedNode) this._applyHighlight(this.selectedNode.id);
          else this._clearHighlight();
        }
        return;
      }
    }

    // If the graph wrapper changed but the actual nodes array is the same reference, skip re-render
    const prev = gc?.previousValue as GenericGraphModel | null;
    const curr = gc?.currentValue as GenericGraphModel | null;
    if (!lc && !limitChanged && prev && curr && prev.nodes === curr.nodes && prev.edges === curr.edges) {
      if (this.cy) {
        if (this.selectedNode) this._applyHighlight(this.selectedNode.id);
        else this._clearHighlight();
      }
      return;
    }

    this._cancelled = true;
    this.cy?.destroy();
    this.cy = null;
    this.showGraph = false;
    this.error = '';
    this.renderWarning = '';
    this.progress = 0;

    this.nodeMap.clear();
    this.edgeMap.clear();
    this.cyNodeIdByOriginal.clear();
    this.graph?.nodes.forEach(n => this.nodeMap.set(n.id, n));
    this.graph?.edges.forEach(e => this.edgeMap.set(e.id, e));

    if (!this.graph || this.graph.nodes.length === 0) {
      this.loading = false;
      this.cdr.detectChanges();
      return;
    }

    this.loading = true;
    this._cancelled = false;
    this.cdr.detectChanges();

    setTimeout(() => this._render(), 16);
  }

  private _applyHighlight(nodeId: string): void {
    if (!this.cy) return;
    this._clearHighlight();
    const cyNodeId = this.cyNodeIdByOriginal.get(nodeId);
    if (!cyNodeId) return;
    const focal = this.cy.getElementById(cyNodeId);
    if (!focal.length) return;
    const connectedEdges = focal.connectedEdges();
    const neighbours = connectedEdges.connectedNodes().not(focal);
    this.cy.elements().addClass('dimmed');
    focal.removeClass('dimmed').addClass('focal');
    neighbours.removeClass('dimmed').addClass('neighbour');
    connectedEdges.removeClass('dimmed').addClass('active-edge');
  }

  private _clearHighlight(): void {
    this.cy?.elements().removeClass('focal neighbour dimmed active-edge');
  }

  private _domainKey(node: GraphNode): string {
    const explicit = String(node.metadata?.['domain_path'] ?? '');
    if (explicit) return explicit;
    const file = node.file || node.label || 'unknown';
    const parts = file.replace(/\\/g, '/').split('/').filter(Boolean);
    return parts.slice(0, Math.min(parts.length - 1, 3)).join('/') || 'unknown';
  }

  private _domainLevel(node: GraphNode): number {
    const raw = Number(node.metadata?.['domain_level'] ?? 0);
    return Number.isFinite(raw) ? Math.max(0, raw) : 0;
  }

  private _hash(value: string): number {
    let hash = 0;
    for (let i = 0; i < value.length; i++) {
      hash = ((hash << 5) - hash + value.charCodeAt(i)) | 0;
    }
    return Math.abs(hash);
  }

  private _nodeColor(node: GraphNode): string {
    if (this.layoutMode !== 'domain') {
      return KIND_COLORS[node.kind] ?? KIND_COLORS['unknown'];
    }
    const key = this._domainKey(node);
    return DOMAIN_COLORS[this._hash(key) % DOMAIN_COLORS.length];
  }

  private _degreeMap(edges: GraphEdge[]): Map<string, number> {
    const degree = new Map<string, number>();
    for (const e of edges) {
      degree.set(e.source, (degree.get(e.source) ?? 0) + 1);
      degree.set(e.target, (degree.get(e.target) ?? 0) + 1);
    }
    return degree;
  }

  private async _positionsByTier(nodes: GraphNode[]): Promise<Map<string, { x: number; y: number }>> {
    const byTier = new Map<number, GraphNode[]>();
    for (const n of nodes) {
      const t = KIND_TIER[n.kind] ?? 4;
      if (!byTier.has(t)) byTier.set(t, []);
      byTier.get(t)!.push(n);
    }

    const positions = new Map<string, { x: number; y: number }>();
    const tiers = [...byTier.keys()].sort((a, b) => a - b);
    let yOffset = 0;
    for (const tier of tiers) {
      if (this._cancelled) return positions;
      const tierNodes = byTier.get(tier)!;
      const cols = Math.min(Math.ceil(Math.sqrt(tierNodes.length * 3)), 40);
      for (let i = 0; i < tierNodes.length; i += CHUNK) {
        if (this._cancelled) return positions;
        const batch = tierNodes.slice(i, i + CHUNK);
        for (let j = 0; j < batch.length; j++) {
          const idx = i + j;
          positions.set(batch[j].id, {
            x: (idx % cols) * GAP_X,
            y: yOffset + Math.floor(idx / cols) * GAP_Y,
          });
        }
        this.progress = Math.round((positions.size / nodes.length) * 55);
        this.cdr.detectChanges();
        await yieldFrame();
      }
      yOffset += (Math.ceil(tierNodes.length / cols) + 1) * GAP_Y;
    }
    return positions;
  }

  private async _positionsByDomain(nodes: GraphNode[]): Promise<Map<string, { x: number; y: number }>> {
    const groups = new Map<string, GraphNode[]>();
    for (const node of nodes) {
      const key = this._domainKey(node);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(node);
    }

    const positions = new Map<string, { x: number; y: number }>();
    const orderedGroups = [...groups.entries()].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]));
    const groupCols = Math.max(1, Math.ceil(Math.sqrt(orderedGroups.length)));
    const blockWidth = GAP_X * 8;
    const blockHeight = GAP_Y * 4;
    for (let groupIndex = 0; groupIndex < orderedGroups.length; groupIndex++) {
      if (this._cancelled) return positions;
      const [, groupNodes] = orderedGroups[groupIndex];
      groupNodes.sort((a, b) => (KIND_TIER[a.kind] ?? 4) - (KIND_TIER[b.kind] ?? 4) || a.label.localeCompare(b.label));
      const baseX = (groupIndex % groupCols) * blockWidth;
      const baseY = Math.floor(groupIndex / groupCols) * blockHeight;
      const cols = Math.min(Math.ceil(Math.sqrt(groupNodes.length * 2)), 8);
      for (let i = 0; i < groupNodes.length; i++) {
        positions.set(groupNodes[i].id, {
          x: baseX + (i % cols) * GAP_X,
          y: baseY + Math.floor(i / cols) * (GAP_Y * 0.75),
        });
      }
      this.progress = Math.round((positions.size / nodes.length) * 55);
      this.cdr.detectChanges();
      await yieldFrame();
    }
    return positions;
  }

  private async _positionsRadial(nodes: GraphNode[], edges: GraphEdge[]): Promise<Map<string, { x: number; y: number }>> {
    const degree = this._degreeMap(edges);
    const byTier = new Map<number, GraphNode[]>();
    for (const node of [...nodes].sort((a, b) => (degree.get(b.id) ?? 0) - (degree.get(a.id) ?? 0))) {
      const tier = KIND_TIER[node.kind] ?? 4;
      if (!byTier.has(tier)) byTier.set(tier, []);
      byTier.get(tier)!.push(node);
    }

    const positions = new Map<string, { x: number; y: number }>();
    const tiers = [...byTier.keys()].sort((a, b) => a - b);
    for (const tier of tiers) {
      if (this._cancelled) return positions;
      const ring = byTier.get(tier)!;
      const radius = 120 + tier * 190 + Math.max(0, ring.length - 12) * 2;
      for (let i = 0; i < ring.length; i++) {
        const angle = (Math.PI * 2 * i) / Math.max(1, ring.length);
        positions.set(ring[i].id, { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius });
      }
      this.progress = Math.round((positions.size / nodes.length) * 55);
      this.cdr.detectChanges();
      await yieldFrame();
    }
    return positions;
  }

  private _nodeSize(node: GraphNode, defaultSize: number): number {
    const domainLevel = this._domainLevel(node);
    const scale = Math.max(0.45, 1 - Math.min(domainLevel, 5) * 0.16);
    return Math.round(defaultSize * scale);
  }

  private _normalisedLimit(value: number | null): number | null {
    if (value === null || value === undefined) return null;
    if (!Number.isFinite(value) || value <= 0) return null;
    return Math.floor(value);
  }

  private _limitedGraph(nodes: GraphNode[], edges: GraphEdge[]): { nodes: GraphNode[]; edges: GraphEdge[] } {
    const nodeLimit = this._normalisedLimit(this.nodeRenderLimit);
    const edgeLimit = this._normalisedLimit(this.edgeRenderLimit);
    this.renderWarning = '';

    if (nodeLimit && nodes.length > nodeLimit) {
      const deg = this._degreeMap(edges);
      nodes = [...nodes]
        .sort((a, b) =>
          (KIND_TIER[a.kind] ?? 4) - (KIND_TIER[b.kind] ?? 4) ||
          (deg.get(b.id) ?? 0) - (deg.get(a.id) ?? 0)
        )
        .slice(0, nodeLimit);
      const kept = new Set(nodes.map(n => n.id));
      edges = edges.filter(e => kept.has(e.source) && kept.has(e.target));
      this.renderWarning = `2D-Ansicht zeigt ${nodes.length} von ${this.graph?.nodes.length ?? nodes.length} Nodes`;
    }

    if (edgeLimit && edges.length > edgeLimit) {
      edges = edges.slice(0, edgeLimit);
      const prefix = this.renderWarning ? `${this.renderWarning}; ` : '';
      this.renderWarning = `${prefix}${edges.length} von ${this.graph?.edges.length ?? edges.length} Edges`;
    }

    return { nodes, edges };
  }

  ngOnDestroy(): void {
    this._cancelled = true;
    this.cy?.destroy();
    this.cy = null;
  }

  private async _render(): Promise<void> {
    if (this._cancelled || !this.graph) return;

    let nodes = this.graph.nodes;
    let edges = this.graph.edges;

    ({ nodes, edges } = this._limitedGraph(nodes, edges));
    this.cdr.detectChanges();

    // ── Phase 1: Positionen berechnen (chunked, O(n)) ─────────────────────
    this.phase = `Positionen berechnen… (${nodes.length} Nodes)`;
    this.progress = 5;
    this.cdr.detectChanges();
    await yieldFrame();

    const positions = this.layoutMode === 'domain'
      ? await this._positionsByDomain(nodes)
      : this.layoutMode === 'radial'
        ? await this._positionsRadial(nodes, edges)
        : await this._positionsByTier(nodes);

    if (this._cancelled) return;

    // ── Phase 2: Elemente aufbauen ────────────────────────────────────────
    this.phase = 'Elemente vorbereiten…';
    this.progress = 60;
    this.cdr.detectChanges();
    await yieldFrame();

    const nodeSize = nodes.length > 400 ? 18 : 36;
    const elements: unknown[] = [];
    for (let i = 0; i < nodes.length; i += CHUNK) {
      if (this._cancelled) return;
      const batch = nodes.slice(i, i + CHUNK);
      for (const n of batch) {
        const pos = positions.get(n.id) ?? { x: 0, y: 0 };
        const cyNodeId = `n${this.cyNodeIdByOriginal.size}`;
        this.cyNodeIdByOriginal.set(n.id, cyNodeId);
        elements.push({
          data: {
            id: cyNodeId, originalId: n.id,
            label: n.label, kind: n.kind, size: this._nodeSize(n, nodeSize),
            domain: this._domainKey(n),
            color: this._nodeColor(n),
          },
          position: pos,
        });
      }
      this.progress = 60 + Math.round((i / nodes.length) * 15);
      this.cdr.detectChanges();
      await yieldFrame();
    }

    for (let i = 0; i < edges.length; i++) {
      const e = edges[i];
      const source = this.cyNodeIdByOriginal.get(e.source);
      const target = this.cyNodeIdByOriginal.get(e.target);
      if (!source || !target) continue;
      elements.push({
        data: {
          id: `e${i}`, originalId: e.id,
          source, target,
          label: e.edgeType,
          color: graphEdgeColor(e.edgeType),
        },
      });
    }

    if (this._cancelled) return;

    // ── Phase 3: Cytoscape initialisieren (preset = sofort) ───────────────
    this.phase = 'Graph rendern…';
    this.progress = 80;
    this.cdr.detectChanges();
    await yieldFrame();

    try {
      const cytoscape = (await import('cytoscape')).default;
      if (this._cancelled) return;

      const showLabels = nodes.length <= 400;

      this.cy = cytoscape({
        container: this.cyContainer.nativeElement,
        elements: elements as any,
        style: [
          // ── Base ──────────────────────────────────────────────────────────
          {
            selector: 'node',
            style: {
              'background-color': 'data(color)',
              'label': showLabels ? 'data(label)' : '',
              'color': '#fff',
              'text-valign': 'center', 'text-halign': 'center',
              'font-size': '9px', 'font-weight': '500',
              'width': 'data(size)', 'height': 'data(size)',
              'text-wrap': 'ellipsis', 'text-max-width': `${nodeSize - 4}px`,
              'transition-property': 'opacity, border-width, border-color, width, height',
              'transition-duration': 150 as any,
            } as any,
          },
          {
            selector: 'edge',
            style: {
              'width': 2,
              'line-color': 'data(color)',
              'target-arrow-color': 'data(color)',
              'target-arrow-shape': 'triangle',
              'curve-style': 'bezier',
              'opacity': 0.85,
              'transition-property': 'opacity, line-color, width',
              'transition-duration': 150 as any,
            } as any,
          },
          // ── Dimmed (everything not in focus) ──────────────────────────────
          {
            selector: 'node.dimmed',
            style: { 'opacity': 0.12 } as any,
          },
          {
            selector: 'edge.dimmed',
            style: { 'opacity': 0.04 } as any,
          },
          // ── Neighbour ─────────────────────────────────────────────────────
          {
            selector: 'node.neighbour',
            style: {
              'border-width': 2,
              'border-color': '#38bdf8',   // sky-400
              'opacity': 1,
              'label': 'data(label)',
              'font-size': '9px',
              'width': nodeSize * 1.15,
              'height': nodeSize * 1.15,
            } as any,
          },
          {
            selector: 'edge.active-edge',
            style: {
              'opacity': 1, 'width': 2.5,
              'line-color': '#38bdf8',
              'target-arrow-color': '#38bdf8',
            } as any,
          },
          // ── Selected (focal node) ─────────────────────────────────────────
          {
            selector: 'node.focal',
            style: {
              'border-width': 4,
              'border-color': '#f59e0b',   // amber
              'border-style': 'solid',
              'background-color': 'data(color)',
              'opacity': 1,
              'label': 'data(label)',
              'font-size': '10px', 'font-weight': '700',
              'color': '#fff',
              'width': nodeSize * 1.4,
              'height': nodeSize * 1.4,
              'text-outline-color': '#000',
              'text-outline-width': '1px' as any,
              'z-index': 999,
            } as any,
          },
        ],
        layout: { name: 'preset' } as any,
        userZoomingEnabled: true,
        userPanningEnabled: true,
      });

      // ── Event handlers ────────────────────────────────────────────────────
      this.cy.on('tap', 'node', (evt) => {
        const cyNode = evt.target as NodeSingular;
        const originalId: string = cyNode.data('originalId');
        this._applyHighlight(originalId);
        const node = this.nodeMap.get(originalId);
        if (node) this.nodeSelected.emit(node);
      });

      this.cy.on('tap', (evt) => {
        if (evt.target === this.cy) this._clearHighlight();
      });

      this.cy.on('tap', 'edge', (evt) => {
        const originalId: string = (evt.target as EdgeSingular).data('originalId');
        const edge = this.edgeMap.get(originalId);
        if (edge) this.edgeSelected.emit(edge);
      });

      this.progress = 100;
      this.showGraph = true;
      this.cdr.detectChanges();
      await yieldFrame();

      // Tell Cytoscape the actual container size after it becomes visible
      this.cy.resize();
      this.cy.fit(undefined, 40);
    } catch (err) {
      this.error = `Renderer-Fehler: ${(err as Error).message ?? err}`;
    } finally {
      if (!this._cancelled) {
        this.loading = false;
        this.cdr.detectChanges();
      }
    }
  }
}
