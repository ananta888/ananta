import { Component, Input, Output, EventEmitter, OnChanges, ChangeDetectionStrategy } from '@angular/core';
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
        <p class="empty-msg">Knoten oder Kante auswählen.</p>
      }

      @if (selectedNode) {
        <div class="detail-block">
          <div class="detail-header">
            <span class="badge kind">{{ selectedNode.kind }}</span>
            <strong class="detail-title" [title]="selectedNode.label">{{ selectedNode.label }}</strong>
            <button class="close-btn" (click)="closed.emit()">✕</button>
          </div>
          <dl class="detail-list">
            <dt>Name</dt>
            <dd class="copyable" (click)="copyText(selectedNode.label)" title="Click to copy">{{ selectedNode.label }}</dd>
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

        <!-- Diff3 shortcut -->
        @if (selectedNode.file) {
          <div class="diff3-section">
            <button class="btn-diff3" (click)="diff3Requested.emit()">⬡ Im 3er Diff öffnen</button>
          </div>
        }

        <!-- Focus controls -->
        <div class="focus-section">
          <div class="focus-row">
            <span class="focus-label">Hop-Tiefe</span>
            <div class="hop-stepper">
              <button class="hop-btn" (click)="decHops()" [disabled]="localHops <= 0">−</button>
              <span class="hop-val">{{ localHops }}</span>
              <button class="hop-btn" (click)="incHops()" [disabled]="localHops >= 6">+</button>
            </div>
          </div>
          <div class="focus-btns">
            <button class="btn-focus" (click)="focusRequested.emit(localHops)">
              Anwenden
            </button>
            @if (focusActive) {
              <button class="btn-clear-focus" (click)="focusCleared.emit()">
                Fokus aufheben
              </button>
            }
          </div>
          @if (focusActive) {
            <p class="focus-hint">Zeigt {{ selectedNode.label }} + {{ localHops }}-Hop-Nachbarn</p>
          } @else {
            <p class="focus-hint">0 zeigt den kompletten geladenen Graph</p>
          }
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
    .detail-title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: .85rem; }
    .close-btn { background: none; border: none; cursor: pointer; color: #888; font-size: 1rem; padding: 0 4px; flex-shrink: 0; }
    .close-btn:hover { color: #333; }
    .badge { display: inline-block; font-size: .7rem; padding: 2px 6px; border-radius: 3px; background: #e2e8f0; color: #334; flex-shrink: 0; }
    .badge.etype { background: #ede9fe; color: #4c1d95; }
    .detail-list { display: grid; grid-template-columns: 7rem 1fr; gap: 3px 8px; margin: 0 0 .75rem; }
    dt { font-weight: 600; color: #555; padding-top: 2px; }
    dd { margin: 0; overflow-wrap: anywhere; }
    .copyable { cursor: pointer; color: #2563eb; text-decoration: underline dotted; }
    .copyable:hover { color: #1d4ed8; }
    .mono { font-family: monospace; font-size: .8rem; }

    /* Diff3 shortcut */
    .diff3-section { border-top: 1px solid #e2e8f0; padding-top: .5rem; margin-bottom: .25rem; }
    .btn-diff3 {
      width: 100%; padding: 5px 10px; border-radius: 5px; border: 1px solid #0284c7;
      background: #e0f2fe; color: #0369a1; font-size: .78rem; font-weight: 600;
      cursor: pointer; text-align: left;
    }
    .btn-diff3:hover { background: #bae6fd; }

    /* Focus controls */
    .focus-section { border-top: 1px solid #e2e8f0; padding-top: .65rem; display: flex; flex-direction: column; gap: .5rem; }
    .focus-row { display: flex; align-items: center; justify-content: space-between; }
    .focus-label { font-size: .78rem; font-weight: 600; color: #555; }
    .hop-stepper { display: flex; align-items: center; gap: 6px; }
    .hop-btn {
      width: 24px; height: 24px; border-radius: 4px; border: 1px solid #cbd5e1;
      background: #f8fafc; cursor: pointer; font-size: 1rem; line-height: 1;
      display: flex; align-items: center; justify-content: center; padding: 0;
    }
    .hop-btn:hover:not(:disabled) { background: #e2e8f0; }
    .hop-btn:disabled { opacity: .4; cursor: default; }
    .hop-val { min-width: 20px; text-align: center; font-weight: 600; font-size: .9rem; }
    .focus-btns { display: flex; gap: .4rem; flex-wrap: wrap; }
    .btn-focus {
      flex: 1; padding: 5px 10px; border-radius: 5px; border: none; cursor: pointer;
      background: #3b82f6; color: #fff; font-size: .78rem; font-weight: 600;
    }
    .btn-focus:hover { background: #2563eb; }
    .btn-clear-focus {
      padding: 5px 10px; border-radius: 5px; border: 1px solid #cbd5e1;
      background: #fff; color: #555; font-size: .78rem; cursor: pointer;
    }
    .btn-clear-focus:hover { background: #f1f5f9; color: #333; }
    .focus-hint { margin: 0; font-size: .72rem; color: #7c3aed; font-style: italic; }
  `],
})
export class GraphDetailPanelComponent implements OnChanges {
  @Input() selectedNode: GraphNode | null = null;
  @Input() selectedEdge: GraphEdge | null = null;
  @Input() focusActive = false;
  @Input() focusHopDepth = 0;

  @Output() closed = new EventEmitter<void>();
  @Output() focusRequested = new EventEmitter<number>();
  @Output() focusCleared = new EventEmitter<void>();
  @Output() diff3Requested = new EventEmitter<void>();

  localHops = 0;

  ngOnChanges(): void {
    this.localHops = this.focusHopDepth;
  }

  incHops(): void {
    if (this.localHops >= 6) return;
    this.localHops++;
    this.emitFocusDepthIfActive();
  }

  decHops(): void {
    if (this.localHops <= 0) return;
    this.localHops--;
    this.emitFocusDepthIfActive();
  }

  private emitFocusDepthIfActive(): void {
    if (this.focusActive) {
      this.focusRequested.emit(this.localHops);
    }
  }

  extraMeta(meta: Record<string, unknown>): Array<{ key: string; value: string }> {
    return Object.entries(meta ?? {})
      .filter(([, v]) => v != null && v !== '')
      .map(([k, v]) => ({ key: k, value: String(v) }));
  }

  copyText(text: string): void {
    navigator.clipboard?.writeText(text).catch(() => {});
  }
}
