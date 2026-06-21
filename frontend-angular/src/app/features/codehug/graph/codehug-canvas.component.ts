import {
  AfterViewInit,
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  EventEmitter,
  HostListener,
  Input,
  OnChanges,
  Output,
  SimpleChanges,
  ViewChild,
  computed,
  signal,
} from '@angular/core';
import { DatePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';

import {
  ChTopologyReadModel,
  ChHubInstanceReadModel,
  ChWorkerInstanceReadModel,
  ChTestLayerReadModel,
  ChRoutingRuleReadModel,
  ChAgentRunReadModel,
  ChAgentStepReadModel,
  ChCliBackend,
} from '../models/codehug.models';

// ─────────────────────────────────────────────────────────────────────────────
// Canvas data models
// ─────────────────────────────────────────────────────────────────────────────

export type ChNodeKind =
  | 'hub'
  | 'worker-llm'
  | 'worker-det'
  | 'policy-layer'
  | 'test-layer'
  | 'routing-rule';

export type ChNodeRunState = 'idle' | 'active' | 'completed' | 'failed' | 'skipped';

export interface ChCanvasNode {
  id: string;
  kind: ChNodeKind;
  label: string;
  sublabel: string;
  badge?: string;
  x: number;
  y: number;
  w: number;
  h: number;
  runState: ChNodeRunState;
  payload: ChHubInstanceReadModel | ChWorkerInstanceReadModel | ChTestLayerReadModel | ChRoutingRuleReadModel;
}

export interface ChCanvasEdge {
  id: string;
  fromId: string;
  toId: string;
  label?: string;
  runState: ChNodeRunState;
}

// ─────────────────────────────────────────────────────────────────────────────
// Node visual config
// ─────────────────────────────────────────────────────────────────────────────

interface NodeStyle { fill: string; stroke: string; strokeWidth: number; textColor: string; icon: string; }

function nodeStyle(kind: ChNodeKind): NodeStyle {
  switch (kind) {
    case 'hub':          return { fill: '#fef3c7', stroke: '#d97706', strokeWidth: 2.5, textColor: '#78350f', icon: '⬡' };
    case 'worker-llm':   return { fill: '#dbeafe', stroke: '#2563eb', strokeWidth: 1.5, textColor: '#1e40af', icon: '◈' };
    case 'worker-det':   return { fill: '#f3f4f6', stroke: '#6b7280', strokeWidth: 1.5, textColor: '#374151', icon: '⚙' };
    case 'policy-layer': return { fill: '#fff7ed', stroke: '#ea580c', strokeWidth: 1.5, textColor: '#7c2d12', icon: '⚖' };
    case 'test-layer':   return { fill: '#f0fdf4', stroke: '#16a34a', strokeWidth: 1.5, textColor: '#14532d', icon: '▣' };
    case 'routing-rule': return { fill: '#f0fdfa', stroke: '#0d9488', strokeWidth: 1.5, textColor: '#134e4a', icon: '⤳' };
  }
}

function runStateGlow(state: ChNodeRunState): string | null {
  switch (state) {
    case 'active':    return '#3b82f6';
    case 'completed': return '#22c55e';
    case 'failed':    return '#ef4444';
    default:          return null;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Auto-layout
// ─────────────────────────────────────────────────────────────────────────────

const NODE_W_HUB = 220;
const NODE_H_HUB = 56;
const NODE_W_WORKER = 160;
const NODE_H_WORKER = 62;
const NODE_W_LAYER = 170;
const NODE_H_LAYER = 58;
const NODE_W_RULE = 170;
const NODE_H_RULE = 50;

function autoLayout(topology: ChTopologyReadModel): { nodes: ChCanvasNode[]; edges: ChCanvasEdge[] } {
  const nodes: ChCanvasNode[] = [];
  const edges: ChCanvasEdge[] = [];

  const canvasW = 900;
  const hubY = 40;
  const workerLlmY = 180;
  const workerDetY = 310;
  const layerY = 60;
  const ruleY = 220;

  // Hubs — centered at top
  const hubTotal = topology.hubs.length * (NODE_W_HUB + 40) - 40;
  topology.hubs.forEach((h, i) => {
    const x = (canvasW - hubTotal) / 2 + i * (NODE_W_HUB + 40);
    nodes.push({
      id: `hub::${h.id}`,
      kind: 'hub',
      label: `Hub · ${h.id.slice(0, 14)}`,
      sublabel: h.url,
      badge: h.status,
      x,
      y: hubY,
      w: NODE_W_HUB,
      h: NODE_H_HUB,
      runState: 'idle',
      payload: h,
    });
  });

  // Workers — LLM below hub, Det below LLM
  const llmWorkers = topology.workers.filter(w => w.cliBackend !== 'deterministic');
  const detWorkers = topology.workers.filter(w => w.cliBackend === 'deterministic');

  const llmGap = llmWorkers.length > 0 ? Math.max(180, canvasW / (llmWorkers.length + 1)) : 180;
  llmWorkers.forEach((w, i) => {
    const x = 40 + i * llmGap;
    const nid = `worker::${w.id}`;
    nodes.push({
      id: nid,
      kind: 'worker-llm',
      label: w.cliBackend,
      sublabel: w.model.length > 18 ? w.model.slice(0, 16) + '…' : w.model,
      badge: w.llmProvider,
      x,
      y: workerLlmY,
      w: NODE_W_WORKER,
      h: NODE_H_WORKER,
      runState: 'idle',
      payload: w,
    });
    // connect each hub to each llm worker
    topology.hubs.forEach(h => {
      edges.push({ id: `e-${h.id}-${w.id}`, fromId: `hub::${h.id}`, toId: nid, runState: 'idle' });
    });
  });

  const detGap = detWorkers.length > 0 ? Math.max(180, canvasW / (detWorkers.length + 1)) : 180;
  detWorkers.forEach((w, i) => {
    const x = 40 + i * detGap;
    const nid = `worker::${w.id}`;
    nodes.push({
      id: nid,
      kind: 'worker-det',
      label: 'deterministic',
      sublabel: w.type,
      x,
      y: workerDetY,
      w: NODE_W_WORKER,
      h: NODE_H_WORKER,
      runState: 'idle',
      payload: w,
    });
    topology.hubs.forEach(h => {
      edges.push({ id: `e-${h.id}-${w.id}`, fromId: `hub::${h.id}`, toId: nid, runState: 'idle' });
    });
  });

  // Layers — left column
  const layerX = 30;
  topology.activeLayers.forEach((l, i) => {
    nodes.push({
      id: `layer::${l.id}`,
      kind: 'test-layer',
      label: l.name,
      sublabel: `order ${l.order}${l.enabled ? '' : ' · deaktiviert'}`,
      badge: l.enabled ? 'on' : 'off',
      x: layerX,
      y: layerY + i * (NODE_H_LAYER + 20),
      w: NODE_W_LAYER,
      h: NODE_H_LAYER,
      runState: 'idle',
      payload: l,
    });
    topology.hubs.forEach(h => {
      edges.push({ id: `e-layer-${h.id}-${l.id}`, fromId: `hub::${h.id}`, toId: `layer::${l.id}`, label: 'governs', runState: 'idle' });
    });
  });

  // Routing rules — right column
  const ruleX = canvasW - NODE_W_RULE - 20;
  topology.routingRules.slice(0, 6).forEach((r, i) => {
    nodes.push({
      id: `rule::${r.id}`,
      kind: 'routing-rule',
      label: r.description.length > 20 ? r.description.slice(0, 18) + '…' : r.description,
      sublabel: `→ ${r.selectedBackend}`,
      badge: `p${r.priority}`,
      x: ruleX,
      y: ruleY + i * (NODE_H_RULE + 16),
      w: NODE_W_RULE,
      h: NODE_H_RULE,
      runState: 'idle',
      payload: r,
    });
  });

  return { nodes, edges };
}

// ─────────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────────

/**
 * CodeHugCanvasComponent — interaktiver Node-Canvas für die Internals-Ansicht.
 *
 * Stellt die Hub/Worker-Topologie, Test-Layer und Routing-Regeln als
 * positionierbare, klickbare Nodes auf einem SVG-Canvas dar.
 * Unterstützt Drag/Zoom/Pan, Node-Inspektion und Live-Lauf-Highlighting.
 */
@Component({
  selector: 'ch-canvas',
  standalone: true,
  imports: [DatePipe, FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="ch-cv-root" [class.ch-cv-inspector-open]="selectedNode() !== null">

      <!-- Toolbar -->
      <div class="ch-cv-toolbar">
        <span class="ch-cv-toolbar-title">System Canvas</span>
        <div class="ch-cv-toolbar-sep"></div>

        @if (activeRun) {
          <span class="ch-cv-run-badge">
            <span class="ch-cv-run-dot"></span>
            Run aktiv · {{ activeRun.actualCliBackend }} · {{ activeRun.deterministicStepCount + activeRun.llmStepCount }} Schritte
          </span>
        }

        <button type="button" class="ch-cv-tb-btn" (click)="zoomIn()" title="Vergrößern">+</button>
        <button type="button" class="ch-cv-tb-btn" (click)="zoomOut()" title="Verkleinern">−</button>
        <button type="button" class="ch-cv-tb-btn" (click)="resetView()" title="Ansicht zurücksetzen">⟲</button>
        <button type="button" class="ch-cv-tb-btn" (click)="fitToContent()" title="Alles einpassen">⊡</button>

        <div class="ch-cv-toolbar-sep"></div>
        <span class="ch-cv-zoom-label">{{ (zoom() * 100).toFixed(0) }}%</span>

        @if (selectedNode() !== null) {
          <button type="button" class="ch-cv-tb-btn" (click)="clearSelection()">✕ Inspector</button>
        }
      </div>

      <!-- SVG Canvas -->
      <div class="ch-cv-canvas-wrap">
        <svg
          #svgEl
          class="ch-cv-svg"
          (mousedown)="onSvgMousedown($event)"
          (wheel)="onWheel($event)"
          [attr.cursor]="isPanning() ? 'grabbing' : 'default'">

          <defs>
            <!-- Grid pattern -->
            <pattern id="ch-cv-grid" width="24" height="24" patternUnits="userSpaceOnUse"
              [attr.patternTransform]="gridTransform()">
              <path d="M 24 0 L 0 0 0 24" fill="none" stroke="var(--border)" stroke-width="0.5" opacity="0.6"/>
            </pattern>
            <!-- Arrow marker for edges -->
            <marker id="ch-cv-arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
              <path d="M0,0 L0,6 L8,3 z" fill="#9ca3af" />
            </marker>
            <marker id="ch-cv-arrow-active" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
              <path d="M0,0 L0,6 L8,3 z" fill="#3b82f6" />
            </marker>

            <!-- Glow filters for run states -->
            <filter id="ch-cv-glow-active" x="-30%" y="-30%" width="160%" height="160%">
              <feGaussianBlur stdDeviation="4" result="blur" />
              <feFlood flood-color="#3b82f6" flood-opacity="0.6" result="color"/>
              <feComposite in="color" in2="blur" operator="in" result="glow"/>
              <feMerge><feMergeNode in="glow"/><feMergeNode in="SourceGraphic"/></feMerge>
            </filter>
            <filter id="ch-cv-glow-completed" x="-30%" y="-30%" width="160%" height="160%">
              <feGaussianBlur stdDeviation="3" result="blur" />
              <feFlood flood-color="#22c55e" flood-opacity="0.5" result="color"/>
              <feComposite in="color" in2="blur" operator="in" result="glow"/>
              <feMerge><feMergeNode in="glow"/><feMergeNode in="SourceGraphic"/></feMerge>
            </filter>
            <filter id="ch-cv-glow-failed" x="-30%" y="-30%" width="160%" height="160%">
              <feGaussianBlur stdDeviation="3" result="blur" />
              <feFlood flood-color="#ef4444" flood-opacity="0.5" result="color"/>
              <feComposite in="color" in2="blur" operator="in" result="glow"/>
              <feMerge><feMergeNode in="glow"/><feMergeNode in="SourceGraphic"/></feMerge>
            </filter>
          </defs>

          <!-- Grid background -->
          <rect width="100%" height="100%" fill="url(#ch-cv-grid)" />

          <!-- Viewport group (pan + zoom) -->
          <g [attr.transform]="viewportTransform()">

            <!-- Edges -->
            @for (edge of edges(); track edge.id) {
              @if (edgePath(edge); as path) {
                <path
                  [attr.d]="path.d"
                  fill="none"
                  [attr.stroke]="edge.runState === 'active' ? '#3b82f6' : edge.runState === 'completed' ? '#22c55e' : '#d1d5db'"
                  [attr.stroke-width]="edge.runState === 'active' ? 2.5 : 1.5"
                  [attr.stroke-dasharray]="edge.runState === 'idle' ? '4 3' : null"
                  [attr.marker-end]="edge.runState === 'active' ? 'url(#ch-cv-arrow-active)' : 'url(#ch-cv-arrow)'"
                  opacity="0.7" />
                @if (edge.label && path.labelX !== undefined) {
                  <text
                    [attr.x]="path.labelX"
                    [attr.y]="path.labelY"
                    text-anchor="middle"
                    font-size="9"
                    fill="#9ca3af">{{ edge.label }}</text>
                }
              }
            }

            <!-- Nodes -->
            @for (node of nodes(); track node.id) {
              <g
                class="ch-cv-node"
                [attr.data-id]="node.id"
                [attr.data-kind]="node.kind"
                [attr.data-run-state]="node.runState"
                [attr.transform]="nodeTransform(node)"
                [attr.filter]="nodeFilter(node)"
                [class.ch-cv-node-selected]="selectedNode()?.id === node.id"
                (mousedown)="onNodeMousedown($event, node)"
                (click)="onNodeClick(node)">

                <!-- Stacked card effect for layers -->
                @if (node.kind === 'test-layer' || node.kind === 'policy-layer') {
                  <rect [attr.width]="node.w - 6" [attr.height]="node.h - 6" rx="8" x="6" y="6"
                    [attr.fill]="getStyle(node.kind).fill" [attr.stroke]="getStyle(node.kind).stroke"
                    stroke-width="1" opacity="0.4" />
                  <rect [attr.width]="node.w - 3" [attr.height]="node.h - 3" rx="8" x="3" y="3"
                    [attr.fill]="getStyle(node.kind).fill" [attr.stroke]="getStyle(node.kind).stroke"
                    stroke-width="1" opacity="0.6" />
                }

                <!-- Main body -->
                <rect
                  [attr.width]="node.w"
                  [attr.height]="node.h"
                  [attr.rx]="node.kind === 'hub' ? 10 : 8"
                  [attr.fill]="getStyle(node.kind).fill"
                  [attr.stroke]="selectedNode()?.id === node.id ? '#8b5cf6' : getStyle(node.kind).stroke"
                  [attr.stroke-width]="selectedNode()?.id === node.id ? 2.5 : getStyle(node.kind).strokeWidth" />

                <!-- Active pulse ring -->
                @if (node.runState === 'active') {
                  <rect
                    [attr.width]="node.w"
                    [attr.height]="node.h"
                    [attr.rx]="node.kind === 'hub' ? 10 : 8"
                    fill="none"
                    stroke="#3b82f6"
                    stroke-width="3"
                    opacity="0.5">
                    <animate attributeName="opacity" values="0.5;0.1;0.5" dur="1.4s" repeatCount="indefinite"/>
                    <animate attributeName="stroke-width" values="3;6;3" dur="1.4s" repeatCount="indefinite"/>
                  </rect>
                }

                <!-- Icon -->
                <text
                  [attr.x]="12"
                  y="22"
                  font-size="14"
                  [attr.fill]="getStyle(node.kind).stroke">{{ getStyle(node.kind).icon }}</text>

                <!-- Label -->
                <text
                  [attr.x]="node.w / 2"
                  [attr.y]="node.kind === 'hub' ? 24 : 22"
                  text-anchor="middle"
                  [attr.font-size]="node.kind === 'hub' ? 13 : 12"
                  font-weight="600"
                  [attr.fill]="getStyle(node.kind).textColor">{{ node.label }}</text>

                <!-- Sublabel -->
                <text
                  [attr.x]="node.w / 2"
                  [attr.y]="node.kind === 'hub' ? 40 : 38"
                  text-anchor="middle"
                  font-size="9"
                  fill="#6b7280">{{ node.sublabel }}</text>

                <!-- Badge (top-right) -->
                @if (node.badge) {
                  <g [attr.transform]="'translate(' + (node.w - 2) + ', -2)'">
                    <rect x="-22" y="0" width="24" height="14" rx="7"
                      [attr.fill]="badgeFill(node)"
                      opacity="0.92" />
                    <text x="-10" y="10" text-anchor="middle" font-size="8" font-weight="700"
                      [attr.fill]="badgeText(node)">{{ node.badge }}</text>
                  </g>
                }

                <!-- Run state icon (bottom-right) -->
                @if (node.runState === 'completed') {
                  <text [attr.x]="node.w - 6" [attr.y]="node.h - 6" text-anchor="end" font-size="13" fill="#22c55e">✓</text>
                }
                @if (node.runState === 'failed') {
                  <text [attr.x]="node.w - 6" [attr.y]="node.h - 6" text-anchor="end" font-size="13" fill="#ef4444">✕</text>
                }
                @if (node.runState === 'skipped') {
                  <text [attr.x]="node.w - 6" [attr.y]="node.h - 6" text-anchor="end" font-size="11" fill="#9ca3af">⇥</text>
                }
              </g>
            }
          </g>
        </svg>

        <!-- Empty state -->
        @if (nodes().length === 0) {
          <div class="ch-cv-empty">
            <p>Keine Topologie-Daten. Hub erreichbar?</p>
            <button type="button" class="ch-cv-empty-btn" (click)="refreshRequested.emit()">Aktualisieren</button>
          </div>
        }
      </div>

      <!-- Inspector Panel -->
      @if (selectedNode(); as node) {
        <aside class="ch-cv-inspector" aria-label="Node-Inspektor">
          <header class="ch-cv-inspector-head">
            <span class="ch-cv-inspector-kind-icon">{{ getStyle(node.kind).icon }}</span>
            <div class="ch-cv-inspector-title-group">
              <span class="ch-cv-inspector-title">{{ node.label }}</span>
              <span class="ch-cv-inspector-kind">{{ kindLabel(node.kind) }}</span>
            </div>
            <button type="button" class="ch-cv-inspector-close" (click)="clearSelection()">✕</button>
          </header>

          <div class="ch-cv-inspector-body">
            <!-- Run State -->
            @if (node.runState !== 'idle') {
              <div class="ch-cv-inspector-run-state" [attr.data-state]="node.runState">
                <span class="ch-cv-rs-dot"></span>
                <span>{{ runStateLabel(node.runState) }}</span>
              </div>
            }

            <!-- Hub details -->
            @if (node.kind === 'hub') {
              @let hub = asHub(node.payload);
              <dl class="ch-cv-inspector-dl">
                <dt>ID</dt><dd class="ch-mono">{{ hub.id }}</dd>
                <dt>URL</dt><dd class="ch-mono">{{ hub.url }}</dd>
                <dt>Status</dt><dd><span class="ch-cv-inspector-badge" [attr.data-status]="hub.status">{{ hub.status }}</span></dd>
                <dt>Version</dt><dd>{{ hub.version }}</dd>
                <dt>Gestartet</dt><dd>{{ hub.startedAt | date:'medium' }}</dd>
              </dl>
            }

            <!-- Worker details -->
            @if (node.kind === 'worker-llm' || node.kind === 'worker-det') {
              @let worker = asWorker(node.payload);
              <dl class="ch-cv-inspector-dl">
                <dt>ID</dt><dd class="ch-mono">{{ worker.id }}</dd>
                <dt>Typ</dt><dd>{{ worker.type }}</dd>
                <dt>CLI-Backend</dt><dd><code class="ch-mono">{{ worker.cliBackend }}</code></dd>
                <dt>Modell</dt><dd class="ch-mono">{{ worker.model }}</dd>
                <dt>Provider</dt><dd>{{ worker.llmProvider }}</dd>
                <dt>Health</dt><dd><span class="ch-cv-inspector-badge" [attr.data-status]="worker.health">{{ worker.health }}</span></dd>
                <dt>Boundary</dt><dd>{{ worker.boundary }}</dd>
                <dt>Capabilities</dt>
                <dd class="ch-cv-caps">
                  @for (cap of worker.capabilities; track cap) {
                    <span class="ch-cv-cap-tag">{{ cap }}</span>
                  }
                </dd>
                @if (worker.lastHeartbeatAt) {
                  <dt>Last Heartbeat</dt><dd>{{ worker.lastHeartbeatAt | date:'mediumTime' }}</dd>
                }
              </dl>
            }

            <!-- Test Layer details -->
            @if (node.kind === 'test-layer') {
              @let layer = asLayer(node.payload);
              <dl class="ch-cv-inspector-dl">
                <dt>ID</dt><dd class="ch-mono">{{ layer.id }}</dd>
                <dt>Name</dt><dd>{{ layer.name }}</dd>
                <dt>Order</dt><dd>{{ layer.order }}</dd>
                <dt>Aktiv</dt>
                <dd>
                  <label class="ch-cv-toggle-label">
                    <input type="checkbox" [checked]="layer.enabled" [disabled]="!writeModeActive"
                      (change)="onLayerToggle(layer, $any($event.target).checked)" />
                    {{ layer.enabled ? 'Ja' : 'Nein' }}
                  </label>
                  @if (!writeModeActive) {
                    <span class="ch-cv-warn-inline">Write-Modus erforderlich</span>
                  }
                </dd>
                @if (layer.parameters && hasKeys(layer.parameters)) {
                  <dt>Parameter</dt>
                  <dd><pre class="ch-cv-pre">{{ stringify(layer.parameters) }}</pre></dd>
                }
              </dl>
            }

            <!-- Routing Rule details -->
            @if (node.kind === 'routing-rule') {
              @let rule = asRule(node.payload);
              <dl class="ch-cv-inspector-dl">
                <dt>ID</dt><dd class="ch-mono">{{ rule.id }}</dd>
                <dt>Beschreibung</dt><dd>{{ rule.description }}</dd>
                <dt>Priorität</dt><dd>{{ rule.priority }}</dd>
                <dt>Backend</dt>
                <dd>
                  <select
                    [disabled]="!writeModeActive"
                    [value]="rule.selectedBackend"
                    (change)="onRuleChange(rule, $any($event.target).value)"
                    class="ch-cv-select">
                    @for (b of backends; track b) {
                      <option [value]="b">{{ b }}</option>
                    }
                  </select>
                </dd>
                <dt>Modell</dt><dd class="ch-mono">{{ rule.selectedModel }}</dd>
              </dl>
            }

            <!-- Active run step info -->
            @if (node.runState === 'active' && activeRunStep()) {
              <div class="ch-cv-inspector-run-step">
                <h4 class="ch-cv-inspector-section">Aktueller Schritt</h4>
                <dl class="ch-cv-inspector-dl">
                  <dt>Phase</dt><dd><code class="ch-mono">{{ activeRunStep()!.phase }}</code></dd>
                  <dt>Titel</dt><dd>{{ activeRunStep()!.title }}</dd>
                  @if (activeRunStep()!.outputSummary) {
                    <dt>Output</dt><dd>{{ activeRunStep()!.outputSummary }}</dd>
                  }
                </dl>
              </div>
            }
          </div>
        </aside>
      }
    </div>
  `,
  styles: [`
    :host { display: block; height: 100%; min-height: 500px; }

    .ch-cv-root {
      display: grid;
      grid-template-rows: 36px 1fr;
      grid-template-columns: 1fr;
      height: 100%;
      min-height: 500px;
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 10px;
      overflow: hidden;
    }
    .ch-cv-root.ch-cv-inspector-open {
      grid-template-columns: 1fr 280px;
      grid-template-rows: 36px 1fr;
    }

    /* Toolbar */
    .ch-cv-toolbar {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 0 10px;
      border-bottom: 1px solid var(--border);
      background: var(--card-bg);
      font-size: 12px;
      grid-column: 1 / -1;
    }
    .ch-cv-toolbar-title { font-weight: 600; font-size: 12px; margin-right: 2px; }
    .ch-cv-toolbar-sep { width: 1px; height: 18px; background: var(--border); margin: 0 2px; }
    .ch-cv-zoom-label { font-size: 11px; color: var(--muted); min-width: 34px; text-align: right; }
    .ch-cv-tb-btn {
      padding: 3px 8px;
      border: 1px solid var(--border);
      border-radius: 5px;
      background: var(--bg);
      color: var(--fg);
      cursor: pointer;
      font-size: 12px;
      line-height: 1;
    }
    .ch-cv-tb-btn:hover { background: var(--card-bg); }
    .ch-cv-run-badge {
      display: flex;
      align-items: center;
      gap: 5px;
      padding: 2px 8px;
      border-radius: 999px;
      background: color-mix(in srgb, #3b82f6 14%, transparent);
      border: 1px solid #3b82f6;
      font-size: 11px;
      color: #1e40af;
    }
    .ch-cv-run-dot {
      width: 6px; height: 6px;
      border-radius: 50%;
      background: #3b82f6;
    }
    .ch-cv-run-dot { animation: ch-cv-pulse 1.4s ease-in-out infinite; }
    @keyframes ch-cv-pulse { 0%,100% { opacity:1; } 50% { opacity:0.3; } }

    /* Canvas */
    .ch-cv-canvas-wrap {
      position: relative;
      overflow: hidden;
      background: var(--bg);
    }
    .ch-cv-svg {
      width: 100%;
      height: 100%;
      display: block;
      user-select: none;
    }

    /* Nodes */
    .ch-cv-node {
      cursor: pointer;
      transition: filter 0.15s;
    }
    .ch-cv-node:hover rect:first-of-type {
      filter: brightness(0.96);
    }
    .ch-cv-node-selected rect:first-of-type {
      stroke: #8b5cf6 !important;
      stroke-width: 2.5 !important;
    }

    /* Empty state */
    .ch-cv-empty {
      position: absolute;
      inset: 0;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 12px;
      color: var(--muted);
      font-size: 13px;
    }
    .ch-cv-empty-btn {
      padding: 6px 14px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--card-bg);
      color: var(--fg);
      cursor: pointer;
    }

    /* Inspector */
    .ch-cv-inspector {
      border-left: 1px solid var(--border);
      background: var(--card-bg);
      overflow: auto;
      grid-row: 2;
      grid-column: 2;
      display: flex;
      flex-direction: column;
    }
    .ch-cv-inspector-head {
      display: flex;
      align-items: flex-start;
      gap: 8px;
      padding: 10px 12px;
      border-bottom: 1px solid var(--border);
      position: sticky;
      top: 0;
      background: var(--card-bg);
      z-index: 1;
    }
    .ch-cv-inspector-kind-icon { font-size: 18px; line-height: 1; margin-top: 2px; }
    .ch-cv-inspector-title-group { flex: 1; min-width: 0; }
    .ch-cv-inspector-title { display: block; font-size: 12px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .ch-cv-inspector-kind { display: block; font-size: 10px; color: var(--muted); margin-top: 1px; }
    .ch-cv-inspector-close {
      background: none; border: none; cursor: pointer;
      color: var(--muted); font-size: 14px; padding: 0 2px;
    }
    .ch-cv-inspector-close:hover { color: var(--fg); }

    .ch-cv-inspector-body { padding: 10px 12px; display: grid; gap: 10px; }
    .ch-cv-inspector-section { margin: 0; font-size: 11px; text-transform: uppercase; letter-spacing: 0.6px; color: var(--muted); }

    .ch-cv-inspector-run-state {
      display: flex;
      align-items: center;
      gap: 7px;
      padding: 5px 9px;
      border-radius: 6px;
      font-size: 12px;
      font-weight: 600;
    }
    .ch-cv-inspector-run-state[data-state="active"] { background: color-mix(in srgb, #3b82f6 12%, transparent); color: #1e40af; }
    .ch-cv-inspector-run-state[data-state="completed"] { background: color-mix(in srgb, #22c55e 12%, transparent); color: #14532d; }
    .ch-cv-inspector-run-state[data-state="failed"] { background: color-mix(in srgb, #ef4444 12%, transparent); color: #7f1d1d; }
    .ch-cv-rs-dot {
      width: 8px; height: 8px; border-radius: 50%;
      background: currentColor;
    }
    .ch-cv-inspector-run-state[data-state="active"] .ch-cv-rs-dot {
      animation: ch-cv-pulse 1.4s ease-in-out infinite;
    }

    .ch-cv-inspector-dl {
      display: grid;
      grid-template-columns: max-content 1fr;
      gap: 4px 10px;
      margin: 0;
      font-size: 11px;
    }
    .ch-cv-inspector-dl dt { color: var(--muted); font-weight: 500; white-space: nowrap; }
    .ch-cv-inspector-dl dd { margin: 0; word-break: break-all; }

    .ch-cv-inspector-badge {
      display: inline-block;
      padding: 1px 7px;
      border-radius: 999px;
      font-size: 10px;
      font-weight: 600;
    }
    .ch-cv-inspector-badge[data-status="online"],
    .ch-cv-inspector-badge[data-status="healthy"] { background: color-mix(in srgb, #22c55e 18%, transparent); color: #14532d; }
    .ch-cv-inspector-badge[data-status="offline"],
    .ch-cv-inspector-badge[data-status="unhealthy"] { background: color-mix(in srgb, #ef4444 14%, transparent); color: #7f1d1d; }
    .ch-cv-inspector-badge[data-status="degraded"] { background: color-mix(in srgb, #f59e0b 16%, transparent); color: #78350f; }

    .ch-cv-caps { display: flex; flex-wrap: wrap; gap: 3px; }
    .ch-cv-cap-tag {
      font-size: 10px;
      padding: 1px 5px;
      border-radius: 4px;
      background: color-mix(in srgb, var(--accent) 14%, transparent);
    }

    .ch-cv-toggle-label { display: flex; align-items: center; gap: 5px; cursor: pointer; }
    .ch-cv-warn-inline { font-size: 10px; color: #92400e; }
    .ch-cv-select {
      padding: 2px 4px;
      border: 1px solid var(--border);
      border-radius: 4px;
      background: var(--bg);
      color: var(--fg);
      font-size: 11px;
      width: 100%;
    }
    .ch-cv-pre {
      font-family: var(--mono, ui-monospace, monospace);
      font-size: 10px;
      background: var(--bg);
      padding: 4px 6px;
      border-radius: 4px;
      overflow: auto;
      max-height: 120px;
      margin: 0;
      white-space: pre-wrap;
    }

    .ch-cv-inspector-run-step {
      display: grid;
      gap: 6px;
      padding-top: 8px;
      border-top: 1px solid var(--border);
    }

    .ch-mono { font-family: var(--mono, ui-monospace, monospace); }
  `],
})
export class CodeHugCanvasComponent implements AfterViewInit, OnChanges {
  @ViewChild('svgEl', { static: false }) svgRef!: ElementRef<SVGSVGElement>;

  @Input() topology: ChTopologyReadModel | null = null;
  @Input() activeRun: ChAgentRunReadModel | null = null;
  @Input() writeModeActive = false;

  @Output() refreshRequested = new EventEmitter<void>();
  @Output() layerToggled = new EventEmitter<{ layer: ChTestLayerReadModel; enabled: boolean }>();
  @Output() ruleChanged = new EventEmitter<{ rule: ChRoutingRuleReadModel; newBackend: ChCliBackend }>();

  readonly nodes = signal<ChCanvasNode[]>([]);
  readonly edges = signal<ChCanvasEdge[]>([]);
  readonly selectedNodeId = signal<string | null>(null);
  readonly selectedNode = computed(() => this.nodes().find(n => n.id === this.selectedNodeId()) ?? null);

  // Viewport
  readonly panX = signal(40);
  readonly panY = signal(30);
  readonly zoom = signal(1);
  readonly isPanning = signal(false);

  readonly viewportTransform = computed(() =>
    `translate(${this.panX()},${this.panY()}) scale(${this.zoom()})`
  );
  readonly gridTransform = computed(() =>
    `translate(${this.panX() % 24},${this.panY() % 24})`
  );

  // Active run step for the currently selected node
  readonly activeRunStep = computed((): ChAgentStepReadModel | null => {
    const node = this.selectedNode();
    const run = this.activeRun;
    if (!node || !run) return null;
    const workerId = this.workerIdFromNodeId(node.id);
    if (!workerId) return null;
    return run.steps.find(s => s.workerId === workerId && s.status === 'running') ?? null;
  });

  readonly backends: ChCliBackend[] = ['sgpt', 'opencode', 'codex', 'claude_code', 'aider', 'mistral', 'deterministic'];

  // Drag state (not signal — purely internal)
  private _draggingNodeId: string | null = null;
  private _didDrag = false;
  private _dragStartSvgX = 0;
  private _dragStartSvgY = 0;
  private _dragOrigNodeX = 0;
  private _dragOrigNodeY = 0;
  private _panStartX = 0;
  private _panStartY = 0;
  private _panOrigX = 0;
  private _panOrigY = 0;
  private _isPanDragging = false;

  ngAfterViewInit(): void {
    if (this.topology) this.rebuildCanvas();
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['topology'] && this.topology) {
      this.rebuildCanvas();
    }
    if (changes['activeRun'] && this.activeRun) {
      this.applyRunState();
    }
  }

  // ── Canvas building ───────────────────────────────────────────────────────

  private rebuildCanvas(): void {
    if (!this.topology) return;
    const { nodes, edges } = autoLayout(this.topology);
    this.nodes.set(nodes);
    this.edges.set(edges);
    if (this.activeRun) this.applyRunState();
  }

  private applyRunState(): void {
    const run = this.activeRun;
    if (!run) return;
    const runningSteps = run.steps.filter(s => s.status === 'running');
    const completedSteps = run.steps.filter(s => s.status === 'succeeded');
    const failedSteps = run.steps.filter(s => s.status === 'failed');

    this.nodes.update(nodes => nodes.map(n => {
      const workerId = this.workerIdFromNodeId(n.id);
      if (!workerId) return n;
      let runState: ChNodeRunState = 'idle';
      if (runningSteps.some(s => s.workerId === workerId)) runState = 'active';
      else if (failedSteps.some(s => s.workerId === workerId)) runState = 'failed';
      else if (completedSteps.some(s => s.workerId === workerId)) runState = 'completed';
      return { ...n, runState };
    }));

    // also mark hub as active if run is running
    this.nodes.update(nodes => nodes.map(n => {
      if (n.kind !== 'hub') return n;
      const state: ChNodeRunState = run.status === 'running' ? 'active' : run.status === 'succeeded' ? 'completed' : run.status === 'failed' ? 'failed' : 'idle';
      return { ...n, runState: state };
    }));
  }

  private workerIdFromNodeId(nodeId: string): string | null {
    if (nodeId.startsWith('worker::')) return nodeId.slice('worker::'.length);
    return null;
  }

  // ── Viewport / Interaction ────────────────────────────────────────────────

  zoomIn(): void { this.zoom.update(z => Math.min(2.5, z + 0.15)); }
  zoomOut(): void { this.zoom.update(z => Math.max(0.2, z - 0.15)); }
  resetView(): void { this.panX.set(40); this.panY.set(30); this.zoom.set(1); }
  fitToContent(): void {
    const ns = this.nodes();
    if (ns.length === 0) return;
    const maxX = Math.max(...ns.map(n => n.x + n.w));
    const maxY = Math.max(...ns.map(n => n.y + n.h));
    const svgEl = this.svgRef?.nativeElement;
    if (!svgEl) return;
    const { width, height } = svgEl.getBoundingClientRect();
    const zx = (width - 80) / maxX;
    const zy = (height - 80) / maxY;
    const z = Math.min(1, Math.min(zx, zy));
    this.zoom.set(z);
    this.panX.set(40);
    this.panY.set(40);
  }

  clearSelection(): void { this.selectedNodeId.set(null); }

  @HostListener('document:mousemove', ['$event'])
  onMousemove(e: MouseEvent): void {
    if (this._draggingNodeId) {
      const svgPt = this.toSvgCoords(e);
      const dx = (svgPt.x - this._dragStartSvgX) / this.zoom();
      const dy = (svgPt.y - this._dragStartSvgY) / this.zoom();
      const newX = this._dragOrigNodeX + dx;
      const newY = this._dragOrigNodeY + dy;
      this._didDrag = true;
      this.nodes.update(ns => ns.map(n =>
        n.id === this._draggingNodeId ? { ...n, x: Math.max(0, newX), y: Math.max(0, newY) } : n
      ));
    } else if (this._isPanDragging) {
      this.panX.set(this._panOrigX + (e.clientX - this._panStartX));
      this.panY.set(this._panOrigY + (e.clientY - this._panStartY));
    }
  }

  @HostListener('document:mouseup')
  onMouseup(): void {
    this._draggingNodeId = null;
    this._isPanDragging = false;
    this.isPanning.set(false);
    // _didDrag is cleared by onNodeClick after suppression check
  }

  onSvgMousedown(e: MouseEvent): void {
    if ((e.target as SVGElement).closest('.ch-cv-node')) return;
    this._isPanDragging = true;
    this._panStartX = e.clientX;
    this._panStartY = e.clientY;
    this._panOrigX = this.panX();
    this._panOrigY = this.panY();
    this.isPanning.set(true);
    e.preventDefault();
  }

  onNodeMousedown(e: MouseEvent, node: ChCanvasNode): void {
    e.stopPropagation();
    const svgPt = this.toSvgCoords(e);
    this._draggingNodeId = node.id;
    this._didDrag = false;
    this._dragStartSvgX = svgPt.x;
    this._dragStartSvgY = svgPt.y;
    this._dragOrigNodeX = node.x;
    this._dragOrigNodeY = node.y;
  }

  onNodeClick(node: ChCanvasNode): void {
    if (this._didDrag) { this._didDrag = false; return; }
    this.selectedNodeId.set(node.id === this.selectedNodeId() ? null : node.id);
  }

  onWheel(e: WheelEvent): void {
    e.preventDefault();
    const delta = e.deltaY > 0 ? -0.1 : 0.1;
    const newZoom = Math.min(2.5, Math.max(0.2, this.zoom() + delta));
    this.zoom.set(newZoom);
  }

  // ── SVG helpers ───────────────────────────────────────────────────────────

  nodeTransform(node: ChCanvasNode): string {
    return `translate(${node.x}, ${node.y})`;
  }

  nodeFilter(node: ChCanvasNode): string | null {
    switch (node.runState) {
      case 'active': return 'url(#ch-cv-glow-active)';
      case 'completed': return 'url(#ch-cv-glow-completed)';
      case 'failed': return 'url(#ch-cv-glow-failed)';
      default: return null;
    }
  }

  edgePath(edge: ChCanvasEdge): { d: string; labelX: number; labelY: number } | null {
    const from = this.nodes().find(n => n.id === edge.fromId);
    const to = this.nodes().find(n => n.id === edge.toId);
    if (!from || !to) return null;
    const x1 = from.x + from.w / 2;
    const y1 = from.y + from.h;
    const x2 = to.x + to.w / 2;
    const y2 = to.y;
    const mx = (x1 + x2) / 2;
    const my = (y1 + y2) / 2;
    const cpY = (y1 + y2) / 2;
    return {
      d: `M ${x1} ${y1} C ${x1} ${cpY}, ${x2} ${cpY}, ${x2} ${y2}`,
      labelX: mx,
      labelY: my - 4,
    };
  }

  getStyle(kind: ChNodeKind): NodeStyle {
    return nodeStyle(kind);
  }

  badgeFill(node: ChCanvasNode): string {
    const s = node.badge;
    if (s === 'online' || s === 'healthy' || s === 'on') return '#16a34a';
    if (s === 'offline' || s === 'unhealthy' || s === 'off') return '#dc2626';
    if (s === 'degraded') return '#f59e0b';
    return '#6b7280';
  }

  badgeText(node: ChCanvasNode): string {
    const f = this.badgeFill(node);
    return f === '#6b7280' ? '#fff' : '#fff';
  }

  kindLabel(kind: ChNodeKind): string {
    const map: Record<ChNodeKind, string> = {
      'hub': 'Hub-Instanz',
      'worker-llm': 'LLM-Worker',
      'worker-det': 'Deterministic Worker',
      'policy-layer': 'Policy-Layer',
      'test-layer': 'Test-/Instruktions-Layer',
      'routing-rule': 'Routing-Regel',
    };
    return map[kind] ?? kind;
  }

  runStateLabel(state: ChNodeRunState): string {
    const map: Record<ChNodeRunState, string> = {
      idle: 'Inaktiv',
      active: 'Aktiv',
      completed: 'Abgeschlossen',
      failed: 'Fehler',
      skipped: 'Übersprungen',
    };
    return map[state] ?? state;
  }

  private toSvgCoords(e: MouseEvent): { x: number; y: number } {
    const svgEl = this.svgRef?.nativeElement;
    if (!svgEl) return { x: e.clientX, y: e.clientY };
    const rect = svgEl.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }

  // ── Inspector type helpers ────────────────────────────────────────────────

  asHub(p: unknown): ChHubInstanceReadModel { return p as ChHubInstanceReadModel; }
  asWorker(p: unknown): ChWorkerInstanceReadModel { return p as ChWorkerInstanceReadModel; }
  asLayer(p: unknown): ChTestLayerReadModel { return p as ChTestLayerReadModel; }
  asRule(p: unknown): ChRoutingRuleReadModel { return p as ChRoutingRuleReadModel; }

  hasKeys(obj: Record<string, unknown>): boolean { return Object.keys(obj).length > 0; }
  stringify(v: unknown): string {
    try { return JSON.stringify(v, null, 2); } catch { return String(v); }
  }

  onLayerToggle(layer: ChTestLayerReadModel, enabled: boolean): void {
    this.layerToggled.emit({ layer, enabled });
    const updatedLayer: ChTestLayerReadModel = { ...layer, enabled };
    this.nodes.update(ns => ns.map(n =>
      n.id === `layer::${layer.id}`
        ? ({ ...n, payload: updatedLayer, sublabel: `order ${layer.order}${enabled ? '' : ' · deaktiviert'}`, badge: enabled ? 'on' : 'off' } satisfies ChCanvasNode)
        : n
    ));
  }

  onRuleChange(rule: ChRoutingRuleReadModel, newBackend: string): void {
    const backend = newBackend as ChCliBackend;
    this.ruleChanged.emit({ rule, newBackend: backend });
    const updatedRule: ChRoutingRuleReadModel = { ...rule, selectedBackend: backend };
    this.nodes.update(ns => ns.map(n =>
      n.id === `rule::${rule.id}`
        ? ({ ...n, payload: updatedRule, sublabel: `→ ${newBackend}` } satisfies ChCanvasNode)
        : n
    ));
  }
}
