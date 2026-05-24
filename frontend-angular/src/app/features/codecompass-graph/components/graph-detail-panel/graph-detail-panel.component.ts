import { Component, Input, Output, EventEmitter, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { GraphNode, GraphEdge } from '../../models/graph.model';

@Component({
  standalone: true,
  selector: 'app-graph-detail-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  template: `
    <div class="panel">
      @if (!selectedNode && !selectedEdge) {
        <p class="empty-msg">Select a node or edge to see details.</p>
      }

      @if (selectedNode) {
        <div class="detail-block">
          <div class="detail-header">
            <span class="badge kind">{{ selectedNode.kind }}</span>
            <strong class="detail-title">{{ selectedNode.label }}</strong>
            <button class="close-btn" (click)="closed.emit()">✕</button>
          </div>
          <dl class="detail-list">
            @if (selectedNode.file) {
              <dt>File</dt>
              <dd class="copyable" (click)="copyText(selectedNode.file)" title="Click to copy">{{ selectedNode.file }}</dd>
            }
            @if (selectedNode.content) {
              <dt>Content</dt>
              <dd>{{ selectedNode.content }}</dd>
            }
            @if (selectedNode.recordId) {
              <dt>Record ID</dt>
              <dd class="mono">{{ selectedNode.recordId }}</dd>
            }
            @for (entry of extraMeta(selectedNode.metadata); track entry.key) {
              <dt>{{ entry.key }}</dt>
              <dd>{{ entry.value }}</dd>
            }
          </dl>
        </div>
      }

      @if (selectedEdge) {
        <div class="detail-block">
          <div class="detail-header">
            <span class="badge etype">{{ selectedEdge.edgeType }}</span>
            <strong class="detail-title">Edge</strong>
            <button class="close-btn" (click)="closed.emit()">✕</button>
          </div>
          <dl class="detail-list">
            <dt>Source</dt>
            <dd class="mono">{{ selectedEdge.source }}</dd>
            <dt>Target</dt>
            <dd class="mono">{{ selectedEdge.target }}</dd>
            <dt>Confidence</dt>
            <dd>{{ (selectedEdge.confidence * 100).toFixed(0) }}%</dd>
            @for (entry of extraMeta(selectedEdge.metadata); track entry.key) {
              <dt>{{ entry.key }}</dt>
              <dd>{{ entry.value }}</dd>
            }
          </dl>
        </div>
      }
    </div>
  `,
  styles: [`
    .panel { padding: .75rem; font-size: .875rem; min-height: 80px; }
    .empty-msg { color: #888; font-style: italic; margin: 0; }
    .detail-header { display: flex; align-items: center; gap: .5rem; margin-bottom: .5rem; }
    .detail-title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .close-btn { background: none; border: none; cursor: pointer; color: #888; font-size: 1rem; padding: 0 4px; }
    .close-btn:hover { color: #333; }
    .badge { display: inline-block; font-size: .7rem; padding: 2px 6px; border-radius: 3px; background: #e2e8f0; color: #334; flex-shrink: 0; }
    .badge.etype { background: #ede9fe; color: #4c1d95; }
    .detail-list { display: grid; grid-template-columns: 7rem 1fr; gap: 3px 8px; margin: 0; }
    dt { font-weight: 600; color: #555; padding-top: 2px; }
    dd { margin: 0; overflow-wrap: anywhere; }
    .copyable { cursor: pointer; color: #2563eb; text-decoration: underline dotted; }
    .copyable:hover { color: #1d4ed8; }
    .mono { font-family: monospace; font-size: .8rem; }
  `],
})
export class GraphDetailPanelComponent {
  @Input() selectedNode: GraphNode | null = null;
  @Input() selectedEdge: GraphEdge | null = null;

  @Output() closed = new EventEmitter<void>();

  extraMeta(meta: Record<string, unknown>): Array<{ key: string; value: string }> {
    return Object.entries(meta ?? {})
      .filter(([, v]) => v != null && v !== '')
      .map(([k, v]) => ({ key: k, value: String(v) }));
  }

  copyText(text: string): void {
    navigator.clipboard?.writeText(text).catch(() => {});
  }
}
