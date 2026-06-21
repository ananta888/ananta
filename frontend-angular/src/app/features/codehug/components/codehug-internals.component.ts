import {
  ChangeDetectionStrategy,
  Component,
  OnDestroy,
  OnInit,
  inject,
  signal,
  computed,
} from '@angular/core';
import { interval, Subscription } from 'rxjs';
import { switchMap } from 'rxjs/operators';

import { InternalsService, AnantaTemplate, AnantaWorker, AutopilotStatus } from '../services/internals.service';

type FlowPhase = 'goal' | 'planning' | 'queue' | 'executing' | 'verification' | 'done';
type PanelView = 'template' | 'worker' | 'flow-node' | null;

interface FlowNodeDef {
  id: FlowPhase;
  label: string;
  sublabel: string;
  x: number; y: number; w: number; h: number;
  shape: 'rounded' | 'diamond' | 'hexagon';
  color: string;
  strokeColor: string;
}

const FLOW_NODES: FlowNodeDef[] = [
  { id: 'goal',         label: 'Ziel',          sublabel: 'Goal-Beschreibung', x: 280, y: 20,  w: 160, h: 50, shape: 'hexagon', color: '#fef3c7', strokeColor: '#d97706' },
  { id: 'planning',     label: 'Planung',        sublabel: 'LMStudio · Gemma',  x: 280, y: 110, w: 160, h: 50, shape: 'diamond', color: '#e0e7ff', strokeColor: '#4f46e5' },
  { id: 'queue',        label: 'Task-Queue',     sublabel: 'Dispatching',       x: 280, y: 200, w: 160, h: 45, shape: 'rounded', color: '#f3f4f6', strokeColor: '#6b7280' },
  { id: 'executing',    label: 'Ausführung',     sublabel: 'Worker-Pool',       x: 280, y: 285, w: 160, h: 50, shape: 'rounded', color: '#dbeafe', strokeColor: '#2563eb' },
  { id: 'verification', label: 'Verifikation',   sublabel: 'Review · Test',     x: 280, y: 375, w: 160, h: 45, shape: 'rounded', color: '#d1fae5', strokeColor: '#059669' },
  { id: 'done',         label: 'Fertig',         sublabel: 'Artefakte bereit',  x: 280, y: 460, w: 160, h: 40, shape: 'rounded', color: '#f0fdf4', strokeColor: '#16a34a' },
];

const CATEGORY_ICONS: Record<string, string> = {
  scrum: '🔄', kanban: '📋', opencode: '⚡', system: '⚙',
};
const CATEGORY_LABELS: Record<string, string> = {
  scrum: 'Scrum', kanban: 'Kanban', opencode: 'OpenCode Scrum', system: 'System',
};

@Component({
  selector: 'ch-internals',
  standalone: true,
  imports: [],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="ch-int">

      <!-- ── LEFT: Template Palette ───────────────────────── -->
      <aside class="ch-int-palette" aria-label="Template-Palette">
        <header class="ch-int-palette-head">
          <span class="ch-int-palette-title">Templates</span>
          <span class="ch-int-palette-count">{{ templates().length }}</span>
        </header>

        @for (cat of templateCategories(); track cat) {
          <div class="ch-int-cat">
            <div class="ch-int-cat-head">
              <span>{{ getCatIcon(cat) }}</span>
              <span>{{ getCatLabel(cat) }}</span>
            </div>
            @for (tpl of templatesByCategory(cat); track tpl.id) {
              <button
                type="button"
                class="ch-int-tpl-card"
                [class.selected]="selectedTemplate()?.id === tpl.id"
                (click)="selectTemplate(tpl)">
                <span class="ch-int-tpl-name">{{ shortName(tpl.name) }}</span>
                <span class="ch-int-tpl-desc">{{ tpl.description }}</span>
              </button>
            }
          </div>
        }

        @if (templates().length === 0) {
          <p class="ch-int-muted" style="padding:10px 12px">Lade Templates…</p>
        }
      </aside>

      <!-- ── CENTER: Flow Canvas ──────────────────────────── -->
      <main class="ch-int-main">

        <!-- Status bar -->
        <div class="ch-int-statusbar">
          <span class="ch-int-statusbar-title">Ananta Flow</span>

          @if (autopilot().running) {
            <span class="ch-int-running-badge">
              <span class="ch-int-running-dot"></span>
              Läuft · {{ autopilot().dispatched_count }} dispatched · {{ autopilot().completed_count }} done
              @if (autopilot().failed_count > 0) {
                · <span class="ch-int-fail-count">{{ autopilot().failed_count }} failed</span>
              }
            </span>
            <span class="ch-int-level-badge" [attr.data-level]="autopilot().effective_security_policy?.level">
              {{ autopilot().effective_security_policy?.level }}
            </span>
          } @else {
            <span class="ch-int-idle-badge">Idle</span>
          }

          <div class="ch-int-statusbar-spacer"></div>
          <button type="button" class="ch-int-refresh-btn" (click)="reload()">↺ Aktualisieren</button>
        </div>

        <!-- SVG Flow Diagram -->
        <div class="ch-int-canvas-wrap">
          <svg viewBox="0 0 720 540" class="ch-int-svg" xmlns="http://www.w3.org/2000/svg">

            <defs>
              <!-- Arrow marker -->
              <marker id="ch-arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">
                <path d="M0,0 L0,6 L8,3 z" fill="#9ca3af"/>
              </marker>
              <marker id="ch-arrow-active" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">
                <path d="M0,0 L0,6 L8,3 z" fill="#3b82f6"/>
              </marker>

              <!-- Glow filters -->
              <filter id="glow-amber" x="-40%" y="-40%" width="180%" height="180%">
                <feGaussianBlur stdDeviation="5" result="blur"/>
                <feFlood flood-color="#f59e0b" flood-opacity="0.5" result="c"/>
                <feComposite in="c" in2="blur" operator="in" result="g"/>
                <feMerge><feMergeNode in="g"/><feMergeNode in="SourceGraphic"/></feMerge>
              </filter>
              <filter id="glow-blue" x="-40%" y="-40%" width="180%" height="180%">
                <feGaussianBlur stdDeviation="5" result="blur"/>
                <feFlood flood-color="#3b82f6" flood-opacity="0.5" result="c"/>
                <feComposite in="c" in2="blur" operator="in" result="g"/>
                <feMerge><feMergeNode in="g"/><feMergeNode in="SourceGraphic"/></feMerge>
              </filter>
              <filter id="glow-green" x="-40%" y="-40%" width="180%" height="180%">
                <feGaussianBlur stdDeviation="4" result="blur"/>
                <feFlood flood-color="#22c55e" flood-opacity="0.4" result="c"/>
                <feComposite in="c" in2="blur" operator="in" result="g"/>
                <feMerge><feMergeNode in="g"/><feMergeNode in="SourceGraphic"/></feMerge>
              </filter>
            </defs>

            <!-- ─── Flow connector lines ─── -->
            <!-- goal → planning -->
            <line x1="360" y1="70" x2="360" y2="110" stroke="#d1d5db" stroke-width="1.5" marker-end="url(#ch-arrow)"/>
            <!-- planning → queue -->
            <line x1="360" y1="160" x2="360" y2="200" stroke="#d1d5db" stroke-width="1.5" marker-end="url(#ch-arrow)"/>
            <!-- queue → executing -->
            <line x1="360" y1="245" x2="360" y2="285" stroke="#d1d5db" stroke-width="1.5" marker-end="url(#ch-arrow)"/>
            <!-- executing → verification -->
            <line x1="360" y1="335" x2="360" y2="375" stroke="#d1d5db" stroke-width="1.5" marker-end="url(#ch-arrow)"/>
            <!-- verification → done -->
            <line x1="360" y1="420" x2="360" y2="460" stroke="#d1d5db" stroke-width="1.5" marker-end="url(#ch-arrow)"/>

            <!-- ─── Worker side branches from "executing" ─── -->
            @for (worker of workers(); track worker.name; let i = $index) {
              @let wx = workerX(i, workers().length);
              <!-- hub line out -->
              <line
                [attr.x1]="wx + 60" y1="248"
                [attr.x2]="wx + 60" y2="270"
                [attr.stroke]="workerActive(worker) ? '#3b82f6' : '#d1d5db'"
                stroke-width="1.5"
                [attr.marker-end]="workerActive(worker) ? 'url(#ch-arrow-active)' : 'url(#ch-arrow)'"/>

              <!-- Worker node -->
              <g class="ch-int-worker-node"
                [attr.transform]="'translate(' + wx + ',270)'"
                (click)="selectWorker(worker)">
                <!-- glow if active -->
                @if (workerActive(worker)) {
                  <rect width="120" height="50" rx="8" fill="none" stroke="#3b82f6" stroke-width="2.5" opacity="0.6">
                    <animate attributeName="opacity" values="0.6;0.15;0.6" dur="1.4s" repeatCount="indefinite"/>
                  </rect>
                }
                <rect width="120" height="50" rx="8"
                  [attr.fill]="workerActive(worker) ? '#dbeafe' : '#f9fafb'"
                  [attr.stroke]="workerActive(worker) ? '#2563eb' : (worker.status === 'online' ? '#6b7280' : '#ef4444')"
                  [attr.stroke-width]="selectedWorker()?.name === worker.name ? 2.5 : 1.5"/>
                @if (selectedWorker()?.name === worker.name) {
                  <rect width="120" height="50" rx="8" fill="none" stroke="#7c3aed" stroke-width="2.5"/>
                }
                <!-- status dot -->
                <circle cx="108" cy="10" r="5"
                  [attr.fill]="worker.status === 'online' ? '#22c55e' : worker.status === 'degraded' ? '#f59e0b' : '#ef4444'"/>
                <text x="60" y="22" text-anchor="middle" font-size="11" font-weight="600" fill="#1f2937">{{ worker.name }}</text>
                <text x="60" y="36" text-anchor="middle" font-size="9" fill="#6b7280">{{ worker.worker_roles.slice(0,3).join(' · ') }}</text>
              </g>

              <!-- line back to verification -->
              <line
                [attr.x1]="wx + 60" y1="320"
                [attr.x2]="wx + 60" y2="342"
                [attr.stroke]="workerActive(worker) ? '#3b82f6' : '#d1d5db'"
                stroke-width="1.5"/>
              <!-- connect to executing box -->
              <line
                [attr.x1]="wx + 60" y1="342"
                x2="360" y2="342"
                [attr.stroke]="workerActive(worker) ? '#3b82f6' : '#e5e7eb'"
                stroke-width="1"/>
            }

            <!-- ─── Main flow nodes ─── -->
            @for (node of flowNodes; track node.id) {
              <g class="ch-int-flow-node"
                [attr.transform]="'translate(' + node.x + ',' + node.y + ')'"
                [attr.filter]="flowNodeFilter(node.id)"
                (click)="selectFlowNode(node.id)">

                @if (node.shape === 'hexagon') {
                  <!-- Goal: hexagon path -->
                  <polygon
                    [attr.points]="hexPoints(node.w, node.h)"
                    [attr.fill]="node.color"
                    [attr.stroke]="selectedFlowNode() === node.id ? '#7c3aed' : node.strokeColor"
                    [attr.stroke-width]="selectedFlowNode() === node.id ? 2.5 : 2"/>
                } @else if (node.shape === 'diamond') {
                  <!-- Planning: diamond -->
                  <polygon
                    [attr.points]="diamondPoints(node.w, node.h)"
                    [attr.fill]="node.color"
                    [attr.stroke]="selectedFlowNode() === node.id ? '#7c3aed' : node.strokeColor"
                    [attr.stroke-width]="selectedFlowNode() === node.id ? 2.5 : 2"/>
                } @else {
                  <!-- Rounded rect -->
                  <rect [attr.width]="node.w" [attr.height]="node.h" rx="8"
                    [attr.fill]="node.color"
                    [attr.stroke]="selectedFlowNode() === node.id ? '#7c3aed' : node.strokeColor"
                    [attr.stroke-width]="selectedFlowNode() === node.id ? 2.5 : 1.5"/>
                }

                <!-- Pulse ring when active -->
                @if (isFlowNodeActive(node.id)) {
                  <rect [attr.width]="node.w" [attr.height]="node.h" rx="8"
                    fill="none" stroke="#3b82f6" stroke-width="3" opacity="0.5">
                    <animate attributeName="opacity" values="0.5;0.1;0.5" dur="1.4s" repeatCount="indefinite"/>
                    <animate attributeName="stroke-width" values="3;7;3" dur="1.4s" repeatCount="indefinite"/>
                  </rect>
                }

                <text [attr.x]="node.w / 2" [attr.y]="node.h / 2 - 5" text-anchor="middle"
                  font-size="12" font-weight="600" fill="#1f2937">{{ node.label }}</text>
                <text [attr.x]="node.w / 2" [attr.y]="node.h / 2 + 11" text-anchor="middle"
                  font-size="9" fill="#6b7280">{{ node.sublabel }}</text>
              </g>
            }

            <!-- Goal text if autopilot has one -->
            @if (autopilot().goal) {
              <text x="360" y="515" text-anchor="middle" font-size="10" fill="#6b7280">
                Ziel: {{ truncate(autopilot().goal, 60) }}
              </text>
            }
          </svg>
        </div>
      </main>

      <!-- ── RIGHT: Inspector / Detail Panel ──────────────── -->
      <aside class="ch-int-inspector" aria-label="Inspektor">
        @if (selectedTemplate(); as tpl) {
          <div class="ch-int-inspector-head">
            <span class="ch-int-inspector-icon">{{ getCatIcon(tpl.category) }}</span>
            <div>
              <div class="ch-int-inspector-title">{{ tpl.name }}</div>
              <div class="ch-int-inspector-sub">{{ getCatLabel(tpl.category) }}</div>
            </div>
            <button class="ch-int-inspector-close" (click)="clearSelection()">✕</button>
          </div>
          <div class="ch-int-inspector-body">
            <p class="ch-int-inspector-desc">{{ tpl.description }}</p>
            <div class="ch-int-inspector-section">Prompt-Vorschau</div>
            <pre class="ch-int-prompt-preview">{{ tpl.prompt_template.slice(0, 400) }}{{ tpl.prompt_template.length > 400 ? '…' : '' }}</pre>
          </div>
        }

        @if (selectedWorker(); as w) {
          <div class="ch-int-inspector-head">
            <span class="ch-int-inspector-icon">◈</span>
            <div>
              <div class="ch-int-inspector-title">{{ w.name }}</div>
              <div class="ch-int-inspector-sub">Worker</div>
            </div>
            <button class="ch-int-inspector-close" (click)="clearSelection()">✕</button>
          </div>
          <div class="ch-int-inspector-body">
            <dl class="ch-int-dl">
              <dt>URL</dt><dd class="ch-mono">{{ w.url }}</dd>
              <dt>Status</dt><dd><span class="ch-int-status-badge" [attr.data-s]="w.status">{{ w.status }}</span></dd>
              <dt>Rollen</dt><dd>{{ w.worker_roles.join(', ') }}</dd>
              <dt>Fähigkeiten</dt>
              <dd class="ch-int-caps">
                @for (c of w.capabilities; track c) {
                  <span class="ch-int-cap">{{ c }}</span>
                }
              </dd>
            </dl>
            @if (workerActive(w)) {
              <div class="ch-int-active-box">
                <span class="ch-int-running-dot"></span> Aktiv: {{ autopilot().dispatched_count }} Tasks dispatched
              </div>
            }
            @if (autopilot().circuit_breakers.failure_streak[w.url]) {
              <div class="ch-int-warn-box">
                ⚠ Fehler-Streak: {{ autopilot().circuit_breakers.failure_streak[w.url] }}
              </div>
            }
          </div>
        }

        @if (selectedFlowNode(); as nodeId) {
          @if (!selectedTemplate() && !selectedWorker()) {
            <div class="ch-int-inspector-head">
              <span class="ch-int-inspector-icon">{{ flowNodeIcon(nodeId) }}</span>
              <div>
                <div class="ch-int-inspector-title">{{ flowNodeLabel(nodeId) }}</div>
                <div class="ch-int-inspector-sub">Ananta-Flow-Schritt</div>
              </div>
              <button class="ch-int-inspector-close" (click)="clearSelection()">✕</button>
            </div>
            <div class="ch-int-inspector-body">
              <p class="ch-int-muted">{{ flowNodeDescription(nodeId) }}</p>
              @if (isFlowNodeActive(nodeId)) {
                <div class="ch-int-active-box">
                  <span class="ch-int-running-dot"></span> Dieser Schritt ist gerade aktiv.
                </div>
              }
              @if (nodeId === 'executing') {
                <div class="ch-int-inspector-section">Workers</div>
                @for (w of workers(); track w.name) {
                  <button class="ch-int-tpl-card" (click)="selectWorker(w)">
                    <span class="ch-int-tpl-name">{{ w.name }}</span>
                    <span class="ch-int-tpl-desc">{{ w.status }} · {{ w.worker_roles.length }} Rollen</span>
                  </button>
                }
              }
            </div>
          }
        }

        @if (!selectedTemplate() && !selectedWorker() && !selectedFlowNode()) {
          <div class="ch-int-inspector-empty">
            <p>Klicke einen Flow-Schritt oder Worker für Details.</p>
            <p class="ch-int-muted">Templates links anklicken für Vorschau.</p>
          </div>
        }
      </aside>
    </div>
  `,
  styles: [`
    :host { display: flex; height: 100%; min-height: 0; }

    .ch-int {
      display: grid;
      grid-template-columns: 200px 1fr 240px;
      height: 100%;
      min-height: 600px;
      overflow: hidden;
    }

    /* ── Left palette ── */
    .ch-int-palette {
      border-right: 1px solid var(--border);
      overflow-y: auto;
      background: var(--card-bg);
    }
    .ch-int-palette-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 10px 12px 6px;
      border-bottom: 1px solid var(--border);
      position: sticky;
      top: 0;
      background: var(--card-bg);
      z-index: 1;
    }
    .ch-int-palette-title { font-size: 12px; font-weight: 700; }
    .ch-int-palette-count {
      font-size: 10px;
      padding: 1px 5px;
      border-radius: 10px;
      background: color-mix(in srgb, var(--accent) 15%, transparent);
    }

    .ch-int-cat { padding: 6px 0 0; }
    .ch-int-cat-head {
      display: flex;
      align-items: center;
      gap: 5px;
      padding: 4px 12px;
      font-size: 10px;
      font-weight: 700;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }

    .ch-int-tpl-card {
      display: block;
      width: 100%;
      text-align: left;
      padding: 6px 12px;
      border: none;
      background: none;
      cursor: pointer;
      border-left: 2px solid transparent;
      transition: background 0.1s, border-color 0.1s;
    }
    .ch-int-tpl-card:hover { background: color-mix(in srgb, var(--accent) 8%, transparent); }
    .ch-int-tpl-card.selected {
      background: color-mix(in srgb, var(--accent) 14%, transparent);
      border-left-color: var(--accent);
    }
    .ch-int-tpl-name { display: block; font-size: 11px; font-weight: 600; color: var(--fg); }
    .ch-int-tpl-desc { display: block; font-size: 9px; color: var(--muted); margin-top: 1px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

    /* ── Center ── */
    .ch-int-main {
      display: flex;
      flex-direction: column;
      min-height: 0;
      overflow: hidden;
    }

    .ch-int-statusbar {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 6px 12px;
      border-bottom: 1px solid var(--border);
      background: var(--card-bg);
      font-size: 11px;
      flex-shrink: 0;
    }
    .ch-int-statusbar-title { font-weight: 700; font-size: 12px; }
    .ch-int-statusbar-spacer { flex: 1; }

    .ch-int-running-badge {
      display: flex;
      align-items: center;
      gap: 5px;
      padding: 2px 8px;
      border-radius: 999px;
      background: color-mix(in srgb, #3b82f6 12%, transparent);
      border: 1px solid #3b82f6;
      color: #1e40af;
      font-weight: 600;
    }
    .ch-int-running-dot {
      display: inline-block;
      width: 6px; height: 6px;
      border-radius: 50%;
      background: #3b82f6;
      animation: ch-pulse 1.4s ease-in-out infinite;
    }
    @keyframes ch-pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }
    .ch-int-fail-count { color: #dc2626; font-weight: 700; }
    .ch-int-idle-badge {
      padding: 2px 8px;
      border-radius: 999px;
      background: var(--bg);
      border: 1px solid var(--border);
      color: var(--muted);
      font-size: 10px;
    }
    .ch-int-level-badge {
      padding: 1px 6px;
      border-radius: 4px;
      font-size: 10px;
      background: color-mix(in srgb, #22c55e 12%, transparent);
      border: 1px solid #22c55e;
      color: #14532d;
    }
    .ch-int-level-badge[data-level="strict"] {
      background: color-mix(in srgb, #f59e0b 12%, transparent);
      border-color: #f59e0b;
      color: #78350f;
    }

    .ch-int-refresh-btn {
      padding: 3px 8px;
      border: 1px solid var(--border);
      border-radius: 5px;
      background: var(--bg);
      color: var(--fg);
      cursor: pointer;
      font-size: 11px;
    }
    .ch-int-refresh-btn:hover { background: var(--card-bg); }

    .ch-int-canvas-wrap {
      flex: 1;
      overflow: auto;
      display: flex;
      align-items: flex-start;
      justify-content: center;
      padding: 20px;
      background: var(--bg);
    }
    .ch-int-svg {
      width: 100%;
      max-width: 720px;
      min-height: 540px;
    }
    .ch-int-flow-node, .ch-int-worker-node { cursor: pointer; }
    .ch-int-flow-node:hover rect, .ch-int-flow-node:hover polygon,
    .ch-int-worker-node:hover rect { filter: brightness(0.96); }

    /* ── Right inspector ── */
    .ch-int-inspector {
      border-left: 1px solid var(--border);
      background: var(--card-bg);
      overflow-y: auto;
      display: flex;
      flex-direction: column;
    }

    .ch-int-inspector-head {
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
    .ch-int-inspector-icon { font-size: 18px; flex-shrink: 0; }
    .ch-int-inspector-title { font-size: 12px; font-weight: 700; }
    .ch-int-inspector-sub { font-size: 10px; color: var(--muted); }
    .ch-int-inspector-close {
      margin-left: auto;
      background: none;
      border: none;
      cursor: pointer;
      color: var(--muted);
      font-size: 14px;
      padding: 0 2px;
    }
    .ch-int-inspector-close:hover { color: var(--fg); }

    .ch-int-inspector-body { padding: 10px 12px; display: flex; flex-direction: column; gap: 10px; }
    .ch-int-inspector-section { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; color: var(--muted); }
    .ch-int-inspector-desc { font-size: 11px; color: var(--muted); margin: 0; }

    .ch-int-prompt-preview {
      font-family: var(--mono, ui-monospace, monospace);
      font-size: 9px;
      background: var(--bg);
      padding: 6px 8px;
      border-radius: 4px;
      overflow: auto;
      max-height: 320px;
      white-space: pre-wrap;
      word-break: break-word;
      margin: 0;
    }

    .ch-int-dl {
      display: grid;
      grid-template-columns: max-content 1fr;
      gap: 4px 10px;
      margin: 0;
      font-size: 11px;
    }
    .ch-int-dl dt { color: var(--muted); font-weight: 500; }
    .ch-int-dl dd { margin: 0; word-break: break-all; }
    .ch-mono { font-family: var(--mono, ui-monospace, monospace); font-size: 10px; }

    .ch-int-status-badge {
      display: inline-block;
      padding: 1px 6px;
      border-radius: 999px;
      font-size: 10px;
      font-weight: 600;
    }
    .ch-int-status-badge[data-s="online"] { background: color-mix(in srgb, #22c55e 16%, transparent); color: #14532d; }
    .ch-int-status-badge[data-s="offline"] { background: color-mix(in srgb, #ef4444 14%, transparent); color: #7f1d1d; }
    .ch-int-status-badge[data-s="degraded"] { background: color-mix(in srgb, #f59e0b 16%, transparent); color: #78350f; }

    .ch-int-caps { display: flex; flex-wrap: wrap; gap: 3px; }
    .ch-int-cap { font-size: 9px; padding: 1px 5px; border-radius: 4px; background: color-mix(in srgb, var(--accent) 12%, transparent); }

    .ch-int-active-box {
      display: flex;
      align-items: center;
      gap: 7px;
      padding: 6px 9px;
      border-radius: 6px;
      background: color-mix(in srgb, #3b82f6 10%, transparent);
      border: 1px solid #3b82f6;
      font-size: 11px;
      color: #1e40af;
      font-weight: 600;
    }
    .ch-int-warn-box {
      padding: 6px 9px;
      border-radius: 6px;
      background: color-mix(in srgb, #ef4444 10%, transparent);
      border: 1px solid #fca5a5;
      font-size: 11px;
      color: #7f1d1d;
    }

    .ch-int-inspector-empty {
      padding: 20px 12px;
      display: flex;
      flex-direction: column;
      gap: 6px;
      font-size: 12px;
      color: var(--muted);
      flex: 1;
    }
    .ch-int-muted { color: var(--muted); font-size: 11px; margin: 0; }
  `],
})
export class CodeHugInternalsComponent implements OnInit, OnDestroy {
  private readonly svc = inject(InternalsService);

  readonly templates = signal<AnantaTemplate[]>([]);
  readonly workers = signal<AnantaWorker[]>([]);
  readonly autopilot = signal<AutopilotStatus>({
    running: false, goal: '', team_id: '', started_at: null,
    tick_count: 0, dispatched_count: 0, completed_count: 0, failed_count: 0,
    last_error: null,
    effective_security_policy: { level: 'safe', max_concurrency_cap: 1, allowed_tool_classes: [] },
    circuit_breakers: { open_workers: [], open_count: 0, failure_streak: {} },
  });

  readonly selectedTemplate = signal<AnantaTemplate | null>(null);
  readonly selectedWorker = signal<AnantaWorker | null>(null);
  readonly selectedFlowNode = signal<FlowPhase | null>(null);

  readonly flowNodes = FLOW_NODES;

  readonly templateCategories = computed((): AnantaTemplate['category'][] => {
    const cats = new Set(this.templates().map(t => t.category));
    return ['scrum', 'opencode', 'kanban', 'system'].filter(c => cats.has(c as any)) as AnantaTemplate['category'][];
  });

  private pollSub: Subscription | null = null;

  ngOnInit(): void {
    this.reload();
    // Poll autopilot status every 3s
    this.pollSub = interval(3000).pipe(
      switchMap(() => this.svc.getAutopilotStatus()),
    ).subscribe(status => this.autopilot.set(status));
  }

  ngOnDestroy(): void {
    this.pollSub?.unsubscribe();
  }

  reload(): void {
    this.svc.getTemplates().subscribe(t => this.templates.set(t));
    this.svc.getWorkers().subscribe(w => this.workers.set(w));
    this.svc.getAutopilotStatus().subscribe(s => this.autopilot.set(s));
  }

  selectTemplate(tpl: AnantaTemplate): void {
    this.selectedWorker.set(null);
    this.selectedFlowNode.set(null);
    this.selectedTemplate.set(this.selectedTemplate()?.id === tpl.id ? null : tpl);
  }

  selectWorker(w: AnantaWorker): void {
    this.selectedTemplate.set(null);
    this.selectedFlowNode.set(null);
    this.selectedWorker.set(this.selectedWorker()?.name === w.name ? null : w);
  }

  selectFlowNode(id: FlowPhase): void {
    this.selectedTemplate.set(null);
    this.selectedWorker.set(null);
    this.selectedFlowNode.set(this.selectedFlowNode() === id ? null : id);
  }

  clearSelection(): void {
    this.selectedTemplate.set(null);
    this.selectedWorker.set(null);
    this.selectedFlowNode.set(null);
  }

  templatesByCategory(cat: AnantaTemplate['category']): AnantaTemplate[] {
    return this.templates().filter(t => t.category === cat);
  }

  getCatIcon(cat: string): string { return CATEGORY_ICONS[cat] ?? '📁'; }
  getCatLabel(cat: string): string { return CATEGORY_LABELS[cat] ?? cat; }
  shortName(name: string): string {
    return name.replace(/^(Scrum|Kanban|OpenCode Scrum)\s*-\s*/i, '');
  }

  workerActive(w: AnantaWorker): boolean {
    const ap = this.autopilot();
    if (!ap.running) return false;
    const openCircuit = ap.circuit_breakers?.open_workers ?? [];
    return w.status === 'online' && !openCircuit.includes(w.url);
  }

  workerX(index: number, total: number): number {
    const totalWidth = total * 120 + (total - 1) * 20;
    const startX = (720 - totalWidth) / 2;
    return startX + index * 140;
  }

  isFlowNodeActive(id: FlowPhase): boolean {
    const ap = this.autopilot();
    if (!ap.running) return false;
    switch (id) {
      case 'planning': return ap.dispatched_count === 0 && ap.tick_count > 0;
      case 'queue': return ap.dispatched_count > 0 && ap.completed_count === 0;
      case 'executing': return ap.dispatched_count > 0;
      case 'verification': return ap.completed_count > 0 && ap.dispatched_count > 0;
      default: return false;
    }
  }

  flowNodeFilter(id: FlowPhase): string | null {
    if (!this.isFlowNodeActive(id)) return null;
    if (id === 'planning') return 'url(#glow-amber)';
    if (id === 'executing') return 'url(#glow-blue)';
    if (id === 'verification') return 'url(#glow-green)';
    return null;
  }

  hexPoints(w: number, h: number): string {
    const m = h / 4;
    return `${m},0 ${w - m},0 ${w},${h / 2} ${w - m},${h} ${m},${h} 0,${h / 2}`;
  }

  diamondPoints(w: number, h: number): string {
    return `${w / 2},0 ${w},${h / 2} ${w / 2},${h} 0,${h / 2}`;
  }

  flowNodeLabel(id: FlowPhase): string {
    return FLOW_NODES.find(n => n.id === id)?.label ?? id;
  }

  flowNodeIcon(id: FlowPhase): string {
    const icons: Record<FlowPhase, string> = {
      goal: '🎯', planning: '🧠', queue: '📥', executing: '⚡', verification: '✅', done: '🏁',
    };
    return icons[id] ?? '●';
  }

  flowNodeDescription(id: FlowPhase): string {
    const desc: Record<FlowPhase, string> = {
      goal: 'Das übergeordnete Ziel, das dem Ananta-System übergeben wird.',
      planning: 'Der Hub plant das Ziel mit dem konfigurierten Planungsmodell (LMStudio / Gemma) in einzelne Tasks auf.',
      queue: 'Geplante Tasks warten auf Dispatch an verfügbare Worker.',
      executing: 'Worker führen Tasks aus: Recherche, Implementierung, Review — je nach ihren Rollen.',
      verification: 'Fertige Tasks werden geprüft: Tests laufen, Diffs werden bewertet.',
      done: 'Alle Tasks abgeschlossen, Artefakte sind bereit.',
    };
    return desc[id] ?? '';
  }

  truncate(s: string, n: number): string {
    return s.length > n ? s.slice(0, n) + '…' : s;
  }
}
