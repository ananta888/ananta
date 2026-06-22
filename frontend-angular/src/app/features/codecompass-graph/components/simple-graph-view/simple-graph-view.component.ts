import { Component, Input, Output, EventEmitter, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { GraphEdge, GraphNode, GenericGraphModel } from '../../models/graph.model';

@Component({
  standalone: true,
  selector: 'app-simple-graph-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  template: `
    @if (!graph || graph.nodes.length === 0) {
      <p class="empty-msg">No nodes to display.</p>
    } @else {
      <div class="sgv-layout">
        <section class="sgv-nodes">
          <h4>Nodes ({{ graph.nodes.length }})</h4>
          <ul>
            @for (node of graph.nodes; track node.id) {
              <li
                class="sgv-node"
                [class.selected]="selectedNode?.id === node.id"
                (click)="nodeSelected.emit(node)"
              >
                <span class="badge kind">{{ node.kind }}</span>
                <span class="label">{{ node.label }}</span>
                @if (node.file) {
                  <span class="file muted">{{ node.file }}</span>
                }
              </li>
            }
          </ul>
        </section>

        <section class="sgv-edges">
          <h4>Edges ({{ graph.edges.length }})</h4>
          <ul>
            @for (edge of graph.edges; track edge.id) {
              <li
                class="sgv-edge"
                [class.selected]="selectedEdge?.id === edge.id"
                (click)="edgeSelected.emit(edge)"
              >
                <span class="badge etype">{{ edge.edgeType }}</span>
                <span class="label">{{ srcLabel(edge) }} → {{ tgtLabel(edge) }}</span>
                @if (edge.confidence < 1) {
                  <span class="muted conf">{{ (edge.confidence * 100).toFixed(0) }}%</span>
                }
              </li>
            }
          </ul>
        </section>
      </div>
    }
  `,
  styles: [`
    :host { display: flex; flex-direction: column; height: 100%; min-height: 0; }
    .sgv-layout { display: flex; gap: 1.5rem; flex: 1; min-height: 0; overflow: hidden; }
    .sgv-nodes, .sgv-edges { display: flex; flex-direction: column; flex: 1; min-width: 240px; min-height: 0; }
    h4 { margin: 0 0 .5rem; font-size: .85rem; text-transform: uppercase; letter-spacing: .05em; color: #555; flex-shrink: 0; }
    ul { list-style: none; margin: 0; padding: 0; overflow-y: auto; flex: 1; min-height: 0; }
    li { display: flex; align-items: baseline; gap: .4rem; padding: 3px 6px; border-radius: 4px; cursor: pointer; font-size: .875rem; }
    li:hover { background: #f0f4ff; }
    li.selected { background: #dbeafe; }
    .badge { display: inline-block; font-size: .7rem; padding: 1px 5px; border-radius: 3px; background: #e2e8f0; color: #334; flex-shrink: 0; }
    .badge.etype { background: #ede9fe; color: #4c1d95; }
    .label { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .file, .conf { font-size: .75rem; color: #888; flex-shrink: 0; max-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .empty-msg { color: #888; font-style: italic; }
  `],
})
export class SimpleGraphViewComponent {
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

  srcLabel(edge: GraphEdge): string {
    return this._nodeMap.get(edge.source)?.label ?? edge.source;
  }

  tgtLabel(edge: GraphEdge): string {
    return this._nodeMap.get(edge.target)?.label ?? edge.target;
  }
}
