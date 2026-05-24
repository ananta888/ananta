import {
  Component, Input, Output, EventEmitter,
  ElementRef, ViewChild, OnChanges, OnDestroy, ChangeDetectionStrategy, ChangeDetectorRef, inject,
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
    :host { display: block; width: 100%; height: 100%; min-height: 500px; }
    .fg3d-container { width: 100%; height: 100%; min-height: 500px; }
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

  ngOnChanges(): void {
    this.nodeMap.clear();
    this.edgeMap.clear();
    this.graph?.nodes.forEach(n => this.nodeMap.set(n.id, n));
    this.graph?.edges.forEach(e => this.edgeMap.set(e.id, e));
    this._render();
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
      this.cdr.markForCheck();
      return;
    }

    this.loading = true;
    this.cdr.markForCheck();

    try {
      const { default: ForceGraph3D } = await import('3d-force-graph');

      const nodes = this.graph.nodes.map(n => ({
        id:    n.id,
        label: n.label,
        kind:  n.kind,
        color: KIND_COLORS[n.kind] ?? KIND_COLORS['unknown'],
      }));

      const links = this.graph.edges.map(e => ({
        id:     e.id,
        source: e.source,
        target: e.target,
        label:  e.edgeType,
      }));

      const el = this.containerRef.nativeElement;
      const w = el.clientWidth  || 800;
      const h = el.clientHeight || 500;

      this.fg = new ForceGraph3D(el, { controlType: 'orbit' })
        .width(w)
        .height(h)
        .backgroundColor('#0f172a')
        .nodeLabel((n: any) => n['label'] as string)
        .nodeColor((n: any) => n['color'] as string)
        .linkLabel((l: any) => l['label'] as string)
        .linkColor(() => '#94a3b8')
        .onNodeClick((node: any) => {
          const gNode = this.nodeMap.get(node['id'] as string);
          if (gNode) this.nodeSelected.emit(gNode);
        })
        .onLinkClick((link: any) => {
          const gEdge = this.edgeMap.get(link['id'] as string);
          if (gEdge) this.edgeSelected.emit(gEdge);
        })
        .graphData({ nodes, links });

    } catch (err) {
      this.error = `Failed to load 3D renderer: ${(err as Error).message ?? err}`;
    } finally {
      this.loading = false;
      this.cdr.markForCheck();
    }
  }
}
