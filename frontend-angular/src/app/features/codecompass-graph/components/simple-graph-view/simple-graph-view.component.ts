import { Component, Input, Output, EventEmitter, ChangeDetectionStrategy, OnChanges } from '@angular/core';

import { GraphEdge, GraphNode, GenericGraphModel } from '../../models/graph.model';
import { EdgeVisualStyle, GraphVisualProjection, NodeVisualStyle } from '../../models/graph-visual-metrics.model';
import { graphVisualTooltipText } from '../graph-tooltip/graph-visual-tooltip';

export const SIMPLE_GRAPH_VIEW_CAPABILITIES = Object.freeze({
  color: true,
  marker: true,
  scoreText: true,
  availability: true,
  nodeSize: false,
  edgeThickness: false,
});

@Component({
  standalone: true,
  selector: 'app-simple-graph-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [],
  template: `
    @if (!graph || graph.nodes.length === 0) {
      <p class="empty-msg">No nodes to display.</p>
    } @else {
      <div class="sgv-layout">
        <section class="sgv-col">
          <h4>Nodes ({{ graph.nodes.length }})</h4>
          <div class="sgv-scroll">
            @for (node of graph.nodes; track trackNode($index, node)) {
              <button type="button"
                class="sgv-row sgv-node"
                [class.selected]="selectedNode?.id === node.id"
                [class.legend-highlight]="highlightedNodeIds.has(node.id)"
                [class.legend-dimmed]="highlightedNodeIds.size > 0 && !highlightedNodeIds.has(node.id)"
                [attr.title]="nodeTooltip(node)"
                (click)="nodeSelected.emit(node)">
                <span class="visual-marker" aria-hidden="true" [style.background-color]="nodeStyle(node).baseColor">{{ markerSymbol(nodeStyle(node).marker) }}</span>
                <span class="badge kind">{{ node.kind }}</span>
                <span class="label">{{ node.label }}</span>
                <span class="score" [attr.data-availability]="nodeStyle(node).availability">
                  Score {{ nodeStyle(node).score.toFixed(3) }} · {{ nodeStyle(node).availability }}
                </span>
                @if (node.file) {
                  <span class="file muted">{{ node.file }}</span>
                }
              </button>
            }
          </div>
        </section>
    
        <section class="sgv-col">
          <h4>Edges ({{ graph.edges.length }})</h4>
          <div class="sgv-scroll">
            @for (edge of graph.edges; track trackEdge($index, edge)) {
              <button type="button"
                class="sgv-row sgv-edge"
                [class.selected]="selectedEdge?.id === edge.id"
                [class.legend-highlight]="highlightedEdgeIds.has(edge.id)"
                [class.legend-dimmed]="highlightedEdgeIds.size > 0 && !highlightedEdgeIds.has(edge.id)"
                [attr.title]="edgeTooltip(edge)"
                (click)="edgeSelected.emit(edge)">
                <span class="visual-marker" aria-hidden="true" [style.background-color]="edgeStyle(edge).baseColor">{{ markerSymbol(edgeStyle(edge).marker) }}</span>
                <span class="badge etype">{{ edge.rawEdgeType ?? edge.edgeType }}</span>
                <span class="label">{{ srcLabel(edge) }} → {{ tgtLabel(edge) }}</span>
                <span class="score" [attr.data-availability]="edgeStyle(edge).availability">
                  Score {{ edgeStyle(edge).score.toFixed(3) }} · {{ edgeStyle(edge).availability }}
                </span>
                @if (edge.confidence < 1) {
                  <span class="muted conf">{{ (edge.confidence * 100).toFixed(0) }}%</span>
                }
              </button>
            }
          </div>
        </section>
      </div>
    }
    `,
  styles: [`
    :host { display: flex; flex-direction: column; flex: 1; width: 100%; height: 100%; min-height: 0; padding: .5rem; box-sizing: border-box; }
    .sgv-layout { display: flex; gap: 1rem; flex: 1; min-height: 0; overflow: hidden; }
    .sgv-col { display: flex; flex-direction: column; flex: 1; min-width: 240px; min-height: 0; }
    h4 { margin: 0 0 .4rem; font-size: .8rem; text-transform: uppercase; letter-spacing: .05em; color: #555; flex-shrink: 0; }
    .sgv-scroll { flex: 1; min-height: 0; height: 100%; overflow: auto; }
    .sgv-row { display: flex; width: 100%; align-items: center; gap: .4rem; padding: 3px 6px; border: 0; background: transparent; color: inherit; text-align: left; border-radius: 4px; cursor: pointer; font: inherit; font-size: .8rem; min-height: 30px; box-sizing: border-box; overflow: hidden; }
    .sgv-row:hover { background: #f0f4ff; }
    .sgv-row:focus-visible { outline: 3px solid #38bdf8; outline-offset: -2px; }
    .sgv-row.selected { background: #dbeafe; }
    .sgv-row.legend-highlight { box-shadow: inset 3px 0 #38bdf8; background: #e0f2fe; }
    .sgv-row.legend-dimmed { opacity: .35; }
    .badge { display: inline-block; font-size: .68rem; padding: 1px 4px; border-radius: 3px; background: #e2e8f0; color: #334; flex-shrink: 0; white-space: nowrap; }
    .badge.etype { background: #ede9fe; color: #4c1d95; }
    .label { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .file, .conf { font-size: .72rem; color: #888; flex-shrink: 0; max-width: 150px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .visual-marker { display: inline-grid; place-items: center; width: 18px; height: 18px; border-radius: 4px; color: #fff; text-shadow: 0 1px 2px #000; flex: 0 0 auto; font-size: .65rem; }
    .score { color: #475569; font-size: .68rem; flex: 0 0 auto; }
    [data-availability='unavailable'], [data-availability='not_applicable'] { color: #b45309; }
    .empty-msg { color: #888; font-style: italic; padding: .5rem; }
  `],
})
export class SimpleGraphViewComponent implements OnChanges {
  @Input() graph: GenericGraphModel | null = null;
  @Input() selectedNode: GraphNode | null = null;
  @Input() selectedEdge: GraphEdge | null = null;
  @Input() visualProjection: GraphVisualProjection | null = null;
  @Input() highlightedNodeIds: ReadonlySet<string> = new Set();
  @Input() highlightedEdgeIds: ReadonlySet<string> = new Set();

  @Output() nodeSelected = new EventEmitter<GraphNode>();
  @Output() edgeSelected = new EventEmitter<GraphEdge>();

  private _nodeMap = new Map<string, GraphNode>();

  ngOnChanges(): void {
    this._nodeMap.clear();
    this.graph?.nodes.forEach(n => this._nodeMap.set(n.id, n));
  }

  trackNode(_: number, n: GraphNode) { return n.id; }
  trackEdge(_: number, e: GraphEdge) { return e.id; }

  srcLabel(edge: GraphEdge): string {
    return this._nodeMap.get(edge.source)?.label ?? edge.source;
  }

  tgtLabel(edge: GraphEdge): string {
    return this._nodeMap.get(edge.target)?.label ?? edge.target;
  }

  nodeStyle(node: GraphNode): Readonly<NodeVisualStyle> {
    return this.visualProjection?.nodeStyles[node.id] ?? {
      nodeId: node.id, baseColor: '#64748b', marker: 'circle', baseSize: 1,
      score: 0, scoreState: 'degraded_no_active_metric', availability: 'unavailable', breakdown: [],
      highlightFactors: { hover: 1, selected: 1, connected: 1 },
    };
  }

  edgeStyle(edge: GraphEdge): Readonly<EdgeVisualStyle> {
    return this.visualProjection?.edgeStyles[edge.id] ?? {
      edgeId: edge.id, baseColor: '#94a3b8', marker: 'triangle', baseThickness: 1,
      score: 0, scoreState: 'degraded_no_active_metric', availability: 'unavailable', breakdown: [],
      highlightFactors: { hover: 1, selected: 1, connected: 1 },
    };
  }

  markerSymbol(marker: string): string {
    return ({ circle: '●', square: '■', triangle: '▲', diamond: '◆', hexagon: '⬢', ring: '○', cross: '×', star: '★' } as Record<string, string>)[marker] ?? '●';
  }

  nodeTooltip(node: GraphNode): string {
    return graphVisualTooltipText(node.label, this.nodeStyle(node));
  }

  edgeTooltip(edge: GraphEdge): string {
    return graphVisualTooltipText(edge.rawEdgeType ?? edge.edgeType, this.edgeStyle(edge));
  }
}
