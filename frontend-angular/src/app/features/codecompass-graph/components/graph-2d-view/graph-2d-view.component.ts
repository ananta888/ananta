import {
  Component, Input, Output, EventEmitter,
  ElementRef, ViewChild, OnChanges, OnDestroy,
  ChangeDetectionStrategy, ChangeDetectorRef, inject,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import type { Core, NodeSingular, EdgeSingular } from 'cytoscape';
import { GenericGraphModel, GraphEdge, GraphNode } from '../../models/graph.model';

const KIND_COLORS: Record<string, string> = {
  java_type: '#3b82f6', java_method: '#10b981', java_file: '#1d4ed8',
  python_file: '#f59e0b', python_class: '#d97706', python_function: '#92400e', python_method: '#78350f',
  typescript_file: '#0284c7', typescript_class: '#0369a1', typescript_function: '#075985',
  python_module_summary: '#7c3aed', typescript_folder_summary: '#0891b2', java_module_summary: '#1e40af',
  config: '#a16207', xml_tag: '#8b5cf6', md_file: '#6b7280', unknown: '#94a3b8',
};

const KIND_TIER: Record<string, number> = {
  python_module_summary: 0, typescript_folder_summary: 0, java_module_summary: 0,
  python_file: 1, typescript_file: 1, java_file: 1, md_file: 1, yaml_file: 1, xml_file: 1,
  python_class: 2, typescript_class: 2, typescript_interface: 2, typescript_enum: 2, java_type: 2,
  python_function: 3, python_method: 3, typescript_function: 3, typescript_method: 3,
  typescript_const: 3, typescript_constructor: 3, java_method: 3, java_constructor: 3,
};

const RENDER_CAP = 800;
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
    :host { display: flex; flex-direction: column; width: 100%; height: 100%; min-height: 0; }
    .cy-container { flex: 1; width: 100%; min-height: 0; }
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
  @Input() selectedNode: GraphNode | null = null;
  @Input() selectedEdge: GraphEdge | null = null;

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
  private _cancelled = false;
  private readonly cdr = inject(ChangeDetectorRef);

  ngOnChanges(): void {
    this._cancelled = true; // cancel any in-progress render
    this.cy?.destroy();
    this.cy = null;
    this.showGraph = false;
    this.error = '';
    this.renderWarning = '';
    this.progress = 0;

    this.nodeMap.clear();
    this.edgeMap.clear();
    this.graph?.nodes.forEach(n => this.nodeMap.set(n.id, n));
    this.graph?.edges.forEach(e => this.edgeMap.set(e.id, e));

    if (!this.graph || this.graph.nodes.length === 0) {
      this.loading = false;
      this.cdr.markForCheck();
      return;
    }

    this.loading = true;
    this._cancelled = false;
    this.cdr.markForCheck();

    // Yield so loading state renders before we block
    setTimeout(() => this._render(), 16);
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

    // ── Cap ──────────────────────────────────────────────────────────────────
    if (nodes.length > RENDER_CAP) {
      const sorted = [...nodes].sort((a, b) => (KIND_TIER[a.kind] ?? 4) - (KIND_TIER[b.kind] ?? 4));
      nodes = sorted.slice(0, RENDER_CAP);
      const kept = new Set(nodes.map(n => n.id));
      edges = edges.filter(e => kept.has(e.source) && kept.has(e.target));
      this.renderWarning = `2D-Ansicht zeigt ${RENDER_CAP} von ${this.graph.nodes.length} Nodes — Simple-List für alle`;
      this.cdr.markForCheck();
    }

    // ── Phase 1: Positionen berechnen (chunked, O(n)) ─────────────────────
    this.phase = `Positionen berechnen… (${nodes.length} Nodes)`;
    this.progress = 0;
    this.cdr.markForCheck();

    // Group by tier → each tier gets its own rows in the grid
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
      if (this._cancelled) return;
      const tierNodes = byTier.get(tier)!;
      const COLS = Math.min(Math.ceil(Math.sqrt(tierNodes.length * 3)), 40);

      for (let i = 0; i < tierNodes.length; i += CHUNK) {
        if (this._cancelled) return;
        const batch = tierNodes.slice(i, i + CHUNK);
        for (let j = 0; j < batch.length; j++) {
          const idx = i + j;
          positions.set(batch[j].id, {
            x: (idx % COLS) * GAP_X,
            y: yOffset + Math.floor(idx / COLS) * GAP_Y,
          });
        }
        this.progress = Math.round((positions.size / nodes.length) * 55);
        this.cdr.markForCheck();
        await yieldFrame();
      }

      yOffset += (Math.ceil(tierNodes.length / COLS) + 1) * GAP_Y;
    }

    if (this._cancelled) return;

    // ── Phase 2: Elemente aufbauen ────────────────────────────────────────
    this.phase = 'Elemente vorbereiten…';
    this.progress = 60;
    this.cdr.markForCheck();
    await yieldFrame();

    const elements: unknown[] = [];
    for (let i = 0; i < nodes.length; i += CHUNK) {
      if (this._cancelled) return;
      const batch = nodes.slice(i, i + CHUNK);
      for (const n of batch) {
        const pos = positions.get(n.id) ?? { x: 0, y: 0 };
        elements.push({ data: { id: n.id, label: n.label, kind: n.kind, color: KIND_COLORS[n.kind] ?? KIND_COLORS['unknown'] }, position: pos });
      }
      this.progress = 60 + Math.round((i / nodes.length) * 15);
      this.cdr.markForCheck();
      await yieldFrame();
    }

    for (const e of edges) {
      elements.push({ data: { id: e.id, source: e.source, target: e.target, label: e.edgeType } });
    }

    if (this._cancelled) return;

    // ── Phase 3: Cytoscape initialisieren (preset = sofort) ───────────────
    this.phase = 'Graph rendern…';
    this.progress = 80;
    this.cdr.markForCheck();
    await yieldFrame();

    try {
      const cytoscape = (await import('cytoscape')).default;
      if (this._cancelled) return;

      this.cy = cytoscape({
        container: this.cyContainer.nativeElement,
        elements: elements as any,
        style: [
          {
            selector: 'node',
            style: {
              'background-color': 'data(color)',
              'label': nodes.length > 400 ? '' : 'data(label)',
              'color': '#fff',
              'text-valign': 'center',
              'text-halign': 'center',
              'font-size': '9px',
              'width': nodes.length > 400 ? 18 : 36,
              'height': nodes.length > 400 ? 18 : 36,
              'text-wrap': 'ellipsis',
              'text-max-width': '32px',
            } as any,
          },
          { selector: 'node:selected', style: { 'border-width': 3, 'border-color': '#f59e0b' } as any },
          {
            selector: 'edge',
            style: {
              'width': 1, 'line-color': '#cbd5e1',
              'target-arrow-color': '#cbd5e1', 'target-arrow-shape': 'triangle',
              'curve-style': 'haystack',
            } as any,
          },
          { selector: 'edge:selected', style: { 'line-color': '#3b82f6', 'target-arrow-color': '#3b82f6' } as any },
        ],
        layout: { name: 'preset' } as any,
        userZoomingEnabled: true,
        userPanningEnabled: true,
      });

      this.cy.on('tap', 'node', (evt) => {
        const node = this.nodeMap.get((evt.target as NodeSingular).id());
        if (node) this.nodeSelected.emit(node);
      });
      this.cy.on('tap', 'edge', (evt) => {
        const edge = this.edgeMap.get((evt.target as EdgeSingular).id());
        if (edge) this.edgeSelected.emit(edge);
      });

      this.progress = 100;
      this.cdr.markForCheck();
      await yieldFrame();

      this.showGraph = true;
    } catch (err) {
      this.error = `Renderer-Fehler: ${(err as Error).message ?? err}`;
    } finally {
      if (!this._cancelled) {
        this.loading = false;
        this.cdr.markForCheck();
      }
    }
  }
}
