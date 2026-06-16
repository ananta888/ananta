import {
  ChangeDetectionStrategy,
  Component,
  EventEmitter,
  Input,
  Output,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { ConfigGraphNode, nodeColor } from '../models/config-graph.model';

@Component({
  standalone: true,
  selector: 'app-config-graph-node-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  template: `
    <div class="node-detail-panel" *ngIf="node">
      <div class="node-detail-header" [style.border-left-color]="nodeColor(node.node_type)">
        <span class="node-type-badge" [style.background]="nodeColor(node.node_type)">
          {{ node.node_type }}
        </span>
        <span class="node-label">{{ node.label }}</span>
        <button class="close-btn" (click)="closed.emit()">✕</button>
      </div>

      <div class="node-detail-body">
        <div class="detail-row" *ngIf="node.source_file">
          <span class="detail-key">Quelldatei</span>
          <code class="detail-value">{{ node.source_file }}</code>
        </div>
        <div class="detail-row" *ngIf="node.runtime_source">
          <span class="detail-key">Laufzeit-Quelle</span>
          <span class="detail-value">{{ node.runtime_source }}</span>
        </div>

        <div class="detail-row">
          <span class="detail-key">Aktiv</span>
          <span class="detail-value badge" [class.badge-ok]="node.runtime_active" [class.badge-warn]="!node.runtime_active">
            {{ node.runtime_active ? 'ja' : 'nein' }}
          </span>
        </div>

        <div class="detail-row" *ngIf="node.stale">
          <span class="detail-key">Veraltet</span>
          <span class="detail-value badge badge-warn">ggf. veraltet</span>
        </div>

        <div *ngIf="node.diagnostics.length > 0" class="diagnostics-block">
          <div class="detail-key">Diagnose</div>
          <ul class="diag-list">
            <li *ngFor="let d of node.diagnostics">{{ d }}</li>
          </ul>
        </div>

        <div *ngIf="dataEntries.length > 0" class="data-section">
          <div class="detail-key">Daten</div>
          <table class="data-table">
            <tr *ngFor="let entry of dataEntries">
              <td class="data-key">{{ entry[0] }}</td>
              <td class="data-val">
                <ng-container *ngIf="isArray(entry[1]); else scalar">
                  <span class="tag" *ngFor="let t of asArray(entry[1])">{{ t }}</span>
                </ng-container>
                <ng-template #scalar>{{ entry[1] }}</ng-template>
              </td>
            </tr>
          </table>
        </div>

        <div class="node-id-row">
          <code class="muted">{{ node.id }}</code>
        </div>

        <div class="action-row" *ngIf="editMode">
          <button class="button-outline danger" (click)="removeRequested.emit(node.id)">
            Node entfernen
          </button>
        </div>
      </div>
    </div>
  `,
  styles: [`
    .node-detail-panel {
      position: fixed;
      right: 16px;
      top: 80px;
      width: 340px;
      background: var(--bg-card, #1e1e1e);
      border: 1px solid var(--border-color, #333);
      border-radius: 8px;
      box-shadow: 0 4px 24px rgba(0,0,0,.4);
      z-index: 100;
      font-size: 13px;
      max-height: calc(100vh - 120px);
      overflow-y: auto;
    }
    .node-detail-header {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 12px 12px 10px;
      border-bottom: 1px solid var(--border-color, #333);
      border-left: 4px solid #555;
    }
    .node-type-badge {
      color: #fff;
      padding: 2px 8px;
      border-radius: 4px;
      font-size: 11px;
      white-space: nowrap;
    }
    .node-label { flex: 1; font-weight: 600; overflow: hidden; text-overflow: ellipsis; }
    .close-btn { background: none; border: none; cursor: pointer; color: var(--text-muted, #888); font-size: 16px; padding: 0 4px; }
    .node-detail-body { padding: 12px; }
    .detail-row { display: flex; gap: 8px; margin-bottom: 8px; align-items: flex-start; }
    .detail-key { color: var(--text-muted, #888); min-width: 110px; }
    .detail-value { word-break: break-all; }
    code.detail-value { font-size: 11px; background: var(--bg-input, #2a2a2a); padding: 2px 4px; border-radius: 3px; }
    .badge { padding: 1px 8px; border-radius: 10px; font-size: 11px; }
    .badge-ok { background: #1b5e20; color: #a5d6a7; }
    .badge-warn { background: #4a1a00; color: #ffcc80; }
    .diagnostics-block { margin-bottom: 8px; }
    .diag-list { margin: 4px 0 0 16px; padding: 0; color: #ffcc80; font-size: 12px; }
    .data-section { margin-top: 8px; }
    .data-table { width: 100%; border-collapse: collapse; font-size: 12px; }
    .data-key { color: var(--text-muted, #888); padding: 2px 6px 2px 0; vertical-align: top; white-space: nowrap; }
    .data-val { word-break: break-all; }
    .tag { display: inline-block; background: var(--bg-input, #2a2a2a); border-radius: 3px; padding: 1px 5px; margin: 1px 2px; font-size: 11px; }
    .node-id-row { margin-top: 12px; }
    code.muted { font-size: 10px; color: var(--text-muted, #555); }
    .action-row { margin-top: 12px; }
  `],
})
export class ConfigGraphNodeDetailComponent {
  @Input() node: ConfigGraphNode | null = null;
  @Input() editMode = false;
  @Output() closed = new EventEmitter<void>();
  @Output() removeRequested = new EventEmitter<string>();

  readonly nodeColor = nodeColor;

  get dataEntries(): [string, unknown][] {
    if (!this.node) return [];
    return Object.entries(this.node.data).filter(([, v]) => v !== '' && v !== null && v !== undefined);
  }

  isArray(v: unknown): boolean {
    return Array.isArray(v);
  }

  asArray(v: unknown): unknown[] {
    return Array.isArray(v) ? v : [];
  }
}
