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
  ChAgentStepReadModel,
} from '../models/codehug.models';
import { TopologyGraphComponent } from '../graph/topology-graph.component';
import { TraceViewComponent } from '../graph/trace-view.component';

/**
 * CodeHugInternalsComponent — CH-014 Container.
 *
 * Zeigt in 3 Tabs:
 * 1. Topology: Hub/Worker-Graph + Routing-Regeln + Layer
 * 2. Trace: Laufzeiten der Agent-Runs (3-stufig)
 * 3. Config: Routing-Regeln + Layer editieren (write-armed erforderlich)
 *
 * Bpmn-js fuer Topology-Graph, eigener SVG-Renderer als Fallback.
 */
@Component({
  selector: 'ch-internals',
  standalone: true,
  imports: [DatePipe, FormsModule, TopologyGraphComponent, TraceViewComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="ch-internals">
      <header class="ch-internals-head">
        <h2 class="ch-internals-title">System Internals</h2>
        <p class="ch-internals-lead">
          Echte Hub/Worker-Topologie, Trace-Daten und Konfiguration.
          Aenderungen erfordern Write-Modus.
        </p>
        <div class="ch-internals-status">
          <span class="ch-mode" [attr.data-mode]="policy.writeMode()">
            Modus: {{ policy.writeMode() === 'read-only' ? 'Read-only' : 'Write armed' }}
          </span>
          @if (!policy.writeModeActive() && policy.writeMode() === 'write-armed') {
            <span class="ch-warn">Write-Modus abgelaufen.</span>
          }
          <button
            type="button"
            class="ch-btn"
            [class.ch-btn-primary]="policy.writeMode() === 'read-only'"
            [class.ch-btn-secondary]="policy.writeMode() === 'write-armed'"
            (click)="toggleWriteMode()">
            {{ policy.writeMode() === 'read-only' ? 'Write-Modus aktivieren' : 'Write-Modus deaktivieren' }}
          </button>
          <button type="button" class="ch-btn ch-btn-secondary" (click)="refreshAll()">Aktualisieren</button>
        </div>
      </header>

      <nav class="ch-internals-tabs" role="tablist">
        <button
          type="button"
          role="tab"
          [attr.aria-selected]="tab() === 'topology'"
          [class.active]="tab() === 'topology'"
          (click)="tab.set('topology')">Topologie</button>
        <button
          type="button"
          role="tab"
          [attr.aria-selected]="tab() === 'trace'"
          [class.active]="tab() === 'trace'"
          (click)="tab.set('trace')">Trace</button>
        <button
          type="button"
          role="tab"
          [attr.aria-selected]="tab() === 'config'"
          [class.active]="tab() === 'config'"
          (click)="tab.set('config')">Konfiguration</button>
      </nav>

      <!-- Tab: Topologie -->
      @if (tab() === 'topology') {
        <section class="ch-tab" aria-label="Topologie">
          @if (topology(); as topo) {
            <div class="ch-topology-summary">
              <span><strong>{{ topo.hubs.length }}</strong> Hub(s)</span>
              <span><strong>{{ topo.workers.length }}</strong> Worker</span>
              <span><strong>{{ topo.routingRules.length }}</strong> Routing-Regeln</span>
              <span><strong>{{ topo.activeLayers.length }}</strong> aktive Layer</span>
            </div>
            <div class="ch-topology-graph-wrap">
              <ch-topology-graph
                [hubs]="topo.hubs"
                [workers]="topo.workers"
                [connections]="topo.connections"
                [selectedWorkerId]="selectedWorkerId()"
                (workerSelected)="selectedWorkerId.set($event)" />
            </div>
            @if (selectedWorker(); as w) {
              <aside class="ch-worker-detail" aria-label="Worker-Detail">
                <h4>Worker: {{ w.id }}</h4>
                <dl>
                  <dt>Typ</dt><dd>{{ w.type }}</dd>
                  <dt>CLI-Backend</dt><dd>{{ w.cliBackend }} {{ w.cliBackend === 'deterministic' ? '(deterministisch)' : '' }}</dd>
                  <dt>Modell</dt><dd>{{ w.model }}</dd>
                  <dt>Provider</dt><dd>{{ w.llmProvider }}</dd>
                  <dt>Capabilities</dt><dd>
                    <ul class="ch-cap-list">
                      @for (cap of w.capabilities; track cap) {
                        <li class="ch-cap">{{ cap }}</li>
                      }
                    </ul>
                  </dd>
                  <dt>Health</dt><dd>{{ w.health }}</dd>
                  <dt>Boundary</dt><dd>{{ w.boundary }}</dd>
                  @if (w.lastHeartbeatAt) {
                    <dt>Last Heartbeat</dt><dd>{{ w.lastHeartbeatAt | date: 'mediumTime' }}</dd>
                  }
                </dl>
              </aside>
            }
          } @else if (topologyError(); as err) {
            <p class="ch-error">Topologie konnte nicht geladen werden: {{ err }}</p>
          } @else {
            <p class="ch-muted">Topologie wird geladen…</p>
          }
        </section>
      }

      <!-- Tab: Trace -->
      @if (tab() === 'trace') {
        <section class="ch-tab" aria-label="Trace">
          @if (runs().length === 0) {
            <p class="ch-muted">Keine Agent-Runs verfuegbar.</p>
          } @else {
            <div class="ch-trace-runs">
              <label>Run:
                <select [value]="selectedRunId() ?? ''" (change)="onRunSelect($any($event.target).value)">
                  @for (run of runs(); track run.id) {
                    <option [value]="run.id">{{ run.id }} — {{ run.actualCliBackend }} ({{ run.startedAt | date: 'short' }})</option>
                  }
                </select>
              </label>
            </div>
            @if (selectedRun(); as run) {
              <div class="ch-trace-run-summary">
                <span><strong>{{ run.deterministicStepCount }}</strong> det</span>
                <span><strong>{{ run.llmStepCount }}</strong> LLM</span>
                <span>Backend: <strong>{{ run.actualCliBackend }}</strong></span>
                <span>Modell: <strong>{{ run.actualModel }}</strong></span>
                <span>Routing-Grund: <em>{{ run.routingReason }}</em></span>
              </div>
              <ch-trace-view
                [steps]="run.steps"
                (stepSelected)="onStepSelected($event)" />
            }
          }
        </section>
      }

      <!-- Tab: Konfiguration -->
      @if (tab() === 'config') {
        <section class="ch-tab" aria-label="Konfiguration">
          @if (!policy.writeModeActive()) {
            <p class="ch-warn">Aenderungen erfordern aktivierten Write-Modus.</p>
          }

          <h4>Routing-Regeln</h4>
          @if (routingRules().length === 0) {
            <p class="ch-muted">Keine Routing-Regeln definiert.</p>
          } @else {
            <table class="ch-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Beschreibung</th>
                  <th>Backend</th>
                  <th>Modell</th>
                  <th>Prioritaet</th>
                </tr>
              </thead>
              <tbody>
                @for (rule of routingRules(); track rule.id) {
                  <tr>
                    <td class="ch-mono">{{ rule.id }}</td>
                    <td>{{ rule.description }}</td>
                    <td>
                      <select
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
                    <td>{{ rule.priority }}</td>
                  </tr>
                }
              </tbody>
            </table>
          }

          <h4>Test-Layer</h4>
          @if (layers().length === 0) {
            <p class="ch-muted">Keine Layer konfiguriert.</p>
          } @else {
            <ul class="ch-layers">
              @for (layer of layers(); track layer.id) {
                <li class="ch-layer" [class.ch-layer-disabled]="!layer.enabled">
                  <label class="ch-layer-toggle">
                    <input
                      type="checkbox"
                      [disabled]="!policy.writeModeActive()"
                      [checked]="layer.enabled"
                      (change)="onLayerToggle(layer, $any($event.target).checked)" />
                    <strong>{{ layer.name }}</strong>
                    <span class="ch-layer-order">Order: {{ layer.order }}</span>
                  </label>
                  @if (layer.parameters && keyCount(layer.parameters) > 0) {
                    <pre class="ch-layer-params">{{ stringify(layer.parameters) }}</pre>
                  }
                </li>
              }
            </ul>
          }
          @if (configError(); as err) {
            <p class="ch-error">{{ err }}</p>
          }
          @if (configSuccess()) {
            <p class="ch-success">Konfiguration aktualisiert.</p>
          }
        </section>
      }
    </section>
  `,
  styles: [`
    :host { display: block; padding: 14px; }
    .ch-internals-head { margin-bottom: 14px; }
    .ch-internals-title { margin: 0 0 4px; font-size: 20px; }
    .ch-internals-lead { margin: 0 0 10px; color: var(--muted); font-size: 12px; }
    .ch-internals-status { display: flex; gap: 10px; align-items: center; font-size: 12px; }
    .ch-mode {
      padding: 3px 8px;
      border-radius: 999px;
      background: color-mix(in srgb, var(--accent) 12%, transparent);
      font-weight: 600;
    }
    .ch-mode[data-mode="write-armed"] {
      background: color-mix(in srgb, #f59e0b 30%, transparent);
      color: #92400e;
    }
    .ch-warn { color: #92400e; font-size: 11px; }
    .ch-error { color: #b91c1c; font-size: 12px; }
    .ch-success { color: #065f46; font-size: 12px; }
    .ch-muted { color: var(--muted); font-size: 12px; }
    .ch-mono { font-family: var(--mono, ui-monospace, monospace); font-size: 11px; }

    .ch-btn {
      padding: 4px 10px;
      border-radius: 6px;
      border: 1px solid var(--border);
      background: var(--bg);
      color: var(--fg);
      cursor: pointer;
      font-size: 12px;
    }
    .ch-btn-primary { background: var(--accent); color: #fff; border-color: var(--accent); }
    .ch-btn-secondary { background: var(--card-bg); }

    .ch-internals-tabs {
      display: flex;
      gap: 4px;
      border-bottom: 1px solid var(--border);
      margin-bottom: 12px;
    }
    .ch-internals-tabs button {
      padding: 6px 12px;
      border: 1px solid var(--border);
      border-bottom: none;
      border-radius: 6px 6px 0 0;
      background: var(--bg);
      color: var(--fg);
      cursor: pointer;
      font-size: 12px;
    }
    .ch-internals-tabs button.active {
      background: var(--card-bg);
      font-weight: 600;
    }

    .ch-tab { display: grid; gap: 12px; }
    .ch-topology-summary {
      display: flex;
      gap: 14px;
      font-size: 12px;
      color: var(--muted);
    }
    .ch-topology-summary strong { color: var(--fg); }
    .ch-topology-graph-wrap {
      height: 420px;
      border: 1px solid var(--border);
      border-radius: 6px;
      overflow: hidden;
      background: var(--card-bg);
    }
    .ch-worker-detail {
      padding: 10px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--card-bg);
      font-size: 12px;
    }
    .ch-worker-detail h4 { margin: 0 0 8px; font-size: 13px; }
    .ch-worker-detail dl {
      display: grid;
      grid-template-columns: max-content 1fr;
      gap: 4px 12px;
      margin: 0;
    }
    .ch-worker-detail dt { color: var(--muted); }
    .ch-worker-detail dd { margin: 0; }
    .ch-cap-list { list-style: none; padding: 0; margin: 0; display: flex; flex-wrap: wrap; gap: 4px; }
    .ch-cap {
      font-size: 10px;
      padding: 1px 6px;
      border-radius: 4px;
      background: color-mix(in srgb, var(--accent) 18%, transparent);
    }

    .ch-trace-runs { display: flex; gap: 8px; align-items: center; font-size: 12px; }
    .ch-trace-runs select {
      padding: 3px 6px;
      border: 1px solid var(--border);
      border-radius: 4px;
      background: var(--bg);
      color: var(--fg);
      font-size: 11px;
    }
    .ch-trace-run-summary {
      display: flex;
      gap: 12px;
      font-size: 11px;
      color: var(--muted);
      padding: 4px 0;
      flex-wrap: wrap;
    }
    .ch-trace-run-summary strong { color: var(--fg); }

    .ch-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 11px;
    }
    .ch-table th, .ch-table td {
      padding: 4px 6px;
      border-bottom: 1px solid var(--border);
      text-align: left;
    }
    .ch-table select {
      padding: 2px 4px;
      border: 1px solid var(--border);
      border-radius: 3px;
      background: var(--bg);
      color: var(--fg);
      font-size: 11px;
    }

    .ch-layers { list-style: none; padding: 0; margin: 0; display: grid; gap: 6px; }
    .ch-layer {
      padding: 6px 10px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--card-bg);
    }
    .ch-layer-disabled { opacity: 0.6; }
    .ch-layer-toggle { display: flex; gap: 8px; align-items: center; font-size: 12px; cursor: pointer; }
    .ch-layer-order { font-size: 10px; color: var(--muted); }
    .ch-layer-params {
      margin: 6px 0 0;
      padding: 4px 8px;
      background: var(--bg);
      border-radius: 4px;
      font-size: 10px;
      max-height: 120px;
      overflow: auto;
    }
  `]
})
export class CodeHugInternalsComponent implements OnInit {
  readonly topologySvc = inject(TopologyService);
  readonly runsSvc = inject(AgentRunService);
  readonly policy = inject(PolicyService);

  readonly tab = signal<'topology' | 'trace' | 'config'>('topology');
  readonly topology = signal<ChTopologyReadModel | null>(null);
  readonly topologyError = signal<string | null>(null);
  readonly selectedWorkerId = signal<string | null>(null);
  readonly routingRules = signal<ChRoutingRuleReadModel[]>([]);
  readonly layers = signal<ChTestLayerReadModel[]>([]);
  readonly runs = signal<ChAgentRunReadModel[]>([]);
  readonly selectedRunId = signal<string | null>(null);
  readonly configError = signal<string | null>(null);
  readonly configSuccess = signal(false);

  readonly selectedRun = computed(() => {
    const id = this.selectedRunId();
    if (!id) return null;
    return this.runs().find(r => r.id === id) ?? null;
  });

  readonly selectedWorker = computed(() => {
    const id = this.selectedWorkerId();
    if (!id) return null;
    return this.topology()?.workers.find(w => w.id === id) ?? null;
  });

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
    if (this.tab() === 'config') {
      this.loadConfig();
    }
  }

  loadTopology(): void {
    this.topologySvc.getTopology().subscribe({
      next: t => {
        this.topology.set(t);
        this.topologyError.set(null);
      },
      error: err => this.topologyError.set(err.message ?? 'Unbekannter Fehler'),
    });
    // Routing + Layer separat laden (anderes endpoint)
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

  loadConfig(): void {
    this.topologySvc.getRoutingRules().subscribe({
      next: rules => this.routingRules.set(rules),
      error: () => this.routingRules.set([]),
    });
    this.topologySvc.getTestLayers().subscribe({
      next: layers => this.layers.set(layers),
      error: () => this.layers.set([]),
    });
  }

  onRunSelect(id: string): void {
    this.selectedRunId.set(id || null);
  }

  onStepSelected(stepId: string): void {
    // Hook fuer CH-014 Folge-Tasks: highlight im Graph, scroll-to, etc.
    // Hier: console-log als Platzhalter.
    console.log('Step selected:', stepId);
  }

  async onRuleBackendChange(rule: ChRoutingRuleReadModel, newBackend: string): Promise<void> {
    if (!this.policy.ensureWriteModeValid()) {
      this.configError.set('Write-Modus nicht aktiv. Aktiviere ihn zuerst.');
      return;
    }
    this.configError.set(null);
    this.configSuccess.set(false);
    try {
      const updated = await firstValueFrom(
        this.topologySvc.updateRoutingRule({ ...rule, selectedBackend: newBackend as any })
      );
      this.routingRules.update(rules => rules.map(r => r.id === updated.id ? updated : r));
      this.configSuccess.set(true);
    } catch (err: any) {
      this.configError.set(err?.message ?? 'Routing-Regel konnte nicht aktualisiert werden');
    }
  }

  async onLayerToggle(layer: ChTestLayerReadModel, enabled: boolean): Promise<void> {
    if (!this.policy.ensureWriteModeValid()) {
      this.configError.set('Write-Modus nicht aktiv. Aktiviere ihn zuerst.');
      return;
    }
    this.configError.set(null);
    this.configSuccess.set(false);
    try {
      const updated = await firstValueFrom(
        this.topologySvc.updateTestLayer({ ...layer, enabled })
      );
      this.layers.update(layers => layers.map(l => l.id === updated.id ? updated : l));
      this.configSuccess.set(true);
    } catch (err: any) {
      this.configError.set(err?.message ?? 'Layer konnte nicht aktualisiert werden');
    }
  }

  stringify(v: unknown): string {
    try {
      return JSON.stringify(v, null, 2);
    } catch {
      return String(v);
    }
  }

  keyCount(obj: Record<string, unknown>): number {
    return Object.keys(obj).length;
  }
}