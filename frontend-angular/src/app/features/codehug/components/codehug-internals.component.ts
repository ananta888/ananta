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
import { interval, Subscription } from 'rxjs';
import { switchMap } from 'rxjs/operators';
import { InternalsService, AnantaWorker, AutopilotStatus } from '../services/internals.service';
import { DecimalPipe } from '@angular/common';

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

type NodeType = 'start' | 'planning' | 'task' | 'det' | 'gate' | 'review' | 'verification' | 'end';
type EdgeCondition = 'always' | 'on_success' | 'on_failure';
type Priority = 'High' | 'Medium' | 'Low';
type RoutingMode = 'auto' | 'backend' | 'worker' | 'capability';
type DetSubtype = 'script' | 'api-call' | 'regex-check' | 'git-op' | 'file-check';
type GateSubtype = 'auto-verify' | 'human-approval' | 'test-run' | 'lint' | 'type-check';
type FailAction = 'block' | 'continue' | 'rollback' | 'retry';

interface StepRouting {
  mode: RoutingMode;
  backend?: string;      // 'ananta' | 'opencode' | 'hermes' | 'sgpt' | 'claude' | 'lmstudio'
  workerName?: string;
  capability?: string;   // 'planner' | 'researcher' | 'coder' | 'reviewer' | 'tester'
}

interface CanvasNode {
  id: string;
  x: number; y: number;
  w: number; h: number;
  type: NodeType;
  title: string;
  subtitle?: string;
  role?: string;
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
};

interface CanvasEdge {
  id: string;
  from: string;
  to: string;
  condition: EdgeCondition;
  label?: string;
}

const NODE_W = 220;
const NODE_H = 68;
const GAP_Y = 52;
const CX = 300;

const PRIORITY_COLOR: Record<Priority, string> = { High: '#ef4444', Medium: '#f59e0b', Low: '#22c55e' };
const COND_COLOR: Record<EdgeCondition, string> = { always: '#9ca3af', on_success: '#22c55e', on_failure: '#ef4444' };

@Component({
  selector: 'ch-internals',
  standalone: true,
  imports: [DecimalPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
<div class="ch-int">

  <!-- ── Top Config Bar ── -->
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

  <!-- ── Body ── -->
  <div class="ch-int-body">

    <!-- Left Palette -->
    <aside class="ch-palette" [class.ch-palette-connect]="connectMode()">
      <div class="ch-pal-hd">Elemente</div>
      <button class="ch-pal-elem ch-pal-elem-task" (click)="addFreeNode()">💬 LLM Task</button>
      <button class="ch-pal-elem ch-pal-elem-det"  (click)="addDetNode()">⚙ Deterministisch</button>
      <button class="ch-pal-elem ch-pal-elem-gate" (click)="addGateNode()">🚦 Gate</button>
      <button class="ch-pal-elem ch-pal-elem-rev"  (click)="addReviewNode()">👁 Review</button>

      <div class="ch-pal-div"></div>
      <div class="ch-pal-hd">Backends</div>
      @for (b of BACKENDS; track b) {
        <div class="ch-pal-backend">
          <span class="ch-pal-backend-dot"></span>
          <span>{{ b }}</span>
        </div>
      }

      <div class="ch-pal-div"></div>
      <div class="ch-pal-hd">Workers (Live)</div>
      @for (w of workers(); track w.name) {
        <div class="ch-pal-w" [class.ch-pal-w-live]="workerIsActive(w)">
          <span class="ch-pal-dot" [class.ch-pal-dot-on]="w.status === 'online'" [class.ch-pal-dot-off]="w.status !== 'online'"></span>
          <div class="ch-pal-winfo">
            <span class="ch-pal-wname">{{ w.name }}</span>
            <span class="ch-pal-wcaps">{{ w.worker_roles.join(' · ') }}</span>
          </div>
        </div>
      }
      @if (workers().length === 0) { <p class="ch-muted" style="padding:6px 8px">Keine Worker</p> }

      <div class="ch-pal-div"></div>
      <div class="ch-pal-hd">Blueprint-Rollen</div>
      @for (r of currentRoles(); track r) { <div class="ch-pal-role">{{ r }}</div> }
    </aside>

    <!-- SVG Canvas -->
    <main class="ch-canvas-wrap" (wheel)="onWheel($event)">
      <div class="ch-zoom-ctrl">
        <button type="button" (click)="zoomIn()">+</button>
        <span>{{ viewScale() * 100 | number:'1.0-0' }}%</span>
        <button type="button" (click)="zoomOut()">−</button>
        <button type="button" (click)="resetView()">⊙</button>
      </div>
      @if (connectMode() && connectSource()) {
        <div class="ch-connect-hint">
          Ziel-Knoten anklicken — <button type="button" (click)="cancelConnect()">Abbrechen</button>
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

              <!-- Node shape (unified via NODE_STYLE lookup) -->
              @if (node.type === 'start' || node.type === 'end') {
                <rect [attr.width]="node.w" [attr.height]="node.h" rx="24"
                  [attr.fill]="NODE_STYLE[node.type].fill"
                  [attr.stroke]="nSrc ? '#f59e0b' : (nSel ? '#7c3aed' : NODE_STYLE[node.type].stroke)"
                  [attr.stroke-width]="nSel || nSrc ? 2.5 : 1.5"/>
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
                <!-- Type icon -->
                @if (node.type === 'det') { <text x="9" y="15" font-size="10">⚙</text> }
                @else if (node.type === 'review') { <text x="9" y="15" font-size="10">👁</text> }
                @else if (node.type === 'gate') { <text x="9" y="15" font-size="10">🚦</text> }
                <!-- Routing badge -->
                @if ((node.type === 'task' || node.type === 'det') && node.routing && node.routing.mode !== 'auto') {
                  <rect [attr.x]="node.w - 2 - routingBadgeW(node)" y="2" [attr.width]="routingBadgeW(node)" height="14" rx="3"
                    fill="#eef2ff" stroke="#4f46e5" stroke-width="0.5"/>
                  <text [attr.x]="node.w - 5" y="13" text-anchor="end" font-size="8" fill="#4f46e5" font-weight="600">
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

              <!-- Labels -->
              <text [attr.x]="node.w/2"
                [attr.y]="isComplexNode(node) ? 22 : node.h/2 + 5"
                text-anchor="middle" font-size="12" font-weight="600"
                [attr.fill]="!node.enabled ? '#9ca3af' : '#111827'">{{ node.title }}</text>

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
          </select>
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
          <div class="ch-goal-hd">Ziel starten</div>
          <textarea class="ch-fta" placeholder="Ziel beschreiben…"
            [value]="goalText()"
            (input)="goalText.set($any($event.target).value)"></textarea>
          <button type="button" class="ch-start-btn"
            [disabled]="!goalText().trim()"
            (click)="submitGoal()">▶ An Ananta senden</button>
          @if (goalResult()) {
            <div class="ch-result" [class.ch-result-ok]="goalOk()">{{ goalResult() }}</div>
          }
        </div>
      }
    </aside>
  </div>
</div>
  `,
  styles: [`
:host { display: flex; flex-direction: column; height: 100%; min-height: 0; }

.ch-int { display: flex; flex-direction: column; height: 100%; min-height: 0; }

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
.ch-run-badge {
  display: flex; align-items: center; gap: 5px; padding: 2px 8px;
  border-radius: 999px; background: color-mix(in srgb, #3b82f6 12%, transparent);
  border: 1px solid #3b82f6; color: #1e40af; font-weight: 600; font-size: 10px;
}
.ch-dot-run { display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: #3b82f6; animation: dp 1.4s infinite; }
@keyframes dp { 0%,100%{opacity:1}50%{opacity:0.25} }
.ch-idle-badge { font-size: 10px; color: var(--muted); padding: 2px 7px; border: 1px solid var(--border); border-radius: 999px; }

/* ── Body ── */
.ch-int-body { display: grid; grid-template-columns: 150px 1fr 225px; flex: 1; min-height: 0; overflow: hidden; }

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

  // ── Lifecycle ─────────────────────────────────────────────────────────────

  ngOnInit(): void {
    this.buildCanvas('scrum', 'bug_fix');
    this.svc.getWorkers().subscribe(w => this.workers.set(w));
    this.svc.getAutopilotStatus().subscribe(s => this.autopilot.set(s));
    this._pollSub = interval(3000).pipe(switchMap(() => this.svc.getAutopilotStatus()))
      .subscribe(s => this.autopilot.set(s));
  }

  ngAfterViewInit(): void {}
  ngOnDestroy(): void { this._pollSub?.unsubscribe(); }

  // ── Blueprint / Playbook ──────────────────────────────────────────────────

  onBlueprintChange(id: string): void {
    this.selectedBlueprint.set(id);
    this.buildCanvas(id, this.selectedPlaybook());
  }

  onPlaybookChange(id: string): void {
    this.selectedPlaybook.set(id);
    this.buildCanvas(this.selectedBlueprint(), id);
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

    nodes.push({ id: 'start', x: CX - 60, y, w: 120, h: 40, type: 'start', title: 'Ziel', enabled: true });
    y += 40 + GAP_Y;

    nodes.push({ id: 'plan', x: CX - 90, y, w: 180, h: 52, type: 'planning', title: 'Planung', subtitle: 'LMStudio · Gemma', enabled: true });
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
      });
      addEdge(prev, nid);
      prev = nid;
      y += NODE_H + GAP_Y;
    });

    nodes.push({ id: 'verif', x: CX - 90, y, w: 180, h: 52, type: 'verification', title: 'Verifikation', subtitle: 'Review · Tests', enabled: true });
    addEdge(prev, 'verif');
    y += 52 + GAP_Y;

    nodes.push({ id: 'end', x: CX - 60, y, w: 120, h: 40, type: 'end', title: 'Fertig', enabled: true });
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
      this.connectSource.set(null);
      this.connectMode.set(false);
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
    }]);
    this.selectedNodeId.set(nid);
  }

  addReviewNode(): void {
    const { cx, cy } = this.viewCenter();
    const nid = `rev-${++this._nodeSeq}`;
    this.nodes.update(ns => [...ns, {
      id: nid, x: cx - 120, y: cy - 30, w: 240, h: 58,
      type: 'review', title: 'Review / Freigabe', subtitle: '',
      failAction: 'block', enabled: true,
    }]);
    this.selectedNodeId.set(nid);
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
    return node.type === 'task' || node.type === 'det' || node.type === 'gate' || node.type === 'review';
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
      role: roles[taskCount % roles.length] ?? '', workerName: null, priority: 'Medium', enabled: true,
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
    return '';
  }

  nodeLabel(nodeId: string): string {
    return this.nodes().find(n => n.id === nodeId)?.title ?? nodeId;
  }

  // ── Live state ────────────────────────────────────────────────────────────

  nodeIsActive(node: CanvasNode): boolean {
    const ap = this.autopilot();
    if (!ap.running) return false;
    if (node.type === 'planning') return ap.dispatched_count === 0 && ap.tick_count > 0;
    if (node.type === 'task') return ap.dispatched_count > 0 && ap.completed_count < ap.dispatched_count;
    if (node.type === 'verification') return ap.completed_count > 0;
    return false;
  }

  workerIsActive(w: AnantaWorker): boolean { return this.autopilot().running && w.status === 'online'; }
  workerStatus(name: string): string { return this.workers().find(w => w.name === name)?.status ?? 'offline'; }

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
}
