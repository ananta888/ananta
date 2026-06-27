import { Component, Input, Output, EventEmitter, ChangeDetectionStrategy, OnChanges } from '@angular/core';

import { GraphEdge, GraphNode, GenericGraphModel } from '../../models/graph.model';

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
              <div
                class="sgv-row sgv-node"
                [class.selected]="selectedNode?.id === node.id"
                (click)="nodeSelected.emit(node)">
                <span class="badge kind">{{ node.kind }}</span>
                <span class="label">{{ node.label }}</span>
                @if (node.file) {
                  <span class="file muted">{{ node.file }}</span>
                }
              </div>
            }
          </div>
        </section>
    
        <section class="sgv-col">
          <h4>Edges ({{ graph.edges.length }})</h4>
          <div class="sgv-scroll">
            @for (edge of graph.edges; track trackEdge($index, edge)) {
              <div
                class="sgv-row sgv-edge"
                [class.selected]="selectedEdge?.id === edge.id"
                (click)="edgeSelected.emit(edge)">
                <span class="badge etype">{{ edge.edgeType }}</span>
                <span class="label">{{ srcLabel(edge) }} → {{ tgtLabel(edge) }}</span>
                @if (edge.confidence < 1) {
                  <span class="muted conf">{{ (edge.confidence * 100).toFixed(0) }}%</span>
                }
              </div>
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
    .sgv-row { display: flex; align-items: center; gap: .4rem; padding: 3px 6px; border-radius: 4px; cursor: pointer; font-size: .8rem; height: 26px; box-sizing: border-box; overflow: hidden; }
    .sgv-row:hover { background: #f0f4ff; }
    .sgv-row.selected { background: #dbeafe; }
    .badge { display: inline-block; font-size: .68rem; padding: 1px 4px; border-radius: 3px; background: #e2e8f0; color: #334; flex-shrink: 0; white-space: nowrap; }
    .badge.etype { background: #ede9fe; color: #4c1d95; }
    .label { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .file, .conf { font-size: .72rem; color: #888; flex-shrink: 0; max-width: 150px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .empty-msg { color: #888; font-style: italic; padding: .5rem; }
  `],
})
export class SimpleGraphViewComponent implements OnChanges {
  @Input() graph: GenericGraphModel | null = null;
  @Input() selectedNode: GraphNode | null = null;
  @Input() selectedEdge: GraphEdge | null = null;

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
}
