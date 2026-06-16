import {
  AfterViewInit,
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  ElementRef,
  OnDestroy,
  OnInit,
  ViewChild,
  inject,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Subject, takeUntil } from 'rxjs';

import { ConfigGraphService } from '../services/config-graph.service';
import {
  ConfigGraph,
  ConfigGraphEdge,
  ConfigGraphNode,
  PatchOp,
  ValidationResult,
  VIEW_IDS,
  ViewId,
  nodeColor,
} from '../models/config-graph.model';
import { ConfigGraphNodeDetailComponent } from './config-graph-node-detail.component';

interface LayoutNode {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
  node: ConfigGraphNode;
}

const NODE_W = 160;
const NODE_H = 44;
const COL_GAP = 200;
const ROW_GAP = 60;

const VIEWS: { id: ViewId; label: string }[] = [
  { id: VIEW_IDS.effectiveConfig, label: 'Effektive Konfiguration' },
  { id: VIEW_IDS.profileActivation, label: 'Profil-Aktivierung' },
  { id: VIEW_IDS.agentRuntime, label: 'Agent-Laufzeit' },
  { id: VIEW_IDS.policyPath, label: 'Policy-Pfad' },
  { id: VIEW_IDS.planningFlow, label: 'Planungs-Flow' },
  { id: VIEW_IDS.contextPipeline, label: 'Kontext-Pipeline' },
];

@Component({
  standalone: true,
  selector: 'app-config-graph-editor',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule, FormsModule, ConfigGraphNodeDetailComponent],
  template: `
    <div class="cge-root">
      <div class="cge-header">
        <h2 class="cge-title">Visual Agent Configuration Graph</h2>

        <div class="cge-controls">
          <!-- View selector -->
          <div class="view-tabs">
            <button
              *ngFor="let v of views"
              class="view-tab"
              [class.active]="activeView === v.id"
              (click)="setView(v.id)"
            >{{ v.label }}</button>
          </div>

          <!-- Effective config resolver -->
          <div class="effective-bar">
            <input [(ngModel)]="effectiveSurface" placeholder="Surface (z.B. ai_snake_chat)" class="eff-input" />
            <input [(ngModel)]="effectiveTaskKind" placeholder="Task-Kind (optional)" class="eff-input" />
            <input [(ngModel)]="effectivePath" placeholder="Pfad (optional)" class="eff-input" />
            <button class="button-outline" (click)="resolveEffective()">Effektiv auflösen</button>
          </div>

          <div class="cge-actions">
            <button class="button-outline" (click)="reload()">Aktualisieren</button>
            <label class="edit-toggle">
              <input type="checkbox" [(ngModel)]="editMode" (ngModelChange)="cdr.markForCheck()" />
              Edit-Modus
            </label>
          </div>
        </div>
      </div>

      <!-- Diagnostics -->
      <div *ngIf="(graph?.diagnostics?.length ?? 0) > 0" class="diag-bar">
        <span *ngFor="let d of graph!.diagnostics" class="diag-item">⚠ {{ d }}</span>
      </div>

      <!-- Effective config panel -->
      <div *ngIf="effectiveResult" class="effective-panel card">
        <div class="effective-panel-header">
          <strong>Effektive Konfiguration für: {{ effectiveResult.surface }}</strong>
          <span *ngIf="effectiveResult.task_kind" class="badge">{{ effectiveResult.task_kind }}</span>
          <span *ngIf="effectiveResult.path" class="badge">{{ effectiveResult.path }}</span>
          <button (click)="effectiveResult = null; cdr.markForCheck()" class="close-btn">✕</button>
        </div>
        <div class="effective-grid">
          <div>
            <div class="eff-label">Profil</div>
            <div>{{ effectiveResult.agent_profile?.['profile_id'] ?? '—' }}</div>
          </div>
          <div>
            <div class="eff-label">Template</div>
            <div>{{ effectiveResult.goal_template?.['template_id'] ?? '—' }}</div>
          </div>
          <div>
            <div class="eff-label">Gesperrte Modi</div>
            <div>
              <span class="tag warn" *ngFor="let m of effectiveResult.effective_ai_modes_blocked">{{ m }}</span>
              <span *ngIf="!effectiveResult.effective_ai_modes_blocked.length" class="muted">keine</span>
            </div>
          </div>
          <div>
            <div class="eff-label">Erlaubte Modi</div>
            <div>
              <span class="tag ok" *ngFor="let m of effectiveResult.effective_ai_modes_allowed">{{ m }}</span>
              <span *ngIf="!effectiveResult.effective_ai_modes_allowed.length" class="muted">alle</span>
            </div>
          </div>
          <div *ngIf="effectiveResult.warnings.length">
            <div class="eff-label">Warnungen</div>
            <ul class="warn-list">
              <li *ngFor="let w of effectiveResult.warnings">{{ w }}</li>
            </ul>
          </div>
          <div>
            <div class="eff-label">Merge-Trace</div>
            <ol class="trace-list">
              <li *ngFor="let t of effectiveResult.merge_trace">{{ t['description'] }}</li>
            </ol>
          </div>
        </div>
      </div>

      <!-- Edit toolbar -->
      <div *ngIf="editMode && pendingOps.length > 0" class="edit-toolbar card">
        <span>{{ pendingOps.length }} ausstehende Änderungen</span>
        <button class="button-outline" (click)="validatePatch()">Validieren</button>
        <button
          class="button-outline"
          [disabled]="!lastValidation?.valid || lastValidation?.requires_approval"
          (click)="applyPatch()"
        >Anwenden</button>
        <button class="button-outline danger" (click)="discardPatch()">Verwerfen</button>
        <span *ngIf="lastValidation" class="risk-badge" [class]="'risk-' + lastValidation.risk_tier">
          Risiko: {{ lastValidation.risk_tier }}
        </span>
        <span *ngIf="lastValidation?.requires_approval" class="warn-inline">Genehmigung erforderlich</span>
        <ul *ngIf="lastValidation?.errors?.length" class="edit-errors">
          <li *ngFor="let e of lastValidation!.errors">{{ e }}</li>
        </ul>
      </div>

      <!-- SVG graph canvas -->
      <div class="cge-canvas-wrap" *ngIf="!loading; else loadingTpl">
        <svg
          #svgEl
          class="cge-svg"
          [attr.width]="svgWidth"
          [attr.height]="svgHeight"
          (click)="onSvgClick($event)"
        >
          <defs>
            <marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
              <path d="M0,0 L0,6 L8,3 z" fill="#555" />
            </marker>
          </defs>

          <!-- Edges -->
          <g class="edges-layer">
            <line
              *ngFor="let edge of visibleEdges"
              [attr.x1]="edgeX1(edge)"
              [attr.y1]="edgeY1(edge)"
              [attr.x2]="edgeX2(edge)"
              [attr.y2]="edgeY2(edge)"
              [class]="'edge edge-' + edge.edge_type"
              stroke="#555"
              stroke-width="1.5"
              marker-end="url(#arrow)"
            />
          </g>

          <!-- Nodes -->
          <g class="nodes-layer">
            <g
              *ngFor="let ln of visibleLayoutNodes"
              class="graph-node"
              [class.selected]="selectedNode?.id === ln.id"
              [class.stale]="ln.node.stale"
              [class.inactive]="!ln.node.runtime_active"
              (click)="selectNode($event, ln.node)"
              style="cursor: pointer;"
            >
              <rect
                [attr.x]="ln.x"
                [attr.y]="ln.y"
                [attr.width]="ln.w"
                [attr.height]="ln.h"
                rx="6"
                [attr.fill]="nodeColor(ln.node.node_type)"
                [attr.fill-opacity]="ln.node.runtime_active ? 0.85 : 0.35"
                [attr.stroke]="selectedNode?.id === ln.id ? '#fff' : 'transparent'"
                stroke-width="2"
              />
              <text
                [attr.x]="ln.x + ln.w / 2"
                [attr.y]="ln.y + ln.h / 2 - 4"
                text-anchor="middle"
                font-size="10"
                fill="#fff"
                font-weight="600"
                style="pointer-events: none;"
              >{{ ln.node.node_type }}</text>
              <text
                [attr.x]="ln.x + ln.w / 2"
                [attr.y]="ln.y + ln.h / 2 + 10"
                text-anchor="middle"
                font-size="11"
                fill="#fff"
                style="pointer-events: none; dominant-baseline: middle;"
              >{{ truncate(ln.node.label, 18) }}</text>
              <!-- Diagnostic dot -->
              <circle
                *ngIf="ln.node.diagnostics.length > 0"
                [attr.cx]="ln.x + ln.w - 6"
                [attr.cy]="ln.y + 6"
                r="5"
                fill="#ff8f00"
              />
            </g>
          </g>
        </svg>

        <div *ngIf="visibleLayoutNodes.length === 0" class="empty-view">
          <p class="muted">Keine Nodes in dieser Ansicht.</p>
        </div>
      </div>

      <ng-template #loadingTpl>
        <div class="loading-wrap">
          <p class="muted">Graph wird geladen…</p>
        </div>
      </ng-template>

      <!-- Node detail panel -->
      <app-config-graph-node-detail
        [node]="selectedNode"
        [editMode]="editMode"
        (closed)="selectedNode = null; cdr.markForCheck()"
        (removeRequested)="queueRemoveNode($event)"
      />

      <!-- Footer stats -->
      <div class="cge-footer" *ngIf="graph">
        <span>{{ graph.node_count }} Nodes · {{ graph.edge_count }} Edges</span>
        <span class="muted">Snapshot: {{ graph.snapshot_id }}</span>
        <span *ngIf="graph.diagnostics.length" class="warn-inline">{{ graph.diagnostics.length }} Diagnose(n)</span>
      </div>
    </div>
  `,
  styles: [`
    .cge-root { display: flex; flex-direction: column; gap: 12px; padding: 16px; height: 100%; box-sizing: border-box; }
    .cge-header { display: flex; flex-direction: column; gap: 10px; }
    .cge-title { margin: 0; font-size: 18px; }
    .cge-controls { display: flex; flex-direction: column; gap: 8px; }
    .view-tabs { display: flex; gap: 4px; flex-wrap: wrap; }
    .view-tab { padding: 4px 10px; border-radius: 4px; border: 1px solid var(--border-color, #444); background: transparent; cursor: pointer; font-size: 12px; color: var(--text, #ccc); }
    .view-tab.active { background: var(--primary, #4A90D9); color: #fff; border-color: transparent; }
    .effective-bar { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
    .eff-input { width: 160px; }
    .cge-actions { display: flex; gap: 8px; align-items: center; }
    .edit-toggle { display: flex; align-items: center; gap: 6px; font-size: 12px; cursor: pointer; }
    .diag-bar { display: flex; gap: 8px; flex-wrap: wrap; background: #4a1a00; border-radius: 6px; padding: 8px 12px; }
    .diag-item { font-size: 12px; color: #ffcc80; }
    .effective-panel { padding: 12px; }
    .effective-panel-header { display: flex; gap: 8px; align-items: center; margin-bottom: 10px; }
    .effective-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 20px; font-size: 13px; }
    .eff-label { color: var(--text-muted, #888); font-size: 11px; margin-bottom: 2px; }
    .badge { background: var(--bg-input, #2a2a2a); border-radius: 10px; padding: 1px 8px; font-size: 11px; }
    .tag { display: inline-block; border-radius: 3px; padding: 1px 6px; font-size: 11px; margin: 1px 2px; }
    .tag.warn { background: #4a1a00; color: #ffcc80; }
    .tag.ok { background: #1b5e20; color: #a5d6a7; }
    .warn-list, .trace-list { margin: 4px 0 0 16px; padding: 0; font-size: 12px; }
    .close-btn { background: none; border: none; cursor: pointer; color: var(--text-muted, #888); font-size: 14px; margin-left: auto; }
    .edit-toolbar { display: flex; align-items: center; gap: 10px; padding: 8px 12px; flex-wrap: wrap; }
    .risk-badge { border-radius: 10px; padding: 2px 8px; font-size: 11px; }
    .risk-low { background: #1b5e20; color: #a5d6a7; }
    .risk-medium { background: #e65100; color: #fff; }
    .risk-high { background: #b71c1c; color: #fff; }
    .risk-critical { background: #4a0000; color: #ff8a80; }
    .warn-inline { color: #ffcc80; font-size: 12px; }
    .edit-errors { color: #ff8a80; font-size: 12px; margin: 4px 0 0 0; padding: 0 0 0 16px; }
    .cge-canvas-wrap { flex: 1; overflow: auto; border: 1px solid var(--border-color, #333); border-radius: 8px; background: var(--bg-canvas, #141414); }
    .cge-svg { display: block; }
    .graph-node.selected rect { stroke-width: 2; }
    .graph-node.inactive { opacity: 0.45; }
    .graph-node.stale rect { stroke: #ff8f00 !important; stroke-width: 1.5 !important; stroke-dasharray: 4 3; }
    .edge { opacity: 0.6; }
    .empty-view { display: flex; justify-content: center; align-items: center; height: 300px; }
    .loading-wrap { display: flex; justify-content: center; align-items: center; height: 300px; }
    .cge-footer { display: flex; gap: 16px; font-size: 11px; color: var(--text-muted, #666); padding-top: 4px; }
  `],
})
export class ConfigGraphEditorComponent implements OnInit, AfterViewInit, OnDestroy {
  private readonly svc = inject(ConfigGraphService);
  readonly cdr = inject(ChangeDetectorRef);
  private readonly destroy$ = new Subject<void>();

  @ViewChild('svgEl') svgEl!: ElementRef<SVGSVGElement>;

  readonly views = VIEWS;
  readonly nodeColor = nodeColor;

  graph: ConfigGraph | null = null;
  loading = true;
  activeView: ViewId = VIEW_IDS.effectiveConfig;
  selectedNode: ConfigGraphNode | null = null;
  editMode = false;

  // Effective config resolver
  effectiveSurface = 'ai_snake_chat';
  effectiveTaskKind = '';
  effectivePath = '';
  effectiveResult: import('../models/config-graph.model').EffectiveConfig | null = null;

  // Patch queue
  pendingOps: PatchOp[] = [];
  lastValidation: ValidationResult | null = null;

  // Layout
  private layoutNodes: Map<string, LayoutNode> = new Map();
  svgWidth = 1200;
  svgHeight = 800;

  ngOnInit(): void {
    this.reload();
  }

  ngAfterViewInit(): void {}

  ngOnDestroy(): void {
    this.destroy$.next();
    this.destroy$.complete();
  }

  reload(): void {
    this.loading = true;
    this.selectedNode = null;
    this.effectiveResult = null;
    this.cdr.markForCheck();
    this.svc.getGraph().pipe(takeUntil(this.destroy$)).subscribe({
      next: (g) => {
        this.graph = g;
        this.computeLayout();
        this.loading = false;
        this.cdr.markForCheck();
      },
      error: () => {
        this.loading = false;
        this.cdr.markForCheck();
      },
    });
  }

  setView(v: ViewId): void {
    this.activeView = v;
    this.selectedNode = null;
    this.computeLayout();
    this.cdr.markForCheck();
  }

  get visibleNodeIds(): string[] {
    if (!this.graph) return [];
    const ids = this.graph.views[this.activeView] ?? [];
    return ids.filter((id) => id in this.graph!.nodes);
  }

  get visibleLayoutNodes(): LayoutNode[] {
    return this.visibleNodeIds.map((id) => this.layoutNodes.get(id)!).filter(Boolean);
  }

  get visibleEdges(): ConfigGraphEdge[] {
    if (!this.graph) return [];
    const visSet = new Set(this.visibleNodeIds);
    return this.graph.edges.filter(
      (e) => visSet.has(e.source) && visSet.has(e.target)
    );
  }

  private computeLayout(): void {
    if (!this.graph) return;
    this.layoutNodes.clear();

    const ids = this.visibleNodeIds;
    const cols = Math.max(1, Math.ceil(Math.sqrt(ids.length)));
    let maxX = 0;
    let maxY = 0;

    ids.forEach((id, i) => {
      const col = i % cols;
      const row = Math.floor(i / cols);
      const x = 20 + col * (NODE_W + COL_GAP);
      const y = 20 + row * (NODE_H + ROW_GAP);
      maxX = Math.max(maxX, x + NODE_W + 20);
      maxY = Math.max(maxY, y + NODE_H + 20);
      this.layoutNodes.set(id, {
        id,
        x,
        y,
        w: NODE_W,
        h: NODE_H,
        node: this.graph!.nodes[id],
      });
    });

    this.svgWidth = Math.max(800, maxX);
    this.svgHeight = Math.max(600, maxY);
  }

  edgeX1(edge: ConfigGraphEdge): number {
    const ln = this.layoutNodes.get(edge.source);
    return ln ? ln.x + ln.w : 0;
  }

  edgeY1(edge: ConfigGraphEdge): number {
    const ln = this.layoutNodes.get(edge.source);
    return ln ? ln.y + ln.h / 2 : 0;
  }

  edgeX2(edge: ConfigGraphEdge): number {
    const ln = this.layoutNodes.get(edge.target);
    return ln ? ln.x : 0;
  }

  edgeY2(edge: ConfigGraphEdge): number {
    const ln = this.layoutNodes.get(edge.target);
    return ln ? ln.y + ln.h / 2 : 0;
  }

  selectNode(event: MouseEvent, node: ConfigGraphNode): void {
    event.stopPropagation();
    this.selectedNode = node;
    this.cdr.markForCheck();
  }

  onSvgClick(_event: MouseEvent): void {
    this.selectedNode = null;
    this.cdr.markForCheck();
  }

  truncate(text: string, max: number): string {
    return text.length > max ? text.slice(0, max - 1) + '…' : text;
  }

  // ── Effective config ───────────────────────────────────────────────────────

  resolveEffective(): void {
    if (!this.effectiveSurface.trim()) return;
    this.svc.getEffectiveConfig({
      surface: this.effectiveSurface.trim(),
      task_kind: this.effectiveTaskKind.trim() || null,
      path: this.effectivePath.trim() || null,
    }).pipe(takeUntil(this.destroy$)).subscribe({
      next: (ec) => {
        this.effectiveResult = ec;
        // Highlight effective nodes in graph
        if (this.graph && ec.effective_node_ids.length) {
          this.activeView = VIEW_IDS.effectiveConfig;
          this.graph.views[VIEW_IDS.effectiveConfig] = ec.effective_node_ids;
          this.computeLayout();
        }
        this.cdr.markForCheck();
      },
    });
  }

  // ── Patch queue ────────────────────────────────────────────────────────────

  queueRemoveNode(nodeId: string): void {
    this.pendingOps.push({ op: 'remove_node', target: nodeId, data: {} });
    this.lastValidation = null;
    this.cdr.markForCheck();
  }

  validatePatch(): void {
    if (!this.pendingOps.length) return;
    this.svc.validatePatch(this.pendingOps).pipe(takeUntil(this.destroy$)).subscribe({
      next: (res) => {
        this.lastValidation = res;
        this.cdr.markForCheck();
      },
    });
  }

  applyPatch(): void {
    if (!this.pendingOps.length || !this.lastValidation?.valid) return;
    this.svc.applyPatch(this.pendingOps).pipe(takeUntil(this.destroy$)).subscribe({
      next: (res) => {
        this.graph = res.graph;
        this.pendingOps = [];
        this.lastValidation = null;
        this.selectedNode = null;
        this.computeLayout();
        this.cdr.markForCheck();
      },
    });
  }

  discardPatch(): void {
    this.pendingOps = [];
    this.lastValidation = null;
    this.cdr.markForCheck();
  }
}
