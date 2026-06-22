import {
  Component, Input, Output, EventEmitter,
  ElementRef, ViewChild, OnChanges, OnDestroy, ChangeDetectionStrategy, ChangeDetectorRef, inject,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import type { Core, NodeSingular, EdgeSingular } from 'cytoscape';
import { GenericGraphModel, GraphEdge, GraphNode } from '../../models/graph.model';

const KIND_COLORS: Record<string, string> = {
  java_type: '#3b82f6', java_method: '#10b981', java_file: '#1d4ed8',
  python_file: '#f59e0b', python_class: '#d97706', python_function: '#92400e', python_method: '#78350f',
  typescript_file: '#0284c7', typescript_class: '#0369a1', typescript_function: '#075985',
  config: '#f59e0b', xml_tag: '#8b5cf6', md_file: '#6b7280',
  python_module_summary: '#7c3aed', typescript_folder_summary: '#0891b2', java_module_summary: '#1e40af',
  unknown: '#94a3b8',
};

// Cytoscape render cap: above this node count, switch to a faster layout / warn
const COSE_MAX = 300;
const RENDER_CAP = 800;

@Component({
  standalone: true,
  selector: 'app-graph-2d-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  template: `
    @if (error) {
      <p class="status-msg error-msg">{{ error }}</p>
    }
    @if (renderWarning) {
      <div class="render-warn">{{ renderWarning }}</div>
    }
    @if (loading) {
      <div class="cy-loading">
        <span class="cy-spinner"></span> Berechne Layout ({{ nodeCount }} Nodes)…
      </div>
    }
    <div #cyContainer class="cy-container"
      [style.display]="showGraph ? 'block' : 'none'"></div>
  `,
  styles: [`
    :host { display: flex; flex-direction: column; width: 100%; height: 100%; min-height: 0; }
    .cy-container { flex: 1; width: 100%; min-height: 0; }
    .status-msg { color: #888; padding: .75rem; font-style: italic; margin: 0; }
    .error-msg { color: #c00; }
    .render-warn {
      flex-shrink: 0; padding: 4px 10px; font-size: 11px; font-weight: 600;
      background: #fef9c3; border-bottom: 1px solid #fde68a; color: #92400e;
    }
    .cy-loading {
      flex: 1; display: flex; align-items: center; justify-content: center;
      gap: 10px; font-size: 13px; color: #555;
    }
    .cy-spinner {
      display: inline-block; width: 18px; height: 18px; border-radius: 50%;
      border: 2px solid #d1d5db; border-top-color: #3b82f6;
      animation: spin .8s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
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
  nodeCount = 0;

  private cy: Core | null = null;
  private nodeMap = new Map<string, GraphNode>();
  private edgeMap = new Map<string, GraphEdge>();
  private _renderTimer: ReturnType<typeof setTimeout> | null = null;
  private readonly cdr = inject(ChangeDetectorRef);

  ngOnChanges(): void {
    this.nodeMap.clear();
    this.edgeMap.clear();
    this.graph?.nodes.forEach(n => this.nodeMap.set(n.id, n));
    this.graph?.edges.forEach(e => this.edgeMap.set(e.id, e));

    // Cancel any pending render
    if (this._renderTimer !== null) {
      clearTimeout(this._renderTimer);
      this._renderTimer = null;
    }

    this.cy?.destroy();
    this.cy = null;
    this.showGraph = false;
    this.error = '';
    this.renderWarning = '';

    if (!this.graph || this.graph.nodes.length === 0) {
      this.loading = false;
      this.cdr.markForCheck();
      return;
    }

    this.nodeCount = this.graph.nodes.length;
    this.loading = true;
    this.cdr.markForCheck();

    // Yield to browser so the "Rendering…" message appears before blocking
    this._renderTimer = setTimeout(() => this._render(), 30);
  }

  ngOnDestroy(): void {
    if (this._renderTimer !== null) clearTimeout(this._renderTimer);
    this.cy?.destroy();
    this.cy = null;
  }

  private async _render(): Promise<void> {
    this._renderTimer = null;
    if (!this.graph) return;

    let nodes = this.graph.nodes;
    let edges = this.graph.edges;
    this.renderWarning = '';

    // Hard cap: trim to RENDER_CAP, prefer lower-tier (summaries/files first)
    if (nodes.length > RENDER_CAP) {
      const TIER: Record<string, number> = {
        python_module_summary: 0, typescript_folder_summary: 0, java_module_summary: 0,
        python_file: 0, typescript_file: 0, java_file: 0, md_file: 0,
        python_class: 1, typescript_class: 1, typescript_interface: 1, java_type: 1,
        python_function: 2, python_method: 2, typescript_function: 2, typescript_method: 2,
      };
      const sorted = [...nodes].sort((a, b) => (TIER[a.kind] ?? 3) - (TIER[b.kind] ?? 3));
      nodes = sorted.slice(0, RENDER_CAP);
      const kept = new Set(nodes.map(n => n.id));
      edges = edges.filter(e => kept.has(e.source) && kept.has(e.target));
      this.renderWarning = `2D-Ansicht: ${RENDER_CAP} von ${this.graph.nodes.length} Nodes (Simple-List für alle)`;
    }

    // Layout choice: CoSE is O(n²) — use faster alternatives for larger graphs
    const layoutName = nodes.length <= COSE_MAX ? 'cose' : 'breadthfirst';
    const layoutOpts: Record<string, unknown> = layoutName === 'cose'
      ? { name: 'cose', animate: false, nodeRepulsion: () => 8000, idealEdgeLength: () => 80 }
      : { name: 'breadthfirst', animate: false, directed: true, spacingFactor: 1.1 };

    try {
      const cytoscape = (await import('cytoscape')).default;

      const elements = [
        ...nodes.map(n => ({
          data: {
            id: n.id,
            label: n.label,
            kind: n.kind,
            color: KIND_COLORS[n.kind] ?? KIND_COLORS['unknown'],
          },
        })),
        ...edges.map(e => ({
          data: { id: e.id, source: e.source, target: e.target, label: e.edgeType },
        })),
      ];

      this.cy = cytoscape({
        container: this.cyContainer.nativeElement,
        elements,
        style: [
          {
            selector: 'node',
            style: {
              'background-color': 'data(color)',
              'label': 'data(label)',
              'color': '#fff',
              'text-valign': 'center',
              'text-halign': 'center',
              'font-size': '9px',
              'width': nodes.length > 200 ? 28 : 44,
              'height': nodes.length > 200 ? 28 : 44,
              'text-wrap': 'ellipsis',
              'text-max-width': nodes.length > 200 ? '24px' : '40px',
            } as any,
          },
          {
            selector: 'node:selected',
            style: { 'border-width': 3, 'border-color': '#f59e0b' } as any,
          },
          {
            selector: 'edge',
            style: {
              'width': 1,
              'line-color': '#94a3b8',
              'target-arrow-color': '#94a3b8',
              'target-arrow-shape': 'triangle',
              'curve-style': 'bezier',
              'font-size': '7px',
              'color': '#999',
            } as any,
          },
          {
            selector: 'edge:selected',
            style: { 'line-color': '#3b82f6', 'target-arrow-color': '#3b82f6' } as any,
          },
        ],
        layout: layoutOpts as any,
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

      this.showGraph = true;
    } catch (err) {
      this.error = `Renderer-Fehler: ${(err as Error).message ?? err}`;
    } finally {
      this.loading = false;
      this.cdr.markForCheck();
    }
  }
}
