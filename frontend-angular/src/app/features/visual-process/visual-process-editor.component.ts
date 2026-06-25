import {
  Component, OnInit, OnDestroy, inject,
  signal, computed, HostListener, ViewChild, ElementRef,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Subscription } from 'rxjs';
import {
  VisualProcessApiService,
  VpGraph, VpStep, VpEdge, ArtifactRef,
  ValidationResult, DryRunResult, SkillProfile, PresetSummary,
  TaskKindInfo, SavedGraphSummary, WorkflowStatus, StepExecutionPlan,
} from './visual-process-api.service';

// ── constants ─────────────────────────────────────────────────────────────────
const NODE_W = 140;
const NODE_H = 52;
const FALLBACK_KINDS: TaskKindInfo[] = [
  { id: 'patch_propose',   label: 'Patch Vorschlagen',    group: 'worker',       dispatch_capable: true,  description: '', implementation_status: 'production', implementation_state: 'wired_and_executable', risk_level: 'low',    uses_llm: true,  uses_network: false, side_effects: [] },
  { id: 'plan_only',       label: 'Planen (LLM)',          group: 'worker',       dispatch_capable: true,  description: '', implementation_status: 'production', implementation_state: 'wired_and_executable', risk_level: 'low',    uses_llm: true,  uses_network: false, side_effects: [] },
  { id: 'review',          label: 'Review (LLM)',           group: 'worker',       dispatch_capable: true,  description: '', implementation_status: 'production', implementation_state: 'wired_and_executable', risk_level: 'none',   uses_llm: true,  uses_network: false, side_effects: [] },
  { id: 'run_tests',       label: 'Tests Ausführen',        group: 'worker',       dispatch_capable: true,  description: '', implementation_status: 'production', implementation_state: 'wired_and_executable', risk_level: 'low',    uses_llm: false, uses_network: false, side_effects: ['shell_execution'] },
  { id: 'shell_execute',   label: 'Shell Ausführen',        group: 'worker',       dispatch_capable: true,  description: '', implementation_status: 'production', implementation_state: 'wired_and_executable', risk_level: 'high',   uses_llm: false, uses_network: false, side_effects: ['shell_execution'] },
  { id: 'workspace_snapshot', label: 'Workspace Snapshot', group: 'worker',       dispatch_capable: true,  description: '', implementation_status: 'production', implementation_state: 'wired_and_executable', risk_level: 'none',   uses_llm: false, uses_network: false, side_effects: ['read_workspace'] },
  { id: 'workspace_diff',    label: 'Workspace Diff',      group: 'worker',       dispatch_capable: true,  description: '', implementation_status: 'production', implementation_state: 'wired_and_executable', risk_level: 'low',    uses_llm: false, uses_network: false, side_effects: ['read_workspace', 'write_manifest'] },
  { id: 'fork',            label: 'Fork (Parallel)',        group: 'control_flow', dispatch_capable: true,  description: '', implementation_status: 'production', implementation_state: 'wired_and_executable', risk_level: 'low',    uses_llm: false, uses_network: false, side_effects: [] },
  { id: 'join',            label: 'Join (Sync)',            group: 'control_flow', dispatch_capable: true,  description: '', implementation_status: 'production', implementation_state: 'wired_and_executable', risk_level: 'low',    uses_llm: false, uses_network: false, side_effects: [] },
  { id: 'approval',        label: 'Approval Gate',          group: 'control_flow', dispatch_capable: true,  description: '', implementation_status: 'production', implementation_state: 'wired_and_executable', risk_level: 'low',    uses_llm: false, uses_network: false, side_effects: [], requires_approval: true },
  { id: 'codecompass_index_build',   label: 'CC: Index aufbauen', group: 'retrieval', dispatch_capable: false, description: '', implementation_status: 'production', implementation_state: 'registered_only', risk_level: 'low',  uses_llm: false, uses_network: false, side_effects: ['write_index'] },
  { id: 'codecompass_vector_search', label: 'CC: Semantic Search', group: 'retrieval', dispatch_capable: false, description: '', implementation_status: 'production', implementation_state: 'registered_only', risk_level: 'none', uses_llm: false, uses_network: false, side_effects: [] },
  { id: 'codecompass_fts_search',    label: 'CC: Full-Text Search', group: 'retrieval', dispatch_capable: false, description: '', implementation_status: 'production', implementation_state: 'registered_only', risk_level: 'none', uses_llm: false, uses_network: false, side_effects: [] },
  { id: 'codecompass_graph_expand',  label: 'CC: Graph-Expansion', group: 'retrieval', dispatch_capable: false, description: '', implementation_status: 'production', implementation_state: 'registered_only', risk_level: 'none', uses_llm: false, uses_network: false, side_effects: [] },
  { id: 'embed_api',       label: 'Embedding API',          group: 'ml',           dispatch_capable: false, description: '', implementation_status: 'production',     implementation_state: 'wired_and_executable', risk_level: 'none',     uses_llm: false, uses_network: true,  side_effects: ['network_egress'] },
  { id: 'embed_chunk',     label: 'Chunk + Einbetten',      group: 'ml',           dispatch_capable: false, description: '', implementation_status: 'production',     implementation_state: 'wired_and_executable', risk_level: 'none',     uses_llm: false, uses_network: true,  side_effects: ['read_workspace', 'network_egress'] },
  { id: 'turboquant_mse',  label: 'TurboQuant MSE (experimentell)', group: 'ml',   dispatch_capable: false, description: '', implementation_status: 'experimental',   implementation_state: 'wired_and_executable', risk_level: 'none',     uses_llm: false, uses_network: false, side_effects: [] },
  { id: 'sign_rotation',   label: 'Sign-Rotation (TQ-011)', group: 'ml',           dispatch_capable: false, description: '', implementation_status: 'production',     implementation_state: 'wired_and_executable', risk_level: 'none',     uses_llm: false, uses_network: false, side_effects: [], deterministic: true },
  { id: 'rag_retrieve',    label: 'RAG Abruf',              group: 'ml',           dispatch_capable: false, description: '', implementation_status: 'production',     implementation_state: 'wired_and_executable', risk_level: 'none',     uses_llm: false, uses_network: false, side_effects: [] },
  { id: 'rerank',          label: 'Reranking',              group: 'ml',           dispatch_capable: false, description: '', implementation_status: 'production',     implementation_state: 'wired_and_executable', risk_level: 'none',     uses_llm: false, uses_network: false, side_effects: [], deterministic: true },
  { id: 'query_rewrite',   label: 'Query-Erweiterung',      group: 'ml',           dispatch_capable: false, description: '', implementation_status: 'production',     implementation_state: 'wired_and_executable', risk_level: 'none',     uses_llm: false, uses_network: false, side_effects: [], deterministic: true },
  { id: 'evolution_analyze',  label: 'Evolution: Analysieren', group: 'ml',        dispatch_capable: false, description: '', implementation_status: 'production',     implementation_state: 'registered_only',     risk_level: 'medium',   uses_llm: true,  uses_network: false, side_effects: ['write_database'] },
  { id: 'evolution_validate', label: 'Evolution: Validieren',  group: 'ml',        dispatch_capable: false, description: '', implementation_status: 'production',     implementation_state: 'registered_only',     risk_level: 'low',      uses_llm: false, uses_network: false, side_effects: [] },
  { id: 'evolution_apply',    label: 'Evolution: Anwenden',    group: 'ml',        dispatch_capable: false, description: '', implementation_status: 'production',     implementation_state: 'registered_only',     risk_level: 'high',     uses_llm: true,  uses_network: false, side_effects: ['write_files', 'write_database'], requires_approval: true },
  { id: 'evolve_prompt',   label: 'Prompt Evolver',         group: 'ml',           dispatch_capable: false, description: '', implementation_status: 'production',     implementation_state: 'registered_only',     risk_level: 'medium',   uses_llm: true,  uses_network: false, side_effects: ['write_database'] },
  { id: 'evolve_project',  label: 'Projekt-Evolver',        group: 'ml',           dispatch_capable: false, description: '', implementation_status: 'production',     implementation_state: 'registered_only',     risk_level: 'critical', uses_llm: true,  uses_network: false, side_effects: ['write_files', 'write_database'], requires_approval: true },
  { id: 'domain_cluster',  label: 'Domain-Clustering',      group: 'ml',           dispatch_capable: false, description: '', implementation_status: 'production',     implementation_state: 'registered_only',     risk_level: 'none',     uses_llm: false, uses_network: false, side_effects: [], deterministic: true },
];

const ENCODING_MODES = ['off', 'float32', 'float16', 'int8', 'symmetric4bit', 'turboquant_mse_experimental'];
const RAG_CHANNELS   = ['dense', 'lexical', 'symbol', 'codecompass_fts', 'codecompass_vector', 'codecompass_graph'];
const POLL_INTERVAL_MS = 3000;
const POLL_MAX_MS = 10 * 60 * 1000;

function uid(): string { return Math.random().toString(36).slice(2, 10); }
function edgeId(): string { return `edge-${uid()}`; }
function stepId(): string { return `step-${uid()}`; }

function emptyGraph(): VpGraph {
  return { id: `vp-${uid()}`, name: 'Neuer Prozess', description: '', version: '1.0',
           steps: [], edges: [], tags: [], metadata: {} };
}

function hintColor(hints: string[]): string {
  if (hints.includes('high_risk') || hints.includes('mutates_production')) return '#ff6b6b';
  if (hints.includes('requires_approval')) return '#fdcb6e';
  if (hints.includes('evolution') || hints.includes('self_modifying')) return '#e84393';
  if (hints.includes('index_write')) return '#6c5ce7';
  if (hints.includes('retrieval')) return '#a29bfe';
  if (hints.includes('vector_operation') || hints.includes('quantization')) return '#00b894';
  if (hints.includes('ml_inference')) return '#00cec9';
  if (hints.includes('read_only')) return '#74b9ff';
  return '#636e72';
}

const RETRIEVAL_KINDS = new Set([
  'codecompass_index_build', 'codecompass_vector_search',
  'codecompass_fts_search', 'codecompass_graph_expand',
]);
const EVOLUTION_KINDS = new Set([
  'evolution_analyze', 'evolution_validate', 'evolution_apply',
  'evolve_prompt', 'evolve_project',
]);
const WORKSPACE_KINDS = new Set(['workspace_snapshot', 'workspace_diff']);

function nodeKindColor(kind: string): string {
  if (kind === 'fork' || kind === 'join' || kind === 'parallel') return '#00b894';
  if (kind === 'approval') return '#55efc4';
  if (RETRIEVAL_KINDS.has(kind)) return '#6c5ce7';
  if (EVOLUTION_KINDS.has(kind)) return '#e84393';
  if (WORKSPACE_KINDS.has(kind)) return '#b2bec3';
  if (kind === 'turboquant_mse' || kind === 'sign_rotation') return '#00b894';
  if (kind === 'embed_api' || kind === 'embed_chunk') return '#00cec9';
  return '';
}

@Component({
  standalone: true,
  selector: 'app-visual-process-editor',
  imports: [CommonModule, FormsModule],
  template: `
<!-- hidden BPMN file input (VPBPMN-002) -->
<input type="file" #bpmnFileInput accept=".bpmn,.xml" hidden (change)="onBpmnFile($event)" />

<!-- ── toolbar ───────────────────────────────────────────────────────────── -->
<div class="vpe-toolbar">
  <span class="vpe-title">
    <input class="vpe-title-input" [ngModel]="graph().name" (ngModelChange)="setGraphName($event)"
           placeholder="Prozessname…" />
    @if (isDirty()) { <span class="vpe-dirty" title="Ungespeicherte Änderungen">●</span> }
  </span>
  <button class="vpe-btn-icon" (click)="showGraphDetails = !showGraphDetails" title="Graph-Details">⚙</button>

  <div class="vpe-tb-group">
    <button class="vpe-btn" (click)="addStep()" title="Neuer Schritt (N)">+ Schritt</button>
    <button class="vpe-btn" [class.active]="edgeMode()" (click)="toggleEdgeMode()" title="Kante zeichnen (E)">
      {{ edgeMode() ? '✏ Kante… klicke Quelle' : '→ Kante' }}
    </button>
    <button class="vpe-btn danger" [disabled]="!selectedId()" (click)="deleteSelected()" title="Löschen (Del)">Löschen</button>
    <button class="vpe-btn" (click)="autoLayout()" title="Auto-Layout">Auto-Layout</button>
  </div>

  <div class="vpe-tb-group">
    <button class="vpe-btn" (click)="loadPresetMenu = !loadPresetMenu; loadSavedMenu = false">Preset ▾</button>
    @if (loadPresetMenu) {
      <div class="vpe-dropdown">
        @for (p of presets(); track p.id) {
          <button class="vpe-dd-item" (click)="loadPreset(p.id)">{{ p.name }}</button>
        }
      </div>
    }
    <button class="vpe-btn" (click)="loadSavedMenu = !loadSavedMenu; loadPresetMenu = false">Geladen ▾</button>
    @if (loadSavedMenu) {
      <div class="vpe-dropdown">
        @if (savedGraphs().length === 0) {
          <div class="vpe-dd-item" style="opacity:0.5;cursor:default">Keine gespeicherten Graphen</div>
        }
        @for (g of savedGraphs(); track g.id) {
          <button class="vpe-dd-item" (click)="loadSavedGraphById(g.id)">
            {{ g.name }}
            <span style="font-size:9px;opacity:0.6;display:block">{{ formatDate(g.updated_at) }}</span>
          </button>
        }
      </div>
    }
  </div>

  <div class="vpe-tb-group">
    <button class="vpe-btn" (click)="saveGraphToServer()">💾 Speichern</button>
    <button class="vpe-btn" (click)="validateGraph()">Validieren</button>
    <button class="vpe-btn" (click)="runDryRun()">Dry-Run</button>
    <button class="vpe-btn" (click)="openMermaid()">Mermaid</button>
    <button class="vpe-btn" (click)="exportBpmn()">BPMN ↓</button>
    <button class="vpe-btn" (click)="bpmnFileInput.click()">BPMN ↑</button>
  </div>

  <div class="vpe-tb-group">
    <button class="vpe-btn success" [disabled]="!canStartWorkflow()" (click)="startWorkflow()"
            [title]="hasNonExecutableSteps() ? '⚠ Graph enthält nicht ausführbare Steps (registered_only) — Dry-Run für Details' : 'Workflow starten'">
      ▶ Starten@if (hasNonExecutableSteps()) { ⚠}
    </button>
    @if (activeWorkflowId()) {
      <button class="vpe-btn danger" (click)="cancelWorkflow()">⏹ Abbrechen</button>
    }
  </div>
</div>

<!-- ── graph details panel ────────────────────────────────────────────────── -->
@if (showGraphDetails) {
  <div class="vpe-graph-meta">
    <label class="vpe-meta-label">Beschreibung
      <textarea class="vpe-meta-input" rows="2" [ngModel]="graph().description"
                (ngModelChange)="setGraphDescription($event)" placeholder="Kurzbeschreibung…"></textarea>
    </label>
    <label class="vpe-meta-label">Tags (komma-getrennt)
      <input class="vpe-meta-input" [ngModel]="graphTagsStr()" (ngModelChange)="setTags($event)" placeholder="code, review, pipeline" />
    </label>
  </div>
}

<!-- ── gate banner ────────────────────────────────────────────────────────── -->
@if (gateStepId()) {
  <div class="vpe-gate-banner">
    <span>🔒 Gate-Freigabe erforderlich: <strong>{{ stepLabel(gateStepId()!) }}</strong></span>
    <button class="vpe-btn success" (click)="approveGate()">✓ Genehmigen</button>
    <button class="vpe-btn danger"  (click)="rejectGate()">✗ Ablehnen</button>
  </div>
}

<!-- ── main area ─────────────────────────────────────────────────────────── -->
<div class="vpe-main">

  <!-- SVG canvas -->
  <div class="vpe-canvas-wrap" #canvasWrap
       (mousedown)="onCanvasMouseDown($event)"
       (mousemove)="onMouseMove($event)"
       (mouseup)="onMouseUp($event)"
       (wheel)="onWheel($event)">

    <svg #svgEl class="vpe-svg">
      <defs>
        <marker id="arrowhead" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
          <polygon points="0 0, 8 3, 0 6" fill="#aaa"/>
        </marker>
        <marker id="arrowback" markerWidth="8" markerHeight="6" refX="0" refY="3" orient="auto">
          <polygon points="8 0, 0 3, 8 6" fill="#7f8c8d" fill-opacity="0.8"/>
        </marker>
      </defs>

      <g [attr.transform]="canvasTransform()">
        <!-- edges -->
        @for (edge of graph().edges; track edge.id) {
          <g (click)="selectEdge(edge.id)" class="vpe-edge-g">
            <path [attr.d]="edgePath(edge)"
                  [class.selected]="selectedId() === edge.id"
                  [class.back-edge]="edge.condition.kind === 'back_edge'"
                  class="vpe-edge"
                  [attr.marker-end]="edge.condition.kind === 'back_edge' ? '' : 'url(#arrowhead)'"
                  [attr.marker-start]="edge.condition.kind === 'back_edge' ? 'url(#arrowback)' : ''"/>
            @if (edge.label) {
              <text [attr.x]="edgeMidpoint(edge).x" [attr.y]="edgeMidpoint(edge).y - 4"
                    class="vpe-edge-label">{{ edge.label }}</text>
            }
          </g>
        }

        <!-- live edge being drawn -->
        @if (drawingEdge()) {
          <path [attr.d]="liveEdgePath()" class="vpe-edge live"/>
        }

        <!-- steps -->
        @for (step of graph().steps; track step.id) {
          <g [attr.transform]="'translate(' + step.position.x + ',' + step.position.y + ')'"
             (mousedown)="onNodeMouseDown($event, step.id)"
             (click)="selectStep(step.id)"
             class="vpe-node-g"
             [class.selected]="selectedId() === step.id"
             [class.edge-source]="edgeSourceId() === step.id"
             [class.awaiting-gate]="step.id === gateStepId()">

            @if (step.kind === 'fork' || step.kind === 'join' || step.kind === 'parallel') {
              <polygon [attr.points]="diamondPoints()"
                       [attr.fill]="nodeColor(step)"
                       class="vpe-node-rect vpe-diamond"/>
            } @else {
              <rect [attr.width]="NODE_W" [attr.height]="NODE_H" rx="7"
                    [attr.fill]="nodeColor(step)"
                    class="vpe-node-rect"/>
            }

            @if (step.gate) { <text x="4" y="14" class="vpe-node-icon">🔒</text> }
            <text [attr.x]="NODE_W / 2" [attr.y]="22" class="vpe-node-label">{{ step.label }}</text>
            <text [attr.x]="NODE_W / 2" [attr.y]="38" class="vpe-node-kind">{{ step.kind }}</text>

            @if (step.run_state) {
              <circle [attr.cx]="NODE_W - 10" cy="10" r="5"
                      [attr.fill]="runStateColor(step.run_state)"/>
            }
          </g>
        }
      </g>
    </svg>

    @if (graph().steps.length === 0) {
      <div class="vpe-empty-hint">Klicke "+ Schritt" um zu beginnen, oder lade ein Preset.</div>
    }
  </div>

  <!-- right panel -->
  <div class="vpe-panel">

    <!-- step inspector -->
    @if (selectedStep()) {
      <div class="vpe-inspector">
        <div class="vpe-panel-title">Schritt</div>

        <label class="vpe-label">Label
          <input class="vpe-input" [ngModel]="selectedStep()!.label"
                 (ngModelChange)="setStepLabel($event)" />
        </label>
        <label class="vpe-label">Typ
          <select class="vpe-input" [ngModel]="selectedStep()!.kind"
                  (ngModelChange)="onKindChange($event)">
            @for (g of kindGroups(); track g.group) {
              <optgroup [label]="g.group">
                @for (k of g.kinds; track k.id) {
                  <option [value]="k.id">{{ k.label }}{{ kindOptionSuffix(k) }}</option>
                }
              </optgroup>
            }
          </select>
        </label>
        <label class="vpe-label">Rolle
          <input class="vpe-input" [ngModel]="selectedStep()!.role"
                 (ngModelChange)="setStepRole($event)" placeholder="z.B. developer" />
        </label>
        <label class="vpe-label">Skill-Profil
          <select class="vpe-input" [ngModel]="selectedStep()!.agent_skill_profile_id"
                  (ngModelChange)="setStepSkillProfile($event)">
            <option value="">— keins —</option>
            @for (p of skillProfiles(); track p.id) {
              <option [value]="p.id">{{ p.name }}</option>
            }
          </select>
        </label>
        <label class="vpe-label">Beschreibung
          <textarea class="vpe-input" rows="2" [ngModel]="stepDescription()"
                    (ngModelChange)="setStepDescription($event)" placeholder="Optionale Beschreibung…"></textarea>
        </label>
        <label class="vpe-label vpe-checkbox">
          <input type="checkbox" [ngModel]="selectedStep()!.gate"
                 (ngModelChange)="setStepGate($event)" />
          Gate (Freigabe erforderlich)
        </label>

        <!-- Runtime-Truth Inspector (VPUI-001) -->
        @if (selectedStepKindInfo()) {
          <div class="vpe-rt-panel">
            <div class="vpe-rt-row">
              <span class="vpe-rt-badge" [class]="'vpe-badge-' + (selectedStepKindInfo()!.implementation_status || 'unknown')">
                {{ selectedStepKindInfo()!.implementation_status || '?' }}
              </span>
              @if ((selectedStepKindInfo()!.implementation_state || '') !== 'wired_and_executable') {
                <span class="vpe-rt-badge vpe-badge-not-exec" title="{{ selectedStepKindInfo()!.implementation_state }}">
                  nicht ausführbar
                </span>
              }
              @if (selectedStepKindInfo()!.uses_llm) {
                <span class="vpe-rt-badge vpe-badge-llm" title="Ruft LLM API auf">LLM</span>
              }
              @if (selectedStepKindInfo()!.uses_network) {
                <span class="vpe-rt-badge vpe-badge-net" title="Macht Netzwerk-Anfragen">Net</span>
              }
              @if (selectedStepKindInfo()!.deterministic) {
                <span class="vpe-rt-badge vpe-badge-det" title="Deterministisch">det</span>
              }
            </div>
            @if (selectedStepKindInfo()!.backend_service) {
              <div class="vpe-rt-detail">
                <span class="vpe-rt-key">Backend:</span> {{ selectedStepKindInfo()!.backend_service }}
              </div>
            }
            @if ((selectedStepKindInfo()!.side_effects || []).length > 0) {
              <div class="vpe-rt-detail">
                <span class="vpe-rt-key">Side-Effects:</span> {{ (selectedStepKindInfo()!.side_effects || []).join(', ') }}
              </div>
            }
            @if (selectedStepKindInfo()!.risk_level && selectedStepKindInfo()!.risk_level !== 'none') {
              <div class="vpe-rt-detail vpe-rt-risk-{{ selectedStepKindInfo()!.risk_level }}">
                <span class="vpe-rt-key">Risiko:</span> {{ selectedStepKindInfo()!.risk_level }}
              </div>
            }
            @if ((selectedStepKindInfo()!.legacy_aliases || []).length > 0) {
              <div class="vpe-rt-detail" style="opacity:0.7;font-size:10px">
                Legacy-Namen: {{ (selectedStepKindInfo()!.legacy_aliases || []).join(', ') }}
              </div>
            }
          </div>
        }

        <!-- Kind-specific meta fields -->

        <!-- embed_api: HTTP-API oder lokal-hash — KEIN lokaler PyTorch/Transformer -->
        @if (selectedStep()!.kind === 'embed_api' || selectedStep()!.kind === 'vector_encode') {
          <div class="vpe-meta-section">
            <div class="vpe-info-note">ℹ Embedding via HTTP-API (OpenAI-kompatibel), lokalem Hash oder Fake. Kein lokaler PyTorch/Transformer im Production-Code.</div>
            <label class="vpe-label">Provider
              <select class="vpe-input" [ngModel]="stepMeta('provider') || 'hash'"
                      (ngModelChange)="setStepMeta('provider', $event)">
                <option value="hash">local_hash (deterministisch, 12 dims)</option>
                <option value="fake">fake (Test-only, 8 dims)</option>
                <option value="openai_compatible">openai_compatible (HTTP API)</option>
              </select>
            </label>
            @if (stepMeta('provider') === 'openai_compatible') {
              <label class="vpe-label">Base-URL
                <input class="vpe-input" [ngModel]="stepMeta('base_url')"
                       (ngModelChange)="setStepMeta('base_url', $event)" placeholder="http://localhost:11434" />
              </label>
              <label class="vpe-label">Modell
                <input class="vpe-input" [ngModel]="stepMeta('model') || 'text-embedding-3-small'"
                       (ngModelChange)="setStepMeta('model', $event)" />
              </label>
              <label class="vpe-label">Dimensionen
                <input class="vpe-input" type="number" min="1"
                       [ngModel]="stepMeta('dimensions') ?? 1536"
                       (ngModelChange)="setStepMeta('dimensions', +$event)" />
              </label>
            }
            @if (stepMeta('provider') === 'hash') {
              <label class="vpe-label">Dimensionen (hash)
                <input class="vpe-input" type="number" min="4" max="128"
                       [ngModel]="stepMeta('dimensions') ?? 12"
                       (ngModelChange)="setStepMeta('dimensions', +$event)" />
              </label>
            }
          </div>
        }

        <!-- embed_chunk: index_builder chunking + embed_api per chunk -->
        @if (selectedStep()!.kind === 'embed_chunk') {
          <div class="vpe-meta-section">
            <label class="vpe-label">Chunk-Größe (Tokens)
              <input class="vpe-input" type="number" min="64"
                     [ngModel]="stepMeta('chunk_size') ?? 512"
                     (ngModelChange)="setStepMeta('chunk_size', +$event)" />
            </label>
            <label class="vpe-label">Chunk-Überlappung
              <input class="vpe-input" type="number" min="0"
                     [ngModel]="stepMeta('chunk_overlap') ?? 64"
                     (ngModelChange)="setStepMeta('chunk_overlap', +$event)" />
            </label>
            <label class="vpe-label">Provider
              <select class="vpe-input" [ngModel]="stepMeta('provider') || 'hash'"
                      (ngModelChange)="setStepMeta('provider', $event)">
                <option value="hash">local_hash</option>
                <option value="openai_compatible">openai_compatible (HTTP)</option>
              </select>
            </label>
            <label class="vpe-label">Embedding-Modell (für openai_compatible)
              <input class="vpe-input" [ngModel]="stepMeta('embedding_model')"
                     (ngModelChange)="setStepMeta('embedding_model', $event)" placeholder="nomic-embed-text" />
            </label>
          </div>
        }

        <!-- sign_rotation: TQ-011 DeterministicSignRotation — selbst-invers, kein NotImplementedError -->
        @if (selectedStep()!.kind === 'sign_rotation') {
          <div class="vpe-meta-section">
            <div class="vpe-info-note">ℹ TQ-011: DeterministicSignRotation — SHA256-basierter Per-Dimension Vorzeichenflip. Selbst-invers. Vollständig implementiert.</div>
            <label class="vpe-label">Seed (für reproduzierbare Rotation)
              <input class="vpe-input" type="number" min="0"
                     [ngModel]="stepMeta('seed') ?? 888"
                     (ngModelChange)="setStepMeta('seed', +$event)" />
            </label>
          </div>
        }

        <!-- turboquant_mse: TQ-012 — funktionierender experimenteller Encoder -->
        @if (selectedStep()!.kind === 'turboquant_mse' || selectedStep()!.kind === 'turboquant_encode') {
          <div class="vpe-meta-section">
            <div class="vpe-info-note">ℹ TurboQuantMseEncoder (TQ-012): sign-rotate + 4-bit scalar quant + Decode funktioniert. Experimentell — kein Produktions-Codebook. TQ-013 ProdStub ist ein separater, unbenutzter Stub und nicht Teil dieses Steps.</div>
            <label class="vpe-label">Seed (Sign-Rotation)
              <input class="vpe-input" type="number" min="0"
                     [ngModel]="stepMeta('seed') ?? 888"
                     (ngModelChange)="setStepMeta('seed', +$event)" />
            </label>
            <label class="vpe-label">Levels (Quantisierungs-Stufen, Standard: 7 für 4-bit)
              <input class="vpe-input" type="number" min="1" max="15"
                     [ngModel]="stepMeta('levels') ?? 7"
                     (ngModelChange)="setStepMeta('levels', +$event)" />
            </label>
            <label class="vpe-label vpe-checkbox">
              <input type="checkbox" [ngModel]="!!stepMeta('store_original')"
                     (ngModelChange)="setStepMeta('store_original', $event)" />
              Original float32 behalten (für Fallback-Policy)
            </label>
          </div>
        }

        <!-- rag_retrieve: HybridRetrievalService mit 6 Channels -->
        @if (selectedStep()!.kind === 'rag_retrieve') {
          <div class="vpe-meta-section">
            <div class="vpe-label">Channels (HybridRetrievalService)
              @for (ch of ragChannels; track ch) {
                <label class="vpe-checkbox" style="margin:2px 0">
                  <input type="checkbox" [ngModel]="isChannelSelected(ch)"
                         (ngModelChange)="toggleChannel(ch, $event)" />
                  {{ ch }}
                  @if (ch.startsWith('codecompass')) {
                    <span class="vpe-info-note" style="display:inline;margin-left:4px">→ erfordert aktiven CC-Index</span>
                  }
                </label>
              }
            </div>
            <label class="vpe-label">Top-K
              <input class="vpe-input" type="number" min="1"
                     [ngModel]="stepMeta('top_k') ?? 20"
                     (ngModelChange)="setStepMeta('top_k', +$event)" />
            </label>
          </div>
        }

        <!-- rerank: Token-Overlap Boost — KEIN neurales Modell -->
        @if (selectedStep()!.kind === 'rerank') {
          <div class="vpe-meta-section">
            <div class="vpe-info-note">ℹ Reranker: Token-Overlap Boost. Neural Stub = NotImplementedError.</div>
            <label class="vpe-label">Reranker-Typ
              <select class="vpe-input" [ngModel]="stepMeta('reranker_type') || 'token_overlap'"
                      (ngModelChange)="setStepMeta('reranker_type', $event)">
                <option value="token_overlap">Token Overlap ✓ (implementiert)</option>
                <option value="neural_stub">Neural Stub ✗ (NotImplementedError)</option>
              </select>
            </label>
            <label class="vpe-label">Gewicht (0.0–1.0)
              <input class="vpe-input" type="number" min="0" max="1" step="0.01"
                     [ngModel]="stepMeta('reranker_weight') ?? 0.15"
                     (ngModelChange)="setStepMeta('reranker_weight', +$event)" />
            </label>
            @if (stepMeta('reranker_type') === 'neural_stub') {
              <div class="vpe-warn-note">⚠ Neural Reranker = NotImplementedError — verwende token_overlap</div>
            }
          </div>
        }

        <!-- query_rewrite: nur Synonym-Expansion, kein LLM -->
        @if (selectedStep()!.kind === 'query_rewrite') {
          <div class="vpe-meta-section">
            <div class="vpe-info-note">ℹ Nur regelbasierte Synonym-Expansion (bug→defect/failure, fix→repair/resolve…). Kein LLM-Rewriting, kein Netzwerk-Egress.</div>
          </div>
        }

        <!-- domain_cluster: deterministisch — Leiden/Louvain NICHT im Production-Code -->
        @if (selectedStep()!.kind === 'domain_cluster' || selectedStep()!.kind === 'cluster') {
          <div class="vpe-meta-section">
            <div class="vpe-warn-note">⚠ Leiden/Louvain/KMeans existieren NICHT im Production-Code. Nur deterministisches Signal-Clustering (Pfad/Paket/Graph-Kohäsion, rag-helper).</div>
            <label class="vpe-label">Min. Domain-Größe
              <input class="vpe-input" type="number" min="1"
                     [ngModel]="stepMeta('min_domain_size') ?? 3"
                     (ngModelChange)="setStepMeta('min_domain_size', +$event)" />
            </label>
          </div>
        }

        <!-- workspace_snapshot / workspace_diff: WorkspaceDiffService — deterministisch -->
        @if (selectedStep()!.kind === 'workspace_snapshot') {
          <div class="vpe-meta-section">
            <div class="vpe-info-note">ℹ WorkspaceDiffService.take_snapshot(): erstellt SHA256-Hash-Map aller Dateien. Deterministisch, kein LLM.</div>
            <label class="vpe-label">Workspace-Root (optional)
              <input class="vpe-input" [ngModel]="stepMeta('workspace_root') || '.'"
                     (ngModelChange)="setStepMeta('workspace_root', $event)" placeholder="." />
            </label>
          </div>
        }
        @if (selectedStep()!.kind === 'workspace_diff') {
          <div class="vpe-meta-section">
            <div class="vpe-info-note">ℹ WorkspaceDiffService.compute_diff() + synthesize_manifest(): erzeugt artifact_manifest.v1 aus FileChangeSet.</div>
            <label class="vpe-label">Workspace-Root
              <input class="vpe-input" [ngModel]="stepMeta('workspace_root') || '.'"
                     (ngModelChange)="setStepMeta('workspace_root', $event)" placeholder="." />
            </label>
          </div>
        }

        <!-- CodeCompass Steps — alle 19 Module vollständig implementiert -->
        @if (selectedStep()!.kind === 'codecompass_index_build') {
          <div class="vpe-meta-section">
            <div class="vpe-info-note">ℹ index_builder.py: Delta-Erkennung (changed/deleted/renamed) → Chunking → Embedding → SQLite-Speicherung.</div>
            <label class="vpe-label vpe-checkbox">
              <input type="checkbox" [ngModel]="stepMeta('incremental') !== false"
                     (ngModelChange)="setStepMeta('incremental', $event)" />
              Inkrementell (Delta-Build statt Full-Rebuild)
            </label>
            <label class="vpe-label">Provider
              <select class="vpe-input" [ngModel]="stepMeta('provider') || 'hash'"
                      (ngModelChange)="setStepMeta('provider', $event)">
                <option value="hash">local_hash</option>
                <option value="openai_compatible">openai_compatible (HTTP)</option>
              </select>
            </label>
          </div>
        }
        @if (selectedStep()!.kind === 'codecompass_vector_search') {
          <div class="vpe-meta-section">
            <div class="vpe-info-note">ℹ codecompass_vector_engine: Semantische Suche mit task_kind/intent-Gewichtung. Vollständig implementiert.</div>
            <label class="vpe-label">Top-K
              <input class="vpe-input" type="number" min="1"
                     [ngModel]="stepMeta('top_k') ?? 20"
                     (ngModelChange)="setStepMeta('top_k', +$event)" />
            </label>
            <label class="vpe-label">Task-Kind-Hint (für Gewichtung)
              <select class="vpe-input" [ngModel]="stepMeta('task_kind_hint') || ''"
                      (ngModelChange)="setStepMeta('task_kind_hint', $event)">
                <option value="">— keins —</option>
                <option value="bugfix">bugfix (×1.0)</option>
                <option value="refactor">refactor (×1.1)</option>
                <option value="architecture">architecture (×1.2)</option>
                <option value="config">config (×1.05)</option>
              </select>
            </label>
            <label class="vpe-label">Retrieval-Intent
              <select class="vpe-input" [ngModel]="stepMeta('retrieval_intent') || 'fuzzy_semantic'"
                      (ngModelChange)="setStepMeta('retrieval_intent', $event)">
                <option value="fuzzy_semantic">fuzzy_semantic (×1.2)</option>
                <option value="architecture">architecture (×1.15)</option>
                <option value="exact_symbol">exact_symbol (×0.9)</option>
                <option value="config_lookup">config_lookup</option>
              </select>
            </label>
          </div>
        }
        @if (selectedStep()!.kind === 'codecompass_fts_search') {
          <div class="vpe-meta-section">
            <div class="vpe-info-note">ℹ codecompass_fts_engine: BM25 Full-Text-Search mit task_kind/intent-Gewichtung. Vollständig implementiert.</div>
            <label class="vpe-label">Top-K
              <input class="vpe-input" type="number" min="1"
                     [ngModel]="stepMeta('top_k') ?? 20"
                     (ngModelChange)="setStepMeta('top_k', +$event)" />
            </label>
            <label class="vpe-label">Task-Kind-Hint
              <select class="vpe-input" [ngModel]="stepMeta('task_kind_hint') || ''"
                      (ngModelChange)="setStepMeta('task_kind_hint', $event)">
                <option value="">— keins —</option>
                <option value="bugfix">bugfix (×1.25)</option>
                <option value="refactor">refactor (×1.15)</option>
                <option value="architecture">architecture (×1.05)</option>
              </select>
            </label>
            <label class="vpe-label">Retrieval-Intent
              <select class="vpe-input" [ngModel]="stepMeta('retrieval_intent') || 'exact_symbol'"
                      (ngModelChange)="setStepMeta('retrieval_intent', $event)">
                <option value="exact_symbol">exact_symbol (×1.25)</option>
                <option value="config_lookup">config_lookup (×1.15)</option>
                <option value="fuzzy_semantic">fuzzy_semantic</option>
              </select>
            </label>
          </div>
        }
        @if (selectedStep()!.kind === 'codecompass_graph_expand') {
          <div class="vpe-meta-section">
            <div class="vpe-info-note">ℹ codecompass_graph_expansion: Nachbarschafts-Expansion von Seed-Knoten im SQLite-Graph-Store.</div>
            <label class="vpe-label">Max. Knoten (Expansion-Limit)
              <input class="vpe-input" type="number" min="1" max="200"
                     [ngModel]="stepMeta('max_nodes') ?? 50"
                     (ngModelChange)="setStepMeta('max_nodes', +$event)" />
            </label>
          </div>
        }

        <!-- Evolution Steps (EvolutionService — vollständig implementiert) -->
        @if (selectedStep()!.kind === 'evolution_analyze') {
          <div class="vpe-meta-section">
            <div class="vpe-info-note">ℹ EvolutionService.analyze(): Kontext → EvolutionProposal(s). Persistiert EvolutionRunDB + EvolutionProposalDB.</div>
            <label class="vpe-label">Provider-Name
              <input class="vpe-input" [ngModel]="stepMeta('provider_name') || 'default'"
                     (ngModelChange)="setStepMeta('provider_name', $event)" />
            </label>
            <label class="vpe-label">Trigger-Typ
              <select class="vpe-input" [ngModel]="stepMeta('trigger_type') || 'manual'"
                      (ngModelChange)="setStepMeta('trigger_type', $event)">
                <option value="manual">MANUAL</option>
                <option value="verification_failure">VERIFICATION_FAILURE</option>
                <option value="error_threshold">ERROR_THRESHOLD</option>
                <option value="periodic_review">PERIODIC_REVIEW</option>
                <option value="policy_request">POLICY_REQUEST</option>
              </select>
            </label>
          </div>
        }
        @if (selectedStep()!.kind === 'evolution_validate') {
          <div class="vpe-meta-section">
            <div class="vpe-info-note">ℹ EvolutionService.validate(): Validiert Proposal ohne Anwendung (dry validation pass).</div>
            <label class="vpe-label">Provider-Name
              <input class="vpe-input" [ngModel]="stepMeta('provider_name') || 'default'"
                     (ngModelChange)="setStepMeta('provider_name', $event)" />
            </label>
          </div>
        }
        @if (selectedStep()!.kind === 'evolution_apply') {
          <div class="vpe-meta-section">
            <div class="vpe-warn-note">⚠ EvolutionService.apply() via MutationGateService — schreibt Änderungen. gate=true ist Pflicht (wird vom Validator erzwungen).</div>
            <label class="vpe-label">Provider-Name
              <input class="vpe-input" [ngModel]="stepMeta('provider_name') || 'default'"
                     (ngModelChange)="setStepMeta('provider_name', $event)" />
            </label>
          </div>
        }

        <!-- evolve_prompt / evolve_project (bestehende Kinds) -->
        @if (selectedStep()!.kind === 'evolve_prompt') {
          <div class="vpe-meta-section">
            <div class="vpe-info-note">ℹ PlanningPromptEvolverService.evolve_from_run(): optimiert User/Repair-Prompt-Templates anhand von Fehler-Triggern (parse_failed, low confidence etc.).</div>
            <label class="vpe-label">Trigger-Typ
              <select class="vpe-input" [ngModel]="stepMeta('trigger_type') || 'manual'"
                      (ngModelChange)="setStepMeta('trigger_type', $event)">
                <option value="manual">manual</option>
                <option value="verification_failure">verification_failure</option>
                <option value="error_threshold">error_threshold</option>
                <option value="periodic_review">periodic_review</option>
                <option value="policy_request">policy_request</option>
              </select>
            </label>
            <label class="vpe-label vpe-checkbox">
              <input type="checkbox" [ngModel]="stepMeta('analyze_only') !== false"
                     (ngModelChange)="setStepMeta('analyze_only', $event)" />
              Nur analysieren (kein DB-Schreiben)
            </label>
            <label class="vpe-label">Output-Format
              <select class="vpe-input" [ngModel]="stepMeta('output_format') || 'json'"
                      (ngModelChange)="setStepMeta('output_format', $event)">
                <option value="json">json</option>
                <option value="markdown">markdown</option>
                <option value="yaml">yaml</option>
              </select>
            </label>
          </div>
        }
        @if (selectedStep()!.kind === 'evolve_project') {
          <div class="vpe-meta-section">
            <label class="vpe-label">Trigger-Typ
              <select class="vpe-input" [ngModel]="stepMeta('trigger_type') || 'manual'"
                      (ngModelChange)="setStepMeta('trigger_type', $event)">
                <option value="manual">manual</option>
                <option value="verification_failure">verification_failure</option>
                <option value="error_threshold">error_threshold</option>
                <option value="periodic_review">periodic_review</option>
                <option value="policy_request">policy_request</option>
              </select>
            </label>
            <label class="vpe-label vpe-checkbox">
              <input type="checkbox" [ngModel]="stepMeta('analyze_only') !== false"
                     (ngModelChange)="setStepMeta('analyze_only', $event)" />
              Nur analysieren
            </label>
            <label class="vpe-label vpe-checkbox">
              <input type="checkbox" [ngModel]="!!stepMeta('apply_allowed')"
                     (ngModelChange)="setStepMeta('apply_allowed', $event)" />
              apply_allowed (Schreibt Produktions-Code!)
            </label>
            @if (stepMeta('apply_allowed')) {
              <div class="vpe-warn-note">⚠ apply_allowed=true schreibt direkt in Code. Empfehle evolution_apply-Step mit gate=true stattdessen.</div>
            }
          </div>
        }

        <!-- inputs -->
        <div class="vpe-io-section">
          <div class="vpe-io-title">Inputs</div>
          @for (inp of selectedStep()!.io.inputs; track $index) {
            <div class="vpe-io-row">
              <input class="vpe-input sm" [ngModel]="inp.name"
                     (ngModelChange)="mutateIOInput($index, 'name', $event)" placeholder="name" />
              <select class="vpe-input sm" [ngModel]="inp.kind"
                      (ngModelChange)="mutateIOInput($index, 'kind', $event)">
                @for (k of artifactKinds; track k) { <option [value]="k">{{ k }}</option> }
              </select>
              <input type="checkbox" [ngModel]="inp.required"
                     (ngModelChange)="mutateIOInput($index, 'required', $event)" title="Pflichtfeld" />
              <button class="vpe-btn-xs danger" (click)="removeInput($index)">✕</button>
            </div>
          }
          <button class="vpe-btn-xs" (click)="addInput()">+ Input</button>
        </div>

        <!-- outputs -->
        <div class="vpe-io-section">
          <div class="vpe-io-title">Outputs</div>
          @for (out of selectedStep()!.io.outputs; track $index) {
            <div class="vpe-io-row">
              <input class="vpe-input sm" [ngModel]="out.name"
                     (ngModelChange)="mutateIOOutput($index, 'name', $event)" placeholder="name" />
              <select class="vpe-input sm" [ngModel]="out.kind"
                      (ngModelChange)="mutateIOOutput($index, 'kind', $event)">
                @for (k of artifactKinds; track k) { <option [value]="k">{{ k }}</option> }
              </select>
              <button class="vpe-btn-xs danger" (click)="removeOutput($index)">✕</button>
            </div>
          }
          <button class="vpe-btn-xs" (click)="addOutput()">+ Output</button>
        </div>

        <!-- policy hints -->
        @if (selectedStep()!.policy_hints.length) {
          <div class="vpe-hints">
            @for (h of selectedStep()!.policy_hints; track h) {
              <span class="vpe-hint-chip">{{ h }}</span>
            }
          </div>
        }
      </div>
    }

    <!-- edge inspector -->
    @if (selectedEdge()) {
      <div class="vpe-inspector">
        <div class="vpe-panel-title">Kante</div>
        <label class="vpe-label">Label
          <input class="vpe-input" [ngModel]="selectedEdge()!.label"
                 (ngModelChange)="setEdgeLabel($event)" placeholder="optional" />
        </label>
        <label class="vpe-label">Bedingung
          <select class="vpe-input" [ngModel]="selectedEdge()!.condition.kind"
                  (ngModelChange)="setEdgeConditionKind($event)">
            @for (k of edgeKinds; track k) { <option [value]="k">{{ k }}</option> }
          </select>
        </label>

        @if (selectedEdge()!.condition.kind === 'back_edge') {
          <div class="vpe-meta-section">
            <label class="vpe-label">Loop-Art
              <select class="vpe-input" [ngModel]="selectedEdge()!.condition.loop_policy?.kind || 'fixed'"
                      (ngModelChange)="setLoopPolicy('kind', $event)">
                <option value="none">none</option>
                <option value="fixed">fixed</option>
                <option value="while">while</option>
                <option value="until">until</option>
              </select>
            </label>
            @if (selectedEdge()!.condition.loop_policy?.kind !== 'none') {
              <label class="vpe-label">Max. Iterationen
                <input class="vpe-input" type="number" min="1" max="20"
                       [ngModel]="selectedEdge()!.condition.loop_policy?.max_iterations ?? 3"
                       (ngModelChange)="setLoopPolicy('max_iterations', +$event)" />
              </label>
            }
            @if (selectedEdge()!.condition.loop_policy?.kind === 'while' || selectedEdge()!.condition.loop_policy?.kind === 'until') {
              <label class="vpe-label">Bedingung (Python-Ausdruck)
                <input class="vpe-input"
                       [ngModel]="selectedEdge()!.condition.loop_policy?.condition"
                       (ngModelChange)="setLoopPolicy('condition', $event)"
                       placeholder="z.B. output.evolved==True" />
              </label>
              @if (!selectedEdge()!.condition.loop_policy?.condition) {
                <div class="vpe-inline-err">Bedingung erforderlich für while/until</div>
              }
            }
            <label class="vpe-label">Break on Output (optional)
              <input class="vpe-input"
                     [ngModel]="selectedEdge()!.condition.loop_policy?.break_on_output"
                     (ngModelChange)="setLoopPolicy('break_on_output', $event || undefined)"
                     placeholder="Artifact-Name, der Schleife beendet" />
            </label>
          </div>
        }

        @if (selectedEdge()!.condition.kind === 'expression') {
          <label class="vpe-label">Ausdruck
            <input class="vpe-input" [ngModel]="selectedEdge()!.condition.expression"
                   (ngModelChange)="setEdgeExpression($event)"
                   placeholder="z.B. output.score > 0.8" />
          </label>
          @if (expressionError()) {
            <div class="vpe-inline-err">{{ expressionError() }}</div>
          }
        }

        @if (selectedEdge()!.condition.kind === 'on_output') {
          <label class="vpe-label">Artifact-Name
            <input class="vpe-input" [ngModel]="selectedEdge()!.condition.output_name"
                   (ngModelChange)="setEdgeOutputName($event)"
                   placeholder="z.B. test_results" />
          </label>
        }

        <div class="vpe-edge-info">
          {{ stepLabel(selectedEdge()!.source) }} → {{ stepLabel(selectedEdge()!.target) }}
        </div>
      </div>
    }

    <!-- agent library -->
    <div class="vpe-library">
      <div class="vpe-panel-title">Agent Library</div>
      @for (p of skillProfiles(); track p.id) {
        <div class="vpe-lib-item" (click)="applyProfile(p.id)" [title]="p.description">
          <span class="vpe-lib-name">{{ p.name }}</span>
          <span class="vpe-lib-role">{{ p.role }}</span>
          <div class="vpe-lib-tags">
            @for (t of p.tags; track t) { <span class="vpe-lib-tag">{{ t }}</span> }
          </div>
        </div>
      }
    </div>
  </div>
</div>

<!-- ── status bar ─────────────────────────────────────────────────────────── -->
<div class="vpe-status">
  <span>{{ graph().steps.length }} Schritte · {{ graph().edges.length }} Kanten</span>
  @if (validationResult()) {
    <span [class.ok]="validationResult()!.valid" [class.err]="!validationResult()!.valid">
      {{ validationResult()!.valid ? '✓ Gültig' : '✗ ' + validationResult()!.error_count + ' Fehler' }}
      @if (validationResult()!.warning_count) { · {{ validationResult()!.warning_count }} Warnungen }
    </span>
  }
  @if (activeWorkflowId()) {
    <span class="vpe-status-wf">⚙ Workflow: {{ workflowStatus()?.status ?? 'läuft…' }}</span>
  }
  @if (statusMsg()) {
    <span class="vpe-status-msg">{{ statusMsg() }}</span>
  }
  <span class="vpe-zoom-hint">Scroll = Zoom · Alt+Drag = Verschieben</span>
</div>

<!-- ── validation issues ──────────────────────────────────────────────────── -->
@if (validationResult() && validationResult()!.issues.length) {
  <div class="vpe-issues">
    @for (issue of validationResult()!.issues; track $index) {
      <div class="vpe-issue" [class.error]="issue.severity === 'error'"
           [class.warning]="issue.severity === 'warning'" [class.info]="issue.severity === 'info'">
        <strong>{{ issue.severity }}</strong> [{{ issue.code }}] {{ issue.message }}
        @if (issue.step_id) { <em>(Schritt: {{ stepLabel(issue.step_id) }})</em> }
      </div>
    }
  </div>
}

<!-- ── dry-run dialog ─────────────────────────────────────────────────────── -->
@if (dryRunResult()) {
  <div class="vpe-dialog-overlay" (click)="dryRunResult.set(null)">
    <div class="vpe-dialog" (click)="$event.stopPropagation()">
      <div class="vpe-dialog-title">Dry-Run Ergebnis</div>
      <pre class="vpe-pre">{{ dryRunSummary() }}</pre>

      <!-- Step Execution Plan (VPUI-002) -->
      @if ((dryRunResult()!.step_execution_plan || []).length > 0) {
        <div class="vpe-dialog-section">
          <div class="vpe-dialog-subtitle">Step Execution Plan</div>
          @if ((dryRunResult()!.non_executable_count || 0) > 0) {
            <div class="vpe-warn-banner">
              ⚠ {{ dryRunResult()!.non_executable_count }} Step(s) sind nicht ausführbar (registered_only / kein VP-Adapter).
              Der Graph kann gespeichert, aber nicht vollständig gestartet werden.
            </div>
          }
          <table class="vpe-exec-table">
            <thead>
              <tr><th>Step</th><th>Mode</th><th>Status</th><th>Risk</th></tr>
            </thead>
            <tbody>
              @for (p of dryRunResult()!.step_execution_plan || []; track p.step_id) {
                <tr [class.vpe-exec-not-exec]="!p.executable">
                  <td>{{ p.step_label }}<br><span style="font-size:10px;opacity:0.6">{{ p.kind }}</span></td>
                  <td>{{ p.execution_mode }}</td>
                  <td>
                    <span class="vpe-rt-badge" [class]="'vpe-badge-' + (p.implementation_status || 'unknown')">
                      {{ p.implementation_status }}
                    </span>
                    @if (!p.executable) {
                      <span class="vpe-rt-badge vpe-badge-not-exec">kein Adapter</span>
                    }
                  </td>
                  <td [class.vpe-risk-high]="p.risk_level === 'high' || p.risk_level === 'critical'">
                    {{ p.risk_level }}
                  </td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      }

      <div style="display:flex;gap:8px;flex-wrap:wrap">
        @if (dryRunResult()!.validation?.valid && dryRunResult()!.blueprint) {
          <button class="vpe-btn success" (click)="saveAsBlueprintFromDryRun()">Als Blueprint speichern</button>
        }
        <button class="vpe-btn" (click)="dryRunResult.set(null)">Schließen</button>
      </div>
    </div>
  </div>
}

<!-- ── mermaid dialog ─────────────────────────────────────────────────────── -->
@if (showMermaidDialog) {
  <div class="vpe-dialog-overlay" (click)="showMermaidDialog = false">
    <div class="vpe-dialog wide" (click)="$event.stopPropagation()">
      <div class="vpe-dialog-title">Mermaid Export</div>
      <div class="vpe-tabs">
        <button class="vpe-tab" [class.active]="mermaidTab === 'mermaid'" (click)="mermaidTab = 'mermaid'">Mermaid</button>
        @if (mermaidTuiText()) {
          <button class="vpe-tab" [class.active]="mermaidTab === 'tui'" (click)="mermaidTab = 'tui'">TUI-Text</button>
        }
      </div>
      @if (mermaidTab === 'mermaid') {
        <pre class="vpe-pre">{{ mermaidText() }}</pre>
      } @else {
        <pre class="vpe-pre">{{ mermaidTuiText() }}</pre>
      }
      <div style="display:flex;gap:8px">
        <button class="vpe-btn" (click)="copyMermaid()">Kopieren</button>
        <button class="vpe-btn" (click)="downloadMermaid()">Herunterladen</button>
        <button class="vpe-btn" (click)="showMermaidDialog = false">Schließen</button>
      </div>
    </div>
  </div>
}
`,
  styles: [`
:host { display: flex; flex-direction: column; height: 100%; min-height: 0; background: #1a1a2e; color: #eee; font-size: 13px; }

/* toolbar */
.vpe-toolbar { display: flex; align-items: center; gap: 6px; padding: 5px 10px; background: #16213e; border-bottom: 1px solid #0f3460; flex-shrink: 0; flex-wrap: wrap; }
.vpe-title { display: flex; align-items: center; gap: 4px; flex: 1 1 160px; }
.vpe-title-input { background: transparent; border: none; border-bottom: 1px solid #aaa; color: #eee; font-size: 14px; font-weight: 600; width: 100%; padding: 2px 0; outline: none; }
.vpe-dirty { color: #fdcb6e; font-size: 16px; line-height: 1; }
.vpe-btn-icon { background: none; border: none; color: #aaa; cursor: pointer; font-size: 14px; padding: 2px 4px; }
.vpe-btn-icon:hover { color: #eee; }
.vpe-tb-group { display: flex; gap: 4px; align-items: center; position: relative; }
.vpe-btn { padding: 4px 9px; border-radius: 4px; border: 1px solid #555; background: #2d3436; color: #eee; cursor: pointer; font-size: 12px; white-space: nowrap; }
.vpe-btn:hover { background: #636e72; }
.vpe-btn.active { background: #0984e3; border-color: #0984e3; }
.vpe-btn.danger { border-color: #e17055; color: #e17055; }
.vpe-btn.danger:hover { background: #e17055; color: #fff; }
.vpe-btn.success { border-color: #55efc4; color: #55efc4; }
.vpe-btn.success:hover { background: #55efc4; color: #1a1a2e; }
.vpe-btn:disabled { opacity: 0.4; cursor: default; }
.vpe-dropdown { position: absolute; top: 100%; left: 0; z-index: 100; background: #2d3436; border: 1px solid #636e72; border-radius: 4px; min-width: 200px; box-shadow: 0 4px 12px #0006; }
.vpe-dd-item { display: block; width: 100%; text-align: left; padding: 7px 12px; background: none; border: none; color: #eee; cursor: pointer; font-size: 12px; }
.vpe-dd-item:hover { background: #0984e3; }

/* graph meta panel */
.vpe-graph-meta { display: flex; gap: 12px; padding: 8px 12px; background: #0f3460; border-bottom: 1px solid #1a1a2e; flex-wrap: wrap; }
.vpe-meta-label { display: flex; flex-direction: column; gap: 2px; font-size: 11px; color: #b2bec3; flex: 1 1 200px; }
.vpe-meta-input { background: #1a1a2e; border: 1px solid #636e72; border-radius: 3px; color: #eee; font-size: 12px; padding: 3px 6px; width: 100%; }

/* gate banner */
.vpe-gate-banner { display: flex; align-items: center; gap: 10px; padding: 8px 14px; background: #2d1b00; border-bottom: 2px solid #fdcb6e; color: #fdcb6e; font-size: 13px; flex-shrink: 0; }

/* main */
.vpe-main { display: flex; flex: 1 1 0; min-height: 0; overflow: hidden; }

/* canvas */
.vpe-canvas-wrap { flex: 1 1 0; position: relative; overflow: hidden; cursor: default; }
.vpe-svg { position: absolute; inset: 0; width: 100%; height: 100%; }
.vpe-empty-hint { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; color: #636e72; font-size: 14px; pointer-events: none; }

/* nodes */
.vpe-node-g { cursor: grab; }
.vpe-node-g.selected .vpe-node-rect { stroke: #fdcb6e; stroke-width: 2; }
.vpe-node-g.edge-source .vpe-node-rect { stroke: #00cec9; stroke-width: 2; }
.vpe-node-rect { stroke: #444; stroke-width: 1; }
.vpe-node-label { text-anchor: middle; fill: #fff; font-size: 12px; font-weight: 600; pointer-events: none; }
.vpe-node-kind { text-anchor: middle; fill: #b2bec3; font-size: 10px; pointer-events: none; }
.vpe-node-icon { fill: #fff; font-size: 11px; pointer-events: none; }
.vpe-diamond { stroke: #444; stroke-width: 1; }
.awaiting-gate .vpe-node-rect { stroke: #fdcb6e; stroke-width: 3; animation: gatePulse 1s ease-in-out infinite alternate; }
@keyframes gatePulse { 0% { stroke-opacity: 1; } 100% { stroke-opacity: 0.3; } }

/* edges */
.vpe-edge { fill: none; stroke: #aaa; stroke-width: 1.5; }
.vpe-edge.selected { stroke: #fdcb6e; stroke-width: 2.5; }
.vpe-edge.back-edge { stroke: #7f8c8d; stroke-dasharray: 6 3; }
.vpe-edge.live { stroke: #00cec9; stroke-dasharray: 5 4; }
.vpe-edge-g { cursor: pointer; }
.vpe-edge-label { fill: #aaa; font-size: 10px; text-anchor: middle; }

/* right panel */
.vpe-panel { width: 250px; flex-shrink: 0; display: flex; flex-direction: column; background: #16213e; border-left: 1px solid #0f3460; overflow-y: auto; }
.vpe-panel-title { font-size: 11px; font-weight: 700; color: #74b9ff; text-transform: uppercase; letter-spacing: 0.06em; padding: 8px 10px 4px; border-bottom: 1px solid #0f3460; }
.vpe-inspector { padding: 8px 10px; border-bottom: 1px solid #0f3460; }
.vpe-label { display: flex; flex-direction: column; gap: 2px; font-size: 11px; color: #b2bec3; margin-bottom: 6px; }
.vpe-input { background: #2d3436; border: 1px solid #636e72; border-radius: 3px; color: #eee; font-size: 12px; padding: 3px 6px; }
.vpe-input.sm { width: 70px; }
.vpe-checkbox { flex-direction: row; align-items: center; gap: 6px; }
.vpe-io-section { margin-top: 6px; }
.vpe-io-title { font-size: 10px; color: #74b9ff; text-transform: uppercase; margin-bottom: 4px; }
.vpe-io-row { display: flex; gap: 4px; align-items: center; margin-bottom: 3px; }
.vpe-btn-xs { padding: 2px 6px; font-size: 10px; border-radius: 3px; border: 1px solid #555; background: #2d3436; color: #eee; cursor: pointer; }
.vpe-btn-xs.danger { border-color: #e17055; color: #e17055; }
.vpe-hints { display: flex; flex-wrap: wrap; gap: 3px; margin-top: 6px; }
.vpe-hint-chip { background: #2d3436; border: 1px solid #636e72; border-radius: 10px; font-size: 10px; padding: 1px 6px; color: #b2bec3; }
.vpe-edge-info { font-size: 11px; color: #74b9ff; margin-top: 6px; }
.vpe-meta-section { background: #0f3460; border-radius: 4px; padding: 6px 8px; margin: 4px 0; }
.vpe-inline-err { color: #ff7675; font-size: 10px; margin-top: 2px; }
.vpe-warn-note { color: #fdcb6e; font-size: 10px; margin-top: 2px; }
.vpe-info-note { color: #74b9ff; font-size: 10px; margin-top: 2px; line-height: 1.3; }

/* library */
.vpe-library { padding: 6px; flex: 1 1 0; overflow-y: auto; }
.vpe-lib-item { padding: 6px 8px; border-radius: 4px; cursor: pointer; margin-bottom: 4px; background: #2d3436; }
.vpe-lib-item:hover { background: #0f3460; }
.vpe-lib-name { font-size: 12px; font-weight: 600; display: block; }
.vpe-lib-role { font-size: 10px; color: #74b9ff; }
.vpe-lib-tags { display: flex; gap: 3px; flex-wrap: wrap; margin-top: 3px; }
.vpe-lib-tag { background: #0f3460; border-radius: 8px; font-size: 9px; padding: 1px 5px; color: #aaa; }

/* status bar */
.vpe-status { display: flex; align-items: center; gap: 12px; padding: 4px 12px; background: #0f3460; font-size: 11px; color: #b2bec3; flex-shrink: 0; flex-wrap: wrap; }
.vpe-status .ok { color: #55efc4; }
.vpe-status .err { color: #ff7675; }
.vpe-status-msg { color: #fdcb6e; }
.vpe-status-wf { color: #74b9ff; }
.vpe-zoom-hint { margin-left: auto; opacity: 0.5; }

/* issues */
.vpe-issues { max-height: 120px; overflow-y: auto; background: #1a1a2e; border-top: 1px solid #0f3460; }
.vpe-issue { padding: 3px 12px; font-size: 11px; }
.vpe-issue.error { color: #ff7675; }
.vpe-issue.warning { color: #fdcb6e; }
.vpe-issue.info { color: #74b9ff; }

/* dialogs */
.vpe-dialog-overlay { position: fixed; inset: 0; background: #000a; z-index: 1000; display: flex; align-items: center; justify-content: center; }
.vpe-dialog { background: #16213e; border: 1px solid #636e72; border-radius: 8px; padding: 20px; max-width: 600px; width: 90%; max-height: 80vh; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; }
.vpe-dialog.wide { max-width: 740px; }
.vpe-dialog-title { font-size: 14px; font-weight: 700; color: #74b9ff; }
.vpe-pre { background: #0f3460; border-radius: 4px; padding: 10px; font-size: 11px; white-space: pre-wrap; word-break: break-all; flex: 1; overflow-y: auto; max-height: 50vh; }
.vpe-tabs { display: flex; gap: 4px; }
.vpe-tab { padding: 4px 10px; border-radius: 4px 4px 0 0; border: 1px solid #555; background: #2d3436; color: #aaa; cursor: pointer; font-size: 12px; }
.vpe-tab.active { background: #0f3460; color: #eee; border-bottom-color: #0f3460; }

/* Runtime-Truth badges (VPUI-001) */
.vpe-rt-panel { background: #0f3460; border-radius: 4px; padding: 6px 8px; margin: 4px 0 8px; }
.vpe-rt-row { display: flex; gap: 4px; flex-wrap: wrap; margin-bottom: 4px; }
.vpe-rt-badge { display: inline-block; border-radius: 3px; font-size: 9px; font-weight: 700; padding: 1px 5px; text-transform: uppercase; letter-spacing: 0.04em; }
.vpe-badge-production     { background: #00b894; color: #fff; }
.vpe-badge-experimental   { background: #fdcb6e; color: #2d3436; }
.vpe-badge-stub           { background: #e17055; color: #fff; }
.vpe-badge-not_implemented{ background: #d63031; color: #fff; }
.vpe-badge-design_only    { background: #b2bec3; color: #2d3436; }
.vpe-badge-unknown        { background: #636e72; color: #eee; }
.vpe-badge-not-exec       { background: #e17055; color: #fff; }
.vpe-badge-llm            { background: #a29bfe; color: #2d3436; }
.vpe-badge-net            { background: #0984e3; color: #fff; }
.vpe-badge-det            { background: #2d3436; color: #74b9ff; border: 1px solid #74b9ff; }
.vpe-rt-detail { font-size: 10px; color: #b2bec3; margin-top: 2px; }
.vpe-rt-key { color: #74b9ff; font-weight: 600; }
.vpe-rt-risk-high     { color: #e17055 !important; }
.vpe-rt-risk-critical { color: #d63031 !important; font-weight: 700; }
.vpe-rt-risk-medium   { color: #fdcb6e !important; }

/* Execution plan table (VPUI-002) */
.vpe-dialog-section { margin-top: 8px; }
.vpe-dialog-subtitle { font-size: 12px; font-weight: 700; color: #74b9ff; margin-bottom: 6px; }
.vpe-warn-banner { background: #2d1b00; border: 1px solid #fdcb6e; border-radius: 4px; padding: 6px 10px; color: #fdcb6e; font-size: 11px; margin-bottom: 6px; }
.vpe-exec-table { width: 100%; border-collapse: collapse; font-size: 11px; }
.vpe-exec-table th { text-align: left; padding: 4px 6px; border-bottom: 1px solid #636e72; color: #74b9ff; font-size: 10px; }
.vpe-exec-table td { padding: 4px 6px; border-bottom: 1px solid #2d3436; vertical-align: top; }
.vpe-exec-not-exec td { opacity: 0.75; }
.vpe-exec-not-exec td:first-child { color: #e17055; }
.vpe-risk-high { color: #e17055; font-weight: 700; }
  `],
})
export class VisualProcessEditorComponent implements OnInit, OnDestroy {
  private api = inject(VisualProcessApiService);
  private subs = new Subscription();

  @ViewChild('bpmnFileInput') bpmnFileInputRef!: ElementRef<HTMLInputElement>;

  // ── public constants for template ─────────────────────────────────────────
  readonly NODE_W = NODE_W;
  readonly artifactKinds = ['text','code','report','json','file','dataset','image','binary','vector','unknown'];
  readonly edgeKinds = ['always','on_success','on_failure','on_output','back_edge','expression'];
  readonly encodingModes = ENCODING_MODES;
  readonly ragChannels = RAG_CHANNELS;

  // ── state ──────────────────────────────────────────────────────────────────
  graph = signal<VpGraph>(emptyGraph());
  presets = signal<PresetSummary[]>([]);
  skillProfiles = signal<SkillProfile[]>([]);
  taskKindList = signal<TaskKindInfo[]>(FALLBACK_KINDS);
  savedGraphs = signal<SavedGraphSummary[]>([]);
  validationResult = signal<ValidationResult | null>(null);
  dryRunResult = signal<DryRunResult | null>(null);
  mermaidText = signal<string>('');
  mermaidTuiText = signal<string>('');
  statusMsg = signal<string>('');
  selectedId = signal<string | null>(null);
  edgeMode = signal<boolean>(false);
  edgeSourceId = signal<string | null>(null);
  isDirty = signal<boolean>(false);
  activeWorkflowId = signal<string | null>(null);
  workflowStatus = signal<WorkflowStatus | null>(null);

  loadPresetMenu = false;
  loadSavedMenu = false;
  showGraphDetails = false;
  mermaidTab: 'mermaid' | 'tui' = 'mermaid';

  private _showMermaidDialog = false;

  // ── canvas pan/zoom ────────────────────────────────────────────────────────
  private panX = 20;
  private panY = 20;
  private zoom = 1;
  private isPanning = false;
  private panStart = { x: 0, y: 0 };
  private panStartOrigin = { x: 0, y: 0 };

  // ── node drag ──────────────────────────────────────────────────────────────
  private dragId: string | null = null;
  private dragOffset = { x: 0, y: 0 };

  // ── live edge drawing ──────────────────────────────────────────────────────
  drawingEdge = signal<boolean>(false);
  private mouseSvg = { x: 0, y: 0 };

  // ── polling ────────────────────────────────────────────────────────────────
  private _pollHandle: ReturnType<typeof setInterval> | null = null;
  private _pollStartTime = 0;

  // ── computed ───────────────────────────────────────────────────────────────
  selectedStep = computed<VpStep | null>(() => {
    const id = this.selectedId();
    return this.graph().steps.find(s => s.id === id) ?? null;
  });

  selectedEdge = computed<VpEdge | null>(() => {
    const id = this.selectedId();
    return this.graph().edges.find(e => e.id === id) ?? null;
  });

  canvasTransform = computed(() => `translate(${this.panX}, ${this.panY}) scale(${this.zoom})`);

  kindGroups = computed(() => {
    const groups: Record<string, TaskKindInfo[]> = {};
    for (const k of this.taskKindList()) {
      (groups[k.group] ??= []).push(k);
    }
    const order = ['control_flow', 'worker', 'retrieval', 'ml'];
    return order.filter(g => groups[g]).map(g => ({ group: g, kinds: groups[g] }));
  });

  graphTagsStr = computed(() => this.graph().tags.join(', '));

  stepDescription = computed(() =>
    (this.selectedStep()?.metadata?.['description'] as string) ?? ''
  );

  gateStepId = computed<string | null>(() => {
    const status = this.workflowStatus();
    if (!status) return null;
    const steps = status['steps'] as any[] | undefined;
    if (!steps) return null;
    const graphSteps = this.graph().steps;
    const found = steps.find(
      s => s.run_state === 'awaiting_approval' && graphSteps.find(gs => gs.id === s.step_id)?.gate,
    );
    return found?.step_id ?? null;
  });

  expressionError = computed<string | null>(() => {
    const edge = this.selectedEdge();
    const result = this.validationResult();
    if (!edge || !result) return null;
    return result.issues.find(
      i => i.code === 'expression_syntax_error' && i.edge_id === edge.id,
    )?.message ?? null;
  });

  dryRunSummary = computed(() => {
    const r = this.dryRunResult();
    if (!r) return '';
    return JSON.stringify({
      valid: r.validation.valid,
      errors: r.validation.error_count,
      warnings: r.validation.warning_count,
      step_count: r.step_count,
      non_executable_count: r.non_executable_count ?? 0,
      policy: r.policy_summary,
    }, null, 2);
  });

  selectedStepKindInfo = computed<TaskKindInfo | null>(() => {
    const step = this.selectedStep();
    if (!step) return null;
    return this.taskKindList().find(k => k.id === step.kind) ?? null;
  });

  hasNonExecutableSteps = computed(() => {
    const plan = this.dryRunResult()?.step_execution_plan;
    if (plan) return plan.some(p => !p.executable);
    return false;
  });

  canStartWorkflow = computed(() => {
    if (!this.validationResult()?.valid) return false;
    if (this.activeWorkflowId()) return false;
    return true;
  });

  kindOptionSuffix(k: TaskKindInfo): string {
    const status = k.implementation_status;
    if (status === 'experimental') return ' [exp]';
    if (status === 'stub' || status === 'not_implemented') return ' [stub]';
    if (status === 'design_only') return ' [design]';
    const state = k.implementation_state;
    if (state === 'registered_only') return ' [reg]';
    if (!k.dispatch_capable && state === 'wired_and_executable') return ' (ML)';
    if (!k.dispatch_capable) return ' (ML)';
    return '';
  }

  // ── lifecycle ──────────────────────────────────────────────────────────────
  ngOnInit(): void {
    this.subs.add(this.api.listPresets().subscribe(p => this.presets.set(p)));
    this.subs.add(this.api.listSkillProfiles().subscribe(p => this.skillProfiles.set(p)));
    this.subs.add(this.api.listTaskKinds().subscribe({
      next: k => this.taskKindList.set(k),
      error: () => { /* keep fallback */ },
    }));
    this.subs.add(this.api.listSavedGraphs().subscribe({
      next: g => this.savedGraphs.set(g),
      error: () => { /* ignore if backend not running */ },
    }));
  }

  ngOnDestroy(): void {
    this.subs.unsubscribe();
    this.stopPolling();
  }

  // ── keyboard ──────────────────────────────────────────────────────────────
  @HostListener('document:keydown', ['$event'])
  onKey(e: KeyboardEvent): void {
    if ((e.target as HTMLElement).tagName === 'INPUT' || (e.target as HTMLElement).tagName === 'TEXTAREA') return;
    if (e.key === 'Delete' || e.key === 'Backspace') this.deleteSelected();
    if (e.key === 'n' || e.key === 'N') this.addStep();
    if (e.key === 'e' || e.key === 'E') this.toggleEdgeMode();
    if (e.key === 'Escape') { this.edgeMode.set(false); this.edgeSourceId.set(null); this.drawingEdge.set(false); }
  }

  // ── canvas interaction ─────────────────────────────────────────────────────
  onCanvasMouseDown(e: MouseEvent): void {
    if (e.altKey) {
      this.isPanning = true;
      this.panStart = { x: e.clientX, y: e.clientY };
      this.panStartOrigin = { x: this.panX, y: this.panY };
      return;
    }
    if ((e.target as SVGElement).closest('.vpe-node-g') || (e.target as SVGElement).closest('.vpe-edge-g')) return;
    this.selectedId.set(null);
    this.loadPresetMenu = false;
    this.loadSavedMenu = false;
  }

  onMouseMove(e: MouseEvent): void {
    if (this.isPanning) {
      this.panX = this.panStartOrigin.x + (e.clientX - this.panStart.x);
      this.panY = this.panStartOrigin.y + (e.clientY - this.panStart.y);
      return;
    }
    if (this.dragId) {
      const { svgX, svgY } = this.clientToSvg(e.clientX, e.clientY);
      this.mutateStep(this.dragId, s => {
        s.position.x = svgX - this.dragOffset.x;
        s.position.y = svgY - this.dragOffset.y;
      });
      return;
    }
    if (this.drawingEdge()) {
      const { svgX, svgY } = this.clientToSvg(e.clientX, e.clientY);
      this.mouseSvg = { x: svgX, y: svgY };
    }
  }

  onMouseUp(_e: MouseEvent): void {
    this.isPanning = false;
    this.dragId = null;
  }

  onWheel(e: WheelEvent): void {
    e.preventDefault();
    const factor = e.deltaY < 0 ? 1.1 : 0.91;
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const cx = e.clientX - rect.left;
    const cy = e.clientY - rect.top;
    this.panX = cx - factor * (cx - this.panX);
    this.panY = cy - factor * (cy - this.panY);
    this.zoom = Math.min(4, Math.max(0.2, this.zoom * factor));
  }

  onNodeMouseDown(e: MouseEvent, id: string): void {
    e.stopPropagation();
    if (this.edgeMode()) return;
    const { svgX, svgY } = this.clientToSvg(e.clientX, e.clientY);
    const step = this.graph().steps.find(s => s.id === id)!;
    this.dragId = id;
    this.dragOffset = { x: svgX - step.position.x, y: svgY - step.position.y };
  }

  selectStep(id: string): void {
    if (this.edgeMode()) {
      const src = this.edgeSourceId();
      if (!src) {
        this.edgeSourceId.set(id);
        this.drawingEdge.set(true);
        this.statusMsg.set('Klicke Zielknoten…');
      } else if (src !== id) {
        this.addEdge(src, id);
        this.edgeMode.set(false);
        this.edgeSourceId.set(null);
        this.drawingEdge.set(false);
        this.statusMsg.set('Kante hinzugefügt');
      }
      return;
    }
    this.selectedId.set(id);
  }

  selectEdge(id: string): void {
    if (this.edgeMode()) return;
    this.selectedId.set(id);
  }

  // ── graph mutations ────────────────────────────────────────────────────────
  addStep(): void {
    const id = stepId();
    const x = 60 + Math.random() * 300;
    const y = 80 + Math.random() * 200;
    const newStep: VpStep = {
      id, label: 'Neuer Schritt', kind: 'patch_propose', role: '',
      io: { inputs: [], outputs: [] },
      position: { x, y }, policy_hints: [], gate: false, metadata: {},
    };
    this.graph.update(g => ({ ...g, steps: [...g.steps, newStep] }));
    this.selectedId.set(id);
    this.validationResult.set(null);
    this.isDirty.set(true);
  }

  addEdge(source: string, target: string): void {
    const e: VpEdge = { id: edgeId(), source, target, condition: { kind: 'always' } };
    this.graph.update(g => ({ ...g, edges: [...g.edges, e] }));
    this.validationResult.set(null);
    this.isDirty.set(true);
  }

  deleteSelected(): void {
    const id = this.selectedId();
    if (!id) return;
    this.graph.update(g => ({
      ...g,
      steps: g.steps.filter(s => s.id !== id),
      edges: g.edges.filter(e => e.id !== id && e.source !== id && e.target !== id),
    }));
    this.selectedId.set(null);
    this.validationResult.set(null);
    this.isDirty.set(true);
  }

  toggleEdgeMode(): void {
    const next = !this.edgeMode();
    this.edgeMode.set(next);
    if (!next) { this.edgeSourceId.set(null); this.drawingEdge.set(false); this.statusMsg.set(''); }
    else this.statusMsg.set('Kante-Modus: klicke Quell-Knoten');
  }

  setGraphName(val: string): void {
    this.graph.update(g => ({ ...g, name: val }));
    this.isDirty.set(true);
  }

  setGraphDescription(val: string): void {
    this.graph.update(g => ({ ...g, description: val }));
    this.isDirty.set(true);
  }

  setTags(val: string): void {
    const tags = val.split(',').map(t => t.trim()).filter(Boolean);
    this.graph.update(g => ({ ...g, tags }));
    this.isDirty.set(true);
  }

  // ── step inspector helpers ─────────────────────────────────────────────────
  mutateSelectedStep(fn: (s: VpStep) => void): void {
    const step = this.selectedStep();
    if (!step) return;
    this.mutateStep(step.id, fn);
    this.isDirty.set(true);
  }

  onKindChange(kind: string): void {
    this.mutateSelectedStep(s => s.kind = kind);
    setTimeout(() => this.refreshPolicyHints(), 200);
  }

  setStepDescription(val: string): void {
    this.mutateSelectedStep(s => {
      s.metadata = { ...(s.metadata ?? {}), description: val };
    });
  }

  stepMeta(key: string): any {
    return this.selectedStep()?.metadata?.[key] ?? null;
  }

  setStepMeta(key: string, val: unknown): void {
    this.mutateSelectedStep(s => {
      s.metadata = { ...(s.metadata ?? {}), [key]: val };
    });
  }

  setStepLabel(val: string): void { this.mutateSelectedStep(s => s.label = val); }
  setStepRole(val: string): void { this.mutateSelectedStep(s => s.role = val); }
  setStepSkillProfile(val: string): void {
    this.mutateSelectedStep(s => s.agent_skill_profile_id = val);
  }
  setStepGate(val: boolean): void { this.mutateSelectedStep(s => s.gate = val); }

  isChannelSelected(ch: string): boolean {
    const channels = this.stepMeta('channels') as string[] | null;
    return channels ? channels.includes(ch) : ch === 'dense' || ch === 'lexical';
  }

  toggleChannel(ch: string, selected: boolean): void {
    const current = (this.stepMeta('channels') as string[] | null) ?? ['dense', 'lexical'];
    const next = selected ? [...new Set([...current, ch])] : current.filter(c => c !== ch);
    this.setStepMeta('channels', next);
  }

  // ── edge inspector helpers ─────────────────────────────────────────────────
  mutateEdge(fn: (e: VpEdge) => void): void {
    const edge = this.selectedEdge();
    if (!edge) return;
    this.graph.update(g => ({
      ...g,
      edges: g.edges.map(e => {
        if (e.id !== edge.id) return e;
        const copy = JSON.parse(JSON.stringify(e)) as VpEdge;
        fn(copy);
        return copy;
      }),
    }));
    this.isDirty.set(true);
  }

  setLoopPolicy(field: 'kind' | 'condition' | 'break_on_output' | 'max_iterations', value: unknown): void {
    this.mutateEdge(e => {
      if (!e.condition.loop_policy) {
        e.condition.loop_policy = { kind: 'fixed', max_iterations: 3 };
      }
      (e.condition.loop_policy as any)[field] = value;
    });
  }

  setEdgeLabel(val: string): void {
    this.mutateEdge(e => e.label = val || undefined);
  }
  setEdgeConditionKind(val: string): void {
    this.mutateEdge(e => e.condition.kind = val as any);
  }
  setEdgeExpression(val: string): void {
    this.mutateEdge(e => e.condition.expression = val);
  }
  setEdgeOutputName(val: string): void {
    this.mutateEdge(e => e.condition.output_name = val);
  }

  // ── io helpers ─────────────────────────────────────────────────────────────
  mutateIOInput(idx: number, field: string, val: unknown): void {
    this.mutateSelectedStep(s => { (s.io.inputs[idx] as any)[field] = val; });
  }

  mutateIOOutput(idx: number, field: string, val: unknown): void {
    this.mutateSelectedStep(s => { (s.io.outputs[idx] as any)[field] = val; });
  }

  addInput(): void {
    this.mutateSelectedStep(s => s.io.inputs.push({ name: 'input', kind: 'text', required: true }));
  }

  removeInput(idx: number): void {
    this.mutateSelectedStep(s => s.io.inputs.splice(idx, 1));
  }

  addOutput(): void {
    this.mutateSelectedStep(s => s.io.outputs.push({ name: 'output', kind: 'text', required: false }));
  }

  removeOutput(idx: number): void {
    this.mutateSelectedStep(s => s.io.outputs.splice(idx, 1));
  }

  applyProfile(profileId: string): void {
    const step = this.selectedStep();
    if (!step) { this.statusMsg.set('Wähle zuerst einen Schritt aus'); return; }
    const profile = this.skillProfiles().find(p => p.id === profileId);
    this.mutateStep(step.id, s => {
      s.agent_skill_profile_id = profileId;
      if (profile?.task_kinds?.[0]) s.kind = profile.task_kinds[0];
    });
    this.statusMsg.set(`Profil "${profileId}" angewendet`);
    this.isDirty.set(true);
    setTimeout(() => this.refreshPolicyHints(), 200);
  }

  // ── presets / saved ────────────────────────────────────────────────────────
  loadPreset(id: string): void {
    this.loadPresetMenu = false;
    this.subs.add(this.api.getPreset(id).subscribe({
      next: g => {
        this.graph.set(g);
        this.selectedId.set(null);
        this.validationResult.set(null);
        this.isDirty.set(false);
        this.statusMsg.set(`Preset "${g.name}" geladen`);
        setTimeout(() => this.refreshPolicyHints(), 300);
      },
      error: () => this.statusMsg.set('Preset konnte nicht geladen werden'),
    }));
  }

  loadSavedGraphById(id: string): void {
    this.loadSavedMenu = false;
    this.subs.add(this.api.loadSavedGraph(id).subscribe({
      next: g => {
        this.graph.set(g);
        this.selectedId.set(null);
        this.validationResult.set(null);
        this.isDirty.set(false);
        this.statusMsg.set(`"${g.name}" geladen`);
        setTimeout(() => this.refreshPolicyHints(), 300);
      },
      error: () => this.statusMsg.set('Graph konnte nicht geladen werden'),
    }));
  }

  saveGraphToServer(): void {
    this.subs.add(this.api.saveGraph(this.graph()).subscribe({
      next: r => {
        this.isDirty.set(false);
        this.statusMsg.set(`Gespeichert ✓ (${this.graph().name})`);
        this.api.listSavedGraphs().subscribe(g => this.savedGraphs.set(g));
      },
      error: () => this.statusMsg.set('Speichern fehlgeschlagen'),
    }));
  }

  // ── validation / dry-run ───────────────────────────────────────────────────
  validateGraph(): void {
    this.subs.add(this.api.validate(this.graph()).subscribe({
      next: r => { this.validationResult.set(r); this.statusMsg.set(r.valid ? 'Gültig ✓' : `${r.error_count} Fehler`); },
      error: () => this.statusMsg.set('Validierung fehlgeschlagen'),
    }));
  }

  runDryRun(): void {
    this.statusMsg.set('Dry-Run läuft…');
    this.subs.add(this.api.dryRun(this.graph()).subscribe({
      next: r => { this.dryRunResult.set(r); this.validationResult.set(r.validation); this.statusMsg.set('Dry-Run abgeschlossen'); },
      error: () => this.statusMsg.set('Dry-Run fehlgeschlagen'),
    }));
  }

  saveAsBlueprintFromDryRun(): void {
    this.subs.add(this.api.saveAsBlueprint(this.graph()).subscribe({
      next: r => this.statusMsg.set(`Blueprint gespeichert (id: ${r.blueprint_id})`),
      error: (err) => this.statusMsg.set(`Blueprint-Fehler: ${err?.error?.detail ?? 'unbekannt'}`),
    }));
  }

  // ── policy hints ───────────────────────────────────────────────────────────
  refreshPolicyHints(): void {
    this.subs.add(this.api.policySummary(this.graph()).subscribe({
      next: result => {
        this.graph.update(g => ({
          ...g,
          steps: g.steps.map(s => ({
            ...s,
            policy_hints: result.per_step[s.id] ?? s.policy_hints,
          })),
        }));
      },
      error: () => { /* keep existing hints */ },
    }));
  }

  // ── workflow execution ─────────────────────────────────────────────────────
  startWorkflow(): void {
    this.subs.add(this.api.startWorkflowFromGraph(this.graph()).subscribe({
      next: status => {
        this.activeWorkflowId.set(status.workflow_id);
        this.workflowStatus.set(status);
        this.statusMsg.set(`Workflow gestartet (id: ${status.workflow_id})`);
        this.startPolling();
      },
      error: (err) => this.statusMsg.set(`Fehler: ${err?.error?.detail ?? 'Workflow konnte nicht gestartet werden'}`),
    }));
  }

  cancelWorkflow(): void {
    const id = this.activeWorkflowId();
    if (!id) return;
    this.subs.add(this.api.cancelWorkflow(id).subscribe({
      next: () => { this.stopPolling(); this.statusMsg.set('Workflow abgebrochen'); },
      error: () => this.statusMsg.set('Abbrechen fehlgeschlagen'),
    }));
  }

  approveGate(): void {
    const wfId = this.activeWorkflowId();
    const gateId = this.gateStepId();
    if (!wfId || !gateId) return;
    this.subs.add(this.api.signalWorkflow(wfId, 'approve', { step_id: gateId }).subscribe({
      next: () => this.statusMsg.set('Gate genehmigt ✓'),
      error: (err) => this.statusMsg.set(`Gate-Fehler: ${err?.error?.detail ?? 'unbekannt'}`),
    }));
  }

  rejectGate(): void {
    const wfId = this.activeWorkflowId();
    const gateId = this.gateStepId();
    if (!wfId || !gateId) return;
    this.subs.add(this.api.signalWorkflow(wfId, 'reject', { step_id: gateId }).subscribe({
      next: () => this.statusMsg.set('Gate abgelehnt'),
      error: (err) => this.statusMsg.set(`Gate-Fehler: ${err?.error?.detail ?? 'unbekannt'}`),
    }));
  }

  private startPolling(): void {
    this.stopPolling();
    this._pollStartTime = Date.now();
    this._pollHandle = setInterval(() => {
      const id = this.activeWorkflowId();
      if (!id) { this.stopPolling(); return; }
      if (Date.now() - this._pollStartTime > POLL_MAX_MS) {
        this.stopPolling();
        this.statusMsg.set('Polling-Timeout (10 min) — Workflow-Status unbekannt');
        return;
      }
      this.api.getWorkflowStatus(id).subscribe({
        next: status => {
          this.workflowStatus.set(status);
          // Patch run_state back onto graph steps if backend provides them
          const steps = status['steps'] as any[] | undefined;
          if (steps?.length) {
            this.graph.update(g => ({
              ...g,
              steps: g.steps.map(s => {
                const found = steps.find((ws: any) => ws.step_id === s.id);
                return found ? { ...s, run_state: found.run_state } : s;
              }),
            }));
          }
          if (['done', 'failed', 'cancelled'].includes(status.status)) {
            this.stopPolling();
            this.activeWorkflowId.set(null);
            const msg = status.status === 'done' ? 'Workflow abgeschlossen ✓' : `Workflow ${status.status}`;
            this.statusMsg.set(msg);
          }
        },
      });
    }, POLL_INTERVAL_MS);
  }

  private stopPolling(): void {
    if (this._pollHandle !== null) {
      clearInterval(this._pollHandle);
      this._pollHandle = null;
    }
  }

  // ── BPMN ───────────────────────────────────────────────────────────────────
  exportBpmn(): void {
    this.subs.add(this.api.exportBpmn(this.graph()).subscribe({
      next: result => {
        const blob = new Blob([result.bpmn_xml], { type: 'application/xml' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = `${this.graph().name}.bpmn`; a.click();
        URL.revokeObjectURL(url);
        this.statusMsg.set(result.warnings?.length
          ? `BPMN exportiert (Warnungen: ${result.warnings.join(', ')})`
          : 'BPMN exportiert ✓');
      },
      error: (err) => this.statusMsg.set(`BPMN-Export-Fehler: ${err?.error?.detail ?? 'unbekannt'}`),
    }));
  }

  onBpmnFile(event: Event): void {
    const file = (event.target as HTMLInputElement).files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (e) => {
      const xml = e.target?.result as string;
      this.subs.add(this.api.importBpmn(xml).subscribe({
        next: result => {
          this.graph.set(result.graph);
          this.validationResult.set(result.validation);
          this.isDirty.set(true);
          const warns = result.warnings?.length ? ` (${result.warnings.join(', ')})` : '';
          this.statusMsg.set(`BPMN importiert: ${result.graph.steps.length} Schritte${warns}`);
          setTimeout(() => this.refreshPolicyHints(), 300);
        },
        error: (err) => this.statusMsg.set(`BPMN-Import-Fehler: ${err?.error?.detail ?? 'ungültige Datei'}`),
      }));
    };
    reader.readAsText(file);
    (event.target as HTMLInputElement).value = '';
  }

  // ── Mermaid ────────────────────────────────────────────────────────────────
  openMermaid(): void {
    this._showMermaidDialog = true;
    this.mermaidTab = 'mermaid';
    this.subs.add(this.api.mermaid(this.graph()).subscribe({
      next: r => { this.mermaidText.set(r.mermaid); this.mermaidTuiText.set(r.tui ?? ''); },
      error: () => this.mermaidText.set('Fehler beim Laden'),
    }));
  }

  get showMermaidDialog(): boolean { return this._showMermaidDialog; }
  set showMermaidDialog(val: boolean) { this._showMermaidDialog = val; }

  copyMermaid(): void {
    navigator.clipboard?.writeText(this.mermaidText()).then(() => this.statusMsg.set('Mermaid kopiert ✓'));
  }

  downloadMermaid(): void {
    const blob = new Blob([`\`\`\`mermaid\n${this.mermaidText()}\n\`\`\``], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `${this.graph().name}.md`; a.click();
    URL.revokeObjectURL(url);
  }

  // ── auto-layout ────────────────────────────────────────────────────────────
  autoLayout(): void {
    const g = this.graph();
    const forwardEdges = g.edges.filter(e => e.condition.kind !== 'back_edge');
    const inDegree: Record<string, number> = {};
    const adj: Record<string, string[]> = {};
    for (const s of g.steps) { inDegree[s.id] = 0; adj[s.id] = []; }
    for (const e of forwardEdges) {
      inDegree[e.target] = (inDegree[e.target] ?? 0) + 1;
      adj[e.source].push(e.target);
    }
    // Kahn's algorithm
    const queue = g.steps.filter(s => !inDegree[s.id]).map(s => s.id);
    const depthMap: Record<string, number> = {};
    let order: string[] = [];
    while (queue.length) {
      const node = queue.shift()!;
      order.push(node);
      for (const next of adj[node]) {
        inDegree[next]--;
        depthMap[next] = Math.max(depthMap[next] ?? 0, (depthMap[node] ?? 0) + 1);
        if (inDegree[next] === 0) queue.push(next);
      }
    }
    // Add any remaining (in cycles, shouldn't happen after validation)
    const placed = new Set(order);
    for (const s of g.steps) if (!placed.has(s.id)) order.push(s.id);

    // Group by column
    const cols: Record<number, string[]> = {};
    for (const id of order) {
      const col = depthMap[id] ?? 0;
      (cols[col] ??= []).push(id);
    }
    const colGap = NODE_W + 60;
    const rowGap = NODE_H + 40;
    const newPositions: Record<string, { x: number; y: number }> = {};
    for (const [colStr, ids] of Object.entries(cols)) {
      const col = Number(colStr);
      ids.forEach((id, row) => {
        newPositions[id] = { x: 40 + col * colGap, y: 40 + row * rowGap };
      });
    }
    this.graph.update(prev => ({
      ...prev,
      steps: prev.steps.map(s => ({
        ...s,
        position: newPositions[s.id] ?? s.position,
      })),
    }));
    this.isDirty.set(true);
    this.statusMsg.set('Auto-Layout angewendet');
  }

  // ── helpers ────────────────────────────────────────────────────────────────
  formatDate(ts: number): string {
    return new Date(ts * 1000).toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' });
  }

  private clientToSvg(cx: number, cy: number): { svgX: number; svgY: number } {
    const wrap = document.querySelector('.vpe-canvas-wrap');
    if (!wrap) return { svgX: cx, svgY: cy };
    const rect = wrap.getBoundingClientRect();
    return {
      svgX: (cx - rect.left - this.panX) / this.zoom,
      svgY: (cy - rect.top - this.panY) / this.zoom,
    };
  }

  edgePath(edge: VpEdge): string {
    const src = this.graph().steps.find(s => s.id === edge.source);
    const tgt = this.graph().steps.find(s => s.id === edge.target);
    if (!src || !tgt) return '';
    return this.bezierPath(
      src.position.x + NODE_W, src.position.y + NODE_H / 2,
      tgt.position.x, tgt.position.y + NODE_H / 2,
      edge.condition.kind === 'back_edge',
    );
  }

  edgeMidpoint(edge: VpEdge): { x: number; y: number } {
    const src = this.graph().steps.find(s => s.id === edge.source);
    const tgt = this.graph().steps.find(s => s.id === edge.target);
    if (!src || !tgt) return { x: 0, y: 0 };
    return {
      x: (src.position.x + NODE_W + tgt.position.x) / 2,
      y: (src.position.y + tgt.position.y) / 2 + NODE_H / 2,
    };
  }

  liveEdgePath(): string {
    const src = this.graph().steps.find(s => s.id === this.edgeSourceId());
    if (!src) return '';
    return this.bezierPath(src.position.x + NODE_W, src.position.y + NODE_H / 2, this.mouseSvg.x, this.mouseSvg.y, false);
  }

  private bezierPath(x1: number, y1: number, x2: number, y2: number, isBack: boolean): string {
    const dx = Math.abs(x2 - x1) * 0.5 || 60;
    if (isBack) {
      const cy = Math.max(y1, y2) + 60;
      return `M ${x1} ${y1} C ${x1} ${cy}, ${x2} ${cy}, ${x2} ${y2}`;
    }
    return `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
  }

  diamondPoints(): string {
    const cx = NODE_W / 2;
    const cy = NODE_H / 2;
    const rx = NODE_W / 2 - 2;
    const ry = NODE_H / 2 - 2;
    return `${cx},${cy - ry} ${cx + rx},${cy} ${cx},${cy + ry} ${cx - rx},${cy}`;
  }

  nodeColor(step: VpStep): string {
    const kindOverride = nodeKindColor(step.kind);
    if (kindOverride) return kindOverride;
    return hintColor(step.policy_hints);
  }

  runStateColor(state: string): string {
    const m: Record<string, string> = { done: '#55efc4', running: '#fdcb6e', failed: '#ff7675', pending: '#636e72', skipped: '#b2bec3', awaiting_approval: '#e17055' };
    return m[state] ?? '#636e72';
  }

  stepLabel(id: string): string {
    return this.graph().steps.find(s => s.id === id)?.label ?? id;
  }

  private mutateStep(id: string, fn: (s: VpStep) => void): void {
    this.graph.update(g => ({
      ...g,
      steps: g.steps.map(s => {
        if (s.id !== id) return s;
        const copy = JSON.parse(JSON.stringify(s)) as VpStep;
        fn(copy);
        return copy;
      }),
    }));
  }
}
