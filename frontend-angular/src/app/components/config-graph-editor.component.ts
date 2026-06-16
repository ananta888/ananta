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

interface ViewMeta {
  id: ViewId;
  label: string;
  color: string;
  description: string;
}

const NODE_W = 160;
const NODE_H = 44;
const COL_GAP = 200;
const ROW_GAP = 60;

const VIEWS: ViewMeta[] = [
  {
    id: VIEW_IDS.effectiveConfig,
    label: 'Effektive Konfiguration',
    color: '#4A90D9',
    description: 'Welche Nodes für eine Surface aktuell aktiv sind',
  },
  {
    id: VIEW_IDS.profileActivation,
    label: 'Profil-Aktivierung',
    color: '#4CAF50',
    description: 'Agenten-Profile und deren Aktivierungspfade',
  },
  {
    id: VIEW_IDS.agentRuntime,
    label: 'Agent-Laufzeit',
    color: '#9C27B0',
    description: 'Agenten-Instanzen, Worker und Laufzeit-Konfiguration',
  },
  {
    id: VIEW_IDS.policyPath,
    label: 'Policy-Pfad',
    color: '#FF9800',
    description: 'Policy-Knoten und deren Wirkungskette',
  },
  {
    id: VIEW_IDS.planningFlow,
    label: 'Planungs-Flow',
    color: '#00BCD4',
    description: 'Planung, Templates und Goal-Erstellung',
  },
  {
    id: VIEW_IDS.contextPipeline,
    label: 'Kontext-Pipeline',
    color: '#CDDC39',
    description: 'Kontext-Quellen, CodeCompass und RAG-Konfiguration',
  },
];

@Component({
  standalone: true,
  selector: 'app-config-graph-editor',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule, FormsModule, ConfigGraphNodeDetailComponent],
  template: `
    <div class="cge-root">

      <!-- ── Header ────────────────────────────────────────────── -->
      <div class="cge-header">
        <div class="cge-title-row">
          <h2 class="cge-title">Visual Agent Configuration Graph</h2>
          <div class="cge-header-actions">
            <button class="button-outline" (click)="reload()">↻ Aktualisieren</button>
            <label class="edit-toggle">
              <input type="checkbox" [(ngModel)]="editMode" (ngModelChange)="cdr.markForCheck()" />
              Edit-Modus
            </label>
          </div>
        </div>

        <div *ngIf="(graph?.diagnostics?.length ?? 0) > 0" class="diag-bar">
          <span *ngFor="let d of graph!.diagnostics" class="diag-item">⚠ {{ d }}</span>
        </div>
      </div>

      <!-- ── Body: Sidebar + Canvas ─────────────────────────────── -->
      <div class="cge-body">

        <!-- Left sidebar -->
        <div class="cge-sidebar">
          <div class="sidebar-section-label">Ansichten</div>

          <div class="view-cards">
            <button
              *ngFor="let v of views"
              class="view-card"
              [class.active]="activeView === v.id"
              (click)="setView(v.id)"
            >
              <div class="view-card-dot" [style.background]="v.color"></div>
              <div class="view-card-body">
                <div class="view-card-title">{{ v.label }}</div>
                <div class="view-card-desc">{{ v.description }}</div>
                <div class="view-card-count" *ngIf="graph">
                  <span class="count-badge" [style.background]="activeView === v.id ? v.color : undefined">
                    {{ (graph.views[v.id] ?? []).length }} Nodes
                  </span>
                </div>
              </div>
            </button>
          </div>

          <!-- Effective resolver -->
          <div class="sidebar-divider"></div>
          <div class="sidebar-section-label">Effektiv auflösen</div>
          <div class="effective-form">
            <input [(ngModel)]="effectiveSurface" placeholder="Surface (z.B. ai_snake_chat)" class="eff-input" />
            <input [(ngModel)]="effectiveTaskKind" placeholder="Task-Kind (optional)" class="eff-input" />
            <input [(ngModel)]="effectivePath" placeholder="Pfad (optional)" class="eff-input" />
            <button class="button-outline full-width" (click)="resolveEffective()">Auflösen →</button>
          </div>

          <!-- Footer stats -->
          <div class="sidebar-footer" *ngIf="graph">
            <span>{{ graph.node_count }} Nodes</span>
            <span class="dot-sep">·</span>
            <span>{{ graph.edge_count }} Edges</span>
            <span *ngIf="graph.diagnostics.length" class="warn-inline">· {{ graph.diagnostics.length }} ⚠</span>
          </div>
        </div>

        <!-- Main canvas area -->
        <div class="cge-main">

          <!-- View header bar -->
          <div class="view-header" *ngIf="activeViewMeta">
            <div class="view-header-dot" [style.background]="activeViewMeta.color"></div>
            <div class="view-header-text">
              <div class="view-header-title">{{ activeViewMeta.label }}</div>
              <div class="view-header-desc">{{ activeViewMeta.description }}</div>
            </div>
            <div class="view-header-count" *ngIf="graph">
              <span class="count-badge" [style.background]="activeViewMeta.color">
                {{ visibleNodeIds.length }} / {{ graph.node_count }} Nodes
              </span>
              <span class="muted snap-id">{{ graph.snapshot_id }}</span>
            </div>
          </div>

          <!-- Effective config result panel -->
          <div *ngIf="effectiveResult" class="effective-panel card">
            <div class="effective-panel-header">
              <strong>Effektive Konfiguration: {{ effectiveResult.surface }}</strong>
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
            <span>{{ pendingOps.length }} ausstehende Änderung(en)</span>
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

          <!-- SVG canvas -->
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
                  <path d="M0,0 L0,6 L8,3 z" fill="#666" />
                </marker>
              </defs>

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
              <p class="muted">Keine Nodes in dieser Ansicht. Ggf. Effective-Config auflösen.</p>
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
        </div>
      </div>
    </div>
  `,
  styles: [`
    /* ── Root ─────────────────────────────────────────────────── */
    .cge-root {
      display: flex;
      flex-direction: column;
      height: 100%;
      box-sizing: border-box;
      font-size: 13px;
      background: var(--bg, #111);
      color: var(--text, #ddd);
    }

    /* ── Header ───────────────────────────────────────────────── */
    .cge-header {
      padding: 12px 16px 10px;
      border-bottom: 1px solid var(--border-color, #2a2a2a);
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .cge-title-row {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .cge-title {
      margin: 0;
      font-size: 16px;
      font-weight: 600;
      flex: 1;
    }
    .cge-header-actions {
      display: flex;
      gap: 8px;
      align-items: center;
    }
    .edit-toggle {
      display: flex;
      align-items: center;
      gap: 6px;
      cursor: pointer;
      font-size: 12px;
    }

    /* ── Diag bar ─────────────────────────────────────────────── */
    .diag-bar {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      background: #2a1400;
      border-radius: 6px;
      padding: 6px 10px;
    }
    .diag-item { font-size: 12px; color: #ffcc80; }

    /* ── Body layout ─────────────────────────────────────────── */
    .cge-body {
      display: flex;
      flex: 1;
      min-height: 0;
      overflow: hidden;
    }

    /* ── Sidebar ──────────────────────────────────────────────── */
    .cge-sidebar {
      width: 230px;
      min-width: 230px;
      border-right: 1px solid var(--border-color, #2a2a2a);
      display: flex;
      flex-direction: column;
      gap: 0;
      overflow-y: auto;
      background: var(--bg-sidebar, #161616);
    }
    .sidebar-section-label {
      padding: 10px 12px 4px;
      font-size: 10px;
      font-weight: 700;
      letter-spacing: .08em;
      text-transform: uppercase;
      color: var(--text-muted, #666);
    }
    .sidebar-divider {
      border-top: 1px solid var(--border-color, #2a2a2a);
      margin: 8px 0;
    }
    .sidebar-footer {
      margin-top: auto;
      padding: 10px 12px;
      font-size: 11px;
      color: var(--text-muted, #666);
      border-top: 1px solid var(--border-color, #2a2a2a);
    }
    .dot-sep { margin: 0 3px; }

    /* ── View cards ────────────────────────────────────────────── */
    .view-cards {
      display: flex;
      flex-direction: column;
      gap: 3px;
      padding: 4px 8px;
    }
    .view-card {
      display: flex;
      align-items: flex-start;
      gap: 10px;
      padding: 9px 10px;
      border-radius: 8px;
      border: 1px solid transparent;
      background: transparent;
      cursor: pointer;
      color: var(--text, #ccc);
      text-align: left;
      width: 100%;
      transition: background .12s, border-color .12s;
    }
    .view-card:hover {
      background: var(--bg-hover, #1e1e1e);
      border-color: var(--border-color, #333);
    }
    .view-card.active {
      background: var(--bg-selected, #1a2a3a);
      border-color: #4A90D9;
    }
    .view-card-dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      flex-shrink: 0;
      margin-top: 3px;
    }
    .view-card-body { display: flex; flex-direction: column; gap: 2px; flex: 1; min-width: 0; }
    .view-card-title { font-size: 12px; font-weight: 600; line-height: 1.3; }
    .view-card-desc { font-size: 10px; color: var(--text-muted, #888); line-height: 1.4; }
    .view-card-count { margin-top: 3px; }
    .count-badge {
      display: inline-block;
      font-size: 10px;
      border-radius: 10px;
      padding: 1px 7px;
      background: var(--bg-badge, #2a2a2a);
      color: #fff;
      font-weight: 600;
      opacity: .85;
    }

    /* ── Effective resolver form ──────────────────────────────── */
    .effective-form {
      display: flex;
      flex-direction: column;
      gap: 5px;
      padding: 6px 10px 10px;
    }
    .eff-input {
      width: 100%;
      box-sizing: border-box;
      font-size: 12px;
      padding: 5px 8px;
      border-radius: 5px;
      border: 1px solid var(--border-color, #333);
      background: var(--bg-input, #1e1e1e);
      color: var(--text, #ccc);
    }
    .full-width { width: 100%; }

    /* ── Main canvas area ────────────────────────────────────── */
    .cge-main {
      flex: 1;
      display: flex;
      flex-direction: column;
      min-width: 0;
      overflow: hidden;
    }

    /* ── View header ──────────────────────────────────────────── */
    .view-header {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 10px 16px;
      border-bottom: 1px solid var(--border-color, #2a2a2a);
      background: var(--bg-header, #161616);
    }
    .view-header-dot {
      width: 12px;
      height: 12px;
      border-radius: 50%;
      flex-shrink: 0;
    }
    .view-header-text { flex: 1; }
    .view-header-title { font-size: 14px; font-weight: 600; }
    .view-header-desc { font-size: 11px; color: var(--text-muted, #888); }
    .view-header-count {
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .snap-id { font-size: 10px; font-family: monospace; color: var(--text-muted, #666); }

    /* ── Effective config result panel ────────────────────────── */
    .effective-panel {
      margin: 10px 12px 0;
      padding: 12px;
      border-radius: 8px;
      border: 1px solid var(--border-color, #333);
      background: var(--bg-card, #1a1a1a);
    }
    .effective-panel-header {
      display: flex;
      gap: 8px;
      align-items: center;
      margin-bottom: 10px;
      font-size: 13px;
    }
    .effective-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px 20px;
      font-size: 12px;
    }
    .eff-label { color: var(--text-muted, #888); font-size: 10px; margin-bottom: 2px; text-transform: uppercase; letter-spacing: .05em; }
    .badge {
      background: var(--bg-input, #2a2a2a);
      border-radius: 10px;
      padding: 1px 8px;
      font-size: 11px;
    }
    .tag {
      display: inline-block;
      border-radius: 3px;
      padding: 1px 6px;
      font-size: 11px;
      margin: 1px 2px;
    }
    .tag.warn { background: #4a1a00; color: #ffcc80; }
    .tag.ok { background: #1b3a20; color: #a5d6a7; }
    .warn-list, .trace-list { margin: 4px 0 0 16px; padding: 0; font-size: 12px; }
    .close-btn {
      background: none;
      border: none;
      cursor: pointer;
      color: var(--text-muted, #888);
      font-size: 14px;
      margin-left: auto;
    }

    /* ── Edit toolbar ─────────────────────────────────────────── */
    .edit-toolbar {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 8px 14px;
      margin: 8px 12px 0;
      border-radius: 8px;
      border: 1px solid var(--border-color, #333);
      background: var(--bg-card, #1a1a1a);
      flex-wrap: wrap;
      font-size: 12px;
    }
    .risk-badge { border-radius: 10px; padding: 2px 8px; font-size: 11px; }
    .risk-low { background: #1b3a20; color: #a5d6a7; }
    .risk-medium { background: #5a2500; color: #ffcc80; }
    .risk-high { background: #5a0000; color: #ff8a80; }
    .risk-critical { background: #3a0000; color: #ff5252; }
    .warn-inline { color: #ffcc80; font-size: 12px; }
    .edit-errors { color: #ff8a80; font-size: 12px; margin: 4px 0 0 0; padding: 0 0 0 16px; }
    button.danger { color: #ff8a80; border-color: #5a0000; }

    /* ── SVG canvas ───────────────────────────────────────────── */
    .cge-canvas-wrap {
      flex: 1;
      overflow: auto;
      margin: 10px 12px 8px;
      border: 1px solid var(--border-color, #2a2a2a);
      border-radius: 8px;
      background: var(--bg-canvas, #0e0e0e);
    }
    .cge-svg { display: block; }
    .graph-node.selected rect { stroke: #fff !important; stroke-width: 2 !important; }
    .graph-node.inactive { opacity: 0.4; }
    .graph-node.stale rect { stroke: #ff8f00 !important; stroke-width: 1.5 !important; stroke-dasharray: 4 3; }
    .edge { opacity: 0.55; }
    .empty-view {
      display: flex;
      justify-content: center;
      align-items: center;
      min-height: 300px;
    }
    .loading-wrap {
      display: flex;
      justify-content: center;
      align-items: center;
      min-height: 300px;
    }

    /* ── Utils ────────────────────────────────────────────────── */
    .muted { color: var(--text-muted, #666); }
    .card { border-radius: 8px; border: 1px solid var(--border-color, #333); background: var(--bg-card, #1a1a1a); }
    .button-outline {
      padding: 5px 12px;
      border-radius: 5px;
      border: 1px solid var(--border-color, #444);
      background: transparent;
      cursor: pointer;
      color: var(--text, #ccc);
      font-size: 12px;
    }
    .button-outline:hover { background: var(--bg-hover, #222); }
    .button-outline:disabled { opacity: .4; cursor: default; }
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

  effectiveSurface = 'ai_snake_chat';
  effectiveTaskKind = '';
  effectivePath = '';
  effectiveResult: import('../models/config-graph.model').EffectiveConfig | null = null;

  pendingOps: PatchOp[] = [];
  lastValidation: ValidationResult | null = null;

  private layoutNodes: Map<string, LayoutNode> = new Map();
  svgWidth = 1200;
  svgHeight = 800;

  get activeViewMeta(): ViewMeta | null {
    return this.views.find(v => v.id === this.activeView) ?? null;
  }

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
      const x = 24 + col * (NODE_W + COL_GAP);
      const y = 24 + row * (NODE_H + ROW_GAP);
      maxX = Math.max(maxX, x + NODE_W + 24);
      maxY = Math.max(maxY, y + NODE_H + 24);
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

  resolveEffective(): void {
    if (!this.effectiveSurface.trim()) return;
    this.svc.getEffectiveConfig({
      surface: this.effectiveSurface.trim(),
      task_kind: this.effectiveTaskKind.trim() || null,
      path: this.effectivePath.trim() || null,
    }).pipe(takeUntil(this.destroy$)).subscribe({
      next: (ec) => {
        this.effectiveResult = ec;
        if (this.graph && ec.effective_node_ids.length) {
          this.activeView = VIEW_IDS.effectiveConfig;
          this.graph.views[VIEW_IDS.effectiveConfig] = ec.effective_node_ids;
          this.computeLayout();
        }
        this.cdr.markForCheck();
      },
    });
  }

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
