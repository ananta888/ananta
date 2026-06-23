import {
  AfterViewInit,
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  HostListener,
  OnDestroy,
  OnInit,
  ViewChild,
  computed,
  inject,
  signal,
} from '@angular/core';
import { interval, Subject, Subscription } from 'rxjs';
import { debounceTime, distinctUntilChanged, switchMap } from 'rxjs/operators';
import { InternalsService, AnantaWorker, AutopilotStatus, VpPreset, VpSkillProfile, VpGraph } from '../services/internals.service';
import { DecimalPipe, SlicePipe } from '@angular/common';
import { GraphViewerComponent } from '../../codecompass-graph/components/graph-viewer/graph-viewer.component';

// ─── Static Config Data (mirrored from Hub DB) ───────────────────────────────

interface BlueprintDef { id: string; name: string; roles: string[]; }
interface PlaybookTask { id: string; title: string; description: string; priority: 'High' | 'Medium' | 'Low'; }
interface PlaybookDef { id: string; name: string; tasks: PlaybookTask[]; }

const BLUEPRINTS: BlueprintDef[] = [
  { id: 'scrum', name: 'Scrum', roles: ['Product Owner', 'Scrum Master', 'Developer'] },
  { id: 'scrum-opencode', name: 'Scrum-OpenCode', roles: ['Product Owner', 'Scrum Master', 'Developer'] },
  { id: 'kanban', name: 'Kanban', roles: ['Service Delivery Manager', 'Flow Manager', 'Developer'] },
  { id: 'tdd', name: 'TDD', roles: ['Behavior Analyst', 'Test Driver', 'Refactor Verifier'] },
  { id: 'code-repair', name: 'Code-Repair', roles: ['Repair Lead', 'Fix Engineer', 'QA Verifier'] },
  { id: 'research', name: 'Research', roles: ['Research Lead', 'Source Analyst', 'Reviewer'] },
  { id: 'security-review', name: 'Security-Review', roles: ['Security Lead', 'Security Analyst', 'Compliance Reviewer'] },
  { id: 'release-prep', name: 'Release-Prep', roles: ['Release Manager', 'Verification Engineer', 'Operations Liaison'] },
  { id: 'story-domain', name: 'Story-Domain', roles: ['Story Analyst', 'Domain Modeler', 'Implementation Coder', 'Verification Tester'] },
  { id: 'research-evolution', name: 'Research-Evolution', roles: ['Research Lead', 'Evolution Strategist', 'Review Gate Owner'] },
];

const PLAYBOOKS: PlaybookDef[] = [
  { id: 'bug_fix', name: 'Bug Fix', tasks: [
    { id: 't1', title: 'Bug reproduzieren', description: 'Reproduktionsschritte dokumentieren', priority: 'High' },
    { id: 't2', title: 'Root Cause Analyse', description: 'Ursache identifizieren', priority: 'High' },
    { id: 't3', title: 'Fix implementieren', description: 'Korrektur umsetzen', priority: 'High' },
    { id: 't4', title: 'Test schreiben', description: 'Unit/Integration-Test erstellen', priority: 'Medium' },
    { id: 't5', title: 'Code Review', description: 'Fix zur Prüfung einreichen', priority: 'Medium' },
  ]},
  { id: 'feature', name: 'Feature', tasks: [
    { id: 't1', title: 'Anforderungen definieren', description: 'Funktionale & nicht-funktionale Anforderungen', priority: 'High' },
    { id: 't2', title: 'Design / Architektur', description: 'Technisches Design erstellen', priority: 'High' },
    { id: 't3', title: 'Implementierung', description: 'Feature implementieren', priority: 'High' },
    { id: 't4', title: 'Tests schreiben', description: 'Unit und Integration Tests', priority: 'Medium' },
    { id: 't5', title: 'Dokumentation', description: 'Feature dokumentieren', priority: 'Low' },
  ]},
  { id: 'tdd', name: 'TDD', tasks: [
    { id: 't1', title: 'Verhalten klären', description: 'Akzeptanzkriterien festhalten', priority: 'High' },
    { id: 't2', title: 'Test zuerst', description: 'Test für Zielverhalten erstellen', priority: 'High' },
    { id: 't3', title: 'Red-Phase', description: 'Test läuft fehl – Evidenz sichern', priority: 'High' },
    { id: 't4', title: 'Minimaler Patch', description: 'Kleinste Änderung umsetzen', priority: 'High' },
    { id: 't5', title: 'Green-Phase', description: 'Tests bestehen verifizieren', priority: 'High' },
    { id: 't6', title: 'Refactoring', description: 'Qualität verbessern', priority: 'Medium' },
    { id: 't7', title: 'Finale Verifikation', description: 'Abschluss + Approval-Gate', priority: 'Medium' },
  ]},
  { id: 'refactor', name: 'Refactoring', tasks: [
    { id: 't1', title: 'Code-Analyse', description: 'Verbesserungspotenzial identifizieren', priority: 'Medium' },
    { id: 't2', title: 'Refactoring-Plan', description: 'Schritte planen', priority: 'Medium' },
    { id: 't3', title: 'Refactoring', description: 'Code umstrukturieren', priority: 'Medium' },
    { id: 't4', title: 'Tests verifizieren', description: 'Alle Tests noch grün', priority: 'High' },
  ]},
  { id: 'test', name: 'Testing', tasks: [
    { id: 't1', title: 'Test-Strategie', description: 'Strategie und Abdeckung definieren', priority: 'High' },
    { id: 't2', title: 'Unit Tests', description: 'Unit Tests schreiben', priority: 'High' },
    { id: 't3', title: 'Integration Tests', description: 'Integration Tests implementieren', priority: 'Medium' },
    { id: 't4', title: 'Coverage-Report', description: 'Abdeckung analysieren', priority: 'Low' },
  ]},
  { id: 'architecture_review', name: 'Architektur-Review', tasks: [
    { id: 't1', title: 'Struktur-Audit', description: 'Modulabhängigkeiten und Boundaries', priority: 'Medium' },
    { id: 't2', title: 'SOLID Check', description: 'Engineering-Prinzipien untersuchen', priority: 'Medium' },
    { id: 't3', title: 'Design-Docs', description: 'ADRs sichten oder erstellen', priority: 'Low' },
    { id: 't4', title: 'Empfehlungsliste', description: 'Design-Verbesserungen vorschlagen', priority: 'Medium' },
  ]},
  { id: 'incident', name: 'Incident', tasks: [
    { id: 't1', title: 'Systemstatus prüfen', description: 'Logs und Metriken sofort scannen', priority: 'High' },
    { id: 't2', title: 'Eingrenzung', description: 'Betroffene Komponente identifizieren', priority: 'High' },
    { id: 't3', title: 'Mitigation', description: 'Sofortmaßnahmen einleiten', priority: 'High' },
    { id: 't4', title: 'Post-Mortem', description: 'Ursache dokumentieren', priority: 'Medium' },
  ]},
  { id: 'repo_analysis', name: 'Repo-Analyse', tasks: [
    { id: 't1', title: 'Projektstruktur scannen', description: 'Ordnerstruktur auflisten', priority: 'High' },
    { id: 't2', title: 'Abhängigkeiten prüfen', description: 'Bibliotheken auf Aktualität', priority: 'Medium' },
    { id: 't3', title: 'Code-Qualität', description: 'Stichproben SOLID-Prinzipien', priority: 'Medium' },
    { id: 't4', title: 'Sicherheits-Audit', description: 'Offensichtliche Lücken suchen', priority: 'High' },
    { id: 't5', title: 'Analyse-Bericht', description: 'Strukturiertes Artefakt', priority: 'Medium' },
  ]},
];

// ─── Canvas Types ─────────────────────────────────────────────────────────────

type NodeType = 'start' | 'planning' | 'task' | 'det' | 'gate' | 'review' | 'verification' | 'end' | 'fork' | 'join';
type EdgeCondition = 'always' | 'on_success' | 'on_failure' | 'back_edge' | 'on_output';
type Priority = 'High' | 'Medium' | 'Low';
type RoutingMode = 'auto' | 'backend' | 'worker' | 'capability';
type DetSubtype = 'script' | 'api-call' | 'regex-check' | 'git-op' | 'file-check';
type GateSubtype = 'auto-verify' | 'human-approval' | 'test-run' | 'lint' | 'type-check';
type FailAction = 'block' | 'continue' | 'rollback' | 'retry';

// VP kind values for step classification
const VP_KINDS = ['coding', 'analysis', 'run_tests', 'code_review', 'refactor', 'bugfix',
  'research', 'llm_generate', 'goal_plan', 'deploy', 'spec', 'breakdown'] as const;

interface StepRouting {
  mode: RoutingMode;
  backend?: string;
  workerName?: string;
  capability?: string;
}

interface ArtifactSlot {
  name: string;
  kind: 'code' | 'text' | 'json' | 'report' | 'binary' | 'file';
  required: boolean;
  description: string;
  producedByStepId?: string;
  producedByOutputName?: string;
}

interface CanvasNode {
  id: string;
  x: number; y: number;
  w: number; h: number;
  type: NodeType;
  title: string;
  subtitle?: string;
  role?: string;
  // Function Composition Pipeline I/O
  inputs: ArtifactSlot[];
  outputs: ArtifactSlot[];
  // VP model fields
  skillProfileId?: string;
  vpKind?: string;
  gate?: boolean;
  // Worker routing (task + det)
  routing?: StepRouting;
  // Deterministic step
  detSubtype?: DetSubtype;
  detCommand?: string;
  detExpectedResult?: string;
  failAction?: FailAction;
  // Gate
  gateSubtype?: GateSubtype;
  gateTimeout?: number;
  // General
  priority?: Priority;
  enabled: boolean;
}

const BACKENDS = ['ananta', 'opencode', 'hermes', 'sgpt', 'claude', 'lmstudio', 'ollama'] as const;
const CAPABILITIES = ['planner', 'researcher', 'coder', 'reviewer', 'tester'] as const;

const NODE_STYLE: Record<string, { fill: string; stroke: string; dash?: string }> = {
  task:         { fill: 'white',    stroke: '#d1d5db' },
  det:          { fill: '#fefce8',  stroke: '#ca8a04', dash: '5,3' },
  gate:         { fill: '#fff7ed',  stroke: '#ea580c', dash: '5,3' },
  review:       { fill: '#faf5ff',  stroke: '#9333ea' },
  planning:     { fill: '#e0e7ff',  stroke: '#4f46e5' },
  verification: { fill: '#d1fae5',  stroke: '#059669' },
  start:        { fill: '#fef3c7',  stroke: '#d97706' },
  end:          { fill: '#f0fdf4',  stroke: '#16a34a' },
  fork:         { fill: '#fdf4ff',  stroke: '#a855f7' },
  join:         { fill: '#f0f9ff',  stroke: '#0284c7' },
};

interface CanvasEdge {
  id: string;
  from: string;
  to: string;
  condition: EdgeCondition;
  label?: string;
  loopMaxIter?: number;
  outputName?: string;  // for on_output condition: which artifact triggers this edge
  bindings?: ArtifactBinding[];
}

interface ArtifactBinding {
  outputName: string;
  inputName: string;
}

const NODE_W = 220;
const NODE_H = 68;
const GAP_Y = 52;
const CX = 300;

const PRIORITY_COLOR: Record<Priority, string> = { High: '#ef4444', Medium: '#f59e0b', Low: '#22c55e' };
const COND_COLOR: Record<EdgeCondition, string> = { always: '#9ca3af', on_success: '#22c55e', on_failure: '#ef4444', back_edge: '#7c3aed', on_output: '#0284c7' };
const ARTIFACT_KINDS = ['code', 'text', 'json', 'report', 'binary', 'file'] as const;

@Component({
  selector: 'ch-internals',
  standalone: true,
  imports: [DecimalPipe, SlicePipe, GraphViewerComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
<div class="ch-int">

  <!-- ── Tab Switcher ── -->
  <div class="ch-tabs">
    <button type="button" class="ch-tab" [class.ch-tab-on]="activeTab() === 'graph'" (click)="activeTab.set('graph')">📊 Quellgraph</button>
    <button type="button" class="ch-tab" [class.ch-tab-on]="activeTab() === 'vp'"    (click)="activeTab.set('vp')">⚙ VP Editor</button>
    @if (activeTab() === 'graph') {
      <div class="ch-tab-spacer"></div>
      <label class="ch-lbl" style="margin-left:8px">Quelle</label>
      <select class="ch-sel" style="max-width:200px" [value]="ccGraphMode()"
        (change)="onGraphSourceChange($any($event.target).value)">
        <option value="self">Ananta (Codebase)</option>
        @if (ccIndexes().length > 0) {
          <option disabled>──────────────</option>
          @for (idx of ccIndexes(); track idx.id) {
            <option [value]="idx.id">{{ indexLabel(idx) }}</option>
          }
        }
      </select>
      @if (ccGraphMode() === 'self') {
        <label class="ch-lbl" style="margin-left:8px">Modul</label>
        <select class="ch-sel" style="max-width:200px" [value]="ccDomain()"
          (change)="ccDomain.set($any($event.target).value); loadSelfGraph()">
          @for (d of ccDomains(); track d.domain) {
            <option [value]="d.domain">{{ domainOptionLabel(d) }}</option>
          }
        </select>
        <label class="ch-lbl" style="margin-left:8px" title="Welche Node-Arten geladen werden">Detailgrad</label>
        <select class="ch-sel" style="width:116px" [value]="ccDetailLevel()"
          (change)="ccDetailLevel.set(+$any($event.target).value); loadSelfGraph()">
          <option value="0">Dateien</option>
          <option value="1">+ Typen</option>
          <option value="2">+ Funktionen</option>
          <option value="3">+ Details</option>
        </select>
        <label class="ch-lbl" style="margin-left:8px" title="Globale Graph-Hop-Tiefe (0 = komplett)">Tiefe</label>
        <input type="number" class="ch-sel" style="width:52px" min="0" max="12" step="1"
          [value]="ccGraphDepth()"
          (change)="ccGraphDepth.set(+$any($event.target).value); loadSelfGraph()" />
        <label class="ch-lbl" style="margin-left:8px" title="Max. Nodes (0 = unbegrenzt)">Max Nodes</label>
        <input type="number" class="ch-sel" style="width:64px" min="0" step="500"
          [value]="ccMaxNodes()"
          (change)="ccMaxNodes.set(+$any($event.target).value); loadSelfGraph()" />
        <label class="ch-lbl" style="margin-left:8px" title="Max. Edges (0 = unbegrenzt)">Max Edges</label>
        <input type="number" class="ch-sel" style="width:64px" min="0" step="500"
          [value]="ccMaxEdges()"
          (change)="ccMaxEdges.set(+$any($event.target).value); loadSelfGraph()" />
      }
      @if (ccLoading()) { <span class="ch-lbl" style="color:var(--muted);margin-left:8px">Lädt…</span> }
      @if (ccError()) { <span class="ch-lbl" style="color:#ef4444;margin-left:8px">{{ ccError() }}</span> }
    }
  </div>

  <!-- ── Top Config Bar (VP only) ── -->
  @if (activeTab() === 'vp') {
  <div class="ch-int-bar">
    <div class="ch-int-bg">
      <label class="ch-lbl">Blueprint</label>
      <select class="ch-sel" [value]="selectedBlueprint()"
        (change)="onBlueprintChange($any($event.target).value)">
        @for (b of BLUEPRINTS; track b.id) { <option [value]="b.id">{{ b.name }}</option> }
      </select>
    </div>
    <div class="ch-int-bg">
      <label class="ch-lbl">Playbook</label>
      <select class="ch-sel" [value]="selectedPlaybook()"
        (change)="onPlaybookChange($any($event.target).value)">
        @for (p of PLAYBOOKS; track p.id) { <option [value]="p.id">{{ p.name }}</option> }
      </select>
    </div>
    <div class="ch-bar-sep"></div>
    <div class="ch-int-bg">
      <label class="ch-lbl">VP-Preset</label>
      <select class="ch-sel" [value]="selectedPresetId()"
        (change)="onPresetChange($any($event.target).value)">
        <option value="">— wählen —</option>
        @for (p of vpPresets(); track p.id) { <option [value]="p.id">{{ p.name }}</option> }
      </select>
    </div>
    <div class="ch-bar-sep"></div>
    <div class="ch-int-bg">
      <label class="ch-lbl">Security</label>
      <select class="ch-sel" [value]="selectedSecurity()"
        (change)="selectedSecurity.set($any($event.target).value)">
        <option value="permissive">Permissive</option>
        <option value="safe">Safe</option>
        <option value="strict">Strict</option>
      </select>
    </div>
    <div class="ch-int-bg">
      <label class="ch-lbl">Workers</label>
      <select class="ch-sel ch-sel-sm" [value]="maxConcurrency()"
        (change)="maxConcurrency.set(+$any($event.target).value)">
        <option value="1">1</option><option value="2">2</option><option value="3">3</option>
      </select>
      <span class="ch-wdots">
        @for (w of workers(); track w.name) {
          <span class="ch-pal-dot"
            [class.ch-pal-dot-on]="w.status === 'online'"
            [class.ch-pal-dot-off]="w.status !== 'online'"
            [title]="w.name + ' · ' + w.worker_roles.join(', ')"></span>
        }
        @if (workers().length === 0) { <span class="ch-wdots-none">–</span> }
      </span>
    </div>
    <div class="ch-bar-sep"></div>
    <button type="button" class="ch-btn" [class.ch-btn-on]="connectMode()"
      (click)="toggleConnect()" title="Verbinden-Modus">
      {{ connectMode() ? '🔗 Ein' : '🔗 Verbinden' }}
    </button>
    <button type="button" class="ch-btn ch-btn-task" (click)="addFreeNode()" title="LLM-Task hinzufügen">💬 Task</button>
    <button type="button" class="ch-btn ch-btn-det"  (click)="addDetNode()"  title="Deterministischer Schritt">⚙ Det</button>
    <button type="button" class="ch-btn ch-btn-gate" (click)="addGateNode()" title="Verification-Gate">🚦 Gate</button>
    <button type="button" class="ch-btn ch-btn-rev"  (click)="addReviewNode()" title="Review-Checkpoint">👁 Review</button>
    <button type="button" class="ch-btn ch-btn-fork" (click)="addForkNode()" title="Parallel Fork — mehrere Zweige parallel">⑂ Fork</button>
    <button type="button" class="ch-btn ch-btn-join" (click)="addJoinNode()" title="Join — alle Zweige zusammenführen">⊕ Join</button>
    <button type="button" class="ch-btn ch-btn-muted"
      (click)="buildCanvas(selectedBlueprint(), selectedPlaybook())" title="Layout zurücksetzen">↺ Reset</button>
    <div style="flex:1"></div>
    @if (autopilot().running) {
      <span class="ch-run-badge">
        <span class="ch-dot-run"></span>
        {{ autopilot().dispatched_count }} disp · {{ autopilot().completed_count }} done
        @if (autopilot().failed_count > 0) { · <span style="color:#dc2626">{{ autopilot().failed_count }}✗</span> }
      </span>
    } @else {
      <span class="ch-idle-badge">Idle</span>
    }
  </div>
  } <!-- /vp bar -->

  <!-- ── Quellgraph View ── -->
  @if (activeTab() === 'graph') {
    @if (ccMeta()) {
      <div class="ch-graph-info">
        <span>{{ ccMeta()!['node_count'] }} Nodes · {{ ccMeta()!['edge_count'] }} Edges</span>
        @if (ccMeta()!['capped'] || ccMeta()!['edge_capped']) {
          <span class="ch-cap-warn">
            ⚠ Cap: {{ ccMeta()!['node_count'] }}/{{ ccMeta()!['tier_total_nodes'] }} Nodes,
            {{ ccMeta()!['edge_count'] }}/{{ ccMeta()!['pre_edge_cap_edge_count'] || ccMeta()!['pre_cap_edge_count'] }} Edges
            — erhöhe Max Nodes/Edges für mehr
          </span>
        } @else {
          <span class="ch-graph-hint">{{ ccMeta()!['domain_total_nodes'] }} Nodes im Modul</span>
        }
      </div>
    }
    <div class="ch-graph-wrap">
      @if (ccRawGraph() && (ccRawGraph().nodes?.length > 0 || ccRawGraph().entities?.length > 0)) {
        <app-graph-viewer [rawGraphData]="ccRawGraph()" />
      } @else if (!ccLoading() && ccGraphMode() !== 'self') {
        <!-- Wiki graph explorer -->
        @let selIdx = ccIndexes().find(i => i.id === ccGraphMode());
        <div class="ch-wg-panel">
          <!-- Header -->
          <div class="ch-wg-header">
            <span class="ch-wg-title">{{ indexLabel(selIdx) }}</span>
            @if (selIdx?.index_metadata?.manifest_summary; as ms) {
              <span class="ch-wg-stat">{{ ms.index_record_count | number }} Chunks · {{ ms.relation_record_count | number }} Kanten</span>
            }
          </div>

          <!-- Build status -->
          @if (wgStatus(); as st) {
            @if (st.status === 'not_built') {
              <div class="ch-wg-build-box">
                <p class="muted" style="font-size:13px;margin:0 0 10px">
                  Für interaktive Exploration wird ein Artikelgraph-Index benötigt
                  (~2,9 M Artikel + 65 M Kanten → SQLite, einmalig ca. 60–90 Min.).
                </p>
                <button class="ch-btn" style="font-size:12px" (click)="wgBuild()">Index aufbauen</button>
              </div>
            } @else if (st.status === 'building') {
              <div class="ch-wg-build-box">
                <span class="ch-dot-run" style="display:inline-block;margin-right:6px"></span>
                <span style="font-size:13px">Baut Index auf…
                  @if (st.phase) { <span class="muted">({{ st.phase }})</span> }
                  @if (st.article_count) { · {{ st.article_count | number }} Artikel }
                  @if (st.edge_count)    { · {{ st.edge_count | number }} Kanten }
                </span>
              </div>
            } @else if (st.status === 'error') {
              <div class="ch-wg-build-box" style="color:#dc2626;font-size:13px">
                Fehler: {{ st.error }}
                <button class="ch-btn" style="margin-left:8px;font-size:11px" (click)="wgBuild(true)">Erneut versuchen</button>
              </div>
            } @else if (st.status === 'ready') {
              <!-- Search box -->
              <div class="ch-wg-search-row">
                <input class="ch-wg-search-input" type="search" placeholder="Artikel suchen…"
                  [value]="wgSearchQuery()"
                  (input)="wgSearch($any($event.target).value)" />
                @if (wgSearchLoading()) {
                  <span class="ch-lbl muted" style="margin-left:8px">Suche…</span>
                }
              </div>
              @if (wgSearchResults().length > 0) {
                <div class="ch-wg-results">
                  @for (r of wgSearchResults(); track r.slug) {
                    <button class="ch-wg-result-item"
                      [class.ch-wg-result-active]="wgExpandedSlug() === r.slug"
                      (click)="wgExpand(r.slug, r.title)">
                      {{ r.title }}
                    </button>
                  }
                </div>
              } @else if (wgSearchQuery() && !wgSearchLoading()) {
                <div class="ch-wg-noresult">Kein Artikel gefunden.</div>
              } @else if (!wgSearchQuery()) {
                <div class="ch-wg-hint muted">
                  {{ st.article_count | number }} Artikel · {{ st.edge_count | number }} Kanten geladen —
                  Artikel suchen um Nachbarschafts-Graph anzuzeigen.
                </div>
              }
            }
          } @else {
            <div class="muted" style="padding:16px;font-size:13px">Lade Status…</div>
          }
        </div>
      } @else if (!ccLoading() && !ccError()) {
        <div class="ch-graph-empty">Lade Modul-Graph…</div>
      }
    </div>
  }

  <!-- ── VP Body ── -->
  @if (activeTab() === 'vp') {
  <!-- ── Body ── -->
  <div class="ch-int-body">

    <!-- Left Palette -->
    <aside class="ch-palette" [class.ch-palette-connect]="connectMode()">
      <div class="ch-pal-hd">Elemente</div>
      <button class="ch-pal-elem ch-pal-elem-task" (click)="addFreeNode()">💬 LLM Task</button>
      <button class="ch-pal-elem ch-pal-elem-det"  (click)="addDetNode()">⚙ Det</button>
      <button class="ch-pal-elem ch-pal-elem-gate" (click)="addGateNode()">🚦 Gate</button>
      <button class="ch-pal-elem ch-pal-elem-rev"  (click)="addReviewNode()">👁 Review</button>
      <button class="ch-pal-elem ch-pal-elem-fork" (click)="addForkNode()">⑂ Fork</button>
      <button class="ch-pal-elem ch-pal-elem-join" (click)="addJoinNode()">⊕ Join</button>
      <div class="ch-pal-div"></div>
      <div class="ch-pal-hd">Workers</div>
      @for (w of workers(); track w.name) {
        <div class="ch-pal-w" [class.ch-pal-w-live]="workerIsActive(w)">
          <span class="ch-pal-dot" [class.ch-pal-dot-on]="w.status === 'online'" [class.ch-pal-dot-off]="w.status !== 'online'" [title]="w.name"></span>
          <span class="ch-pal-wname">{{ w.name }}</span>
        </div>
      }
      @if (workers().length === 0) { <p class="ch-muted" style="padding:4px 8px;font-size:10px">–</p> }
    </aside>

    <!-- SVG Canvas -->
    <main class="ch-canvas-wrap" (wheel)="onWheel($event)">
      <div class="ch-zoom-ctrl">
        <button type="button" (click)="zoomIn()">+</button>
        <span>{{ viewScale() * 100 | number:'1.0-0' }}%</span>
        <button type="button" (click)="zoomOut()">−</button>
        <button type="button" (click)="resetView()">⊙</button>
      </div>
      @if (connectMode()) {
        <div class="ch-connect-hint">
          @if (connectSource()) {
            🎯 Ziel-Knoten anklicken —
          } @else {
            🔗 Verbinden-Modus aktiv · Quell-Knoten anklicken —
          }
          <button type="button" (click)="cancelConnect()">Abbrechen (Esc)</button>
        </div>
      }

      <svg #svgEl class="ch-svg"
        [attr.data-connect]="connectMode() || null"
        (mousedown)="onBgMouseDown($event)">
        <defs>
          <marker id="arr" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">
            <path d="M0,0 L0,6 L8,3 z" fill="#9ca3af"/>
          </marker>
          <marker id="arr-ok" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">
            <path d="M0,0 L0,6 L8,3 z" fill="#22c55e"/>
          </marker>
          <marker id="arr-fail" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">
            <path d="M0,0 L0,6 L8,3 z" fill="#ef4444"/>
          </marker>
          <marker id="arr-sel" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">
            <path d="M0,0 L0,6 L8,3 z" fill="#7c3aed"/>
          </marker>
          <marker id="arr-loop" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">
            <path d="M0,0 L0,6 L8,3 z" fill="#7c3aed"/>
          </marker>
          <filter id="glow-b" x="-30%" y="-30%" width="160%" height="160%">
            <feGaussianBlur stdDeviation="4" result="b"/>
            <feFlood flood-color="#3b82f6" flood-opacity="0.45" result="c"/>
            <feComposite in="c" in2="b" operator="in" result="g"/>
            <feMerge><feMergeNode in="g"/><feMergeNode in="SourceGraphic"/></feMerge>
          </filter>
          <filter id="glow-p" x="-30%" y="-30%" width="160%" height="160%">
            <feGaussianBlur stdDeviation="3" result="b"/>
            <feFlood flood-color="#7c3aed" flood-opacity="0.35" result="c"/>
            <feComposite in="c" in2="b" operator="in" result="g"/>
            <feMerge><feMergeNode in="g"/><feMergeNode in="SourceGraphic"/></feMerge>
          </filter>
        </defs>
        <rect class="ch-bg-rect" width="100%" height="100%" fill="transparent"/>

        <g [attr.transform]="svgTransform()">

          <!-- Edges -->
          @for (edge of edges(); track edge.id) {
            @let ep = edgePath(edge);
            @let mp = edgeMidpoint(edge);
            @let eSel = selectedEdgeId() === edge.id;
            <g class="ch-edge" (click)="onEdgeClick($event, edge.id)">
              <path [attr.d]="ep" stroke="transparent" stroke-width="12" fill="none" style="cursor:pointer"/>
              <path [attr.d]="ep" fill="none"
                [attr.stroke]="eSel ? '#7c3aed' : COND_COLOR[edge.condition]"
                [attr.stroke-width]="eSel ? 2.5 : 1.5"
                [attr.stroke-dasharray]="edge.condition === 'on_failure' ? '6,3' : null"
                [attr.marker-end]="'url(#arr' + edgeMarkerSuffix(edge, eSel) + ')'"/>
              @if (edge.label) {
                <text [attr.x]="mp.x + 7" [attr.y]="mp.y - 4" font-size="9" fill="#6b7280">{{ edge.label }}</text>
              } @else if (edgeBindingLabel(edge)) {
                <text [attr.x]="mp.x + 7" [attr.y]="mp.y - 4" font-size="9" fill="#0284c7">{{ edgeBindingLabel(edge) }}</text>
              }
              <!-- Midpoint + button -->
              <circle [attr.cx]="mp.x" [attr.cy]="mp.y" r="9"
                class="ch-edge-mid"
                fill="var(--card-bg)" stroke="#d1d5db" stroke-width="1.5"
                (click)="insertOnEdge($event, edge.id)"/>
              <text [attr.x]="mp.x" [attr.y]="mp.y + 4.5" text-anchor="middle"
                font-size="13" fill="#9ca3af" pointer-events="none">+</text>
            </g>
          }

          <!-- Nodes -->
          @for (node of nodes(); track node.id) {
            @let nSel = selectedNodeId() === node.id;
            @let nAct = nodeIsActive(node);
            @let nSrc = connectSource() === node.id;
            <g [attr.transform]="'translate(' + node.x + ',' + node.y + ')'"
              [attr.filter]="nAct ? 'url(#glow-b)' : (nSel ? 'url(#glow-p)' : null)"
              class="ch-node"
              [class.ch-node-off]="!node.enabled"
              (mousedown)="onNodeMouseDown($event, node.id)"
              (click)="onNodeClick($event, node.id)">

              <!-- Node shape -->
              @if (node.type === 'start' || node.type === 'end') {
                <rect [attr.width]="node.w" [attr.height]="node.h" rx="24"
                  [attr.fill]="NODE_STYLE[node.type].fill"
                  [attr.stroke]="nSrc ? '#f59e0b' : (nSel ? '#7c3aed' : NODE_STYLE[node.type].stroke)"
                  [attr.stroke-width]="nSel || nSrc ? 2.5 : 1.5"/>
              } @else if (node.type === 'fork') {
                <!-- Diamond shape for Fork -->
                <polygon [attr.points]="forkPoints(node)"
                  [attr.fill]="nAct ? '#f5d0fe' : NODE_STYLE['fork'].fill"
                  [attr.stroke]="nSrc ? '#f59e0b' : (nSel ? '#7c3aed' : NODE_STYLE['fork'].stroke)"
                  [attr.stroke-width]="nSel || nSrc ? 2.5 : 1.5"/>
                <text [attr.x]="node.w/2" [attr.y]="node.h/2 - 6" text-anchor="middle" font-size="12">⑂</text>
              } @else if (node.type === 'join') {
                <!-- Hexagon / wide-oval for Join -->
                <rect [attr.width]="node.w" [attr.height]="node.h" rx="20"
                  [attr.fill]="nAct ? '#bae6fd' : NODE_STYLE['join'].fill"
                  [attr.stroke]="nSrc ? '#f59e0b' : (nSel ? '#7c3aed' : NODE_STYLE['join'].stroke)"
                  [attr.stroke-width]="nSel || nSrc ? 2.5 : 2"/>
                <text [attr.x]="node.w/2" [attr.y]="node.h/2 - 6" text-anchor="middle" font-size="12">⊕</text>
              } @else {
                <rect [attr.width]="node.w" [attr.height]="node.h" rx="8"
                  [attr.fill]="!node.enabled ? '#f9fafb' : (nAct ? '#dbeafe' : (NODE_STYLE[node.type]?.fill ?? 'white'))"
                  [attr.stroke]="nSrc ? '#f59e0b' : (nSel ? '#7c3aed' : (NODE_STYLE[node.type]?.stroke ?? '#d1d5db'))"
                  [attr.stroke-width]="nSel || nSrc ? 2.5 : (node.type === 'planning' || node.type === 'verification' ? 2 : 1.5)"
                  [attr.stroke-dasharray]="NODE_STYLE[node.type]?.dash ?? null"/>
                <!-- Priority bar -->
                @if ((node.type === 'task' || node.type === 'det') && node.priority) {
                  <rect x="0" y="0" width="4" [attr.height]="node.h" rx="2"
                    [attr.fill]="PRIORITY_COLOR[node.priority]" opacity="0.8"/>
                }
                <!-- I/O badge: shows output count -->
                @if (node.outputs && node.outputs.length > 0) {
                  <rect [attr.x]="node.w - 22" y="2" width="20" height="14" rx="3"
                    fill="#ede9fe" stroke="#7c3aed" stroke-width="0.5"/>
                  <text [attr.x]="node.w - 12" y="13" text-anchor="middle" font-size="8" fill="#6d28d9" font-weight="700">
                    {{ node.outputs.length }}▶
                  </text>
                }
                @if (node.inputs && node.inputs.length > 0) {
                  <rect x="2" y="2" width="20" height="14" rx="3"
                    fill="#ecfdf5" stroke="#059669" stroke-width="0.5"/>
                  <text x="12" y="13" text-anchor="middle" font-size="8" fill="#065f46" font-weight="700">
                    ▶{{ node.inputs.length }}
                  </text>
                }
                <!-- Type icon -->
                @if (node.type === 'det') { <text x="9" y="15" font-size="10">⚙</text> }
                @else if (node.type === 'review') { <text x="9" y="15" font-size="10">👁</text> }
                @else if (node.type === 'gate') { <text x="9" y="15" font-size="10">🚦</text> }
                <!-- Routing badge -->
                @if ((node.type === 'task' || node.type === 'det') && node.routing && node.routing.mode !== 'auto') {
                  <rect [attr.x]="node.w - 2 - routingBadgeW(node)" y="18" [attr.width]="routingBadgeW(node)" height="14" rx="3"
                    fill="#eef2ff" stroke="#4f46e5" stroke-width="0.5"/>
                  <text [attr.x]="node.w - 5" y="29" text-anchor="end" font-size="8" fill="#4f46e5" font-weight="600">
                    {{ routingLabel(node) }}
                  </text>
                }
              }

              @if (nAct) {
                <rect [attr.width]="node.w" [attr.height]="node.h" rx="8"
                  fill="none" stroke="#3b82f6" stroke-width="3" opacity="0.4">
                  <animate attributeName="opacity" values="0.4;0.08;0.4" dur="1.4s" repeatCount="indefinite"/>
                  <animate attributeName="stroke-width" values="3;8;3" dur="1.4s" repeatCount="indefinite"/>
                </rect>
              }

              <!-- Labels (skip for fork/join which show icon) -->
              @if (node.type !== 'fork' && node.type !== 'join') {
                <text [attr.x]="node.w/2"
                  [attr.y]="isComplexNode(node) ? 28 : node.h/2 + 5"
                  text-anchor="middle" font-size="12" font-weight="600"
                  [attr.fill]="!node.enabled ? '#9ca3af' : '#111827'">{{ node.title }}</text>
              } @else {
                <text [attr.x]="node.w/2" [attr.y]="node.h/2 + 14"
                  text-anchor="middle" font-size="9" fill="#6b7280">{{ node.title }}</text>
              }

              @if (isComplexNode(node) && node.h >= 60) {
                @if (node.role) {
                  <text x="9" [attr.y]="node.h - 22" font-size="9" fill="#6b7280">{{ node.role }}</text>
                }
                @if (node.type === 'det' && node.detSubtype) {
                  <text [attr.x]="node.w - 9" [attr.y]="node.h - 22" text-anchor="end" font-size="9" fill="#92400e">{{ node.detSubtype }}</text>
                }
                @if (node.subtitle) {
                  <text [attr.x]="node.w/2" [attr.y]="node.h - 8" text-anchor="middle"
                    font-size="9" fill="#9ca3af">{{ node.subtitle.slice(0, 36) }}</text>
                }
              } @else if (node.subtitle) {
                <text [attr.x]="node.w/2" [attr.y]="node.h/2 + 18" text-anchor="middle"
                  font-size="9" fill="#6b7280">{{ node.subtitle }}</text>
              }

              @if (!node.enabled) {
                <line x1="8" [attr.y1]="node.h/2" [attr.x2]="node.w - 8" [attr.y2]="node.h/2"
                  stroke="#9ca3af" stroke-width="1.5" stroke-dasharray="4,2"/>
              }
            </g>
          }

        </g>
      </svg>
    </main>

    <!-- Right Inspector -->
    <aside class="ch-insp">
      @if (selectedNode(); as n) {
        <div class="ch-insp-head">
          <span class="ch-insp-tag" [attr.data-t]="n.type">{{ n.type }}</span>
          <button class="ch-insp-x" (click)="selectedNodeId.set(null)">✕</button>
        </div>
        <div class="ch-insp-body">
          <label class="ch-fl">Titel</label>
          <input type="text" class="ch-fi" [value]="n.title"
            (change)="patchNode(n.id, { title: $any($event.target).value })"/>

          @if (n.subtitle !== undefined && (n.type === 'task' || n.type === 'det' || n.type === 'gate' || n.type === 'review' || n.type === 'planning' || n.type === 'verification')) {
            <label class="ch-fl">Beschreibung</label>
            <textarea class="ch-fta" [value]="n.subtitle ?? ''"
              (change)="patchNode(n.id, { subtitle: $any($event.target).value })"></textarea>
          }

          <!-- ─── LLM Task config ─── -->
          @if (n.type === 'task') {
            <label class="ch-fl">Priorität</label>
            <select class="ch-fsel" [value]="n.priority ?? 'Medium'"
              (change)="patchNode(n.id, { priority: $any($event.target).value })">
              <option value="High">High</option>
              <option value="Medium">Medium</option>
              <option value="Low">Low</option>
            </select>

            <label class="ch-fl">Rolle (Blueprint)</label>
            <select class="ch-fsel" [value]="n.role ?? ''"
              (change)="patchNode(n.id, { role: $any($event.target).value })">
              <option value="">— keine —</option>
              @for (r of currentRoles(); track r) { <option [value]="r">{{ r }}</option> }
            </select>

            <label class="ch-fl">Skill-Profil (VP)</label>
            <select class="ch-fsel" [value]="n.skillProfileId ?? ''"
              (change)="patchNode(n.id, { skillProfileId: $any($event.target).value || undefined })">
              <option value="">— Auto —</option>
              @for (sp of skillProfiles(); track sp.id) {
                <option [value]="sp.id">{{ sp.name }} ({{ sp.role }})</option>
              }
            </select>

            <label class="ch-fl">VP Kind</label>
            <select class="ch-fsel" [value]="n.vpKind ?? ''"
              (change)="patchNode(n.id, { vpKind: $any($event.target).value || undefined })">
              <option value="">— Auto (aus Typ) —</option>
              @for (k of VP_KINDS; track k) { <option [value]="k">{{ k }}</option> }
            </select>

            <label class="ch-fl">Worker-Routing</label>
            <select class="ch-fsel" [value]="n.routing?.mode ?? 'auto'"
              (change)="setRoutingMode(n.id, $any($event.target).value)">
              <option value="auto">Auto (beliebiger Worker)</option>
              <option value="backend">Nach Backend (Typ)</option>
              <option value="worker">Explizit (bestimmter Worker)</option>
              <option value="capability">Nach Fähigkeit</option>
            </select>
            @if (n.routing?.mode === 'backend') {
              <select class="ch-fsel" [value]="n.routing?.backend ?? 'ananta'"
                (change)="patchRouting(n.id, { backend: $any($event.target).value })">
                @for (b of BACKENDS; track b) { <option [value]="b">{{ b }}</option> }
              </select>
            }
            @if (n.routing?.mode === 'worker') {
              <select class="ch-fsel" [value]="n.routing?.workerName ?? ''"
                (change)="patchRouting(n.id, { workerName: $any($event.target).value })">
                <option value="">— wählen —</option>
                @for (w of workers(); track w.name) {
                  <option [value]="w.name">{{ w.name }} ({{ w.status }})</option>
                }
              </select>
            }
            @if (n.routing?.mode === 'capability') {
              <select class="ch-fsel" [value]="n.routing?.capability ?? 'coder'"
                (change)="patchRouting(n.id, { capability: $any($event.target).value })">
                @for (c of CAPABILITIES; track c) { <option [value]="c">{{ c }}</option> }
              </select>
            }
          }

          <!-- ─── Deterministic step config ─── -->
          @if (n.type === 'det') {
            <label class="ch-fl">Priorität</label>
            <select class="ch-fsel" [value]="n.priority ?? 'Medium'"
              (change)="patchNode(n.id, { priority: $any($event.target).value })">
              <option value="High">High</option><option value="Medium">Medium</option><option value="Low">Low</option>
            </select>

            <label class="ch-fl">Schritt-Typ</label>
            <select class="ch-fsel" [value]="n.detSubtype ?? 'script'"
              (change)="patchNode(n.id, { detSubtype: $any($event.target).value })">
              <option value="script">Shell-Script / Befehl</option>
              <option value="api-call">API-Aufruf (HTTP)</option>
              <option value="regex-check">Regex-Prüfung</option>
              <option value="git-op">Git-Operation</option>
              <option value="file-check">Datei-Check</option>
            </select>

            <label class="ch-fl">{{ n.detSubtype === 'api-call' ? 'URL' : n.detSubtype === 'regex-check' ? 'Regex-Pattern' : 'Befehl / Script' }}</label>
            <input type="text" class="ch-fi" [value]="n.detCommand ?? ''"
              placeholder="{{ n.detSubtype === 'api-call' ? 'https://...' : n.detSubtype === 'regex-check' ? '^OK.*' : 'npm test' }}"
              (change)="patchNode(n.id, { detCommand: $any($event.target).value })"/>

            <label class="ch-fl">Erwartetes Ergebnis (optional)</label>
            <input type="text" class="ch-fi" [value]="n.detExpectedResult ?? ''"
              placeholder="exit 0 / 200 OK / …"
              (change)="patchNode(n.id, { detExpectedResult: $any($event.target).value })"/>

            <button type="button" class="ch-det-test-btn"
              [disabled]="!n.detCommand || detRunning()"
              (click)="runDetStep(n)">
              {{ detRunning() ? '⏳ Läuft…' : '▶ Jetzt testen' }}
            </button>
            @if (detRunResult()) {
              <div class="ch-det-result" [class.ch-det-ok]="detRunResult()?.['success']" [class.ch-det-fail]="!detRunResult()?.['success']">
                <div class="ch-det-status">{{ detRunResult()?.['success'] ? '✓ OK' : '✗ Fehler' }}
                  @if (detRunResult()?.['exit_code'] !== undefined) {
                    · exit {{ detRunResult()?.['exit_code'] }}
                  }
                  @if (detRunResult()?.['duration_ms']) {
                    · {{ detRunResult()?.['duration_ms'] }}ms
                  }
                  @if (detRunResult()?.['http_code']) {
                    · HTTP {{ detRunResult()?.['http_code'] }}
                  }
                </div>
                @if (detRunResult()?.['stdout']) {
                  <pre class="ch-det-out">{{ detRunResult()?.['stdout'] | slice:0:300 }}</pre>
                }
                @if (detRunResult()?.['stderr']) {
                  <pre class="ch-det-err">{{ detRunResult()?.['stderr'] | slice:0:200 }}</pre>
                }
              </div>
            }

            <label class="ch-fl">Bei Fehler</label>
            <select class="ch-fsel" [value]="n.failAction ?? 'block'"
              (change)="patchNode(n.id, { failAction: $any($event.target).value })">
              <option value="block">Blockieren (Flow stoppt)</option>
              <option value="continue">Weiter (ignorieren)</option>
              <option value="rollback">Rollback</option>
              <option value="retry">Erneut versuchen</option>
            </select>

            <label class="ch-fl">Worker-Routing</label>
            <select class="ch-fsel" [value]="n.routing?.mode ?? 'auto'"
              (change)="setRoutingMode(n.id, $any($event.target).value)">
              <option value="auto">Auto</option>
              <option value="backend">Backend-Typ</option>
              <option value="worker">Expliziter Worker</option>
            </select>
            @if (n.routing?.mode === 'backend') {
              <select class="ch-fsel" [value]="n.routing?.backend ?? 'ananta'"
                (change)="patchRouting(n.id, { backend: $any($event.target).value })">
                @for (b of BACKENDS; track b) { <option [value]="b">{{ b }}</option> }
              </select>
            }
            @if (n.routing?.mode === 'worker') {
              <select class="ch-fsel" [value]="n.routing?.workerName ?? ''"
                (change)="patchRouting(n.id, { workerName: $any($event.target).value })">
                @for (w of workers(); track w.name) { <option [value]="w.name">{{ w.name }}</option> }
              </select>
            }
          }

          <!-- ─── Gate config ─── -->
          @if (n.type === 'gate') {
            <label class="ch-fl">Gate-Typ</label>
            <select class="ch-fsel" [value]="n.gateSubtype ?? 'auto-verify'"
              (change)="patchNode(n.id, { gateSubtype: $any($event.target).value })">
              <option value="auto-verify">Auto-Verifikation</option>
              <option value="human-approval">Manuelle Freigabe</option>
              <option value="test-run">Test-Ausführung</option>
              <option value="lint">Lint-Prüfung</option>
              <option value="type-check">Type-Check</option>
            </select>
            <label class="ch-fl">Timeout (Sekunden)</label>
            <input type="number" class="ch-fi" [value]="n.gateTimeout ?? 60"
              (change)="patchNode(n.id, { gateTimeout: +$any($event.target).value })"/>
            <label class="ch-fl">Bei Fehler</label>
            <select class="ch-fsel" [value]="n.failAction ?? 'block'"
              (change)="patchNode(n.id, { failAction: $any($event.target).value })">
              <option value="block">Blockieren</option>
              <option value="continue">Weiter</option>
              <option value="rollback">Rollback</option>
            </select>
          }

          <!-- ─── Review config ─── -->
          @if (n.type === 'review') {
            <label class="ch-fl">Reviewer-Rolle</label>
            <select class="ch-fsel" [value]="n.role ?? ''"
              (change)="patchNode(n.id, { role: $any($event.target).value })">
              <option value="">— alle —</option>
              @for (r of currentRoles(); track r) { <option [value]="r">{{ r }}</option> }
            </select>
            <label class="ch-fl">Bei Ablehnung</label>
            <select class="ch-fsel" [value]="n.failAction ?? 'block'"
              (change)="patchNode(n.id, { failAction: $any($event.target).value })">
              <option value="block">Blockieren</option>
              <option value="rollback">Rollback zu vorherigem Schritt</option>
            </select>
          }

          @if (n.type !== 'start' && n.type !== 'end') {
            <label class="ch-fl">Human-Approval Gate</label>
            <label class="ch-ftoggle">
              <input type="checkbox" [checked]="n.gate ?? false"
                (change)="patchNode(n.id, { gate: $any($event.target).checked })"/>
              <span>{{ (n.gate ?? false) ? 'Ja (blockiert bis Freigabe)' : 'Nein' }}</span>
            </label>
          }

          <!-- ─── Function Composition I/O ─── -->
          @if (n.type !== 'start' && n.type !== 'end') {
            <div class="ch-io-section">
              <div class="ch-io-hd">
                <span>▶ Inputs</span>
                <button class="ch-io-add" (click)="addInput(n.id)">+</button>
              </div>
              @for (slot of n.inputs; track slot.name; let i = $index) {
                <div class="ch-io-row">
                  <input class="ch-io-name" type="text" [value]="slot.name"
                    placeholder="name"
                    (change)="patchSlot(n.id, 'inputs', i, { name: $any($event.target).value })"/>
                  <select class="ch-io-kind"
                    (change)="patchSlot(n.id, 'inputs', i, { kind: $any($event.target).value })">
                    @for (k of ARTIFACT_KINDS; track k) {
                      <option [value]="k" [attr.selected]="slot.kind === k ? '' : null">{{ k }}</option>
                    }
                  </select>
                  <label class="ch-io-req">
                    <input type="checkbox" [checked]="slot.required"
                      (change)="patchSlot(n.id, 'inputs', i, { required: $any($event.target).checked })"/>
                    req
                  </label>
                  <button class="ch-io-del" (click)="removeSlot(n.id, 'inputs', i)">✕</button>
                </div>
                @if (slot.producedByStepId) {
                  <div class="ch-io-source">
                    von {{ nodeLabel(slot.producedByStepId) }}.{{ slot.producedByOutputName ?? slot.name }}
                  </div>
                }
              }
              @if (n.inputs.length === 0) { <div class="ch-io-empty">Keine Inputs</div> }

              <div class="ch-io-hd" style="margin-top:5px">
                <span>◀ Outputs</span>
                <button class="ch-io-add" (click)="addOutput(n.id)">+</button>
              </div>
              @for (slot of n.outputs; track slot.name; let i = $index) {
                <div class="ch-io-row">
                  <input class="ch-io-name" type="text" [value]="slot.name"
                    placeholder="name"
                    (change)="patchSlot(n.id, 'outputs', i, { name: $any($event.target).value })"/>
                  <select class="ch-io-kind"
                    (change)="patchSlot(n.id, 'outputs', i, { kind: $any($event.target).value })">
                    @for (k of ARTIFACT_KINDS; track k) {
                      <option [value]="k" [attr.selected]="slot.kind === k ? '' : null">{{ k }}</option>
                    }
                  </select>
                  <button class="ch-io-del" (click)="removeSlot(n.id, 'outputs', i)">✕</button>
                </div>
              }
              @if (n.outputs.length === 0) { <div class="ch-io-empty">Keine Outputs</div> }
            </div>
          }

          <label class="ch-fl">Aktiv</label>
          <label class="ch-ftoggle">
            <input type="checkbox" [checked]="n.enabled"
              (change)="patchNode(n.id, { enabled: $any($event.target).checked })"/>
            <span>{{ n.enabled ? 'Ja' : 'Deaktiviert' }}</span>
          </label>

          <label class="ch-fl">Größe (B × H)</label>
          <div class="ch-frow">
            <input type="number" class="ch-fnum" [value]="n.w" min="60"
              (change)="patchNode(n.id, { w: +$any($event.target).value })"/>
            <span class="ch-fdim">×</span>
            <input type="number" class="ch-fnum" [value]="n.h" min="30"
              (change)="patchNode(n.id, { h: +$any($event.target).value })"/>
          </div>

          <label class="ch-fl">Position (X, Y)</label>
          <div class="ch-frow">
            <input type="number" class="ch-fnum" [value]="n.x | number:'1.0-0'"
              (change)="patchNode(n.id, { x: +$any($event.target).value })"/>
            <span class="ch-fdim">,</span>
            <input type="number" class="ch-fnum" [value]="n.y | number:'1.0-0'"
              (change)="patchNode(n.id, { y: +$any($event.target).value })"/>
          </div>

          @if (nodeIsActive(n)) {
            <div class="ch-active-badge"><span class="ch-dot-run"></span> Aktuell aktiv</div>
          }

          @if (n.type !== 'start' && n.type !== 'end') {
            <button type="button" class="ch-del-btn" (click)="deleteNode(n.id)">✕ Schritt löschen</button>
          }
        </div>
      }

      @if (selectedEdge(); as e) {
        <div class="ch-insp-head">
          <span class="ch-insp-tag" data-t="edge">Pfeil</span>
          <button class="ch-insp-x" (click)="selectedEdgeId.set(null)">✕</button>
        </div>
        <div class="ch-insp-body">
          <label class="ch-fl">Bedingung</label>
          <select class="ch-fsel" [value]="e.condition"
            (change)="patchEdge(e.id, { condition: $any($event.target).value })">
            <option value="always">Immer →</option>
            <option value="on_success">Nur bei Erfolg ✓</option>
            <option value="on_failure">Nur bei Fehler ✗</option>
            <option value="back_edge">↩ Loop / Back-Edge</option>
            <option value="on_output">📦 Wenn Artifact produziert</option>
          </select>
          @if (e.condition === 'back_edge') {
            <label class="ch-fl">Max. Iterationen</label>
            <input type="number" class="ch-fi" min="1" max="20" [value]="e.loopMaxIter ?? 3"
              (change)="patchEdge(e.id, { loopMaxIter: +$any($event.target).value })"/>
          }
          @if (e.condition === 'on_output') {
            <label class="ch-fl">Artifact-Name (Output des Quell-Steps)</label>
            <input type="text" class="ch-fi" [value]="e.outputName ?? ''"
              placeholder="z.B. code_artifact, report, test_results"
              (change)="patchEdge(e.id, { outputName: $any($event.target).value })"/>
          }
          <label class="ch-fl">Artifact-Bindung</label>
          <div class="ch-bind-list">
            @for (binding of e.bindings ?? []; track binding.outputName + ':' + binding.inputName) {
              <div class="ch-bind-row">
                <span>{{ nodeLabel(e.from) }}.{{ binding.outputName }}</span>
                <span>→</span>
                <span>{{ nodeLabel(e.to) }}.{{ binding.inputName }}</span>
                <button type="button" class="ch-io-del" (click)="removeArtifactBinding(e.id, binding)">✕</button>
              </div>
            }
            @if (!(e.bindings ?? []).length) {
              <div class="ch-io-empty">Keine Bindung</div>
            }
            <select class="ch-fsel" [value]="availableBindingOptions(e)[0]?.value ?? ''"
              (change)="addArtifactBinding(e.id, $any($event.target).value)">
              <option value="">— Output → Input wählen —</option>
              @for (opt of availableBindingOptions(e); track opt.value) {
                <option [value]="opt.value">{{ opt.label }}</option>
              }
            </select>
          </div>
          <label class="ch-fl">Label</label>
          <input type="text" class="ch-fi" [value]="e.label ?? ''" placeholder="Optionales Label…"
            (change)="patchEdge(e.id, { label: $any($event.target).value })"/>
          <label class="ch-fl">Von</label>
          <div class="ch-fval">{{ nodeLabel(e.from) }}</div>
          <label class="ch-fl">Nach</label>
          <div class="ch-fval">{{ nodeLabel(e.to) }}</div>
          <button type="button" class="ch-del-btn" (click)="deleteEdge(e.id)">✕ Pfeil löschen</button>
        </div>
      }

      @if (!selectedNode() && !selectedEdge()) {
        <div class="ch-insp-empty">
          <p>Knoten oder Pfeil anklicken für Details.</p>
          <p class="ch-muted">Knoten ziehen = verschieben.<br>»+« auf Pfeil = Schritt einfügen.<br>»Verbinden« = neue Verbindung ziehen.</p>
        </div>
        <div class="ch-goal-panel">
          <div class="ch-goal-hd">Workflow starten</div>
          <textarea class="ch-fta" placeholder="Ziel / Name des Workflows…"
            [value]="goalText()"
            (input)="goalText.set($any($event.target).value)"></textarea>
          <div class="ch-btn-row">
            <button type="button" class="ch-dry-btn" (click)="dryRunWorkflow()">✓ Prüfen</button>
            <button type="button" class="ch-start-btn"
              [disabled]="!goalText().trim()"
              (click)="startVpWorkflow()">▶ VP-Workflow starten</button>
          </div>
          <button type="button" class="ch-start-btn ch-start-btn-goal"
            [disabled]="!goalText().trim()"
            (click)="submitGoal()">▶ Classic Goal senden</button>
          @if (goalResult()) {
            <div class="ch-result" [class.ch-result-ok]="goalOk()">{{ goalResult() }}</div>
          }
          @if (workflowId()) {
            <div class="ch-result ch-result-ok">Workflow: {{ workflowId() }}</div>
          }
          @if (workflowStatus(); as ws) {
            <div class="ch-wf-status">
              <div class="ch-wf-status-hd">Status: {{ ws['status'] ?? 'unknown' }}</div>
              @if (activeWorkflowStepId()) {
                <div>Aktiv: {{ nodeLabel(activeWorkflowStepId() ?? '') }}</div>
              }
              @if (workflowEvents().length) {
                <div class="ch-wf-events">
                  @for (ev of workflowEvents().slice(-3); track ev['event_id'] ?? ev['timestamp']) {
                    <div>{{ ev['event_type'] ?? 'event' }} · {{ ev['status'] ?? '' }}</div>
                  }
                </div>
              }
            </div>
          }
        </div>
      }
    </aside>
  </div>
  } <!-- /vp body -->

</div>
  `,
  styles: [`
:host { display: flex; flex-direction: column; height: 100%; min-height: 0; }

.ch-int { display: flex; flex-direction: column; height: 100%; min-height: 0; }

/* ── Tabs ── */
.ch-tabs {
  display: flex; align-items: center; gap: 2px;
  padding: 4px 8px; border-bottom: 1px solid var(--border);
  background: var(--card-bg); flex-shrink: 0;
}
.ch-tab {
  padding: 4px 12px; border: 1px solid transparent; border-radius: 5px;
  background: none; color: var(--muted); font-size: 12px; cursor: pointer;
  white-space: nowrap;
}
.ch-tab:hover { background: var(--bg); color: var(--fg); }
.ch-tab-on {
  background: color-mix(in srgb, var(--accent) 12%, transparent);
  border-color: color-mix(in srgb, var(--accent) 30%, transparent);
  color: var(--accent); font-weight: 600;
}
.ch-tab-spacer { flex: 1; }

/* ── Quellgraph ── */
.ch-graph-info {
  display: flex; align-items: center; gap: 10px; flex-shrink: 0;
  padding: 3px 10px; border-bottom: 1px solid var(--border);
  background: var(--card-bg); font-size: 11px; color: var(--muted);
}
.ch-cap-warn {
  color: #b45309; background: color-mix(in srgb, #fef3c7 70%, transparent);
  border: 1px solid #fbbf24; border-radius: 4px; padding: 1px 7px; font-weight: 600;
}
.ch-graph-hint { color: var(--muted); font-size: 10px; }
.ch-graph-wrap {
  flex: 1; min-height: 0; display: flex; flex-direction: column;
  overflow: hidden; padding: 8px;
}
.ch-graph-empty {
  flex: 1; display: flex; align-items: center; justify-content: center;
  font-size: 13px; color: var(--muted);
}
/* ── Wiki Graph Explorer ── */
.ch-wg-panel {
  flex: 1; display: flex; flex-direction: column; min-height: 0;
  padding: 12px; gap: 10px; overflow-y: auto;
}
.ch-wg-header {
  display: flex; align-items: baseline; gap: 12px;
}
.ch-wg-title { font-size: 15px; font-weight: 600; }
.ch-wg-stat  { font-size: 12px; color: var(--muted); }
.ch-wg-build-box {
  padding: 12px 14px; background: var(--bg-subtle,#f8f8f8);
  border-radius: 8px; border: 1px solid var(--border);
}
.ch-wg-search-row {
  display: flex; align-items: center; gap: 8px;
}
.ch-wg-search-input {
  flex: 1; padding: 6px 10px; border-radius: 6px;
  border: 1px solid var(--border); font-size: 13px;
  background: var(--input-bg, #fff); color: var(--text);
}
.ch-wg-results {
  display: flex; flex-direction: column; gap: 2px;
  max-height: 220px; overflow-y: auto;
  border: 1px solid var(--border); border-radius: 6px; padding: 4px;
}
.ch-wg-result-item {
  text-align: left; padding: 5px 10px; border-radius: 4px; font-size: 13px;
  background: transparent; border: none; cursor: pointer; color: var(--text);
  transition: background .1s;
}
.ch-wg-result-item:hover { background: var(--bg-subtle,#f0f0f0); }
.ch-wg-result-active { background: color-mix(in srgb,var(--primary,#6366f1) 12%,transparent); font-weight:600; }
.ch-wg-noresult { font-size: 13px; color: var(--muted); padding: 4px 2px; }
.ch-wg-hint { font-size: 12px; padding: 8px 0; }

/* ── Top Bar ── */
.ch-int-bar {
  display: flex; align-items: center; gap: 5px; flex-wrap: wrap;
  padding: 5px 10px; border-bottom: 1px solid var(--border);
  background: var(--card-bg); flex-shrink: 0; font-size: 11px;
}
.ch-int-bg { display: flex; align-items: center; gap: 4px; }
.ch-lbl { font-size: 10px; color: var(--muted); white-space: nowrap; }
.ch-sel {
  padding: 3px 5px; border: 1px solid var(--border); border-radius: 5px;
  background: var(--bg); color: var(--fg); font-size: 11px; cursor: pointer;
}
.ch-sel-sm { width: 50px; }
.ch-wdots { display: flex; align-items: center; gap: 3px; margin-left: 4px; }
.ch-wdots-none { font-size: 9px; color: var(--muted); }
.ch-bar-sep { width: 1px; height: 18px; background: var(--border); margin: 0 2px; }
.ch-btn {
  padding: 3px 8px; border: 1px solid var(--border); border-radius: 5px;
  background: var(--bg); color: var(--fg); font-size: 11px; cursor: pointer;
  white-space: nowrap;
}
.ch-btn:hover { background: var(--card-bg); }
.ch-btn-on   { background: color-mix(in srgb, #7c3aed 14%, transparent) !important; border-color: #7c3aed; color: #7c3aed; font-weight: 600; }
.ch-btn-muted { color: var(--muted); }
.ch-btn-task { border-color: #d1d5db; }
.ch-btn-det  { border-color: #ca8a04; color: #92400e; background: color-mix(in srgb, #fef9c3 50%, var(--bg)); }
.ch-btn-gate { border-color: #ea580c; color: #9a3412; background: color-mix(in srgb, #fff7ed 50%, var(--bg)); }
.ch-btn-rev  { border-color: #9333ea; color: #6b21a8; background: color-mix(in srgb, #faf5ff 50%, var(--bg)); }
.ch-btn-fork { border-color: #a855f7; color: #7e22ce; background: color-mix(in srgb, #fdf4ff 50%, var(--bg)); }
.ch-btn-join { border-color: #0284c7; color: #0c4a6e; background: color-mix(in srgb, #f0f9ff 50%, var(--bg)); }
.ch-run-badge {
  display: flex; align-items: center; gap: 5px; padding: 2px 8px;
  border-radius: 999px; background: color-mix(in srgb, #3b82f6 12%, transparent);
  border: 1px solid #3b82f6; color: #1e40af; font-weight: 600; font-size: 10px;
}
.ch-dot-run { display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: #3b82f6; animation: dp 1.4s infinite; }
@keyframes dp { 0%,100%{opacity:1}50%{opacity:0.25} }
.ch-idle-badge { font-size: 10px; color: var(--muted); padding: 2px 7px; border: 1px solid var(--border); border-radius: 999px; }

/* ── Body ── */
.ch-int-body { display: grid; grid-template-columns: 140px 1fr 225px; grid-auto-rows: minmax(0, 1fr); flex: 1; min-height: 0; overflow: hidden; }

/* ── Left Palette ── */
.ch-palette { border-right: 1px solid var(--border); background: var(--card-bg); overflow-y: auto; }
.ch-palette-connect { background: color-mix(in srgb, #7c3aed 5%, var(--card-bg)); }
.ch-pal-hd { padding: 6px 8px 3px; font-size: 10px; font-weight: 700; color: var(--muted); text-transform: uppercase; letter-spacing: 0.4px; position: sticky; top: 0; background: inherit; }
.ch-pal-w { display: flex; align-items: center; gap: 5px; padding: 3px 8px; font-size: 11px; }
.ch-pal-w-live { background: color-mix(in srgb, #3b82f6 8%, transparent); }
.ch-pal-dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.ch-pal-dot-on { background: #22c55e; } .ch-pal-dot-off { background: #9ca3af; }
.ch-pal-wname { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 10px; font-weight: 500; }
.ch-pal-div { height: 1px; background: var(--border); margin: 4px 0; }
.ch-pal-role { padding: 2px 8px; font-size: 9px; color: var(--muted); }
.ch-pal-elem {
  display: block; width: 100%; text-align: left; padding: 4px 8px;
  border: none; border-left: 3px solid transparent; background: none; cursor: pointer; font-size: 11px; color: var(--fg);
}
.ch-pal-elem:hover { background: color-mix(in srgb, var(--accent) 8%, transparent); }
.ch-pal-elem-task { border-left-color: #d1d5db; }
.ch-pal-elem-det  { border-left-color: #ca8a04; }
.ch-pal-elem-gate { border-left-color: #ea580c; }
.ch-pal-elem-rev  { border-left-color: #9333ea; }
.ch-pal-backend { display: flex; align-items: center; gap: 5px; padding: 2px 8px; font-size: 10px; color: var(--muted); }
.ch-pal-backend-dot { display: inline-block; width: 5px; height: 5px; border-radius: 50%; background: #d1d5db; flex-shrink: 0; }
.ch-pal-winfo { display: flex; flex-direction: column; min-width: 0; }
.ch-pal-wname { font-size: 10px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ch-pal-wcaps { font-size: 8px; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* ── Canvas ── */
.ch-canvas-wrap {
  position: relative; overflow: hidden; background: var(--bg);
  background-image: radial-gradient(circle, color-mix(in srgb, var(--border) 70%, transparent) 1px, transparent 1px);
  background-size: 22px 22px;
}
.ch-svg { width: 100%; height: 100%; cursor: grab; display: block; }
.ch-svg:active { cursor: grabbing; }
.ch-svg[data-connect] { cursor: crosshair; }
.ch-bg-rect { }

.ch-zoom-ctrl {
  position: absolute; top: 8px; right: 8px; display: flex; align-items: center; gap: 3px;
  background: var(--card-bg); border: 1px solid var(--border); border-radius: 6px;
  padding: 2px 6px; font-size: 11px; z-index: 5;
}
.ch-zoom-ctrl button { padding: 0 4px; border: none; background: none; cursor: pointer; font-size: 14px; color: var(--fg); }
.ch-zoom-ctrl button:hover { color: var(--accent); }
.ch-connect-hint {
  position: absolute; top: 8px; left: 50%; transform: translateX(-50%);
  background: color-mix(in srgb, #7c3aed 12%, var(--card-bg)); border: 1px solid #7c3aed;
  border-radius: 6px; padding: 4px 10px; font-size: 11px; color: #7c3aed; font-weight: 600; z-index: 5;
}
.ch-connect-hint button { background: none; border: none; cursor: pointer; color: inherit; text-decoration: underline; font-size: 11px; }

.ch-edge { cursor: pointer; }
.ch-edge-mid { cursor: pointer; }
.ch-edge-mid:hover { fill: color-mix(in srgb, var(--accent) 10%, white) !important; stroke: var(--accent) !important; }
.ch-node { cursor: pointer; }
.ch-node-off { opacity: 0.45; }

/* ── Inspector ── */
.ch-insp { border-left: 1px solid var(--border); background: var(--card-bg); overflow-y: auto; display: flex; flex-direction: column; }
.ch-insp-head { display: flex; align-items: center; gap: 7px; padding: 7px 9px; border-bottom: 1px solid var(--border); position: sticky; top: 0; background: var(--card-bg); z-index: 1; }
.ch-insp-tag { padding: 2px 6px; border-radius: 4px; font-size: 10px; font-weight: 700; text-transform: uppercase; background: color-mix(in srgb, var(--accent) 14%, transparent); color: var(--accent); }
.ch-insp-tag[data-t="start"],.ch-insp-tag[data-t="end"] { background: color-mix(in srgb, #22c55e 14%, transparent); color: #15803d; }
.ch-insp-tag[data-t="planning"] { background: color-mix(in srgb, #4f46e5 14%, transparent); color: #4f46e5; }
.ch-insp-tag[data-t="verification"] { background: color-mix(in srgb, #059669 14%, transparent); color: #059669; }
.ch-insp-tag[data-t="gate"]   { background: color-mix(in srgb, #ea580c 14%, transparent); color: #9a3412; }
.ch-insp-tag[data-t="det"]    { background: color-mix(in srgb, #ca8a04 14%, transparent); color: #92400e; }
.ch-insp-tag[data-t="review"] { background: color-mix(in srgb, #9333ea 14%, transparent); color: #6b21a8; }
.ch-insp-tag[data-t="edge"]   { background: color-mix(in srgb, #6b7280 14%, transparent); color: #6b7280; }
.ch-insp-x { margin-left: auto; background: none; border: none; cursor: pointer; color: var(--muted); font-size: 13px; }
.ch-insp-x:hover { color: var(--fg); }
.ch-insp-body { padding: 9px; display: flex; flex-direction: column; gap: 5px; }
.ch-fl { font-size: 9px; font-weight: 700; color: var(--muted); text-transform: uppercase; letter-spacing: 0.4px; margin-top: 3px; }
.ch-fi { width: 100%; padding: 4px 6px; border: 1px solid var(--border); border-radius: 4px; background: var(--bg); color: var(--fg); font-size: 11px; box-sizing: border-box; }
.ch-fta { width: 100%; padding: 4px 6px; border: 1px solid var(--border); border-radius: 4px; background: var(--bg); color: var(--fg); font-size: 10px; box-sizing: border-box; min-height: 52px; resize: vertical; }
.ch-fsel { width: 100%; padding: 4px 6px; border: 1px solid var(--border); border-radius: 4px; background: var(--bg); color: var(--fg); font-size: 11px; box-sizing: border-box; }
.ch-ftoggle { display: flex; align-items: center; gap: 5px; font-size: 11px; cursor: pointer; }
.ch-frow { display: flex; align-items: center; gap: 4px; }
.ch-fnum { width: 64px; padding: 4px 5px; border: 1px solid var(--border); border-radius: 4px; background: var(--bg); color: var(--fg); font-size: 11px; }
.ch-fdim { color: var(--muted); font-size: 11px; }
.ch-fval { font-size: 11px; padding: 3px 6px; background: var(--bg); border-radius: 4px; border: 1px solid var(--border); }
.ch-active-badge { display: flex; align-items: center; gap: 6px; padding: 4px 7px; border-radius: 4px; background: color-mix(in srgb, #3b82f6 10%, transparent); border: 1px solid #3b82f6; font-size: 11px; color: #1e40af; font-weight: 600; }
.ch-del-btn { margin-top: 6px; padding: 5px 10px; border: 1px solid color-mix(in srgb, #ef4444 35%, transparent); border-radius: 4px; background: color-mix(in srgb, #ef4444 8%, transparent); color: #b91c1c; font-size: 11px; cursor: pointer; width: 100%; }
.ch-del-btn:hover { background: color-mix(in srgb, #ef4444 14%, transparent); }
.ch-insp-empty { padding: 14px 10px; font-size: 12px; color: var(--muted); flex: 1; display: flex; flex-direction: column; gap: 7px; }
.ch-goal-panel { padding: 9px; border-top: 1px solid var(--border); display: flex; flex-direction: column; gap: 5px; }
.ch-goal-hd { font-size: 11px; font-weight: 700; }
.ch-start-btn { padding: 5px 10px; border-radius: 5px; border: none; background: var(--accent); color: white; font-size: 12px; font-weight: 600; cursor: pointer; }
.ch-start-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.ch-start-btn:not(:disabled):hover { filter: brightness(1.1); }
.ch-result { font-size: 10px; padding: 3px 7px; border-radius: 4px; background: color-mix(in srgb, #ef4444 9%, transparent); color: #b91c1c; }
.ch-result-ok { background: color-mix(in srgb, #22c55e 9%, transparent) !important; color: #15803d !important; }
.ch-muted { color: var(--muted); font-size: 11px; margin: 0; }
.ch-btn-row { display: flex; gap: 5px; }
.ch-dry-btn { flex: 0 0 auto; padding: 5px 10px; border-radius: 5px; border: 1px solid var(--border); background: var(--bg); color: var(--fg); font-size: 12px; cursor: pointer; }
.ch-dry-btn:hover { background: var(--card-bg); }
.ch-start-btn-goal { background: color-mix(in srgb, var(--accent) 60%, transparent); font-size: 11px; padding: 4px 10px; }

/* ── Det Step Test ── */
.ch-det-test-btn { width: 100%; padding: 5px; border-radius: 5px; border: 1px solid #ca8a04; background: color-mix(in srgb, #fef9c3 50%, var(--bg)); color: #92400e; font-size: 11px; font-weight: 600; cursor: pointer; margin-top: 4px; }
.ch-det-test-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.ch-det-test-btn:not(:disabled):hover { background: color-mix(in srgb, #fef9c3 80%, var(--bg)); }
.ch-det-result { margin-top: 5px; border-radius: 4px; padding: 5px 6px; font-size: 10px; border: 1px solid; }
.ch-det-ok   { border-color: #22c55e; background: color-mix(in srgb, #22c55e 8%, transparent); color: #15803d; }
.ch-det-fail { border-color: #ef4444; background: color-mix(in srgb, #ef4444 8%, transparent); color: #b91c1c; }
.ch-det-status { font-weight: 700; margin-bottom: 3px; }
.ch-det-out, .ch-det-err { margin: 3px 0 0; padding: 3px 5px; border-radius: 3px; font-size: 9px; white-space: pre-wrap; word-break: break-all; background: rgba(0,0,0,0.04); max-height: 100px; overflow: auto; font-family: monospace; }
.ch-det-err { color: #b91c1c; }

/* ── I/O Artifact Editor ── */
.ch-io-section { border: 1px solid var(--border); border-radius: 5px; padding: 6px; background: var(--bg); margin-top: 4px; }
.ch-io-hd { display: flex; align-items: center; justify-content: space-between; font-size: 9px; font-weight: 700; color: var(--muted); text-transform: uppercase; letter-spacing: 0.4px; margin-bottom: 3px; }
.ch-io-add { padding: 1px 6px; border: 1px solid var(--border); border-radius: 3px; background: var(--card-bg); cursor: pointer; font-size: 12px; line-height: 1; color: var(--accent); }
.ch-io-row { display: flex; gap: 3px; align-items: center; margin-bottom: 2px; }
.ch-io-name { flex: 1; min-width: 0; padding: 2px 4px; border: 1px solid var(--border); border-radius: 3px; font-size: 10px; background: var(--bg); color: var(--fg); }
.ch-io-kind { width: 58px; padding: 2px 2px; border: 1px solid var(--border); border-radius: 3px; font-size: 9px; background: var(--bg); color: var(--fg); }
.ch-io-req { display: flex; align-items: center; gap: 2px; font-size: 9px; color: var(--muted); white-space: nowrap; cursor: pointer; }
.ch-io-del { padding: 1px 5px; border: none; background: none; cursor: pointer; color: #ef4444; font-size: 11px; }
.ch-io-empty { font-size: 9px; color: var(--muted); padding: 2px 0; }
.ch-io-source { margin: -1px 0 3px 2px; font-size: 9px; color: #0284c7; }
.ch-bind-list { display: flex; flex-direction: column; gap: 4px; }
.ch-bind-row { display: grid; grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr) auto; align-items: center; gap: 4px; font-size: 9px; color: var(--muted); }
.ch-bind-row span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ch-wf-status { margin-top: 8px; border: 1px solid var(--border); border-radius: 5px; padding: 6px; font-size: 10px; color: var(--muted); background: var(--card-bg); }
.ch-wf-status-hd { font-weight: 700; color: var(--fg); margin-bottom: 3px; }
.ch-wf-events { margin-top: 4px; display: flex; flex-direction: column; gap: 2px; }

/* ── Palette Fork/Join ── */
.ch-pal-elem-fork { border-left-color: #a855f7; }
.ch-pal-elem-join { border-left-color: #0284c7; }
  `],
})
export class CodeHugInternalsComponent implements OnInit, AfterViewInit, OnDestroy {
  @ViewChild('svgEl') svgElRef!: ElementRef<SVGSVGElement>;

  private readonly svc = inject(InternalsService);

  // ── Exposed statics for template ─────────────────────────────────────────
  readonly BLUEPRINTS = BLUEPRINTS;
  readonly PLAYBOOKS = PLAYBOOKS;
  readonly PRIORITY_COLOR = PRIORITY_COLOR;
  readonly COND_COLOR = COND_COLOR;
  readonly NODE_STYLE = NODE_STYLE;
  readonly BACKENDS = BACKENDS;
  readonly CAPABILITIES = CAPABILITIES;
  readonly VP_KINDS = VP_KINDS;
  readonly ARTIFACT_KINDS = ARTIFACT_KINDS;

  // ── VP API signals ────────────────────────────────────────────────────────
  readonly vpPresets = signal<VpPreset[]>([]);
  readonly skillProfiles = signal<VpSkillProfile[]>([]);
  readonly selectedPresetId = signal('');
  readonly workflowId = signal<string | null>(null);
  readonly workflowStatus = signal<Record<string, unknown> | null>(null);
  readonly workflowEvents = signal<Record<string, unknown>[]>([]);
  readonly dryRunResult = signal<string | null>(null);
  readonly detRunResult = signal<Record<string, unknown> | null>(null);
  readonly detRunning = signal(false);

  // ── Live data ─────────────────────────────────────────────────────────────
  readonly workers = signal<AnantaWorker[]>([]);
  readonly autopilot = signal<AutopilotStatus>({
    running: false, goal: '', team_id: '', started_at: null,
    tick_count: 0, dispatched_count: 0, completed_count: 0, failed_count: 0,
    last_error: null,
    effective_security_policy: { level: 'safe', max_concurrency_cap: 1, allowed_tool_classes: [] },
    circuit_breakers: { open_workers: [], open_count: 0, failure_streak: {} },
  });

  // ── Config ────────────────────────────────────────────────────────────────
  readonly selectedBlueprint = signal('scrum');
  readonly selectedPlaybook = signal('bug_fix');
  readonly selectedSecurity = signal('safe');
  readonly maxConcurrency = signal(1);
  readonly goalText = signal('');
  readonly goalResult = signal<string | null>(null);
  readonly goalOk = signal(false);

  // ── Canvas state ──────────────────────────────────────────────────────────
  readonly nodes = signal<CanvasNode[]>([]);
  readonly edges = signal<CanvasEdge[]>([]);
  readonly selectedNodeId = signal<string | null>(null);
  readonly selectedEdgeId = signal<string | null>(null);
  readonly viewTx = signal(40);
  readonly viewTy = signal(20);
  readonly viewScale = signal(1);

  readonly svgTransform = computed(() => `translate(${this.viewTx()},${this.viewTy()}) scale(${this.viewScale()})`);
  readonly selectedNode = computed(() => this.nodes().find(n => n.id === this.selectedNodeId()) ?? null);
  readonly selectedEdge = computed(() => this.edges().find(e => e.id === this.selectedEdgeId()) ?? null);
  readonly currentRoles = computed(() => BLUEPRINTS.find(b => b.id === this.selectedBlueprint())?.roles ?? []);

  // ── Tab / Quellgraph ──────────────────────────────────────────────────────
  readonly activeTab = signal<'vp' | 'graph'>('graph');
  readonly ccIndexes = signal<any[]>([]);
  readonly ccSelectedId = signal('');
  readonly ccGraphMode = signal<string>('self');
  readonly ccRawGraph = signal<any>(null);
  readonly ccLoading = signal(false);
  readonly ccError = signal('');
  readonly ccDomains = signal<{domain: string; display_name: string; file_count: number; kind: string; depth?: number; parent_domain?: string}[]>([]);
  readonly ccDomain = signal('agent.routes');
  readonly ccDetailLevel = signal(2);
  readonly ccGraphDepth = signal(0);
  readonly ccMaxNodes = signal(0);
  readonly ccMaxEdges = signal(0);
  readonly ccMeta = signal<Record<string, unknown> | null>(null);

  // ── Wiki Graph Explorer ────────────────────────────────────────────────────
  readonly wgStatus = signal<any>(null);
  readonly wgSearchQuery = signal('');
  readonly wgSearchResults = signal<{slug: string; title: string}[]>([]);
  readonly wgSearchLoading = signal(false);
  readonly wgExpandedSlug = signal('');
  private _wgSearch$ = new Subject<string>();
  private _wgSearchSub: Subscription | null = null;

  // ── Connect mode ──────────────────────────────────────────────────────────
  readonly connectMode = signal(false);
  readonly connectSource = signal<string | null>(null);

  // ── Drag state ────────────────────────────────────────────────────────────
  private _dragging: { nodeId: string; ox: number; oy: number } | null = null;
  private _panning: { mx: number; my: number; tx: number; ty: number } | null = null;
  private _didDrag = false;
  private _nodeSeq = 0;
  private _edgeSeq = 0;
  private _pollSub: Subscription | null = null;
  private _workflowPollSub: Subscription | null = null;

  // ── Lifecycle ─────────────────────────────────────────────────────────────

  ngOnInit(): void {
    this.buildCanvas('scrum', 'bug_fix');
    this.svc.getWorkers().subscribe(w => this.workers.set(w));
    this.svc.getAutopilotStatus().subscribe(s => this.autopilot.set(s));
    this.svc.getVpPresets().subscribe(p => this.vpPresets.set(p));
    this.svc.getVpSkillProfiles().subscribe(sp => this.skillProfiles.set(sp));
    this.svc.getSelfGraphDomains().subscribe(domains => {
      this.ccDomains.set(domains);
      // pick a good default: prefer agent.routes → agent → first with files
      const best = domains.find(d => d.domain === 'agent.routes')
        ?? domains.find(d => d.domain === 'agent')
        ?? domains.find(d => d.file_count > 0)
        ?? domains[0];
      if (best) this.ccDomain.set(best.domain);
      this.loadSelfGraph();
    });
    this.svc.listKnowledgeIndexes().subscribe(items => this.ccIndexes.set(items));
    this._pollSub = interval(3000).pipe(switchMap(() => this.svc.getAutopilotStatus()))
      .subscribe(s => this.autopilot.set(s));
  }

  ngAfterViewInit(): void {}
  ngOnDestroy(): void {
    this._pollSub?.unsubscribe();
    this._workflowPollSub?.unsubscribe();
    this._wgSearchSub?.unsubscribe();
  }

  // ── Quellgraph ────────────────────────────────────────────────────────────

  loadCCGraph(id: string): void {
    if (!id) return;
    this.ccSelectedId.set(id);
    this.ccLoading.set(true);
    this.ccError.set('');
    this.ccRawGraph.set(null);
    this.svc.getCodeCompassGraph(id).subscribe({
      next: data => {
        this.ccLoading.set(false);
        if (data) { this.ccRawGraph.set(data); }
        else { this.ccError.set('Graph nicht verfügbar'); }
      },
      error: () => {
        this.ccLoading.set(false);
        this.ccError.set('Fehler beim Laden');
      },
    });
  }

  loadSelfGraph(): void {
    this.ccSelectedId.set('ananta');
    this.ccLoading.set(true);
    this.ccError.set('');
    this.ccRawGraph.set(null);
    this.svc.getSelfGraph(
      this.ccDomain(),
      this.ccDetailLevel(),
      this.ccGraphDepth(),
      this.ccMaxNodes(),
      this.ccMaxEdges(),
    ).subscribe({
      next: data => {
        this.ccLoading.set(false);
        if (data) {
          this.ccRawGraph.set(data);
          this.ccMeta.set((data as any)?.metadata ?? null);
        } else { this.ccError.set('Self-Graph nicht verfügbar'); }
      },
      error: () => {
        this.ccLoading.set(false);
        this.ccError.set('Fehler beim Laden des Self-Graphs');
      },
    });
  }

  domainOptionLabel(domain: {display_name: string; file_count: number; depth?: number}): string {
    const depth = Math.min(Math.max(domain.depth ?? 0, 0), 4);
    const indent = depth > 0 ? `${'--'.repeat(depth)} ` : '';
    return `${indent}${domain.display_name}${domain.file_count > 0 ? ` (${domain.file_count})` : ''}`;
  }

  indexLabel(idx: any): string {
    const scope: string = idx?.source_scope ?? '';
    const sourceId: string = idx?.index_metadata?.source_id ?? idx?.collection_id ?? idx?.id ?? '?';
    const scopePrefix: Record<string, string> = { wiki: 'Wiki', artifact: 'Artefakt' };
    const label = sourceId.replace(/[-_]/g, ' ').replace(/\b\w/g, c => c.toUpperCase()).replace(/Dewiki.*/, 'DE').trim();
    return `${scopePrefix[scope] ?? scope}: ${label}`;
  }

  onGraphSourceChange(value: string): void {
    this.ccGraphMode.set(value);
    this.ccMeta.set(null);
    this.ccRawGraph.set(null);
    this.wgStatus.set(null);
    this.wgSearchResults.set([]);
    this.wgSearchQuery.set('');
    this.wgExpandedSlug.set('');
    if (value === 'self') {
      this.loadSelfGraph();
    } else {
      this.loadCCGraph(value);
      this._initWikiGraphExplorer(value);
    }
  }

  private _initWikiGraphExplorer(indexId: string): void {
    this._wgSearchSub?.unsubscribe();
    this.svc.getWikiGraphStatus(indexId).subscribe(s => this.wgStatus.set(s));
    this._wgSearchSub = this._wgSearch$.pipe(
      debounceTime(300),
      distinctUntilChanged(),
    ).subscribe(q => {
      if (!q) { this.wgSearchResults.set([]); this.wgSearchLoading.set(false); return; }
      this.wgSearchLoading.set(true);
      this.svc.searchWikiArticles(indexId, q).subscribe(r => {
        this.wgSearchResults.set(r);
        this.wgSearchLoading.set(false);
      });
    });
  }

  wgSearch(q: string): void {
    this.wgSearchQuery.set(q);
    this._wgSearch$.next(q);
  }

  wgExpand(slug: string, title: string): void {
    const indexId = this.ccGraphMode();
    if (indexId === 'self') return;
    this.wgExpandedSlug.set(slug);
    this.ccLoading.set(true);
    this.ccError.set('');
    this.svc.expandWikiArticle(indexId, slug).subscribe({
      next: data => {
        this.ccLoading.set(false);
        if (data?.nodes?.length > 0) {
          this.ccRawGraph.set(data);
          this.ccMeta.set(data.metadata ?? null);
        } else {
          this.ccError.set('Keine Nachbarn gefunden');
        }
      },
      error: () => { this.ccLoading.set(false); this.ccError.set('Fehler beim Laden'); },
    });
  }

  wgBuild(force = false): void {
    const indexId = this.ccGraphMode();
    if (indexId === 'self') return;
    this.wgStatus.set({ status: 'building' });
    this.svc.triggerWikiGraphBuild(indexId, force).subscribe(() => {
      this._pollWgStatus(indexId);
    });
  }

  private _pollWgStatus(indexId: string): void {
    const tick = () => {
      this.svc.getWikiGraphStatus(indexId).subscribe(s => {
        this.wgStatus.set(s);
        if (s?.status === 'building') {
          setTimeout(tick, 5000);
        }
      });
    };
    setTimeout(tick, 3000);
  }

  // ── Blueprint / Playbook / VP Preset ─────────────────────────────────────

  onBlueprintChange(id: string): void {
    this.selectedBlueprint.set(id);
    this.buildCanvas(id, this.selectedPlaybook());
  }

  onPlaybookChange(id: string): void {
    this.selectedPlaybook.set(id);
    this.buildCanvas(this.selectedBlueprint(), id);
  }

  onPresetChange(presetId: string): void {
    this.selectedPresetId.set(presetId);
    if (!presetId) return;
    this.svc.getVpPreset(presetId).subscribe(graph => {
      if (!graph) return;
      this.loadFromVpGraph(graph);
    });
  }

  loadFromVpGraph(graph: VpGraph): void {
    let maxX = 0;
    const nodes: CanvasNode[] = graph.steps.map(s => {
      const x = s.position.x + 40;
      const y = s.position.y + 40;
      if (x > maxX) maxX = x;
      const type = this.vpKindToNodeType(s.kind, s.gate);
      return {
        id: s.id, x, y, w: NODE_W, h: NODE_H,
        type, title: s.label,
        subtitle: s.io?.outputs?.map((o: any) => o.name).join(', ') || undefined,
        role: s.role ?? undefined,
        skillProfileId: s.agent_skill_profile_id ?? undefined,
        vpKind: s.kind,
        gate: s.gate,
        enabled: true,
        routing: { mode: 'auto' as RoutingMode },
        inputs: (s.io?.inputs ?? []).map((inp: any) => ({
          name: inp.name,
          kind: inp.kind ?? 'text',
          required: inp.required ?? true,
          description: inp.description ?? '',
          producedByStepId: inp.produced_by_step ?? undefined,
          producedByOutputName: inp.produced_by_output ?? undefined,
        })),
        outputs: (s.io?.outputs ?? []).map((out: any) => ({ name: out.name, kind: out.kind ?? 'text', required: out.required ?? false, description: out.description ?? '' })),
      };
    });
    const edges: CanvasEdge[] = graph.edges.map(e => ({
      id: e.id, from: e.source, to: e.target,
      condition: (e.condition.kind as EdgeCondition) ?? 'always',
      label: e.label ?? undefined,
      loopMaxIter: e.condition.loop_policy?.max_iterations ?? undefined,
      outputName: e.condition.output_name ?? undefined,
      bindings: Array.isArray(e.metadata?.['artifact_bindings']) ? (e.metadata['artifact_bindings'] as any[]).map(b => ({
        outputName: String(b.output_name ?? b.outputName ?? ''),
        inputName: String(b.input_name ?? b.inputName ?? ''),
      })).filter(b => b.outputName && b.inputName) : [],
    }));
    this.nodes.set(nodes);
    this.edges.set(edges);
    this.selectedNodeId.set(null);
    this.selectedEdgeId.set(null);
    this.workflowId.set(null);
    this.workflowStatus.set(null);
    this.workflowEvents.set([]);
    this.dryRunResult.set(null);
  }

  private vpKindToNodeType(kind: string, gate: boolean): NodeType {
    if (gate) return 'gate';
    if (kind === 'goal_plan' || kind === 'spec' || kind === 'breakdown') return 'planning';
    if (kind === 'run_tests' || kind === 'testing') return 'verification';
    if (kind === 'code_review') return 'review';
    return 'task';
  }

  private nodeKindFor(n: CanvasNode): string {
    if (n.vpKind) return n.vpKind;
    if (n.type === 'det') return 'run_tests';
    if (n.type === 'gate') return 'approval';
    if (n.type === 'review') return 'code_review';
    if (n.type === 'planning') return 'goal_plan';
    if (n.type === 'verification') return 'run_tests';
    if (n.type === 'fork') return 'fork';
    if (n.type === 'join') return 'join';
    if (n.type === 'start' || n.type === 'end') return 'llm_generate';
    return 'coding';
  }

  toVpGraph(): VpGraph {
    this.syncInputBindings();
    const name = this.goalText().trim() || 'Canvas Workflow';
    return {
      id: `vp-canvas-${Date.now()}`,
      name, description: name,
      tags: [this.selectedBlueprint(), this.selectedPlaybook()],
      metadata: {
        security_level: this.selectedSecurity(),
        blueprint: this.selectedBlueprint(),
        playbook: this.selectedPlaybook(),
      },
      steps: this.nodes().map(n => ({
        id: n.id, label: n.title,
        kind: this.nodeKindFor(n),
        role: n.role ?? null,
        agent_skill_profile_id: n.skillProfileId ?? null,
        gate: n.gate ?? false,
        position: { x: n.x, y: n.y },
        policy_hints: n.gate ? ['requires_approval'] : [],
        io: {
          inputs: (n.inputs ?? []).map(s => ({
            name: s.name,
            kind: s.kind,
            required: s.required,
            description: s.description,
            produced_by_step: s.producedByStepId ?? null,
            produced_by_output: s.producedByOutputName ?? null,
          })),
          outputs: (n.outputs ?? []).map(s => ({ name: s.name, kind: s.kind, required: s.required, description: s.description })),
        },
        metadata: {
          det_subtype: n.detSubtype ?? null,
          det_command: n.detCommand ?? null,
          det_expected: n.detExpectedResult ?? null,
          fail_action: n.failAction ?? null,
          routing: n.routing ?? null,
          node_type: n.type,
        },
      })),
      edges: this.edges().map(e => ({
        id: e.id, source: e.from, target: e.to,
        label: this.edgeBindingLabel(e) || (e.outputName ? `📦 ${e.outputName}` : (e.label ?? null)),
        metadata: {
          artifact_bindings: (e.bindings ?? []).map(b => ({
            output_name: b.outputName,
            input_name: b.inputName,
          })),
        },
        condition: {
          kind: e.condition,
          expression: null,
          output_name: e.condition === 'on_output' ? (e.outputName ?? null) : null,
          loop_policy: e.condition === 'back_edge'
            ? { kind: 'fixed', max_iterations: e.loopMaxIter ?? 3, condition: null, break_on_output: null }
            : null,
        },
      })),
    };
  }

  runDetStep(node: CanvasNode): void {
    if (!node.detCommand) return;
    this.detRunning.set(true);
    this.detRunResult.set(null);
    this.svc.runDetStep(
      node.detSubtype ?? 'script',
      node.detCommand,
      node.detExpectedResult ?? '',
    ).subscribe(result => {
      this.detRunResult.set(result);
      this.detRunning.set(false);
    });
  }

  dryRunWorkflow(): void {
    const graph = this.toVpGraph();
    this.svc.dryRunVpGraph(graph).subscribe(result => {
      const v = result?.validation;
      if (!v) { this.goalResult.set('Dry-run: keine Antwort'); this.goalOk.set(false); return; }
      if (v.valid) {
        this.goalOk.set(true);
        this.goalResult.set(`✓ Valide (${result.step_count} Schritte, ${result.edge_count} Kanten)`);
      } else {
        this.goalOk.set(false);
        this.goalResult.set(`✗ Fehler: ${v.errors?.join(', ') ?? 'unbekannt'}`);
      }
    });
  }

  startVpWorkflow(): void {
    const text = this.goalText().trim();
    if (!text) return;
    const graph = this.toVpGraph();
    this.svc.startVpWorkflow(graph, {
      requested_by: 'codehug_internals',
      workflow_type: 'visual_process',
    }).subscribe(result => {
      const wid = (result as any)?.workflow_id ?? (result as any)?.id ?? null;
      if (wid) {
        this.workflowId.set(wid);
        this.startWorkflowPolling(wid);
        this.goalOk.set(true);
        this.goalResult.set(`Workflow gestartet: ${wid}`);
      } else {
        this.goalOk.set(!!(result as any)?.status && (result as any)?.status !== 'error');
        this.goalResult.set((result as any)?.status ?? 'Gestartet');
      }
    });
  }

  buildCanvas(blueprintId: string, playbookId: string): void {
    const bp = BLUEPRINTS.find(b => b.id === blueprintId);
    const pb = PLAYBOOKS.find(p => p.id === playbookId);
    if (!bp || !pb) return;

    const roles = bp.roles;
    const nodes: CanvasNode[] = [];
    const edges: CanvasEdge[] = [];
    let y = 30;
    const addEdge = (from: string, to: string, cond: EdgeCondition = 'always') =>
      edges.push({ id: `e-${++this._edgeSeq}`, from, to, condition: cond });

    nodes.push({ id: 'start', x: CX - 60, y, w: 120, h: 40, type: 'start', title: 'Ziel', enabled: true, inputs: [], outputs: [] });
    y += 40 + GAP_Y;

    nodes.push({ id: 'plan', x: CX - 90, y, w: 180, h: 52, type: 'planning', title: 'Planung', subtitle: 'LMStudio · Gemma', enabled: true, inputs: [], outputs: [{ name: 'plan', kind: 'json' as const, required: false, description: '' }] });
    addEdge('start', 'plan');
    y += 52 + GAP_Y;

    let prev = 'plan';
    pb.tasks.forEach((task, i) => {
      const nid = `task-${++this._nodeSeq}`;
      nodes.push({
        id: nid, x: CX - NODE_W / 2, y, w: NODE_W, h: NODE_H, type: 'task',
        title: task.title, subtitle: task.description,
        role: roles[i % roles.length],
        priority: task.priority, enabled: true,
        routing: { mode: 'auto' as RoutingMode },
        inputs: [], outputs: [],
      });
      addEdge(prev, nid);
      prev = nid;
      y += NODE_H + GAP_Y;
    });

    nodes.push({ id: 'verif', x: CX - 90, y, w: 180, h: 52, type: 'verification', title: 'Verifikation', subtitle: 'Review · Tests', enabled: true, inputs: [], outputs: [{ name: 'verification_report', kind: 'report' as const, required: false, description: '' }] });
    addEdge(prev, 'verif');
    y += 52 + GAP_Y;

    nodes.push({ id: 'end', x: CX - 60, y, w: 120, h: 40, type: 'end', title: 'Fertig', enabled: true, inputs: [], outputs: [] });
    addEdge('verif', 'end');

    this.nodes.set(nodes);
    this.edges.set(edges);
    this.selectedNodeId.set(null);
    this.selectedEdgeId.set(null);
    this.connectMode.set(false);
    this.connectSource.set(null);
  }

  // ── Interactions ──────────────────────────────────────────────────────────

  onBgMouseDown(e: MouseEvent): void {
    const tag = (e.target as SVGElement).tagName;
    if (tag === 'svg' || (e.target as SVGElement).classList.contains('ch-bg-rect')) {
      this._panning = { mx: e.clientX, my: e.clientY, tx: this.viewTx(), ty: this.viewTy() };
    }
  }

  onNodeMouseDown(e: MouseEvent, nodeId: string): void {
    if (this.connectMode()) return;
    e.stopPropagation();
    const n = this.nodes().find(n => n.id === nodeId);
    if (!n) return;
    const p = this.toCanvas(e);
    this._dragging = { nodeId, ox: p.x - n.x, oy: p.y - n.y };
    this._didDrag = false;
  }

  onNodeClick(e: MouseEvent, nodeId: string): void {
    if (this._didDrag) { this._didDrag = false; return; }
    e.stopPropagation();
    if (this.connectMode()) {
      const src = this.connectSource();
      if (!src) { this.connectSource.set(nodeId); return; }
      if (src !== nodeId) {
        this.edges.update(es => [...es, { id: `e-${++this._edgeSeq}`, from: src, to: nodeId, condition: 'always' }]);
      }
      this.connectSource.set(null); // stay in connect mode for next edge
      return;
    }
    this.selectedEdgeId.set(null);
    this.selectedNodeId.set(this.selectedNodeId() === nodeId ? null : nodeId);
  }

  onEdgeClick(e: MouseEvent, edgeId: string): void {
    e.stopPropagation();
    if (this.connectMode()) return;
    this.selectedNodeId.set(null);
    this.selectedEdgeId.set(this.selectedEdgeId() === edgeId ? null : edgeId);
  }

  @HostListener('document:mousemove', ['$event'])
  onMouseMove(e: MouseEvent): void {
    if (this._dragging) {
      this._didDrag = true;
      const p = this.toCanvas(e);
      this.nodes.update(ns => ns.map(n =>
        n.id === this._dragging!.nodeId
          ? { ...n, x: Math.max(0, p.x - this._dragging!.ox), y: Math.max(0, p.y - this._dragging!.oy) }
          : n
      ));
    }
    if (this._panning) {
      this.viewTx.set(this._panning.tx + e.clientX - this._panning.mx);
      this.viewTy.set(this._panning.ty + e.clientY - this._panning.my);
    }
  }

  @HostListener('document:keydown', ['$event'])
  onKeyDown(e: KeyboardEvent): void {
    if (e.key === 'Escape' && this.connectMode()) { this.cancelConnect(); }
  }

  @HostListener('document:mouseup')
  onMouseUp(): void {
    this._dragging = null;
    this._panning = null;
  }

  onWheel(e: WheelEvent): void {
    e.preventDefault();
    const old = this.viewScale();
    const next = Math.max(0.15, Math.min(3, old * (e.deltaY > 0 ? 0.92 : 1.08)));
    const rect = this.svgElRef.nativeElement.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    this.viewScale.set(next);
    this.viewTx.set(mx - (mx - this.viewTx()) * (next / old));
    this.viewTy.set(my - (my - this.viewTy()) * (next / old));
  }

  zoomIn(): void { this.viewScale.update(s => Math.min(3, s * 1.15)); }
  zoomOut(): void { this.viewScale.update(s => Math.max(0.15, s * 0.87)); }
  resetView(): void { this.viewScale.set(1); this.viewTx.set(40); this.viewTy.set(20); }
  toggleConnect(): void { this.connectMode.update(v => !v); if (!this.connectMode()) this.connectSource.set(null); }
  cancelConnect(): void { this.connectMode.set(false); this.connectSource.set(null); }

  // ── Node / Edge Operations ────────────────────────────────────────────────

  addFreeNode(): void {
    const { cx, cy } = this.viewCenter();
    const roles = this.currentRoles();
    const nid = `task-${++this._nodeSeq}`;
    this.nodes.update(ns => [...ns, {
      id: nid, x: cx - NODE_W / 2, y: cy - NODE_H / 2, w: NODE_W, h: NODE_H,
      type: 'task', title: 'Neuer Schritt', subtitle: '',
      role: roles[ns.filter(n => n.type === 'task').length % roles.length] ?? '',
      priority: 'Medium', enabled: true,
      routing: { mode: 'auto' as RoutingMode },
      inputs: [], outputs: [],
    }]);
    this.selectedNodeId.set(nid);
  }

  addGateNode(): void {
    const { cx, cy } = this.viewCenter();
    const nid = `gate-${++this._nodeSeq}`;
    this.nodes.update(ns => [...ns, {
      id: nid, x: cx - 120, y: cy - 30, w: 240, h: 58,
      type: 'gate', title: 'Verification Gate', subtitle: '',
      gateSubtype: 'auto-verify', failAction: 'block', enabled: true,
      inputs: [], outputs: [],
    }]);
    this.selectedNodeId.set(nid);
  }

  addDetNode(): void {
    const { cx, cy } = this.viewCenter();
    const nid = `det-${++this._nodeSeq}`;
    this.nodes.update(ns => [...ns, {
      id: nid, x: cx - NODE_W / 2, y: cy - NODE_H / 2, w: NODE_W, h: NODE_H,
      type: 'det', title: 'Deterministischer Schritt', subtitle: '',
      detSubtype: 'script', detCommand: '', failAction: 'block',
      priority: 'Medium', enabled: true,
      routing: { mode: 'auto' },
      inputs: [], outputs: [],
    }]);
    this.selectedNodeId.set(nid);
  }

  addReviewNode(): void {
    const { cx, cy } = this.viewCenter();
    const nid = `rev-${++this._nodeSeq}`;
    this.nodes.update(ns => [...ns, {
      id: nid, x: cx - 120, y: cy - 30, w: 240, h: 58,
      type: 'review', title: 'Review / Freigabe', subtitle: '',
      failAction: 'block', enabled: true, inputs: [], outputs: [],
    }]);
    this.selectedNodeId.set(nid);
  }

  addForkNode(): void {
    const { cx, cy } = this.viewCenter();
    const nid = `fork-${++this._nodeSeq}`;
    this.nodes.update(ns => [...ns, {
      id: nid, x: cx - 50, y: cy - 30, w: 100, h: 60,
      type: 'fork', title: 'Fork', enabled: true, inputs: [], outputs: [],
    }]);
    this.selectedNodeId.set(nid);
  }

  addJoinNode(): void {
    const { cx, cy } = this.viewCenter();
    const nid = `join-${++this._nodeSeq}`;
    this.nodes.update(ns => [...ns, {
      id: nid, x: cx - 60, y: cy - 22, w: 120, h: 44,
      type: 'join', title: 'Join', enabled: true, inputs: [], outputs: [],
    }]);
    this.selectedNodeId.set(nid);
  }

  // ── I/O Artifact Slots ────────────────────────────────────────────────────

  addInput(nodeId: string): void {
    this.nodes.update(ns => ns.map(n => n.id === nodeId ? {
      ...n, inputs: [...n.inputs, { name: `input_${n.inputs.length + 1}`, kind: 'text' as const, required: true, description: '' }]
    } : n));
  }

  addArtifactBinding(edgeId: string, raw: string): void {
    if (!raw) return;
    const [outputName, inputName] = raw.split('=>');
    if (!outputName || !inputName) return;
    this.edges.update(es => es.map(e => {
      if (e.id !== edgeId) return e;
      const bindings = [...(e.bindings ?? [])];
      if (!bindings.some(b => b.outputName === outputName && b.inputName === inputName)) {
        bindings.push({ outputName, inputName });
      }
      return { ...e, bindings, outputName: e.outputName ?? outputName };
    }));
    this.syncInputBindings();
  }

  removeArtifactBinding(edgeId: string, binding: ArtifactBinding): void {
    this.edges.update(es => es.map(e => e.id === edgeId
      ? { ...e, bindings: (e.bindings ?? []).filter(b => b.outputName !== binding.outputName || b.inputName !== binding.inputName) }
      : e
    ));
    this.syncInputBindings();
  }

  availableBindingOptions(edge: CanvasEdge): { value: string; label: string }[] {
    const src = this.nodes().find(n => n.id === edge.from);
    const dst = this.nodes().find(n => n.id === edge.to);
    if (!src || !dst) return [];
    const existing = new Set((edge.bindings ?? []).map(b => `${b.outputName}=>${b.inputName}`));
    const options: { value: string; label: string }[] = [];
    for (const out of src.outputs ?? []) {
      for (const inp of dst.inputs ?? []) {
        if (inp.kind !== out.kind && inp.kind !== 'text' && out.kind !== 'text') continue;
        const value = `${out.name}=>${inp.name}`;
        if (!existing.has(value)) {
          options.push({ value, label: `${out.name} → ${inp.name}` });
        }
      }
    }
    return options;
  }

  edgeBindingLabel(edge: CanvasEdge): string {
    const bindings = edge.bindings ?? [];
    if (!bindings.length) return '';
    if (bindings.length === 1) return `📦 ${bindings[0].outputName}`;
    return `📦 ${bindings.length} artifacts`;
  }

  addOutput(nodeId: string): void {
    this.nodes.update(ns => ns.map(n => n.id === nodeId ? {
      ...n, outputs: [...n.outputs, { name: `output_${n.outputs.length + 1}`, kind: 'text' as const, required: false, description: '' }]
    } : n));
  }

  patchSlot(nodeId: string, field: 'inputs' | 'outputs', index: number, patch: Partial<ArtifactSlot>): void {
    this.nodes.update(ns => ns.map(n => {
      if (n.id !== nodeId) return n;
      const slots = [...n[field]];
      slots[index] = { ...slots[index], ...patch };
      return { ...n, [field]: slots };
    }));
  }

  removeSlot(nodeId: string, field: 'inputs' | 'outputs', index: number): void {
    this.nodes.update(ns => ns.map(n => {
      if (n.id !== nodeId) return n;
      const slots = n[field].filter((_, i) => i !== index);
      return { ...n, [field]: slots };
    }));
  }

  // ── Geometry ──────────────────────────────────────────────────────────────

  forkPoints(node: CanvasNode): string {
    const cx = node.w / 2, cy = node.h / 2;
    return `${cx},0 ${node.w},${cy} ${cx},${node.h} 0,${cy}`;
  }

  setRoutingMode(nodeId: string, mode: RoutingMode): void {
    this.nodes.update(ns => ns.map(n => n.id === nodeId
      ? { ...n, routing: { mode, backend: 'ananta', capability: 'coder', workerName: '' } }
      : n
    ));
  }

  patchRouting(nodeId: string, patch: Partial<StepRouting>): void {
    this.nodes.update(ns => ns.map(n => n.id === nodeId
      ? { ...n, routing: { ...(n.routing ?? { mode: 'auto' as RoutingMode }), ...patch } }
      : n
    ));
  }

  isComplexNode(node: CanvasNode): boolean {
    return node.type === 'task' || node.type === 'det' || node.type === 'gate' || node.type === 'review' || node.type === 'fork' || node.type === 'join';
  }

  routingLabel(node: CanvasNode): string {
    const r = node.routing;
    if (!r || r.mode === 'auto') return '';
    if (r.mode === 'backend') return r.backend ?? '';
    if (r.mode === 'worker') return r.workerName?.split('-').pop() ?? '';
    if (r.mode === 'capability') return r.capability ?? '';
    return '';
  }

  routingBadgeW(node: CanvasNode): number {
    return this.routingLabel(node).length * 5.5 + 10;
  }

  private viewCenter(): { cx: number; cy: number } {
    const svg = this.svgElRef.nativeElement;
    return {
      cx: (svg.clientWidth / 2 - this.viewTx()) / this.viewScale(),
      cy: (svg.clientHeight / 2 - this.viewTy()) / this.viewScale(),
    };
  }

  insertOnEdge(e: MouseEvent, edgeId: string): void {
    e.stopPropagation();
    const edge = this.edges().find(ed => ed.id === edgeId);
    if (!edge) return;
    const mp = this.edgeMidpoint(edge);
    const roles = this.currentRoles();
    const taskCount = this.nodes().filter(n => n.type === 'task').length;
    const nid = `task-${++this._nodeSeq}`;
    const e1 = `e-${++this._edgeSeq}`;
    const e2 = `e-${++this._edgeSeq}`;
    this.nodes.update(ns => [...ns, {
      id: nid, x: mp.x - NODE_W / 2, y: mp.y - NODE_H / 2, w: NODE_W, h: NODE_H,
      type: 'task', title: 'Eingefügter Schritt', subtitle: '',
      role: roles[taskCount % roles.length] ?? '', priority: 'Medium', enabled: true,
      routing: { mode: 'auto' as RoutingMode },
      inputs: [], outputs: [],
    }]);
    this.edges.update(es => [
      ...es.filter(ed => ed.id !== edgeId),
      { id: e1, from: edge.from, to: nid, condition: edge.condition },
      { id: e2, from: nid, to: edge.to, condition: 'always' as EdgeCondition },
    ]);
    this.selectedNodeId.set(nid);
    this.selectedEdgeId.set(null);
  }

  deleteNode(nodeId: string): void {
    this.nodes.update(ns => ns.filter(n => n.id !== nodeId));
    this.edges.update(es => es.filter(e => e.from !== nodeId && e.to !== nodeId));
    this.selectedNodeId.set(null);
  }

  patchNode(id: string, patch: Partial<CanvasNode>): void {
    this.nodes.update(ns => ns.map(n => n.id === id ? { ...n, ...patch } : n));
  }

  deleteEdge(edgeId: string): void {
    this.edges.update(es => es.filter(e => e.id !== edgeId));
    this.selectedEdgeId.set(null);
  }

  patchEdge(id: string, patch: Partial<CanvasEdge>): void {
    this.edges.update(es => es.map(e => e.id === id ? { ...e, ...patch } : e));
  }

  // ── Geometry ──────────────────────────────────────────────────────────────

  edgePath(edge: CanvasEdge): string {
    const src = this.nodes().find(n => n.id === edge.from);
    const dst = this.nodes().find(n => n.id === edge.to);
    if (!src || !dst) return '';
    if (edge.condition === 'back_edge') {
      // Loop: goes left side of graph to target above source
      const sx = src.x, sy = src.y + src.h / 2;
      const tx = dst.x, ty = dst.y + dst.h / 2;
      const lx = Math.min(sx, tx) - 60;
      return `M ${sx} ${sy} C ${lx} ${sy}, ${lx} ${ty}, ${tx} ${ty}`;
    }
    const sx = src.x + src.w / 2, sy = src.y + src.h;
    const tx = dst.x + dst.w / 2, ty = dst.y;
    const dy = Math.max(28, Math.abs(ty - sy) * 0.38);
    return `M ${sx} ${sy} C ${sx} ${sy + dy}, ${tx} ${ty - dy}, ${tx} ${ty}`;
  }

  edgeMidpoint(edge: CanvasEdge): { x: number; y: number } {
    const src = this.nodes().find(n => n.id === edge.from);
    const dst = this.nodes().find(n => n.id === edge.to);
    if (!src || !dst) return { x: 0, y: 0 };
    return { x: (src.x + src.w / 2 + dst.x + dst.w / 2) / 2, y: (src.y + src.h + dst.y) / 2 };
  }

  edgeMarkerSuffix(edge: CanvasEdge, selected: boolean): string {
    if (selected) return '-sel';
    if (edge.condition === 'on_success') return '-ok';
    if (edge.condition === 'on_failure') return '-fail';
    if (edge.condition === 'back_edge') return '-loop';
    return '';
  }

  nodeLabel(nodeId: string): string {
    return this.nodes().find(n => n.id === nodeId)?.title ?? nodeId;
  }

  // ── Live state ────────────────────────────────────────────────────────────

  nodeIsActive(node: CanvasNode): boolean {
    const activeStep = this.activeWorkflowStepId();
    if (activeStep) return node.id === activeStep;
    const ap = this.autopilot();
    if (!ap.running) return false;
    if (node.type === 'planning') return ap.dispatched_count === 0 && ap.tick_count > 0;
    if (node.type === 'task') return ap.dispatched_count > 0 && ap.completed_count < ap.dispatched_count;
    if (node.type === 'verification') return ap.completed_count > 0;
    return false;
  }

  workerIsActive(w: AnantaWorker): boolean { return this.autopilot().running && w.status === 'online'; }
  workerStatus(name: string): string { return this.workers().find(w => w.name === name)?.status ?? 'offline'; }

  activeWorkflowStepId(): string | null {
    const status = this.workflowStatus();
    const steps = Array.isArray(status?.['steps']) ? status?.['steps'] as Record<string, unknown>[] : [];
    const active = steps.find(s => ['running', 'waiting_for_approval'].includes(String(s['status'] ?? '')));
    return String(active?.['step_id'] ?? active?.['id'] ?? '') || null;
  }

  private startWorkflowPolling(workflowId: string): void {
    this._workflowPollSub?.unsubscribe();
    const load = () => {
      this.svc.getVpWorkflowStatus(workflowId).subscribe(status => this.workflowStatus.set(status));
      this.svc.getVpWorkflowEvents(workflowId).subscribe(events => this.workflowEvents.set(events));
    };
    load();
    this._workflowPollSub = interval(2000).subscribe(() => load());
  }

  // ── Goal Submit ───────────────────────────────────────────────────────────

  submitGoal(): void {
    const text = this.goalText().trim();
    if (!text) return;
    fetch('http://127.0.0.1:5000/goals', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        description: text,
        security_level: this.selectedSecurity(),
        config_profile: this.selectedBlueprint(),
        playbook: this.selectedPlaybook(),
      }),
    }).then(r => {
      if (r.ok) {
        this.goalOk.set(true);
        this.goalResult.set('Ziel gesendet.');
        this.goalText.set('');
      } else {
        r.text().then(t => { this.goalOk.set(false); this.goalResult.set(`Fehler ${r.status}: ${t.slice(0, 80)}`); });
      }
    }).catch(err => { this.goalOk.set(false); this.goalResult.set(`Netzwerkfehler: ${err}`); });
  }

  // ── Private ───────────────────────────────────────────────────────────────

  private toCanvas(e: MouseEvent): { x: number; y: number } {
    const rect = this.svgElRef.nativeElement.getBoundingClientRect();
    return {
      x: (e.clientX - rect.left - this.viewTx()) / this.viewScale(),
      y: (e.clientY - rect.top - this.viewTy()) / this.viewScale(),
    };
  }

  private syncInputBindings(): void {
    const bindingByTarget = new Map<string, ArtifactBinding & { sourceId: string }>();
    for (const edge of this.edges()) {
      for (const binding of edge.bindings ?? []) {
        bindingByTarget.set(`${edge.to}:${binding.inputName}`, { ...binding, sourceId: edge.from });
      }
    }
    this.nodes.update(ns => ns.map(n => ({
      ...n,
      inputs: n.inputs.map(inp => {
        const binding = bindingByTarget.get(`${n.id}:${inp.name}`);
        return {
          ...inp,
          producedByStepId: binding?.sourceId,
          producedByOutputName: binding?.outputName,
        };
      }),
    })));
  }
}
