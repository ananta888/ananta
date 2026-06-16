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

// ── Interfaces ────────────────────────────────────────────────────────────────

interface LayoutNode { id: string; x: number; y: number; w: number; h: number; node: ConfigGraphNode; }
interface ViewMeta { id: ViewId; label: string; color: string; description: string; }
interface CloneFormField { key: string; label: string; type: 'text' | 'select'; options?: string[]; hint?: string; }
interface CloneFormState {
  sourceNode: ConfigGraphNode | null;
  entryType: 'agent_profile' | 'path_rule';
  fields: CloneFormField[];
  values: Record<string, string>;
  saving: boolean;
  error: string | null;
}
interface ConnectedNode { node: ConfigGraphNode; direction: 'out' | 'in'; edgeType: string; }

// ── Constants ─────────────────────────────────────────────────────────────────

const NODE_W = 160, NODE_H = 44, COL_GAP = 200, ROW_GAP = 60;

const VIEWS: ViewMeta[] = [
  { id: VIEW_IDS.effectiveConfig,  label: 'Effektive Konfiguration', color: '#4A90D9', description: 'Welche Nodes für eine Surface aktuell aktiv sind' },
  { id: VIEW_IDS.profileActivation, label: 'Profil-Aktivierung',     color: '#4CAF50', description: 'Agenten-Profile und deren Aktivierungspfade' },
  { id: VIEW_IDS.agentRuntime,     label: 'Agent-Laufzeit',          color: '#9C27B0', description: 'Agenten-Instanzen, Worker und Laufzeit-Konfiguration' },
  { id: VIEW_IDS.policyPath,       label: 'Policy-Pfad',             color: '#FF9800', description: 'Pfad-Regeln und KI-Modus-Einschränkungen' },
  { id: VIEW_IDS.planningFlow,     label: 'Planungs-Flow',           color: '#00BCD4', description: 'Planung, Templates und Goal-Erstellung' },
  { id: VIEW_IDS.contextPipeline,  label: 'Kontext-Pipeline',        color: '#CDDC39', description: 'Kontext-Quellen, CodeCompass und RAG-Konfiguration' },
];

const VIEW_PRIMARY_TYPES: Partial<Record<ViewId, string[]>> = {
  [VIEW_IDS.profileActivation]: ['agent_profile'],
  [VIEW_IDS.policyPath]:        ['path_rule'],
  [VIEW_IDS.planningFlow]:      ['goal_template'],
  [VIEW_IDS.agentRuntime]:      ['model_provider', 'tool_group'],
  [VIEW_IDS.contextPipeline]:   ['context_source', 'codecompass_profile', 'rag_profile', 'embedding_model', 'restricted_inference_model'],
  [VIEW_IDS.effectiveConfig]:   ['agent_profile', 'path_rule', 'goal_template', 'model_provider'],
};

const CLONEABLE = new Set(['agent_profile', 'path_rule']);

const CLONE_DEFS: Record<string, CloneFormField[]> = {
  agent_profile: [
    { key: 'profile_id',         label: 'Neue Profil-ID',             type: 'text',   hint: 'Eindeutig, nur Buchstaben/Ziffern/_/-' },
    { key: 'primary_role',       label: 'Primäre Rolle',              type: 'text',   hint: 'z.B. code_writer, planner, reviewer' },
    { key: 'activation',         label: 'Aktivierungsbedingungen',     type: 'text',   hint: 'Kommagetrennt, z.B. surface:ai_snake_chat' },
    { key: 'allowed_task_kinds', label: 'Erlaubte Task-Arten',         type: 'text',   hint: 'Kommagetrennt, z.B. code, plan, research' },
    { key: 'code_change_policy', label: 'Code-Änderungs-Policy',       type: 'select', options: ['allowed', 'review_required', 'blocked'] },
    { key: 'context_policy_hint',label: 'Kontext-Policy-Hinweis',      type: 'text' },
  ],
  path_rule: [
    { key: 'path_glob',                  label: 'Pfad-Muster (Glob)',      type: 'text',   hint: 'z.B. agent/routes/** oder src/security/**' },
    { key: 'blocked_ai_modes',           label: 'Gesperrte KI-Modi',       type: 'text',   hint: 'Kommagetrennt: full_llm, restricted, code_gen' },
    { key: 'allowed_ai_modes',           label: 'Explizit erlaubte Modi',   type: 'text',   hint: 'Leer lassen = alle erlaubt (außer gesperrte)' },
    { key: 'allow_free_text_generation', label: 'Freitext-Generierung',     type: 'select', options: ['true', 'false'] },
    { key: 'allow_code_generation',      label: 'Code-Generierung',         type: 'select', options: ['true', 'false'] },
  ],
};

// ── Component ─────────────────────────────────────────────────────────────────

@Component({
  standalone: true,
  selector: 'app-config-graph-editor',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule, FormsModule, ConfigGraphNodeDetailComponent],
  template: `
    <div class="cge-root">

      <!-- Header -->
      <div class="cge-header">
        <div class="cge-title-row">
          <h2 class="cge-title">Visual Agent Configuration Graph</h2>
          <div class="header-actions">
            <div class="mode-toggle">
              <button class="mode-btn" [class.active]="displayMode==='config'" (click)="setDisplayMode('config')">☰ Konfiguration</button>
              <button class="mode-btn" [class.active]="displayMode==='graph'"  (click)="setDisplayMode('graph')">◈ Graph</button>
            </div>
            <button class="button-outline" (click)="reload()">↻ Aktualisieren</button>
            <label *ngIf="displayMode==='graph'" class="edit-toggle">
              <input type="checkbox" [(ngModel)]="editMode" (ngModelChange)="cdr.markForCheck()" /> Edit-Modus
            </label>
          </div>
        </div>
        <div *ngIf="(graph?.diagnostics?.length ?? 0) > 0" class="diag-bar">
          <span *ngFor="let d of graph!.diagnostics" class="diag-item">⚠ {{ d }}</span>
        </div>
      </div>

      <!-- Body -->
      <div class="cge-body">

        <!-- Sidebar -->
        <div class="cge-sidebar">
          <div class="sidebar-section-label">Ansichten</div>
          <div class="view-cards">
            <button *ngFor="let v of views" class="view-card" [class.active]="activeView===v.id" (click)="setView(v.id)">
              <div class="vcard-dot" [style.background]="v.color"></div>
              <div class="vcard-body">
                <div class="vcard-title">{{ v.label }}</div>
                <div class="vcard-desc">{{ v.description }}</div>
                <span class="count-badge" [style.background]="activeView===v.id ? v.color : undefined" *ngIf="graph">
                  {{ (graph.views[v.id] ?? []).length }} Nodes
                </span>
              </div>
            </button>
          </div>
          <div class="sidebar-divider"></div>
          <div class="sidebar-section-label">Effektiv auflösen</div>
          <div class="effective-form">
            <input [(ngModel)]="effectiveSurface" placeholder="Surface (z.B. ai_snake_chat)" class="eff-input" />
            <input [(ngModel)]="effectiveTaskKind" placeholder="Task-Kind (optional)" class="eff-input" />
            <input [(ngModel)]="effectivePath" placeholder="Pfad (optional)" class="eff-input" />
            <button class="button-outline full-w" (click)="resolveEffective()">Auflösen →</button>
          </div>
          <div class="sidebar-footer" *ngIf="graph">
            <span>{{ graph.node_count }} Nodes · {{ graph.edge_count }} Edges</span>
            <span *ngIf="graph.diagnostics.length" class="warn-inline"> · {{ graph.diagnostics.length }} ⚠</span>
          </div>
        </div>

        <!-- Main -->
        <div class="cge-main">

          <!-- View header -->
          <div class="view-header" *ngIf="activeViewMeta">
            <div class="vhdot" [style.background]="activeViewMeta.color"></div>
            <div class="vh-text">
              <div class="vh-title">{{ activeViewMeta.label }}</div>
              <div class="vh-desc">{{ activeViewMeta.description }}</div>
            </div>
            <div class="vh-right" *ngIf="graph">
              <span class="count-badge" [style.background]="activeViewMeta.color">
                {{ visibleNodeIds.length }} / {{ graph.node_count }} Nodes
              </span>
              <span *ngIf="graphFilterIds" class="filter-badge" (click)="clearGraphFilter()">
                Filter aktiv ✕
              </span>
              <span class="snap-id muted">{{ graph.snapshot_id }}</span>
            </div>
          </div>

          <!-- ══════════ CONFIG MODE ══════════ -->
          <ng-container *ngIf="displayMode === 'config'">

            <!-- Effective result -->
            <div *ngIf="effectiveResult" class="effective-panel">
              <div class="ep-header">
                <strong>Effektiv: {{ effectiveResult.surface }}</strong>
                <span *ngIf="effectiveResult.task_kind" class="badge">{{ effectiveResult.task_kind }}</span>
                <span *ngIf="effectiveResult.path" class="badge">{{ effectiveResult.path }}</span>
                <button (click)="effectiveResult = null; cdr.markForCheck()" class="close-btn">✕</button>
              </div>
              <div class="ep-grid">
                <div><div class="eff-label">Profil</div>{{ effectiveResult.agent_profile?.['profile_id'] ?? '—' }}</div>
                <div><div class="eff-label">Template</div>{{ effectiveResult.goal_template?.['template_id'] ?? '—' }}</div>
                <div>
                  <div class="eff-label">Gesperrte Modi</div>
                  <span class="tag warn" *ngFor="let m of effectiveResult.effective_ai_modes_blocked">{{ m }}</span>
                  <span *ngIf="!effectiveResult.effective_ai_modes_blocked.length" class="muted">keine</span>
                </div>
                <div>
                  <div class="eff-label">Erlaubte Modi</div>
                  <span class="tag ok" *ngFor="let m of effectiveResult.effective_ai_modes_allowed">{{ m }}</span>
                  <span *ngIf="!effectiveResult.effective_ai_modes_allowed.length" class="muted">alle</span>
                </div>
                <div *ngIf="effectiveResult.warnings.length" class="ep-span2">
                  <div class="eff-label">Warnungen</div>
                  <ul class="warn-list"><li *ngFor="let w of effectiveResult.warnings">{{ w }}</li></ul>
                </div>
              </div>
            </div>

            <!-- effectiveConfig empty hint -->
            <div *ngIf="activeView === VIEW_IDS.effectiveConfig && !effectiveResult" class="config-hint">
              <div class="config-hint-icon">◈</div>
              <div>
                <strong>Effektive Konfiguration auflösen</strong>
                <p class="muted">Surface und optionalen Task-Kind in der Sidebar eingeben und "Auflösen" klicken.</p>
              </div>
            </div>

            <!-- Config panel -->
            <div *ngIf="activeView !== VIEW_IDS.effectiveConfig || effectiveResult" class="config-panel">

              <!-- Breadcrumb (detail mode) -->
              <div *ngIf="selectedConfigItem && !cloneState" class="cp-breadcrumb">
                <button class="breadcrumb-back" (click)="clearItemSelection()">← Übersicht</button>
                <span class="breadcrumb-sep">/</span>
                <span class="breadcrumb-dot" [style.background]="activeViewMeta?.color"></span>
                <span class="breadcrumb-label">{{ selectedConfigItem.label }}</span>
              </div>

              <!-- Overview header -->
              <div *ngIf="!selectedConfigItem && !cloneState" class="cp-header">
                <span class="cp-count">{{ configPanelItems.length }} {{ activeViewMeta?.label }}-Einträge</span>
                <button *ngIf="creatableTypeForView" class="button-outline" (click)="startNewEntry()">+ Neu erstellen</button>
              </div>

              <!-- Clone form header (breadcrumb variant) -->
              <div *ngIf="cloneState" class="cp-breadcrumb">
                <button class="breadcrumb-back" (click)="cancelClone()">← Zurück</button>
                <span class="breadcrumb-sep">/</span>
                <span class="breadcrumb-label">
                  {{ cloneState.sourceNode ? 'Klonen: ' + cloneState.sourceNode.label : 'Neu: ' + cloneState.entryType }}
                </span>
              </div>

              <!-- ── OVERVIEW: card grid ── -->
              <div class="config-cards" *ngIf="!selectedConfigItem && !cloneState && !loading">
                <div *ngFor="let item of configPanelItems"
                  class="config-card selectable"
                  [class.card-inactive]="!item.runtime_active"
                  [class.card-has-diags]="item.diagnostics.length > 0"
                  (click)="selectConfigItem(item)">
                  <div class="card-head">
                    <div class="card-dot" [style.background]="activeViewMeta?.color"></div>
                    <strong class="card-label" [title]="item.id">{{ item.label }}</strong>
                    <span class="card-type">{{ item.node_type }}</span>
                    <span *ngIf="!item.runtime_active" class="inactive-tag">inaktiv</span>
                  </div>
                  <div class="card-fields">
                    <div *ngFor="let f of keyFieldsFor(item)" class="card-field">
                      <span class="cf-label">{{ f.label }}</span>
                      <span class="cf-value" [class.cf-empty]="!f.value || f.value==='—'">{{ f.value }}</span>
                    </div>
                  </div>
                  <div *ngIf="item.diagnostics.length > 0" class="card-diags">
                    <span *ngFor="let d of item.diagnostics" class="diag-item">⚠ {{ d }}</span>
                  </div>
                  <div class="card-open-hint">Klicken zum Öffnen →</div>
                </div>
                <div *ngIf="configPanelItems.length === 0" class="cp-empty">
                  <p class="muted">Keine Einträge für diese Ansicht konfiguriert.</p>
                </div>
              </div>

              <div *ngIf="loading && !selectedConfigItem && !cloneState" class="loading-wrap">
                <p class="muted">Wird geladen…</p>
              </div>

              <!-- ── DETAIL VIEW ── -->
              <div class="config-detail" *ngIf="selectedConfigItem && !cloneState">
                <div class="detail-head">
                  <div class="detail-type-dot" [style.background]="activeViewMeta?.color"></div>
                  <div class="detail-head-text">
                    <h3 class="detail-title">{{ selectedConfigItem.label }}</h3>
                    <div class="detail-meta">
                      <span class="card-type">{{ selectedConfigItem.node_type }}</span>
                      <span *ngIf="!selectedConfigItem.runtime_active" class="inactive-tag">inaktiv</span>
                      <span class="detail-id muted">{{ selectedConfigItem.id }}</span>
                    </div>
                  </div>
                  <div class="detail-head-actions">
                    <button *ngIf="isCloneable(selectedConfigItem)" class="button-outline" (click)="startClone(selectedConfigItem)">
                      ⎘ Klonen & anpassen
                    </button>
                    <button class="button-outline" (click)="showInGraph(selectedConfigItem)">Im Graph zeigen</button>
                  </div>
                </div>

                <div class="detail-section">
                  <div class="section-label">Konfiguration</div>
                  <div class="detail-fields">
                    <div *ngFor="let f of allFieldsFor(selectedConfigItem)" class="detail-field">
                      <span class="df-label">{{ f.label }}</span>
                      <span class="df-value" [class.df-empty]="f.value==='—'">{{ f.value }}</span>
                    </div>
                  </div>
                </div>

                <div class="detail-section" *ngIf="connectedNodes.length > 0">
                  <div class="section-label">Verbundene Nodes ({{ connectedNodes.length }})</div>
                  <div class="connected-list">
                    <div *ngFor="let cn of connectedNodes" class="connected-node">
                      <span class="cn-dir" [class.cn-out]="cn.direction==='out'" [class.cn-in]="cn.direction==='in'">
                        {{ cn.direction === 'out' ? '→' : '←' }}
                      </span>
                      <span class="cn-edge-type">{{ cn.edgeType }}</span>
                      <div class="cn-dot" [style.background]="nodeTypeColor(cn.node.node_type)"></div>
                      <strong class="cn-label">{{ cn.node.label }}</strong>
                      <span class="card-type">{{ cn.node.node_type }}</span>
                      <button *ngIf="isPrimaryTypeInView(cn.node)" class="button-outline cn-open-btn" (click)="selectConfigItem(cn.node)">Öffnen</button>
                    </div>
                  </div>
                </div>

                <div class="detail-section" *ngIf="selectedConfigItem.diagnostics.length > 0">
                  <div class="section-label">Diagnosen</div>
                  <div class="card-diags">
                    <span *ngFor="let d of selectedConfigItem.diagnostics" class="diag-item">⚠ {{ d }}</span>
                  </div>
                </div>
              </div>

              <!-- ── CLONE / CREATE FORM ── -->
              <div *ngIf="cloneState" class="clone-form">
                <div *ngIf="cloneState.sourceNode" class="cf-source-hint">
                  Vorausgefüllt aus: <em>{{ cloneState.sourceNode.label }}</em> — Felder anpassen und speichern.
                </div>
                <div class="cf-fields">
                  <div *ngFor="let f of cloneState.fields" class="cf-field">
                    <label class="cf-field-label">
                      {{ f.label }}
                      <span *ngIf="f.key==='profile_id' || f.key==='path_glob'" class="required-mark">*</span>
                    </label>
                    <select *ngIf="f.type==='select'" [(ngModel)]="cloneState.values[f.key]" class="cf-input">
                      <option *ngFor="let o of f.options" [value]="o">{{ o }}</option>
                    </select>
                    <input *ngIf="f.type==='text'" [(ngModel)]="cloneState.values[f.key]" class="cf-input" />
                    <div *ngIf="f.hint" class="cf-hint">{{ f.hint }}</div>
                  </div>
                </div>
                <div *ngIf="cloneState.error" class="cf-error">{{ cloneState.error }}</div>
                <div class="cf-actions">
                  <button class="button-primary" (click)="saveClone()" [disabled]="cloneState.saving">
                    {{ cloneState.saving ? 'Wird gespeichert…' : 'Speichern' }}
                  </button>
                  <button class="button-outline" (click)="cancelClone()">Abbrechen</button>
                </div>
              </div>

            </div>
          </ng-container>

          <!-- ══════════ GRAPH MODE ══════════ -->
          <ng-container *ngIf="displayMode === 'graph'">
            <div *ngIf="effectiveResult" class="effective-panel">
              <div class="ep-header">
                <strong>Effektiv: {{ effectiveResult.surface }}</strong>
                <span *ngIf="effectiveResult.task_kind" class="badge">{{ effectiveResult.task_kind }}</span>
                <button (click)="effectiveResult = null; cdr.markForCheck()" class="close-btn">✕</button>
              </div>
            </div>
            <div *ngIf="editMode && pendingOps.length > 0" class="edit-toolbar">
              <span>{{ pendingOps.length }} Änderung(en)</span>
              <button class="button-outline" (click)="validatePatch()">Validieren</button>
              <button class="button-outline" [disabled]="!lastValidation?.valid || lastValidation?.requires_approval" (click)="applyPatch()">Anwenden</button>
              <button class="button-outline danger" (click)="discardPatch()">Verwerfen</button>
              <span *ngIf="lastValidation" class="risk-badge" [class]="'risk-' + lastValidation.risk_tier">{{ lastValidation.risk_tier }}</span>
              <ul *ngIf="lastValidation?.errors?.length" class="edit-errors">
                <li *ngFor="let e of lastValidation!.errors">{{ e }}</li>
              </ul>
            </div>
            <div class="cge-canvas-wrap" *ngIf="!loading; else loadingTpl">
              <svg #svgEl class="cge-svg" [attr.width]="svgWidth" [attr.height]="svgHeight" (click)="onSvgClick($event)">
                <defs>
                  <marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
                    <path d="M0,0 L0,6 L8,3 z" fill="#666" />
                  </marker>
                </defs>
                <g class="edges-layer">
                  <line *ngFor="let edge of visibleEdges"
                    [attr.x1]="edgeX1(edge)" [attr.y1]="edgeY1(edge)"
                    [attr.x2]="edgeX2(edge)" [attr.y2]="edgeY2(edge)"
                    stroke="#555" stroke-width="1.5" marker-end="url(#arrow)" />
                </g>
                <g class="nodes-layer">
                  <g *ngFor="let ln of visibleLayoutNodes"
                    class="graph-node"
                    [class.selected]="selectedNode?.id === ln.id"
                    [class.stale]="ln.node.stale"
                    [class.inactive]="!ln.node.runtime_active"
                    (click)="selectNode($event, ln.node)"
                    style="cursor:pointer">
                    <rect [attr.x]="ln.x" [attr.y]="ln.y" [attr.width]="ln.w" [attr.height]="ln.h"
                      rx="6" [attr.fill]="nodeColor(ln.node.node_type)"
                      [attr.fill-opacity]="ln.node.runtime_active ? 0.85 : 0.35"
                      [attr.stroke]="selectedNode?.id===ln.id ? '#fff' : 'transparent'" stroke-width="2" />
                    <text [attr.x]="ln.x+ln.w/2" [attr.y]="ln.y+ln.h/2-4"
                      text-anchor="middle" font-size="10" fill="#fff" font-weight="600" style="pointer-events:none">{{ ln.node.node_type }}</text>
                    <text [attr.x]="ln.x+ln.w/2" [attr.y]="ln.y+ln.h/2+10"
                      text-anchor="middle" font-size="11" fill="#fff" style="pointer-events:none;dominant-baseline:middle">{{ truncate(ln.node.label,18) }}</text>
                    <circle *ngIf="ln.node.diagnostics.length>0" [attr.cx]="ln.x+ln.w-6" [attr.cy]="ln.y+6" r="5" fill="#ff8f00" />
                  </g>
                </g>
              </svg>
              <div *ngIf="visibleLayoutNodes.length===0" class="empty-view"><p class="muted">Keine Nodes in dieser Ansicht.</p></div>
            </div>
            <ng-template #loadingTpl><div class="loading-wrap"><p class="muted">Graph wird geladen…</p></div></ng-template>
            <app-config-graph-node-detail
              [node]="selectedNode" [editMode]="editMode"
              (closed)="selectedNode=null; cdr.markForCheck()"
              (removeRequested)="queueRemoveNode($event)" />
          </ng-container>

        </div>
      </div>
    </div>
  `,
  styles: [`
    /* ── Root / Header ─────────────────────────────────────── */
    .cge-root { display:flex; flex-direction:column; height:100%; box-sizing:border-box; font-size:13px; background:var(--bg,#111); color:var(--text,#ddd); }
    .cge-header { padding:10px 16px 8px; border-bottom:1px solid var(--border-color,#2a2a2a); display:flex; flex-direction:column; gap:6px; flex-shrink:0; }
    .cge-title-row { display:flex; align-items:center; gap:12px; }
    .cge-title { margin:0; font-size:15px; font-weight:600; flex:1; }
    .header-actions { display:flex; gap:8px; align-items:center; }
    .mode-toggle { display:flex; border:1px solid var(--border-color,#444); border-radius:6px; overflow:hidden; }
    .mode-btn { padding:4px 12px; background:transparent; border:none; cursor:pointer; color:var(--text,#ccc); font-size:12px; font-weight:500; }
    .mode-btn.active { background:var(--primary,#4A90D9); color:#fff; }
    .edit-toggle { display:flex; align-items:center; gap:5px; cursor:pointer; font-size:12px; }
    .diag-bar { display:flex; gap:8px; flex-wrap:wrap; background:#2a1400; border-radius:6px; padding:6px 10px; }
    .diag-item { font-size:11px; color:#ffcc80; }

    /* ── Body / Sidebar ─────────────────────────────────────── */
    .cge-body { display:flex; flex:1; min-height:0; overflow:hidden; }
    .cge-sidebar { width:222px; min-width:222px; border-right:1px solid var(--border-color,#2a2a2a); display:flex; flex-direction:column; overflow-y:auto; background:var(--bg-sidebar,#161616); flex-shrink:0; }
    .sidebar-section-label { padding:10px 12px 3px; font-size:10px; font-weight:700; letter-spacing:.08em; text-transform:uppercase; color:var(--text-muted,#666); }
    .sidebar-divider { border-top:1px solid var(--border-color,#2a2a2a); margin:6px 0; }
    .sidebar-footer { margin-top:auto; padding:8px 12px; font-size:11px; color:var(--text-muted,#666); border-top:1px solid var(--border-color,#2a2a2a); }

    /* View cards */
    .view-cards { display:flex; flex-direction:column; gap:2px; padding:4px 7px; }
    .view-card { display:flex; align-items:flex-start; gap:9px; padding:8px 9px; border-radius:7px; border:1px solid transparent; background:transparent; cursor:pointer; color:var(--text,#ccc); text-align:left; width:100%; transition:background .1s; }
    .view-card:hover { background:var(--bg-hover,#1e1e1e); }
    .view-card.active { background:var(--bg-selected,#1a2a3a); border-color:#4A90D9; }
    .vcard-dot { width:9px; height:9px; border-radius:50%; flex-shrink:0; margin-top:3px; }
    .vcard-body { display:flex; flex-direction:column; gap:2px; flex:1; min-width:0; }
    .vcard-title { font-size:12px; font-weight:600; line-height:1.3; }
    .vcard-desc { font-size:10px; color:var(--text-muted,#888); line-height:1.3; }
    .count-badge { display:inline-block; font-size:10px; border-radius:8px; padding:1px 6px; background:var(--bg-badge,#2a2a2a); color:#fff; font-weight:600; margin-top:3px; opacity:.85; }

    /* Effective form */
    .effective-form { display:flex; flex-direction:column; gap:5px; padding:5px 9px 8px; }
    .eff-input { width:100%; box-sizing:border-box; font-size:12px; padding:5px 8px; border-radius:5px; border:1px solid var(--border-color,#333); background:var(--bg-input,#1e1e1e); color:var(--text,#ccc); }
    .full-w { width:100%; }

    /* ── Main ─────────────────────────────────────────────────── */
    .cge-main { flex:1; display:flex; flex-direction:column; min-width:0; overflow:hidden; }

    /* View header */
    .view-header { display:flex; align-items:center; gap:10px; padding:9px 14px; border-bottom:1px solid var(--border-color,#2a2a2a); background:var(--bg-sidebar,#161616); flex-shrink:0; }
    .vhdot { width:11px; height:11px; border-radius:50%; flex-shrink:0; }
    .vh-text { flex:1; }
    .vh-title { font-size:13px; font-weight:600; }
    .vh-desc { font-size:11px; color:var(--text-muted,#888); }
    .vh-right { display:flex; align-items:center; gap:8px; }
    .filter-badge { font-size:11px; background:#3a2800; color:#ffcc80; border-radius:8px; padding:2px 8px; cursor:pointer; }
    .snap-id { font-size:10px; font-family:monospace; }

    /* Effective panel */
    .effective-panel { margin:10px 14px 0; padding:12px; border-radius:8px; border:1px solid var(--border-color,#333); background:var(--bg-card,#1a1a1a); flex-shrink:0; }
    .ep-header { display:flex; gap:8px; align-items:center; margin-bottom:8px; font-size:13px; }
    .ep-grid { display:grid; grid-template-columns:1fr 1fr; gap:8px 16px; font-size:12px; }
    .ep-span2 { grid-column:span 2; }
    .eff-label { font-size:10px; color:var(--text-muted,#888); text-transform:uppercase; letter-spacing:.05em; margin-bottom:2px; }
    .badge { background:var(--bg-input,#2a2a2a); border-radius:8px; padding:1px 7px; font-size:11px; }
    .tag { display:inline-block; border-radius:3px; padding:1px 5px; font-size:11px; margin:1px; }
    .tag.warn { background:#4a1a00; color:#ffcc80; }
    .tag.ok { background:#1b3a20; color:#a5d6a7; }
    .warn-list { margin:4px 0 0 16px; padding:0; font-size:12px; }
    .close-btn { background:none; border:none; cursor:pointer; color:var(--text-muted,#888); font-size:14px; margin-left:auto; padding:0 4px; }

    /* Config hint */
    .config-hint { display:flex; gap:16px; align-items:flex-start; margin:24px 16px; padding:18px 20px; border-radius:10px; border:1px dashed var(--border-color,#333); background:var(--bg-card,#1a1a1a); }
    .config-hint-icon { font-size:28px; color:var(--text-muted,#555); flex-shrink:0; margin-top:2px; }
    .config-hint p { margin:4px 0 0; }

    /* ── Config panel ─────────────────────────────────────────── */
    .config-panel { display:flex; flex-direction:column; flex:1; overflow:hidden; }

    /* Breadcrumb */
    .cp-breadcrumb { display:flex; align-items:center; gap:8px; padding:9px 14px; border-bottom:1px solid var(--border-color,#2a2a2a); background:var(--bg-sidebar,#161616); flex-shrink:0; font-size:12px; }
    .breadcrumb-back { background:none; border:none; cursor:pointer; color:var(--primary,#4A90D9); font-size:12px; padding:0; }
    .breadcrumb-back:hover { text-decoration:underline; }
    .breadcrumb-sep { color:var(--text-muted,#555); }
    .breadcrumb-dot { width:8px; height:8px; border-radius:50%; flex-shrink:0; }
    .breadcrumb-label { font-weight:600; color:var(--text,#ddd); }

    /* Overview header */
    .cp-header { display:flex; align-items:center; justify-content:space-between; padding:10px 14px 6px; flex-shrink:0; }
    .cp-count { font-size:12px; color:var(--text-muted,#888); }
    .cp-empty { padding:24px 14px; text-align:center; }

    /* Card grid */
    .config-cards { flex:1; overflow-y:auto; padding:8px 12px 12px; display:flex; flex-direction:column; gap:6px; }
    .config-card { border-radius:9px; border:1px solid var(--border-color,#2c2c2c); background:var(--bg-card,#1a1a1a); padding:11px 13px; transition:border-color .12s, background .12s; }
    .config-card.selectable { cursor:pointer; }
    .config-card.selectable:hover { border-color:var(--primary,#4A90D9); background:var(--bg-hover,#1e1e1e); }
    .config-card.card-inactive { opacity:.55; }
    .config-card.card-has-diags { border-color:#5a3500; }
    .card-head { display:flex; align-items:center; gap:8px; margin-bottom:8px; }
    .card-dot { width:9px; height:9px; border-radius:50%; flex-shrink:0; }
    .card-label { font-size:13px; flex:1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .card-type { font-size:10px; background:var(--bg-input,#252525); border-radius:4px; padding:1px 6px; color:var(--text-muted,#888); white-space:nowrap; flex-shrink:0; }
    .inactive-tag { font-size:10px; background:#3a1a00; color:#ff8f00; border-radius:4px; padding:1px 6px; flex-shrink:0; }
    .card-fields { display:grid; grid-template-columns:1fr 1fr; gap:4px 12px; margin-bottom:6px; }
    .card-field { display:flex; flex-direction:column; gap:1px; }
    .cf-label { font-size:10px; color:var(--text-muted,#777); text-transform:uppercase; letter-spacing:.04em; }
    .cf-value { font-size:12px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .cf-value.cf-empty { color:var(--text-muted,#555); }
    .card-diags { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:6px; }
    .card-open-hint { font-size:10px; color:var(--text-muted,#555); text-align:right; margin-top:2px; }

    /* ── Detail view ─────────────────────────────────────────── */
    .config-detail { flex:1; overflow-y:auto; padding:14px; display:flex; flex-direction:column; gap:14px; }
    .detail-head { display:flex; align-items:flex-start; gap:12px; padding-bottom:12px; border-bottom:1px solid var(--border-color,#2a2a2a); }
    .detail-type-dot { width:14px; height:14px; border-radius:50%; flex-shrink:0; margin-top:4px; }
    .detail-head-text { flex:1; }
    .detail-title { margin:0 0 4px; font-size:16px; font-weight:700; }
    .detail-meta { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
    .detail-id { font-size:10px; font-family:monospace; }
    .detail-head-actions { display:flex; gap:6px; flex-wrap:wrap; align-items:flex-start; }

    .detail-section { display:flex; flex-direction:column; gap:8px; }
    .section-label { font-size:10px; font-weight:700; text-transform:uppercase; letter-spacing:.08em; color:var(--text-muted,#666); padding-bottom:4px; border-bottom:1px solid var(--border-color,#222); }
    .detail-fields { display:grid; grid-template-columns:max-content 1fr; gap:6px 20px; font-size:12px; }
    .detail-field { display:contents; }
    .df-label { color:var(--text-muted,#888); font-size:11px; align-self:start; padding-top:1px; white-space:nowrap; }
    .df-value { word-break:break-word; }
    .df-value.df-empty { color:var(--text-muted,#555); }

    /* Connected nodes */
    .connected-list { display:flex; flex-direction:column; gap:5px; }
    .connected-node { display:flex; align-items:center; gap:8px; padding:6px 10px; border-radius:6px; background:var(--bg-card,#1a1a1a); border:1px solid var(--border-color,#2a2a2a); font-size:12px; }
    .cn-dir { width:16px; text-align:center; font-size:14px; font-weight:700; flex-shrink:0; }
    .cn-dir.cn-out { color:#4A90D9; }
    .cn-dir.cn-in  { color:#9C27B0; }
    .cn-edge-type { font-size:10px; background:var(--bg-input,#252525); border-radius:4px; padding:1px 5px; color:var(--text-muted,#888); white-space:nowrap; }
    .cn-dot { width:8px; height:8px; border-radius:50%; flex-shrink:0; }
    .cn-label { flex:1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .cn-open-btn { font-size:11px; padding:2px 8px; flex-shrink:0; }

    /* ── Clone form ──────────────────────────────────────────── */
    .clone-form { padding:14px; display:flex; flex-direction:column; gap:10px; overflow-y:auto; flex:1; }
    .cf-source-hint { font-size:11px; color:var(--text-muted,#888); padding:7px 10px; background:var(--bg-input,#1e1e1e); border-radius:5px; }
    .cf-fields { display:grid; grid-template-columns:1fr 1fr; gap:10px 16px; }
    .cf-field { display:flex; flex-direction:column; gap:4px; }
    .cf-field-label { font-size:11px; font-weight:600; color:var(--text-muted,#aaa); }
    .required-mark { color:#ff8f00; margin-left:2px; }
    .cf-input { padding:6px 8px; border-radius:5px; border:1px solid var(--border-color,#333); background:var(--bg-input,#1e1e1e); color:var(--text,#ddd); font-size:12px; width:100%; box-sizing:border-box; }
    .cf-hint { font-size:10px; color:var(--text-muted,#666); }
    .cf-error { color:#ff8a80; font-size:12px; padding:6px 10px; background:#2a0000; border-radius:5px; }
    .cf-actions { display:flex; gap:8px; }
    .button-primary { padding:6px 16px; border-radius:5px; border:none; background:var(--primary,#4A90D9); color:#fff; font-size:12px; cursor:pointer; font-weight:600; }
    .button-primary:hover { background:#3a7fc9; }
    .button-primary:disabled { opacity:.4; cursor:default; }

    /* ── Graph mode ──────────────────────────────────────────── */
    .cge-canvas-wrap { flex:1; overflow:auto; margin:10px 12px 8px; border:1px solid var(--border-color,#2a2a2a); border-radius:8px; background:var(--bg-canvas,#0e0e0e); }
    .cge-svg { display:block; }
    .graph-node.selected rect { stroke:#fff !important; stroke-width:2 !important; }
    .graph-node.inactive { opacity:.4; }
    .graph-node.stale rect { stroke:#ff8f00 !important; stroke-width:1.5 !important; stroke-dasharray:4 3; }
    .empty-view, .loading-wrap { display:flex; justify-content:center; align-items:center; min-height:280px; }
    .edit-toolbar { display:flex; align-items:center; gap:10px; padding:7px 14px; margin:8px 12px 0; border-radius:7px; border:1px solid var(--border-color,#333); background:var(--bg-card,#1a1a1a); flex-wrap:wrap; font-size:12px; flex-shrink:0; }
    .risk-badge { border-radius:8px; padding:2px 7px; font-size:11px; }
    .risk-low { background:#1b3a20; color:#a5d6a7; }
    .risk-medium { background:#5a2500; color:#ffcc80; }
    .risk-high, .risk-critical { background:#5a0000; color:#ff8a80; }
    .warn-inline { color:#ffcc80; font-size:12px; }
    .edit-errors { color:#ff8a80; font-size:12px; margin:0; padding:0 0 0 14px; }
    button.danger { color:#ff8a80; }

    /* ── Shared utils ─────────────────────────────────────────── */
    .muted { color:var(--text-muted,#666); }
    .button-outline { padding:5px 11px; border-radius:5px; border:1px solid var(--border-color,#444); background:transparent; cursor:pointer; color:var(--text,#ccc); font-size:12px; }
    .button-outline:hover { background:var(--bg-hover,#222); }
    .button-outline:disabled { opacity:.4; cursor:default; }
  `],
})
export class ConfigGraphEditorComponent implements OnInit, AfterViewInit, OnDestroy {
  private readonly svc = inject(ConfigGraphService);
  readonly cdr = inject(ChangeDetectorRef);
  private readonly destroy$ = new Subject<void>();

  @ViewChild('svgEl') svgEl!: ElementRef<SVGSVGElement>;

  readonly views = VIEWS;
  readonly VIEW_IDS = VIEW_IDS;
  readonly nodeColor = nodeColor;

  graph: ConfigGraph | null = null;
  loading = true;
  activeView: ViewId = VIEW_IDS.profileActivation;
  selectedNode: ConfigGraphNode | null = null;
  selectedConfigItem: ConfigGraphNode | null = null;
  displayMode: 'config' | 'graph' = 'config';
  editMode = false;
  graphFilterIds: string[] | null = null;

  effectiveSurface = 'ai_snake_chat';
  effectiveTaskKind = '';
  effectivePath = '';
  effectiveResult: import('../models/config-graph.model').EffectiveConfig | null = null;

  pendingOps: PatchOp[] = [];
  lastValidation: ValidationResult | null = null;
  cloneState: CloneFormState | null = null;

  private layoutNodes: Map<string, LayoutNode> = new Map();
  svgWidth = 1200;
  svgHeight = 800;

  // ── Getters ────────────────────────────────────────────────────────────────

  get activeViewMeta(): ViewMeta | null {
    return VIEWS.find(v => v.id === this.activeView) ?? null;
  }

  get visibleNodeIds(): string[] {
    if (!this.graph) return [];
    const all = (this.graph.views[this.activeView] ?? []).filter(id => id in this.graph!.nodes);
    if (!this.graphFilterIds) return all;
    const fs = new Set(this.graphFilterIds);
    return all.filter(id => fs.has(id));
  }

  get visibleLayoutNodes(): LayoutNode[] {
    return this.visibleNodeIds.map(id => this.layoutNodes.get(id)!).filter(Boolean);
  }

  get visibleEdges(): ConfigGraphEdge[] {
    if (!this.graph) return [];
    const vis = new Set(this.visibleNodeIds);
    return this.graph.edges.filter(e => vis.has(e.source) && vis.has(e.target));
  }

  get configPanelItems(): ConfigGraphNode[] {
    if (!this.graph) return [];
    const primaryTypes = VIEW_PRIMARY_TYPES[this.activeView] ?? [];
    return (this.graph.views[this.activeView] ?? [])
      .filter(id => id in this.graph!.nodes)
      .map(id => this.graph!.nodes[id])
      .filter(n => primaryTypes.includes(n.node_type));
  }

  get creatableTypeForView(): 'agent_profile' | 'path_rule' | null {
    const types = VIEW_PRIMARY_TYPES[this.activeView] ?? [];
    if (types.includes('agent_profile')) return 'agent_profile';
    if (types.includes('path_rule')) return 'path_rule';
    return null;
  }

  get connectedNodes(): ConnectedNode[] {
    if (!this.graph || !this.selectedConfigItem) return [];
    const nid = this.selectedConfigItem.id;
    const seen = new Set<string>();
    const result: ConnectedNode[] = [];
    for (const e of this.graph.edges) {
      if (e.source === nid && e.target in this.graph.nodes && !seen.has(e.target)) {
        seen.add(e.target);
        result.push({ node: this.graph.nodes[e.target], direction: 'out', edgeType: e.edge_type });
      }
      if (e.target === nid && e.source in this.graph.nodes && !seen.has(e.source)) {
        seen.add(e.source);
        result.push({ node: this.graph.nodes[e.source], direction: 'in', edgeType: e.edge_type });
      }
    }
    return result;
  }

  // ── Lifecycle ──────────────────────────────────────────────────────────────

  ngOnInit(): void { this.reload(); }
  ngAfterViewInit(): void {}
  ngOnDestroy(): void { this.destroy$.next(); this.destroy$.complete(); }

  // ── Navigation ─────────────────────────────────────────────────────────────

  reload(): void {
    this.loading = true;
    this.selectedNode = null;
    this.selectedConfigItem = null;
    this.effectiveResult = null;
    this.cloneState = null;
    this.graphFilterIds = null;
    this.cdr.markForCheck();
    this.svc.getGraph().pipe(takeUntil(this.destroy$)).subscribe({
      next: g => { this.graph = g; this.computeLayout(); this.loading = false; this.cdr.markForCheck(); },
      error: () => { this.loading = false; this.cdr.markForCheck(); },
    });
  }

  setView(v: ViewId): void {
    this.activeView = v;
    this.selectedNode = null;
    this.selectedConfigItem = null;
    this.cloneState = null;
    this.graphFilterIds = null;
    this.computeLayout();
    this.cdr.markForCheck();
  }

  setDisplayMode(m: 'config' | 'graph'): void {
    this.displayMode = m;
    if (m === 'config') { this.graphFilterIds = null; }
    else { this.computeLayout(); }
    this.cdr.markForCheck();
  }

  clearGraphFilter(): void {
    this.graphFilterIds = null;
    this.computeLayout();
    this.cdr.markForCheck();
  }

  // ── Config item selection ──────────────────────────────────────────────────

  selectConfigItem(node: ConfigGraphNode): void {
    this.selectedConfigItem = node;
    this.cloneState = null;
    this.cdr.markForCheck();
  }

  clearItemSelection(): void {
    this.selectedConfigItem = null;
    this.cloneState = null;
    this.cdr.markForCheck();
  }

  showInGraph(node: ConfigGraphNode): void {
    this.displayMode = 'graph';
    this.selectedNode = node;
    // Find the best view that contains this node
    for (const v of VIEWS) {
      if ((this.graph?.views[v.id] ?? []).includes(node.id)) {
        this.activeView = v.id;
        break;
      }
    }
    // Filter to node + direct neighbors
    const neighbors = this.getNeighborIds(node.id);
    this.graphFilterIds = [node.id, ...neighbors];
    this.computeLayout();
    this.cdr.markForCheck();
  }

  isPrimaryTypeInView(node: ConfigGraphNode): boolean {
    const types = VIEW_PRIMARY_TYPES[this.activeView] ?? [];
    return types.includes(node.node_type);
  }

  // ── Field helpers ──────────────────────────────────────────────────────────

  keyFieldsFor(node: ConfigGraphNode): { label: string; value: string }[] {
    const d = node.data as Record<string, unknown>;
    const arr = (k: string) => (Array.isArray(d[k]) ? (d[k] as string[]).join(', ') : '') || '—';
    const str = (k: string) => String(d[k] ?? '') || '—';
    switch (node.node_type) {
      case 'agent_profile': return [
        { label: 'Rolle', value: str('primary_role') },
        { label: 'Aktivierung', value: arr('activation') },
        { label: 'Task-Arten', value: arr('allowed_task_kinds') },
        { label: 'Code-Policy', value: str('code_change_policy') },
      ];
      case 'path_rule': return [
        { label: 'Muster', value: str('path_glob') },
        { label: 'Gesperrt', value: arr('blocked_ai_modes') },
        { label: 'Erlaubt', value: arr('allowed_ai_modes') },
      ];
      case 'goal_template': return [{ label: 'Beschreibung', value: str('description') }];
      case 'model_provider': return [{ label: 'Backend', value: str('backend') }];
      case 'tool_group': return [{ label: 'Gruppe', value: str('group') }];
      case 'embedding_model': return [{ label: 'Provider', value: str('provider') }];
      default: return Object.entries(d).slice(0, 3).map(([k, v]) => ({ label: k, value: Array.isArray(v) ? (v as string[]).join(', ') : String(v ?? '—') }));
    }
  }

  allFieldsFor(node: ConfigGraphNode): { label: string; value: string }[] {
    const fmt = (v: unknown): string => {
      if (Array.isArray(v)) return (v as string[]).join(', ') || '—';
      if (v == null || v === '') return '—';
      return String(v);
    };
    return Object.entries(node.data as Record<string, unknown>).map(([k, v]) => ({ label: k, value: fmt(v) }));
  }

  nodeTypeColor(nodeType: string): string {
    return nodeColor(nodeType);
  }

  isCloneable(node: ConfigGraphNode): boolean {
    return CLONEABLE.has(node.node_type);
  }

  // ── Clone / Create ─────────────────────────────────────────────────────────

  startClone(source: ConfigGraphNode): void {
    const entryType = source.node_type as 'agent_profile' | 'path_rule';
    const fields = CLONE_DEFS[entryType] ?? [];
    const values: Record<string, string> = {};
    const d = source.data as Record<string, unknown>;
    for (const f of fields) {
      const raw = d[f.key];
      values[f.key] = Array.isArray(raw) ? (raw as string[]).join(', ') : String(raw ?? '');
    }
    if (entryType === 'agent_profile') values['profile_id'] = '';
    if (entryType === 'path_rule') values['path_glob'] = '';
    this.cloneState = { sourceNode: source, entryType, fields, values, saving: false, error: null };
    this.cdr.markForCheck();
  }

  startNewEntry(): void {
    const entryType = this.creatableTypeForView;
    if (!entryType) return;
    const fields = CLONE_DEFS[entryType] ?? [];
    const values: Record<string, string> = {};
    for (const f of fields) values[f.key] = f.type === 'select' && f.options?.length ? f.options[0] : '';
    this.cloneState = { sourceNode: null, entryType, fields, values, saving: false, error: null };
    this.cdr.markForCheck();
  }

  saveClone(): void {
    if (!this.cloneState) return;
    const { entryType, values } = this.cloneState;
    const data: Record<string, unknown> = { ...values };
    for (const k of ['activation', 'allowed_task_kinds', 'blocked_ai_modes', 'allowed_ai_modes']) {
      data[k] = String(data[k] ?? '').split(',').map((s: string) => s.trim()).filter(Boolean);
    }
    for (const k of ['allow_free_text_generation', 'allow_code_generation']) {
      data[k] = data[k] !== 'false';
    }
    this.cloneState.saving = true;
    this.cloneState.error = null;
    this.cdr.markForCheck();
    this.svc.createConfigEntry(entryType, data).pipe(takeUntil(this.destroy$)).subscribe({
      next: g => {
        this.graph = g;
        this.cloneState = null;
        this.selectedConfigItem = null;
        this.computeLayout();
        this.cdr.markForCheck();
      },
      error: e => {
        if (this.cloneState) { this.cloneState.error = e?.error?.error ?? 'Speichern fehlgeschlagen'; this.cloneState.saving = false; }
        this.cdr.markForCheck();
      },
    });
  }

  cancelClone(): void { this.cloneState = null; this.cdr.markForCheck(); }

  // ── Effective config ───────────────────────────────────────────────────────

  resolveEffective(): void {
    if (!this.effectiveSurface.trim()) return;
    this.svc.getEffectiveConfig({
      surface: this.effectiveSurface.trim(),
      task_kind: this.effectiveTaskKind.trim() || null,
      path: this.effectivePath.trim() || null,
    }).pipe(takeUntil(this.destroy$)).subscribe({
      next: ec => {
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

  // ── Patch ──────────────────────────────────────────────────────────────────

  queueRemoveNode(nodeId: string): void { this.pendingOps.push({ op: 'remove_node', target: nodeId, data: {} }); this.lastValidation = null; this.cdr.markForCheck(); }

  validatePatch(): void {
    if (!this.pendingOps.length) return;
    this.svc.validatePatch(this.pendingOps).pipe(takeUntil(this.destroy$)).subscribe({
      next: r => { this.lastValidation = r; this.cdr.markForCheck(); },
    });
  }

  applyPatch(): void {
    if (!this.pendingOps.length || !this.lastValidation?.valid) return;
    this.svc.applyPatch(this.pendingOps).pipe(takeUntil(this.destroy$)).subscribe({
      next: r => { this.graph = r.graph; this.pendingOps = []; this.lastValidation = null; this.selectedNode = null; this.computeLayout(); this.cdr.markForCheck(); },
    });
  }

  discardPatch(): void { this.pendingOps = []; this.lastValidation = null; this.cdr.markForCheck(); }

  // ── SVG / Layout ───────────────────────────────────────────────────────────

  private computeLayout(): void {
    if (!this.graph) return;
    this.layoutNodes.clear();
    const ids = this.visibleNodeIds;
    const cols = Math.max(1, Math.ceil(Math.sqrt(ids.length)));
    let maxX = 0, maxY = 0;
    ids.forEach((id, i) => {
      const col = i % cols, row = Math.floor(i / cols);
      const x = 24 + col * (NODE_W + COL_GAP), y = 24 + row * (NODE_H + ROW_GAP);
      maxX = Math.max(maxX, x + NODE_W + 24); maxY = Math.max(maxY, y + NODE_H + 24);
      this.layoutNodes.set(id, { id, x, y, w: NODE_W, h: NODE_H, node: this.graph!.nodes[id] });
    });
    this.svgWidth = Math.max(800, maxX);
    this.svgHeight = Math.max(600, maxY);
  }

  private getNeighborIds(nodeId: string): string[] {
    if (!this.graph) return [];
    const neighbors = new Set<string>();
    for (const e of this.graph.edges) {
      if (e.source === nodeId && e.target in this.graph.nodes) neighbors.add(e.target);
      if (e.target === nodeId && e.source in this.graph.nodes) neighbors.add(e.source);
    }
    return Array.from(neighbors);
  }

  edgeX1(e: ConfigGraphEdge): number { return (this.layoutNodes.get(e.source)?.x ?? 0) + NODE_W; }
  edgeY1(e: ConfigGraphEdge): number { const ln = this.layoutNodes.get(e.source); return ln ? ln.y + NODE_H / 2 : 0; }
  edgeX2(e: ConfigGraphEdge): number { return this.layoutNodes.get(e.target)?.x ?? 0; }
  edgeY2(e: ConfigGraphEdge): number { const ln = this.layoutNodes.get(e.target); return ln ? ln.y + NODE_H / 2 : 0; }

  selectNode(event: MouseEvent, node: ConfigGraphNode): void { event.stopPropagation(); this.selectedNode = node; this.cdr.markForCheck(); }
  onSvgClick(_: MouseEvent): void { this.selectedNode = null; this.cdr.markForCheck(); }
  truncate(text: string, max: number): string { return text.length > max ? text.slice(0, max - 1) + '…' : text; }
}
