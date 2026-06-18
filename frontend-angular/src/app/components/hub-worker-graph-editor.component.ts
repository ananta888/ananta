import { CommonModule } from '@angular/common';
import { ChangeDetectionStrategy, ChangeDetectorRef, Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { HubWorkerGraph, HubWorkerGraphEdge, HubWorkerGraphNode } from '../models/config-graph.model';
import { ConfigGraphService } from '../services/config-graph.service';

interface PositionedNode {
  id: string;
  x: number;
  y: number;
  r: number;
  node: HubWorkerGraphNode;
}

@Component({
  standalone: true,
  selector: 'app-hub-worker-graph-editor',
  imports: [CommonModule, FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="hwo-root">
      <header class="hwo-header">
        <div>
          <h2>Hub-/Worker-Orchestration</h2>
          <p>Hub-zentrierte Sicht auf konfigurierte Worker, Task-Routing und Runtime-Diagnosen.</p>
        </div>
        <div class="hwo-actions">
          <input [(ngModel)]="path" class="path-input" placeholder="Projektpfad filtern" (keyup.enter)="reload()" />
          <button class="btn" (click)="reload()" [disabled]="loading">Aktualisieren</button>
        </div>
      </header>

      <div *ngIf="graph?.diagnostics?.length" class="diag-row">
        <span *ngFor="let item of graph!.diagnostics">{{ item }}</span>
      </div>

      <main class="hwo-shell" *ngIf="graph && !loading">
        <section class="graph-band">
          <svg class="graph-svg" viewBox="0 0 960 620" role="img" aria-label="Hub Worker Graph">
            <defs>
              <marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto" markerUnits="strokeWidth">
                <path d="M0,0 L0,6 L9,3 z" fill="#7c91ad"></path>
              </marker>
            </defs>

            <g *ngFor="let edge of visibleEdges()">
              <line
                [attr.x1]="point(edge.source)?.x"
                [attr.y1]="point(edge.source)?.y"
                [attr.x2]="point(edge.target)?.x"
                [attr.y2]="point(edge.target)?.y"
                class="edge-line"
                marker-end="url(#arrow)"
              ></line>
              <text
                *ngIf="edge.label"
                [attr.x]="edgeLabelX(edge)"
                [attr.y]="edgeLabelY(edge)"
                class="edge-label"
              >{{ edge.label }}</text>
            </g>

            <g
              *ngFor="let item of layout"
              class="node"
              [class.selected]="selected?.id === item.node.id"
              [class.inactive]="!item.node.runtime_active"
              (click)="select(item.node)"
            >
              <circle [attr.cx]="item.x" [attr.cy]="item.y" [attr.r]="item.r" [attr.fill]="nodeFill(item.node)"></circle>
              <text [attr.x]="item.x" [attr.y]="item.y - 3" class="node-title">{{ item.node.label }}</text>
              <text [attr.x]="item.x" [attr.y]="item.y + 15" class="node-sub">{{ item.node.node_type }}</text>
            </g>
          </svg>
        </section>

        <aside class="detail-panel" *ngIf="selected">
          <div class="detail-head">
            <span class="type-pill">{{ selected.node_type }}</span>
            <button class="icon-btn" (click)="selected = null; cdr.markForCheck()" title="Schließen">x</button>
          </div>
          <h3>{{ selected.label }}</h3>
          <dl>
            <div><dt>Status</dt><dd>{{ selected.runtime_active ? 'aktiv' : 'inaktiv' }}</dd></div>
            <div><dt>Quelle</dt><dd>{{ selected.source_file || 'runtime' }}</dd></div>
            <div><dt>Pointer</dt><dd>{{ selected.source_pointer || '-' }}</dd></div>
            <div><dt>Schreibbar</dt><dd>{{ selected.writable ? 'ja' : 'readonly' }}</dd></div>
          </dl>
          <div *ngIf="selected.diagnostics.length" class="diag-box">
            <strong>Diagnosen</strong>
            <ul><li *ngFor="let item of selected.diagnostics">{{ item }}</li></ul>
          </div>
          <div class="json-block">
            <strong>Konfiguration</strong>
            <pre>{{ selected.data | json }}</pre>
          </div>
          <div *ngIf="selected.writable" class="edit-block">
            <strong>Konfiguration bearbeiten</strong>
            <textarea [(ngModel)]="editJson" class="edit-json"></textarea>
            <div *ngIf="editError" class="edit-error">{{ editError }}</div>
            <div *ngIf="lastSourceDiffs.length" class="diff-box">
              <strong>Source-Diff</strong>
              <pre *ngFor="let diff of lastSourceDiffs">{{ diff }}</pre>
            </div>
            <button class="btn" (click)="saveSelectedConfig()" [disabled]="saving">
              {{ saving ? 'Speichern...' : 'Speichern' }}
            </button>
          </div>
        </aside>

        <aside class="inventory-panel">
          <div class="metric-row">
            <div><strong>{{ graph.node_count }}</strong><span>Nodes</span></div>
            <div><strong>{{ graph.edge_count }}</strong><span>Edges</span></div>
          </div>
          <h3>Worker</h3>
          <button
            *ngFor="let worker of workers()"
            class="worker-row"
            [class.selected]="selected?.id === worker.id"
            (click)="select(worker)"
          >
            <span class="status-dot" [class.off]="!worker.runtime_active"></span>
            <span>{{ worker.label }}</span>
            <small>{{ worker.diagnostics.length ? worker.diagnostics[0] : 'konfiguriert' }}</small>
          </button>
          <h3>Task-Routing</h3>
          <div class="route-row" *ngFor="let edge of taskRoutes()">
            <span>{{ edge.data['task_kind'] || edge.label }}</span>
            <strong>{{ edge.data['preferred_worker'] || edge.label }}</strong>
          </div>
        </aside>
      </main>

      <div class="loading" *ngIf="loading">Lade Hub-/Worker-Graph...</div>
      <div class="error" *ngIf="error">{{ error }}</div>
    </div>
  `,
  styles: [`
    .hwo-root { min-height: calc(100vh - 70px); background: #0b1118; color: #d7e2ef; padding: 18px; }
    .hwo-header { display:flex; justify-content:space-between; align-items:flex-start; gap:18px; margin-bottom:14px; }
    h2 { margin:0 0 4px; font-size:22px; font-weight:650; letter-spacing:0; }
    p { margin:0; color:#8fa2b8; font-size:13px; }
    .hwo-actions { display:flex; gap:8px; align-items:center; }
    .path-input { width:260px; background:#111c28; color:#d7e2ef; border:1px solid #243449; border-radius:4px; padding:8px 10px; }
    .btn { background:#1d6fb8; border:1px solid #2c82cf; color:#fff; border-radius:4px; padding:8px 12px; cursor:pointer; }
    .btn:disabled { opacity:.6; cursor:default; }
    .diag-row { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:12px; }
    .diag-row span { background:#3a2d11; color:#ffd98a; border:1px solid #6a531f; border-radius:4px; padding:6px 8px; font-size:12px; }
    .hwo-shell { display:grid; grid-template-columns:minmax(520px,1fr) 330px 300px; gap:14px; align-items:stretch; }
    .graph-band { min-height:620px; border:1px solid #1d2d3d; background:#0f1721; border-radius:6px; overflow:hidden; }
    .graph-svg { width:100%; height:100%; min-height:620px; display:block; }
    .edge-line { stroke:#6c829c; stroke-width:1.5; opacity:.8; }
    .edge-label { fill:#9fb1c5; font-size:10px; text-anchor:middle; paint-order:stroke; stroke:#0f1721; stroke-width:4px; }
    .node { cursor:pointer; }
    .node circle { stroke:#d7e2ef; stroke-width:1.2; }
    .node.selected circle { stroke:#fff; stroke-width:3; }
    .node.inactive { opacity:.48; }
    .node-title { fill:#fff; text-anchor:middle; font-size:13px; font-weight:650; pointer-events:none; }
    .node-sub { fill:#c4d0df; text-anchor:middle; font-size:10px; pointer-events:none; }
    .detail-panel, .inventory-panel { border:1px solid #1d2d3d; background:#101923; border-radius:6px; padding:14px; min-width:0; overflow:auto; max-height:620px; }
    .detail-head { display:flex; justify-content:space-between; align-items:center; gap:8px; }
    .type-pill { background:#223347; color:#c8d8ea; border-radius:4px; padding:3px 7px; font-size:11px; }
    .icon-btn { background:transparent; border:0; color:#8fa2b8; cursor:pointer; font-size:15px; }
    h3 { margin:14px 0 10px; font-size:15px; }
    dl { display:grid; gap:8px; margin:0; }
    dl div { display:grid; grid-template-columns:82px 1fr; gap:8px; }
    dt { color:#8fa2b8; font-size:12px; }
    dd { margin:0; min-width:0; overflow-wrap:anywhere; font-size:12px; }
    .diag-box { margin-top:12px; background:#2b2110; border:1px solid #594119; padding:10px; border-radius:4px; color:#ffd98a; }
    .diag-box ul { margin:8px 0 0 16px; padding:0; }
    .json-block { margin-top:12px; }
    .edit-block { margin-top:12px; display:flex; flex-direction:column; gap:8px; }
    .edit-json { min-height:130px; resize:vertical; background:#071019; color:#d7e2ef; border:1px solid #294057; border-radius:4px; padding:10px; font-family:monospace; font-size:12px; }
    .edit-error { color:#ffb4b4; background:#331313; border:1px solid #663333; border-radius:4px; padding:8px; font-size:12px; }
    .diff-box { background:#071019; border:1px solid #294057; border-radius:4px; padding:8px; }
    pre { white-space:pre-wrap; overflow-wrap:anywhere; background:#071019; border:1px solid #1a2b3b; border-radius:4px; padding:10px; font-size:11px; color:#b8c7d8; }
    .metric-row { display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-bottom:12px; }
    .metric-row div { background:#0b141e; border:1px solid #1b2a3a; border-radius:4px; padding:10px; }
    .metric-row strong { display:block; font-size:22px; }
    .metric-row span { color:#8fa2b8; font-size:11px; }
    .worker-row { width:100%; display:grid; grid-template-columns:12px 1fr; gap:8px; align-items:center; text-align:left; background:#0b141e; color:#d7e2ef; border:1px solid #1b2a3a; border-radius:4px; padding:9px; margin-bottom:8px; cursor:pointer; }
    .worker-row.selected { border-color:#67a7e8; }
    .worker-row small { grid-column:2; color:#8fa2b8; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .status-dot { width:8px; height:8px; border-radius:999px; background:#47c278; }
    .status-dot.off { background:#8a3b3b; }
    .route-row { display:flex; justify-content:space-between; gap:10px; border-bottom:1px solid #1b2a3a; padding:8px 0; font-size:12px; }
    .route-row span { color:#aebed0; }
    .loading, .error { padding:18px; border:1px solid #1d2d3d; border-radius:6px; background:#101923; }
    .error { color:#ffb4b4; border-color:#663333; }
    @media (max-width: 1120px) {
      .hwo-shell { grid-template-columns:1fr; }
      .detail-panel, .inventory-panel { max-height:none; }
    }
  `],
})
export class HubWorkerGraphEditorComponent implements OnInit {
  private readonly graphService = inject(ConfigGraphService);
  readonly cdr = inject(ChangeDetectorRef);

  graph: HubWorkerGraph | null = null;
  layout: PositionedNode[] = [];
  selected: HubWorkerGraphNode | null = null;
  editJson = '';
  editError = '';
  lastSourceDiffs: string[] = [];
  saving = false;
  path = '';
  loading = false;
  error = '';

  ngOnInit(): void {
    this.reload();
  }

  reload(): void {
    this.loading = true;
    this.error = '';
    this.graphService.getHubWorkerGraph(this.path.trim() || null).subscribe({
      next: graph => {
        this.graph = graph;
        this.layout = this.computeLayout(graph);
        this.selected = graph.nodes['hub::ananta'] ?? null;
        this.loading = false;
        this.cdr.markForCheck();
      },
      error: err => {
        this.error = err?.error?.error || err?.message || 'Hub-/Worker-Graph konnte nicht geladen werden';
        this.loading = false;
        this.cdr.markForCheck();
      },
    });
  }

  select(node: HubWorkerGraphNode): void {
    this.selected = node;
    this.editJson = JSON.stringify(node.data['config'] ?? {}, null, 2);
    this.editError = '';
    this.lastSourceDiffs = [];
    this.cdr.markForCheck();
  }

  saveSelectedConfig(): void {
    if (!this.selected?.writable) return;
    let payload: Record<string, unknown>;
    try {
      const parsed = JSON.parse(this.editJson || '{}');
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('JSON muss ein Objekt sein');
      }
      payload = parsed as Record<string, unknown>;
    } catch (err) {
      this.editError = err instanceof Error ? err.message : 'Ungueltiges JSON';
      this.cdr.markForCheck();
      return;
    }
    this.saving = true;
    this.editError = '';
    this.graphService.updateHubWorkerConfig(
      this.selected.id,
      payload,
      this.path.trim() || null,
    ).subscribe({
      next: response => {
        this.graph = response.graph;
        this.layout = this.computeLayout(response.graph);
        const refreshed = response.graph.nodes[this.selected?.id || ''];
        this.selected = refreshed ?? null;
        this.editJson = JSON.stringify(refreshed?.data?.['config'] ?? {}, null, 2);
        const sourceDiffs = response.result['source_diffs'];
        this.lastSourceDiffs = Array.isArray(sourceDiffs)
          ? sourceDiffs
              .map(item => String((item as Record<string, unknown>)['diff'] ?? ''))
              .filter(Boolean)
          : [];
        this.saving = false;
        this.cdr.markForCheck();
      },
      error: err => {
        this.editError = err?.error?.error || err?.message || 'Speichern fehlgeschlagen';
        this.saving = false;
        this.cdr.markForCheck();
      },
    });
  }

  workers(): HubWorkerGraphNode[] {
    return Object.values(this.graph?.nodes ?? {})
      .filter(node => node.node_type === 'worker_instance')
      .sort((a, b) => a.label.localeCompare(b.label));
  }

  taskRoutes(): HubWorkerGraphEdge[] {
    return (this.graph?.edges ?? [])
      .filter(edge => edge.edge_type === 'routes_task_to_worker' && edge.source === 'hub::ananta');
  }

  visibleEdges(): HubWorkerGraphEdge[] {
    const ids = new Set(this.layout.map(item => item.id));
    return (this.graph?.edges ?? []).filter(edge => ids.has(edge.source) && ids.has(edge.target));
  }

  point(id: string): PositionedNode | undefined {
    return this.layout.find(item => item.id === id);
  }

  edgeLabelX(edge: HubWorkerGraphEdge): number {
    const source = this.point(edge.source);
    const target = this.point(edge.target);
    return source && target ? (source.x + target.x) / 2 : 0;
  }

  edgeLabelY(edge: HubWorkerGraphEdge): number {
    const source = this.point(edge.source);
    const target = this.point(edge.target);
    return source && target ? (source.y + target.y) / 2 - 6 : 0;
  }

  nodeFill(node: HubWorkerGraphNode): string {
    if (node.node_type === 'hub') return '#1f73b7';
    if (node.node_type === 'worker_instance') return node.runtime_active ? '#247a4d' : '#5b3030';
    if (node.node_type === 'task_kind') return '#7752a8';
    if (node.node_type === 'taskflow') return '#9a5a22';
    if (node.node_type === 'taskflow_step') return '#6f6a2a';
    if (node.node_type === 'fallback_chain') return '#3f6f82';
    return '#44566b';
  }

  private computeLayout(graph: HubWorkerGraph): PositionedNode[] {
    const nodes = Object.values(graph.nodes);
    const hub = graph.nodes['hub::ananta'];
    const workers = nodes.filter(node => node.node_type === 'worker_instance');
    const tasks = nodes.filter(node => node.node_type === 'task_kind');
    const flows = nodes.filter(node => node.node_type === 'taskflow');
    const steps = nodes.filter(node => node.node_type === 'taskflow_step');
    const fallbacks = nodes.filter(node => node.node_type === 'fallback_chain');
    const layout: PositionedNode[] = [];
    if (hub) {
      layout.push({ id: hub.id, x: 480, y: 300, r: 74, node: hub });
    }
    workers.forEach((node, index) => {
      const angle = (Math.PI * 2 * index) / Math.max(workers.length, 1) - Math.PI / 2;
      layout.push({
        id: node.id,
        x: 480 + Math.cos(angle) * 230,
        y: 300 + Math.sin(angle) * 210,
        r: 54,
        node,
      });
    });
    tasks.forEach((node, index) => {
      const y = 92 + index * Math.min(72, 430 / Math.max(tasks.length, 1));
      layout.push({ id: node.id, x: 85, y, r: 38, node });
    });
    fallbacks.forEach((node, index) => {
      layout.push({ id: node.id, x: 845, y: 120 + index * 90, r: 42, node });
    });
    flows.forEach((node, index) => {
      layout.push({ id: node.id, x: 835, y: 330 + index * 110, r: 42, node });
    });
    steps.forEach((node, index) => {
      layout.push({
        id: node.id,
        x: 690 + (index % 2) * 140,
        y: 405 + Math.floor(index / 2) * 75,
        r: 34,
        node,
      });
    });
    return layout;
  }
}
