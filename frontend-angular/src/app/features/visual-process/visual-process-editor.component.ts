import {
  Component, OnInit, OnDestroy, inject,
  signal, computed, HostListener, ElementRef, ViewChild,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Subscription } from 'rxjs';
import {
  VisualProcessApiService,
  VpGraph, VpStep, VpEdge, ArtifactRef,
  ValidationResult, DryRunResult, SkillProfile, PresetSummary,
} from './visual-process-api.service';

// ── constants ─────────────────────────────────────────────────────────────────
const NODE_W = 140;
const NODE_H = 52;
const TASK_KINDS = ['coding','analysis','run_tests','code_review','llm_generate',
                    'goal_plan','deploy','research','read_file','grep_search','refactor'];

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
  if (hints.includes('read_only')) return '#74b9ff';
  return '#a29bfe';
}

@Component({
  standalone: true,
  selector: 'app-visual-process-editor',
  imports: [CommonModule, FormsModule],
  template: `
<!-- ── toolbar ───────────────────────────────────────────────────────────── -->
<div class="vpe-toolbar">
  <span class="vpe-title">
    <input class="vpe-title-input" [(ngModel)]="graph().name" placeholder="Prozessname…" />
  </span>

  <div class="vpe-tb-group">
    <button class="vpe-btn" (click)="addStep()" title="Neuer Schritt (N)">+ Schritt</button>
    <button class="vpe-btn" [class.active]="edgeMode()" (click)="toggleEdgeMode()" title="Kante zeichnen (E)">
      {{ edgeMode() ? '✏ Kante… klicke Quelle' : '→ Kante' }}
    </button>
    <button class="vpe-btn danger" [disabled]="!selectedId()" (click)="deleteSelected()" title="Löschen (Del)">Löschen</button>
  </div>

  <div class="vpe-tb-group">
    <button class="vpe-btn" (click)="loadPresetMenu = !loadPresetMenu">Preset ▾</button>
    @if (loadPresetMenu) {
      <div class="vpe-dropdown">
        @for (p of presets(); track p.id) {
          <button class="vpe-dd-item" (click)="loadPreset(p.id)">{{ p.name }}</button>
        }
      </div>
    }
    <button class="vpe-btn" (click)="validateGraph()">Validieren</button>
    <button class="vpe-btn" (click)="runDryRun()">Dry-Run</button>
    <button class="vpe-btn" (click)="showMermaidDialog = true">Mermaid</button>
  </div>
</div>

<!-- ── main area ─────────────────────────────────────────────────────────── -->
<div class="vpe-main">

  <!-- SVG canvas -->
  <div class="vpe-canvas-wrap" #canvasWrap
       (mousedown)="onCanvasMouseDown($event)"
       (mousemove)="onMouseMove($event)"
       (mouseup)="onMouseUp($event)"
       (wheel)="onWheel($event)">

    <svg #svgEl class="vpe-svg"
         [attr.width]="'100%'" [attr.height]="'100%'">
      <defs>
        <marker id="arrowhead" markerWidth="8" markerHeight="6"
                refX="8" refY="3" orient="auto">
          <polygon points="0 0, 8 3, 0 6" fill="#aaa"/>
        </marker>
        <marker id="arrowback" markerWidth="8" markerHeight="6"
                refX="0" refY="3" orient="auto">
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
             [class.edge-source]="edgeSourceId() === step.id">

            <rect [attr.width]="NODE_W" [attr.height]="NODE_H" rx="7"
                  [attr.fill]="nodeColor(step)"
                  class="vpe-node-rect"/>

            @if (step.gate) {
              <text x="4" y="14" class="vpe-node-icon">🔒</text>
            }
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

    <!-- pan/zoom hint -->
    @if (graph().steps.length === 0) {
      <div class="vpe-empty-hint">
        Klicke "+ Schritt" um zu beginnen, oder lade ein Preset.
      </div>
    }
  </div>

  <!-- right panel -->
  <div class="vpe-panel">
    <!-- step inspector -->
    @if (selectedStep()) {
      <div class="vpe-inspector">
        <div class="vpe-panel-title">Schritt</div>

        <label class="vpe-label">Label
          <input class="vpe-input" [(ngModel)]="selectedStep()!.label" />
        </label>
        <label class="vpe-label">Typ
          <select class="vpe-input" [(ngModel)]="selectedStep()!.kind">
            @for (k of taskKinds; track k) { <option [value]="k">{{ k }}</option> }
          </select>
        </label>
        <label class="vpe-label">Rolle
          <input class="vpe-input" [(ngModel)]="selectedStep()!.role" placeholder="z.B. developer" />
        </label>
        <label class="vpe-label">Skill-Profil
          <select class="vpe-input" [(ngModel)]="selectedStep()!.agent_skill_profile_id">
            <option value="">— keins —</option>
            @for (p of skillProfiles(); track p.id) {
              <option [value]="p.id">{{ p.name }}</option>
            }
          </select>
        </label>
        <label class="vpe-label vpe-checkbox">
          <input type="checkbox" [(ngModel)]="selectedStep()!.gate" />
          Gate (Freigabe erforderlich)
        </label>

        <!-- inputs -->
        <div class="vpe-io-section">
          <div class="vpe-io-title">Inputs</div>
          @for (inp of selectedStep()!.io.inputs; track $index) {
            <div class="vpe-io-row">
              <input class="vpe-input sm" [(ngModel)]="inp.name" placeholder="name" />
              <select class="vpe-input sm" [(ngModel)]="inp.kind">
                @for (k of artifactKinds; track k) { <option [value]="k">{{ k }}</option> }
              </select>
              <input type="checkbox" [(ngModel)]="inp.required" title="Pflichtfeld" />
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
              <input class="vpe-input sm" [(ngModel)]="out.name" placeholder="name" />
              <select class="vpe-input sm" [(ngModel)]="out.kind">
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
          <input class="vpe-input" [(ngModel)]="selectedEdge()!.label" placeholder="optional" />
        </label>
        <label class="vpe-label">Bedingung
          <select class="vpe-input" [(ngModel)]="selectedEdge()!.condition.kind">
            @for (k of edgeKinds; track k) { <option [value]="k">{{ k }}</option> }
          </select>
        </label>
        @if (selectedEdge()!.condition.kind === 'back_edge') {
          <label class="vpe-label">Max. Iterationen
            <input class="vpe-input" type="number" min="1" max="20"
                   [ngModel]="selectedEdge()!.condition.loop_policy?.max_iterations ?? 3"
                   (ngModelChange)="setLoopIterations($event)" />
          </label>
        }
        @if (selectedEdge()!.condition.kind === 'expression') {
          <label class="vpe-label">Ausdruck
            <input class="vpe-input" [(ngModel)]="selectedEdge()!.condition.expression" placeholder="z.B. output.score > 0.8" />
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
      @if (validationResult()!.warning_count) {
        · {{ validationResult()!.warning_count }} Warnungen
      }
    </span>
  }
  @if (statusMsg()) {
    <span class="vpe-status-msg">{{ statusMsg() }}</span>
  }
  <span class="vpe-zoom-hint">Scroll = Zoom · Alt+Drag = Verschieben</span>
</div>

<!-- ── validation issues ──────────────────────────────────────────────────── -->
@if (validationResult() && !validationResult()!.valid) {
  <div class="vpe-issues">
    @for (issue of validationResult()!.issues; track $index) {
      <div class="vpe-issue" [class.error]="issue.severity === 'error'" [class.warning]="issue.severity === 'warning'">
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
      <button class="vpe-btn" (click)="dryRunResult.set(null)">Schließen</button>
    </div>
  </div>
}

<!-- ── mermaid dialog ─────────────────────────────────────────────────────── -->
@if (showMermaidDialog) {
  <div class="vpe-dialog-overlay" (click)="showMermaidDialog = false">
    <div class="vpe-dialog wide" (click)="$event.stopPropagation()">
      <div class="vpe-dialog-title">Mermaid Export</div>
      <pre class="vpe-pre">{{ mermaidText() }}</pre>
      <button class="vpe-btn" (click)="copyMermaid()">Kopieren</button>
      <button class="vpe-btn" (click)="showMermaidDialog = false">Schließen</button>
    </div>
  </div>
}
`,
  styles: [`
:host { display: flex; flex-direction: column; height: 100%; min-height: 0; background: #1a1a2e; color: #eee; font-size: 13px; }

/* toolbar */
.vpe-toolbar { display: flex; align-items: center; gap: 8px; padding: 6px 12px; background: #16213e; border-bottom: 1px solid #0f3460; flex-shrink: 0; flex-wrap: wrap; }
.vpe-title { flex: 1 1 200px; }
.vpe-title-input { background: transparent; border: none; border-bottom: 1px solid #aaa; color: #eee; font-size: 14px; font-weight: 600; width: 100%; padding: 2px 0; outline: none; }
.vpe-tb-group { display: flex; gap: 4px; align-items: center; position: relative; }
.vpe-btn { padding: 4px 10px; border-radius: 4px; border: 1px solid #555; background: #2d3436; color: #eee; cursor: pointer; font-size: 12px; }
.vpe-btn:hover { background: #636e72; }
.vpe-btn.active { background: #0984e3; border-color: #0984e3; }
.vpe-btn.danger { border-color: #e17055; color: #e17055; }
.vpe-btn.danger:hover { background: #e17055; color: #fff; }
.vpe-btn:disabled { opacity: 0.4; cursor: default; }
.vpe-dropdown { position: absolute; top: 100%; left: 0; z-index: 100; background: #2d3436; border: 1px solid #636e72; border-radius: 4px; min-width: 180px; box-shadow: 0 4px 12px #0006; }
.vpe-dd-item { display: block; width: 100%; text-align: left; padding: 7px 12px; background: none; border: none; color: #eee; cursor: pointer; font-size: 12px; }
.vpe-dd-item:hover { background: #0984e3; }

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

/* edges */
.vpe-edge { fill: none; stroke: #aaa; stroke-width: 1.5; }
.vpe-edge.selected { stroke: #fdcb6e; stroke-width: 2.5; }
.vpe-edge.back-edge { stroke: #7f8c8d; stroke-dasharray: 6 3; }
.vpe-edge.live { stroke: #00cec9; stroke-dasharray: 5 4; }
.vpe-edge-g { cursor: pointer; }
.vpe-edge-label { fill: #aaa; font-size: 10px; text-anchor: middle; }

/* right panel */
.vpe-panel { width: 240px; flex-shrink: 0; display: flex; flex-direction: column; background: #16213e; border-left: 1px solid #0f3460; overflow-y: auto; }
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

/* library */
.vpe-library { padding: 6px; flex: 1 1 0; overflow-y: auto; }
.vpe-lib-item { padding: 6px 8px; border-radius: 4px; cursor: pointer; margin-bottom: 4px; background: #2d3436; }
.vpe-lib-item:hover { background: #0f3460; }
.vpe-lib-name { font-size: 12px; font-weight: 600; display: block; }
.vpe-lib-role { font-size: 10px; color: #74b9ff; }
.vpe-lib-tags { display: flex; gap: 3px; flex-wrap: wrap; margin-top: 3px; }
.vpe-lib-tag { background: #0f3460; border-radius: 8px; font-size: 9px; padding: 1px 5px; color: #aaa; }

/* status bar */
.vpe-status { display: flex; align-items: center; gap: 12px; padding: 4px 12px; background: #0f3460; font-size: 11px; color: #b2bec3; flex-shrink: 0; }
.vpe-status .ok { color: #55efc4; }
.vpe-status .err { color: #ff7675; }
.vpe-status-msg { color: #fdcb6e; }
.vpe-zoom-hint { margin-left: auto; opacity: 0.5; }

/* issues */
.vpe-issues { max-height: 120px; overflow-y: auto; background: #1a1a2e; border-top: 1px solid #0f3460; }
.vpe-issue { padding: 3px 12px; font-size: 11px; }
.vpe-issue.error { color: #ff7675; }
.vpe-issue.warning { color: #fdcb6e; }

/* dialogs */
.vpe-dialog-overlay { position: fixed; inset: 0; background: #000a; z-index: 1000; display: flex; align-items: center; justify-content: center; }
.vpe-dialog { background: #16213e; border: 1px solid #636e72; border-radius: 8px; padding: 20px; max-width: 600px; width: 90%; max-height: 80vh; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; }
.vpe-dialog.wide { max-width: 740px; }
.vpe-dialog-title { font-size: 14px; font-weight: 700; color: #74b9ff; }
.vpe-pre { background: #0f3460; border-radius: 4px; padding: 10px; font-size: 11px; white-space: pre-wrap; word-break: break-all; flex: 1; overflow-y: auto; max-height: 50vh; }
  `],
})
export class VisualProcessEditorComponent implements OnInit, OnDestroy {
  private api = inject(VisualProcessApiService);
  private subs = new Subscription();

  // ── public constants for template
  readonly NODE_W = NODE_W;
  readonly taskKinds = TASK_KINDS;
  readonly artifactKinds = ['text','code','report','json','file','dataset','image','binary'];
  readonly edgeKinds = ['always','on_success','on_failure','on_output','back_edge','expression'];

  // ── state
  graph = signal<VpGraph>(emptyGraph());
  presets = signal<PresetSummary[]>([]);
  skillProfiles = signal<SkillProfile[]>([]);
  validationResult = signal<ValidationResult | null>(null);
  dryRunResult = signal<DryRunResult | null>(null);
  mermaidText = signal<string>('');
  statusMsg = signal<string>('');
  selectedId = signal<string | null>(null);
  edgeMode = signal<boolean>(false);
  edgeSourceId = signal<string | null>(null);

  loadPresetMenu = false;

  // ── canvas pan/zoom
  private panX = 20;
  private panY = 20;
  private zoom = 1;
  private isPanning = false;
  private panStart = { x: 0, y: 0 };
  private panStartOrigin = { x: 0, y: 0 };

  // ── node drag
  private dragId: string | null = null;
  private dragOffset = { x: 0, y: 0 };

  // ── live edge drawing
  drawingEdge = signal<boolean>(false);
  private mouseSvg = { x: 0, y: 0 };

  // ── computed
  selectedStep = computed<VpStep | null>(() => {
    const id = this.selectedId();
    return this.graph().steps.find(s => s.id === id) ?? null;
  });

  selectedEdge = computed<VpEdge | null>(() => {
    const id = this.selectedId();
    return this.graph().edges.find(e => e.id === id) ?? null;
  });

  canvasTransform = computed(() =>
    `translate(${this.panX}, ${this.panY}) scale(${this.zoom})`
  );

  dryRunSummary = computed(() => {
    const r = this.dryRunResult();
    if (!r) return '';
    return JSON.stringify({
      valid: r.validation.valid,
      errors: r.validation.error_count,
      warnings: r.validation.warning_count,
      step_count: r.step_count,
      policy: r.policy_summary,
    }, null, 2);
  });

  ngOnInit(): void {
    this.subs.add(this.api.listPresets().subscribe(p => this.presets.set(p)));
    this.subs.add(this.api.listSkillProfiles().subscribe(p => this.skillProfiles.set(p)));
  }

  ngOnDestroy(): void { this.subs.unsubscribe(); }

  // ── keyboard
  @HostListener('document:keydown', ['$event'])
  onKey(e: KeyboardEvent): void {
    if ((e.target as HTMLElement).tagName === 'INPUT') return;
    if (e.key === 'Delete' || e.key === 'Backspace') this.deleteSelected();
    if (e.key === 'n' || e.key === 'N') this.addStep();
    if (e.key === 'e' || e.key === 'E') this.toggleEdgeMode();
    if (e.key === 'Escape') { this.edgeMode.set(false); this.edgeSourceId.set(null); this.drawingEdge.set(false); }
  }

  // ── canvas interaction
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

  onNodeMouseDown(e: MouseEvent, stepId: string): void {
    e.stopPropagation();
    if (this.edgeMode()) return;
    const { svgX, svgY } = this.clientToSvg(e.clientX, e.clientY);
    const step = this.graph().steps.find(s => s.id === stepId)!;
    this.dragId = stepId;
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

  // ── graph mutation
  addStep(): void {
    const id = stepId();
    const x = 60 + Math.random() * 300;
    const y = 80 + Math.random() * 200;
    const newStep: VpStep = {
      id, label: 'Neuer Schritt', kind: 'coding', role: '',
      io: { inputs: [], outputs: [] },
      position: { x, y }, policy_hints: [], gate: false,
    };
    this.graph.update(g => ({ ...g, steps: [...g.steps, newStep] }));
    this.selectedId.set(id);
    this.validationResult.set(null);
  }

  addEdge(source: string, target: string): void {
    const e: VpEdge = {
      id: edgeId(), source, target,
      condition: { kind: 'always' },
    };
    this.graph.update(g => ({ ...g, edges: [...g.edges, e] }));
    this.validationResult.set(null);
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
  }

  toggleEdgeMode(): void {
    const next = !this.edgeMode();
    this.edgeMode.set(next);
    if (!next) { this.edgeSourceId.set(null); this.drawingEdge.set(false); this.statusMsg.set(''); }
    else this.statusMsg.set('Kante-Modus: klicke Quell-Knoten');
  }

  addInput(): void {
    const step = this.selectedStep();
    if (!step) return;
    this.mutateStep(step.id, s => s.io.inputs.push({ name: 'input', kind: 'text', required: true }));
  }

  removeInput(idx: number): void {
    const step = this.selectedStep();
    if (!step) return;
    this.mutateStep(step.id, s => s.io.inputs.splice(idx, 1));
  }

  addOutput(): void {
    const step = this.selectedStep();
    if (!step) return;
    this.mutateStep(step.id, s => s.io.outputs.push({ name: 'output', kind: 'text', required: false }));
  }

  removeOutput(idx: number): void {
    const step = this.selectedStep();
    if (!step) return;
    this.mutateStep(step.id, s => s.io.outputs.splice(idx, 1));
  }

  applyProfile(profileId: string): void {
    const step = this.selectedStep();
    if (!step) { this.statusMsg.set('Wähle zuerst einen Schritt aus'); return; }
    this.mutateStep(step.id, s => { s.agent_skill_profile_id = profileId; });
    const profile = this.skillProfiles().find(p => p.id === profileId);
    if (profile?.task_kinds?.[0]) {
      this.mutateStep(step.id, s => { s.kind = profile.task_kinds[0]; });
    }
    this.statusMsg.set(`Profil "${profileId}" angewendet`);
  }

  setLoopIterations(val: number): void {
    const edge = this.selectedEdge();
    if (!edge) return;
    this.graph.update(g => ({
      ...g,
      edges: g.edges.map(e => e.id !== edge.id ? e : {
        ...e,
        condition: {
          ...e.condition,
          loop_policy: { kind: 'fixed', max_iterations: Number(val) },
        },
      }),
    }));
  }

  // ── presets
  loadPreset(id: string): void {
    this.loadPresetMenu = false;
    this.subs.add(this.api.getPreset(id).subscribe({
      next: g => { this.graph.set(g); this.selectedId.set(null); this.validationResult.set(null); this.statusMsg.set(`Preset "${g.name}" geladen`); },
      error: () => this.statusMsg.set('Preset konnte nicht geladen werden'),
    }));
  }

  // ── API actions
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

  copyMermaid(): void {
    navigator.clipboard?.writeText(this.mermaidText()).then(() => this.statusMsg.set('Mermaid kopiert ✓'));
  }

  // Called before showing the dialog
  ngDoCheck(): void {
    // noop — dialog managed via showMermaidDialog toggle + lazy loading
  }

  // ── SVG helpers
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
    return this.bezierPath(
      src.position.x + NODE_W, src.position.y + NODE_H / 2,
      this.mouseSvg.x, this.mouseSvg.y, false,
    );
  }

  private bezierPath(x1: number, y1: number, x2: number, y2: number, isBack: boolean): string {
    const dx = Math.abs(x2 - x1) * 0.5 || 60;
    if (isBack) {
      const cy = Math.max(y1, y2) + 60;
      return `M ${x1} ${y1} C ${x1} ${cy}, ${x2} ${cy}, ${x2} ${y2}`;
    }
    return `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
  }

  nodeColor(step: VpStep): string {
    return hintColor(step.policy_hints);
  }

  runStateColor(state: string): string {
    const m: Record<string, string> = { done: '#55efc4', running: '#fdcb6e', failed: '#ff7675', pending: '#636e72', skipped: '#b2bec3' };
    return m[state] ?? '#636e72';
  }

  stepLabel(id: string): string {
    return this.graph().steps.find(s => s.id === id)?.label ?? id;
  }

  private mutateStep(id: string, fn: (s: VpStep) => void): void {
    this.graph.update(g => {
      const steps = g.steps.map(s => {
        if (s.id !== id) return s;
        const copy = JSON.parse(JSON.stringify(s)) as VpStep;
        fn(copy);
        return copy;
      });
      return { ...g, steps };
    });
  }

  // open mermaid dialog and fetch
  set showMermaidDialog(val: boolean) {
    this._showMermaidDialog = val;
    if (val) {
      this.subs.add(this.api.mermaid(this.graph()).subscribe({
        next: r => this.mermaidText.set(r.mermaid),
        error: () => this.mermaidText.set('Fehler beim Laden'),
      }));
    }
  }
  get showMermaidDialog(): boolean { return this._showMermaidDialog; }
  private _showMermaidDialog = false;
}
