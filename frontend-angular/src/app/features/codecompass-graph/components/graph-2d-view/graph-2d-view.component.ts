import {
  Component, Input, Output, EventEmitter,
  ElementRef, ViewChild, OnChanges, OnDestroy, ChangeDetectionStrategy,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import type { Core, NodeSingular, EdgeSingular } from 'cytoscape';
import { GenericGraphModel, GraphEdge, GraphNode } from '../../models/graph.model';

// Colour map for node kinds
const KIND_COLORS: Record<string, string> = {
  java_type:   '#3b82f6',
  java_method: '#10b981',
  config:      '#f59e0b',
  xml_tag:     '#8b5cf6',
  unknown:     '#94a3b8',
};

@Component({
  standalone: true,
  selector: 'app-graph-2d-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  template: `
    @if (error) {
      <p class="error-msg">{{ error }}</p>
    } @else if (loading) {
      <p class="status-msg">Rendering graph…</p>
    } @else if (!graph || graph.nodes.length === 0) {
      <p class="status-msg">No nodes to display.</p>
    }
    <div #cyContainer class="cy-container" [style.display]="graph && graph.nodes.length && !error ? 'block' : 'none'"></div>
  `,
  styles: [`
    :host { display: block; width: 100%; height: 100%; min-height: 400px; }
    .cy-container { width: 100%; height: 100%; min-height: 400px; }
    .error-msg { color: #c00; padding: .75rem; }
    .status-msg { color: #888; padding: .75rem; font-style: italic; }
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

  private cy: Core | null = null;
  private nodeMap = new Map<string, GraphNode>();
  private edgeMap = new Map<string, GraphEdge>();

  ngOnChanges(): void {
    this.nodeMap.clear();
    this.edgeMap.clear();
    this.graph?.nodes.forEach(n => this.nodeMap.set(n.id, n));
    this.graph?.edges.forEach(e => this.edgeMap.set(e.id, e));
    this._render();
  }

  ngOnDestroy(): void {
    this.cy?.destroy();
    this.cy = null;
  }

  private async _render(): Promise<void> {
    this.cy?.destroy();
    this.cy = null;
    if (!this.graph || this.graph.nodes.length === 0) return;

    this.loading = true;
    this.error = '';

    try {
      const cytoscape = (await import('cytoscape')).default;

      const elements = [
        ...this.graph.nodes.map(n => ({
          data: {
            id: n.id,
            label: n.label,
            kind: n.kind,
            color: KIND_COLORS[n.kind] ?? KIND_COLORS['unknown'],
          },
        })),
        ...this.graph.edges.map(e => ({
          data: {
            id: e.id,
            source: e.source,
            target: e.target,
            label: e.edgeType,
          },
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
              'font-size': '10px',
              'width': 50,
              'height': 50,
              'text-wrap': 'ellipsis',
              'text-max-width': '45px',
            } as any,
          },
          {
            selector: 'node:selected',
            style: { 'border-width': 3, 'border-color': '#f59e0b' } as any,
          },
          {
            selector: 'edge',
            style: {
              'width': 1.5,
              'line-color': '#94a3b8',
              'target-arrow-color': '#94a3b8',
              'target-arrow-shape': 'triangle',
              'curve-style': 'bezier',
              'label': 'data(label)',
              'font-size': '8px',
              'color': '#555',
              'text-rotation': 'autorotate',
            } as any,
          },
          {
            selector: 'edge:selected',
            style: { 'line-color': '#3b82f6', 'target-arrow-color': '#3b82f6' } as any,
          },
        ],
        layout: { name: 'cose', animate: false } as any,
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

    } catch (err) {
      this.error = `Failed to load graph renderer: ${(err as Error).message ?? err}`;
    } finally {
      this.loading = false;
    }
  }
}
