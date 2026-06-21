import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  inject,
  signal,
  computed,
} from '@angular/core';
import { DatePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';

import { TopologyService } from '../services/topology.service';
import { AgentRunService } from '../services/agent-run.service';
import { PolicyService } from '../services/policy.service';
import {
  ChTopologyReadModel,
  ChRoutingRuleReadModel,
  ChTestLayerReadModel,
  ChAgentRunReadModel,
  ChCliBackend,
} from '../models/codehug.models';
import { CodeHugCanvasComponent } from '../graph/codehug-canvas.component';
import { TraceViewComponent } from '../graph/trace-view.component';

/**
 * CodeHugInternalsComponent — CH-014 Internals-Ansicht.
 *
 * Drei Bereiche:
 * 1. Canvas: interaktiver Node-Editor (Hub/Worker/Layer/Rules) mit Live-Run-Highlighting
 * 2. Trace: Chronologische Laufzeitdaten der Agent-Runs
 * 3. Konfiguration: Tabellarische Bearbeitung von Routing-Regeln und Test-Layern
 */
@Component({
  selector: 'ch-internals',
  standalone: true,
  imports: [DatePipe, FormsModule, CodeHugCanvasComponent, TraceViewComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="ch-int">

      <!-- Compact header strip -->
      <header class="ch-int-header">
        <div class="ch-int-header-left">
          <span class="ch-int-title">System Internals</span>

          <nav class="ch-int-tabs" role="tablist">
            <button type="button" role="tab"
              [attr.aria-selected]="tab() === 'canvas'"
              [class.active]="tab() === 'canvas'"
              (click)="tab.set('canvas')">Canvas</button>
            <button type="button" role="tab"
              [attr.aria-selected]="tab() === 'trace'"
              [class.active]="tab() === 'trace'"
              (click)="tab.set('trace')">Trace</button>
            <button type="button" role="tab"
              [attr.aria-selected]="tab() === 'config'"
              [class.active]="tab() === 'config'"
              (click)="tab.set('config')">Konfiguration</button>
          </nav>
        </div>

        <div class="ch-int-header-right">
          <!-- Active run indicator -->
          @if (activeRun(); as run) {
            <span class="ch-int-run-pill" [attr.data-status]="run.status">
              <span class="ch-int-run-dot"></span>
              {{ run.actualCliBackend }} · {{ run.deterministicStepCount + run.llmStepCount }} Schritte
            </span>
          }

          <!-- Topology summary -->
          @if (topology(); as topo) {
            <span class="ch-int-summary">
              {{ topo.hubs.length }} Hub · {{ topo.workers.length }} Worker · {{ topo.activeLayers.length }} Layer
            </span>
          }

          <!-- Write mode & actions -->
          <span class="ch-int-mode" [attr.data-mode]="policy.writeMode()">
            {{ policy.writeMode() === 'read-only' ? 'read-only' : 'write armed' }}
          </span>
          <button type="button" class="ch-int-btn"
            (click)="toggleWriteMode()">
            {{ policy.writeMode() === 'read-only' ? 'Arm' : 'Disarm' }}
          </button>
          <button type="button" class="ch-int-btn" (click)="refreshAll()">↺</button>
        </div>
      </header>

      <!-- Error banner -->
      @if (topologyError()) {
        <div class="ch-int-error-banner">Topologie-Fehler: {{ topologyError() }}</div>
      }
      @if (configError()) {
        <div class="ch-int-error-banner">{{ configError() }}</div>
      }
      @if (configSuccess()) {
        <div class="ch-int-success-banner">Konfiguration gespeichert.</div>
      }

      <!-- ───────── TAB: Canvas ───────── -->
      @if (tab() === 'canvas') {
        <div class="ch-int-canvas-wrap">
          @if (topology(); as topo) {
            <ch-canvas
              [topology]="topo"
              [activeRun]="activeRun()"
              [writeModeActive]="policy.writeModeActive()"
              (refreshRequested)="refreshAll()"
              (layerToggled)="onCanvasLayerToggle($event)"
              (ruleChanged)="onCanvasRuleChange($event)" />
          } @else {
            <div class="ch-int-canvas-empty">
              @if (topologyError()) {
                <p>Hub nicht erreichbar: {{ topologyError() }}</p>
              } @else {
                <p class="ch-int-muted">Topologie wird geladen…</p>
              }
              <button type="button" class="ch-int-btn" (click)="loadTopology()">Aktualisieren</button>
            </div>
          }
        </div>
      }

      <!-- ───────── TAB: Trace ───────── -->
      @if (tab() === 'trace') {
        <section class="ch-int-tab" aria-label="Trace">
          @if (runs().length === 0) {
            <p class="ch-int-muted">Keine Agent-Runs vorhanden.</p>
          } @else {
            <div class="ch-int-trace-controls">
              <label class="ch-int-label">
                Run:
                <select class="ch-int-select"
                  [value]="selectedRunId() ?? ''"
                  (change)="selectedRunId.set($any($event.target).value || null)">
                  @for (run of runs(); track run.id) {
                    <option [value]="run.id">
                      {{ run.id.slice(0, 12) }} · {{ run.actualCliBackend }} · {{ run.status }} · {{ run.startedAt | date:'short' }}
                    </option>
                  }
                </select>
              </label>
            </div>
            @if (selectedRun(); as run) {
              <div class="ch-int-run-meta">
                <span><strong>{{ run.deterministicStepCount }}</strong> det</span>
                <span><strong>{{ run.llmStepCount }}</strong> LLM</span>
                <span>Backend: <strong>{{ run.actualCliBackend }}</strong></span>
                <span>Modell: <strong>{{ run.actualModel }}</strong></span>
                <span>Status: <strong>{{ run.status }}</strong></span>
                @if (run.routingReason) {
                  <span class="ch-int-routing-reason">→ {{ run.routingReason }}</span>
                }
                @if (run.warnings.length > 0) {
                  <span class="ch-int-warn">{{ run.warnings.length }} Warnung(en)</span>
                }
              </div>
              <ch-trace-view [steps]="run.steps" (stepSelected)="onStepSelected($event)" />
            }
          }
        </section>
      }

      <!-- ───────── TAB: Konfiguration ───────── -->
      @if (tab() === 'config') {
        <section class="ch-int-tab" aria-label="Konfiguration">
          @if (!policy.writeModeActive()) {
            <p class="ch-int-warn">Aenderungen erfordern Write-Modus. Aktiviere ihn oben rechts.</p>
          }

          <!-- Routing rules -->
          <div class="ch-int-config-section">
            <h4 class="ch-int-section-title">Routing-Regeln</h4>
            @if (routingRules().length === 0) {
              <p class="ch-int-muted">Keine Routing-Regeln definiert.</p>
            } @else {
              <table class="ch-int-table">
                <thead>
                  <tr>
                    <th>Prioritaet</th>
                    <th>Beschreibung</th>
                    <th>Backend</th>
                    <th>Modell</th>
                  </tr>
                </thead>
                <tbody>
                  @for (rule of routingRules(); track rule.id) {
                    <tr>
                      <td>{{ rule.priority }}</td>
                      <td>{{ rule.description }}</td>
                      <td>
                        <select class="ch-int-select-sm"
                          [disabled]="!policy.writeModeActive()"
                          [value]="rule.selectedBackend"
                          (change)="onRuleBackendChange(rule, $any($event.target).value)">
                          <option value="sgpt">sgpt</option>
                          <option value="opencode">opencode</option>
                          <option value="codex">codex</option>
                          <option value="claude_code">claude_code</option>
                          <option value="aider">aider</option>
                          <option value="mistral">mistral</option>
                          <option value="deterministic">deterministic</option>
                        </select>
                      </td>
                      <td class="ch-mono">{{ rule.selectedModel }}</td>
                    </tr>
                  }
                </tbody>
              </table>
            }
          </div>

          <!-- Test layers -->
          <div class="ch-int-config-section">
            <h4 class="ch-int-section-title">Test-Layer</h4>
            @if (layers().length === 0) {
              <p class="ch-int-muted">Keine Layer konfiguriert.</p>
            } @else {
              <ul class="ch-int-layers">
                @for (layer of layers(); track layer.id) {
                  <li class="ch-int-layer" [class.disabled]="!layer.enabled">
                    <label class="ch-int-layer-label">
                      <input type="checkbox"
                        [disabled]="!policy.writeModeActive()"
                        [checked]="layer.enabled"
                        (change)="onLayerToggle(layer, $any($event.target).checked)" />
                      <strong>{{ layer.name }}</strong>
                      <span class="ch-int-layer-order">Order {{ layer.order }}</span>
                    </label>
                    @if (keyCount(layer.parameters) > 0) {
                      <pre class="ch-int-params">{{ stringify(layer.parameters) }}</pre>
                    }
                  </li>
                }
              </ul>
            }
          </div>
        </section>
      }
    </section>
  `,
  styles: [`
    :host { display: block; height: 100%; }

    .ch-int {
      display: flex;
      flex-direction: column;
      height: 100%;
      min-height: 0;
    }

    /* Header */
    .ch-int-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 6px 12px;
      border-bottom: 1px solid var(--border);
      background: var(--card-bg);
      flex-shrink: 0;
      flex-wrap: wrap;
    }
    .ch-int-header-left {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .ch-int-header-right {
      display: flex;
      align-items: center;
      gap: 6px;
      flex-wrap: wrap;
    }
    .ch-int-title {
      font-size: 13px;
      font-weight: 700;
      white-space: nowrap;
    }

    /* Tabs inside header */
    .ch-int-tabs {
      display: flex;
      gap: 2px;
    }
    .ch-int-tabs button {
      padding: 3px 9px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--bg);
      color: var(--muted);
      cursor: pointer;
      font-size: 11px;
    }
    .ch-int-tabs button.active {
      background: color-mix(in srgb, var(--accent) 14%, transparent);
      color: var(--accent);
      font-weight: 600;
      border-color: var(--accent);
    }

    /* Active run pill */
    .ch-int-run-pill {
      display: flex;
      align-items: center;
      gap: 5px;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 600;
    }
    .ch-int-run-pill[data-status="running"] {
      background: color-mix(in srgb, #3b82f6 14%, transparent);
      border: 1px solid #3b82f6;
      color: #1e40af;
    }
    .ch-int-run-pill[data-status="succeeded"] {
      background: color-mix(in srgb, #22c55e 12%, transparent);
      border: 1px solid #22c55e;
      color: #14532d;
    }
    .ch-int-run-pill[data-status="failed"] {
      background: color-mix(in srgb, #ef4444 10%, transparent);
      border: 1px solid #ef4444;
      color: #7f1d1d;
    }
    .ch-int-run-dot {
      width: 6px; height: 6px;
      border-radius: 50%;
      background: currentColor;
    }
    .ch-int-run-pill[data-status="running"] .ch-int-run-dot {
      animation: ch-int-pulse 1.4s ease-in-out infinite;
    }
    @keyframes ch-int-pulse { 0%,100% { opacity:1; } 50% { opacity:0.3; } }

    .ch-int-summary { font-size: 11px; color: var(--muted); }
    .ch-int-mode {
      font-size: 11px;
      font-weight: 600;
      padding: 2px 7px;
      border-radius: 999px;
      background: color-mix(in srgb, var(--accent) 10%, transparent);
    }
    .ch-int-mode[data-mode="write-armed"] {
      background: color-mix(in srgb, #f59e0b 20%, transparent);
      color: #78350f;
    }
    .ch-int-btn {
      padding: 3px 8px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--bg);
      color: var(--fg);
      cursor: pointer;
      font-size: 11px;
    }
    .ch-int-btn:hover { background: var(--card-bg); }

    /* Error / success banners */
    .ch-int-error-banner {
      padding: 6px 12px;
      background: color-mix(in srgb, #ef4444 10%, transparent);
      border-bottom: 1px solid #fca5a5;
      font-size: 12px;
      color: #7f1d1d;
      flex-shrink: 0;
    }
    .ch-int-success-banner {
      padding: 5px 12px;
      background: color-mix(in srgb, #22c55e 10%, transparent);
      border-bottom: 1px solid #bbf7d0;
      font-size: 12px;
      color: #14532d;
      flex-shrink: 0;
    }

    /* Canvas area — fills remaining space, min-height als Fallback wenn height-chain kollabiert */
    .ch-int-canvas-wrap {
      flex: 1;
      min-height: 520px;
      padding: 10px;
    }
    .ch-int-canvas-wrap > ch-canvas {
      display: block;
      height: 100%;
      min-height: 500px;
    }
    .ch-int-canvas-empty {
      min-height: 300px;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 10px;
      color: var(--muted);
      font-size: 13px;
    }

    /* Scrollable tab content */
    .ch-int-tab {
      flex: 1;
      overflow: auto;
      padding: 14px;
      display: grid;
      gap: 16px;
      align-content: start;
    }

    /* Trace controls */
    .ch-int-trace-controls { display: flex; gap: 8px; align-items: center; }
    .ch-int-label { font-size: 12px; display: flex; align-items: center; gap: 6px; }
    .ch-int-select, .ch-int-select-sm {
      padding: 3px 6px;
      border: 1px solid var(--border);
      border-radius: 4px;
      background: var(--bg);
      color: var(--fg);
      font-size: 11px;
    }
    .ch-int-run-meta {
      display: flex;
      gap: 12px;
      font-size: 11px;
      color: var(--muted);
      flex-wrap: wrap;
      padding: 4px 0;
    }
    .ch-int-run-meta strong { color: var(--fg); }
    .ch-int-routing-reason { font-style: italic; }
    .ch-int-warn { color: #92400e; font-size: 11px; }

    /* Config section */
    .ch-int-config-section { display: grid; gap: 8px; }
    .ch-int-section-title { margin: 0; font-size: 13px; }
    .ch-int-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 11px;
    }
    .ch-int-table th, .ch-int-table td {
      padding: 5px 8px;
      border-bottom: 1px solid var(--border);
      text-align: left;
    }
    .ch-int-table th { color: var(--muted); font-weight: 500; }
    .ch-mono { font-family: var(--mono, ui-monospace, monospace); font-size: 11px; }

    .ch-int-layers { list-style: none; padding: 0; margin: 0; display: grid; gap: 6px; }
    .ch-int-layer {
      padding: 7px 10px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--card-bg);
    }
    .ch-int-layer.disabled { opacity: 0.6; }
    .ch-int-layer-label { display: flex; gap: 8px; align-items: center; font-size: 12px; cursor: pointer; }
    .ch-int-layer-order { font-size: 10px; color: var(--muted); }
    .ch-int-params {
      margin: 6px 0 0;
      padding: 4px 8px;
      background: var(--bg);
      border-radius: 4px;
      font-size: 10px;
      max-height: 100px;
      overflow: auto;
      white-space: pre-wrap;
    }
    .ch-int-muted { color: var(--muted); font-size: 12px; margin: 0; }
  `],
})
export class CodeHugInternalsComponent implements OnInit {
  readonly topologySvc = inject(TopologyService);
  readonly runsSvc = inject(AgentRunService);
  readonly policy = inject(PolicyService);


  readonly tab = signal<'canvas' | 'trace' | 'config'>('canvas');
  readonly topology = signal<ChTopologyReadModel | null>(null);
  readonly topologyError = signal<string | null>(null);
  readonly routingRules = signal<ChRoutingRuleReadModel[]>([]);
  readonly layers = signal<ChTestLayerReadModel[]>([]);
  readonly runs = signal<ChAgentRunReadModel[]>([]);
  readonly selectedRunId = signal<string | null>(null);
  readonly configError = signal<string | null>(null);
  readonly configSuccess = signal(false);

  readonly selectedRun = computed(() => {
    const id = this.selectedRunId();
    return id ? (this.runs().find(r => r.id === id) ?? null) : null;
  });

  // The latest running run for canvas live-highlighting
  readonly activeRun = computed((): ChAgentRunReadModel | null =>
    this.runs().find(r => r.status === 'running') ?? this.runs()[0] ?? null
  );

  ngOnInit(): void {
    this.refreshAll();
  }

  toggleWriteMode(): void {
    if (this.policy.writeMode() === 'read-only') {
      this.policy.armWriteMode();
    } else {
      this.policy.disarmWriteMode();
    }
  }

  refreshAll(): void {
    this.loadTopology();
    this.loadRuns();
  }

  loadTopology(): void {
    this.topologySvc.getTopology().subscribe({
      next: t => { this.topology.set(t); this.topologyError.set(null); },
      error: err => this.topologyError.set(err.message ?? 'Unbekannter Fehler'),
    });
    this.topologySvc.getRoutingRules().subscribe({
      next: rules => this.routingRules.set(rules),
      error: () => this.routingRules.set([]),
    });
    this.topologySvc.getTestLayers().subscribe({
      next: layers => this.layers.set(layers),
      error: () => this.layers.set([]),
    });
  }

  loadRuns(): void {
    this.runsSvc.listRuns().subscribe({
      next: list => {
        this.runs.set(list);
        if (list.length > 0 && !this.selectedRunId()) {
          this.selectedRunId.set(list[0].id);
        }
      },
      error: () => this.runs.set([]),
    });
  }

  onStepSelected(stepId: string): void {
    // Placeholder: future integration — highlight in canvas
    console.log('Step selected:', stepId);
  }

  // Canvas events
  async onCanvasLayerToggle(event: { layer: ChTestLayerReadModel; enabled: boolean }): Promise<void> {
    await this.onLayerToggle(event.layer, event.enabled);
  }

  async onCanvasRuleChange(event: { rule: ChRoutingRuleReadModel; newBackend: ChCliBackend }): Promise<void> {
    await this.onRuleBackendChange(event.rule, event.newBackend);
  }

  async onRuleBackendChange(rule: ChRoutingRuleReadModel, newBackend: string): Promise<void> {
    if (!this.policy.ensureWriteModeValid()) {
      this.configError.set('Write-Modus nicht aktiv.');
      return;
    }
    this.configError.set(null);
    this.configSuccess.set(false);
    try {
      const updated = await firstValueFrom(
        this.topologySvc.updateRoutingRule({ ...rule, selectedBackend: newBackend as ChCliBackend })
      );
      this.routingRules.update(rules => rules.map(r => r.id === updated.id ? updated : r));
      this.configSuccess.set(true);
    } catch (err: any) {
      this.configError.set(err?.message ?? 'Routing-Regel konnte nicht aktualisiert werden');
    }
  }

  async onLayerToggle(layer: ChTestLayerReadModel, enabled: boolean): Promise<void> {
    if (!this.policy.ensureWriteModeValid()) {
      this.configError.set('Write-Modus nicht aktiv.');
      return;
    }
    this.configError.set(null);
    this.configSuccess.set(false);
    try {
      const updated = await firstValueFrom(
        this.topologySvc.updateTestLayer({ ...layer, enabled })
      );
      this.layers.update(ls => ls.map(l => l.id === updated.id ? updated : l));

      // sync into topology so canvas reflects the change immediately
      this.topology.update(t => t ? {
        ...t,
        activeLayers: t.activeLayers.map(l => l.id === updated.id ? updated : l),
      } : t);

      this.configSuccess.set(true);
    } catch (err: any) {
      this.configError.set(err?.message ?? 'Layer konnte nicht aktualisiert werden');
    }
  }

  stringify(v: unknown): string {
    try { return JSON.stringify(v, null, 2); } catch { return String(v); }
  }

  keyCount(obj: Record<string, unknown>): number {
    return Object.keys(obj).length;
  }
}
