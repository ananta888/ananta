import { ChangeDetectionStrategy, ChangeDetectorRef, Component, OnDestroy, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Subscription } from 'rxjs';

import {
  WorkflowRuntimeStreamFrame,
  WorkflowRuntimeStreamService,
} from '../../services/workflow-runtime-stream.service';
import { SystemFacade } from '../system/system.facade';
import { WorkflowRuntimeOperationsApiService } from './workflow-runtime-operations-api.service';
import {
  RuntimeGateView,
  RuntimeCapabilityMatrixProjection,
  RuntimeOperationCommandRequest,
  RuntimeOperationsFilters,
  RuntimeOperationsResponse,
  WorkflowRuntimeOperationRun,
} from './workflow-runtime-operations.models';

type RuntimeCommandType = RuntimeOperationCommandRequest['type'];

@Component({
  selector: 'app-workflow-runtime-operations',
  standalone: true,
  imports: [FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="runtime-operations" data-testid="runtime-operations-page">
      <header class="page-header">
        <div>
          <p class="eyebrow">Hub Control Plane</p>
          <h1>Workflow-Runtime Operations</h1>
          <p class="intro">
            Bewertete Hub-Read-Models für Native, LangGraph und Temporal. Die Ansicht spricht weder Worker
            noch Temporal direkt an; Steuerung bleibt evidence- und approval-gebunden.
          </p>
        </div>
        <button type="button" class="refresh" (click)="load()" [disabled]="loading">Aktualisieren</button>
      </header>

      @if (capabilityMatrix) {
        <section class="capability-panel" data-testid="runtime-capability-matrix">
          <header>
            <div>
              <p class="eyebrow">Gemeinsame Hub-Projektion</p>
              <h2>Runtime-Capability-Matrix · {{ capabilityMatrix.matrix_version }}</h2>
            </div>
            <span class="badge">{{ capabilityMatrix.runtimes.length }} Runtimes</span>
          </header>
          <div class="capability-grid">
            @for (runtime of capabilityMatrix.runtimes; track runtime.runtime_id) {
              <article
                [class.degraded]="runtime.health.status !== 'ready' || runtime.selection.state !== 'compatible'"
                [attr.data-runtime-id]="runtime.runtime_id"
              >
                <div class="capability-title">
                  <strong>{{ runtime.runtime_id }}</strong>
                  <span class="badge runtime">{{ runtime.mode }}</span>
                  <span class="badge" [class.danger]="runtime.health.status !== 'ready'">
                    {{ runtime.health.status }}
                  </span>
                  <span class="badge" [class.danger]="runtime.selection.state !== 'compatible'">
                    {{ runtime.selection.state }}
                  </span>
                </div>
                <small>{{ runtime.runtime_version }} · {{ runtime.contract_version }}</small>
                <p>{{ runtime.health.reason_code }}</p>
                <p>{{ runtime.selection.reason_code }}</p>
                @if (runtime.selection.missing_capabilities.length) {
                  <p>
                    Fehlend: {{ runtime.selection.missing_capabilities.join(', ') }}
                  </p>
                }
                <div class="capability-tags" aria-label="Unterstützte Capabilities">
                  @for (capability of runtime.capabilities; track capability) {
                    <span>{{ capability }}</span>
                  }
                </div>
                <ul>
                  @for (restriction of runtime.restrictions; track restriction) {
                    <li>{{ restriction }}</li>
                  }
                </ul>
              </article>
            }
          </div>
        </section>
      } @else if (capabilityError) {
        <div class="inline-warning danger" data-testid="runtime-capability-error">
          Capability-Matrix nicht verfügbar: {{ capabilityError }}
        </div>
      }

      <form class="filters" (submit)="$event.preventDefault(); load()" aria-label="Runtime-Filter">
        <label>
          <span>Suche</span>
          <input name="runtimeSearch" [(ngModel)]="filters.q" placeholder="Run, Workflow oder Task" />
        </label>
        <label>
          <span>Runtime</span>
          <select name="runtime" [(ngModel)]="filters.runtime">
            <option value="">Alle</option>
            @for (runtime of runtimeOptions(); track runtime) { <option [value]="runtime">{{ runtime }}</option> }
          </select>
        </label>
        <label>
          <span>Modus</span>
          <select name="mode" [(ngModel)]="filters.mode">
            <option value="">Alle</option>
            @for (mode of modeOptions(); track mode) { <option [value]="mode">{{ mode }}</option> }
          </select>
        </label>
        <label>
          <span>Status</span>
          <select name="status" [(ngModel)]="filters.status">
            <option value="">Alle</option>
            <option value="running">Laufend</option>
            <option value="completed">Abgeschlossen</option>
            <option value="failed">Fehlgeschlagen</option>
            <option value="cancelled">Abgebrochen</option>
          </select>
        </label>
        <label>
          <span>Bewertung</span>
          <select name="health" [(ngModel)]="filters.health">
            <option value="">Alle</option>
            <option value="healthy">Gesund</option>
            <option value="degraded">Degraded</option>
            <option value="stale">Stale</option>
            <option value="parity_gap">Parity-Lücke</option>
            <option value="unverified">Erfolg unbestätigt</option>
          </select>
        </label>
        <div class="filter-actions">
          <button type="submit" [disabled]="loading">Filtern</button>
          <button type="button" class="secondary" (click)="resetFilters()" [disabled]="loading">Zurücksetzen</button>
        </div>
      </form>

      @if (loading) {
        <div class="state-panel" data-testid="runtime-operations-loading" role="status">
          <span class="spinner" aria-hidden="true"></span> Runtime-Read-Models werden geladen …
        </div>
      } @else if (forbidden) {
        <div class="state-panel danger" data-testid="runtime-operations-forbidden" role="alert">
          <strong>Zugriff verweigert</strong>
          <span>Diese tenant-gebundene Operationssicht benötigt eine gültige Hub-Anmeldung.</span>
        </div>
      } @else if (errorCode) {
        <div class="state-panel danger" data-testid="runtime-operations-error" role="alert">
          <strong>Read Model nicht verfügbar</strong>
          <span>{{ errorCode }}</span>
          <button type="button" class="secondary" (click)="load()">Erneut versuchen</button>
        </div>
      } @else if (snapshot) {
        <section class="summary-grid" aria-label="Runtime-Zusammenfassung">
          <article><span>Läufe</span><strong>{{ snapshot.summary.total_runs }}</strong></article>
          <article [class.warn]="snapshot.summary.degraded_runs > 0"><span>Degraded</span><strong>{{ snapshot.summary.degraded_runs }}</strong></article>
          <article [class.warn]="snapshot.summary.stale_runs > 0"><span>Stale</span><strong>{{ snapshot.summary.stale_runs }}</strong></article>
          <article [class.warn]="snapshot.summary.parity_gap_runs > 0"><span>Parity-Lücken</span><strong>{{ snapshot.summary.parity_gap_runs }}</strong></article>
          <article [class.warn]="snapshot.summary.open_gates > 0"><span>Offene Gates</span><strong>{{ snapshot.summary.open_gates }}</strong></article>
          <article><span>Evidence</span><strong>{{ snapshot.summary.verified_evidence }}</strong></article>
          <article><span>Kosten</span><strong>{{ formatCost(snapshot.summary.total_cost_micros) }}</strong></article>
          <article><span>Latenz p95</span><strong>{{ formatLatency(snapshot.summary.latency_p95_ms) }}</strong></article>
        </section>

        @if (snapshot.runs.length === 0) {
          <div class="state-panel" data-testid="runtime-operations-empty">
            <strong>Keine Runtime-Läufe</strong>
            <span>Für die aktuelle Tenant- und Filterauswahl liegen noch keine Hub-Evaluationen vor.</span>
          </div>
        } @else {
          <div class="run-list" data-testid="runtime-operations-runs">
            @for (run of snapshot.runs; track run.run_id) {
              <article
                class="run-card"
                [class.degraded]="run.degraded"
                [class.stale]="run.stale"
                [attr.data-run-id]="run.run_id">
                <header class="run-header">
                  <div>
                    <div class="run-title-row">
                      <h2>{{ run.workflow_id || run.run_id }}</h2>
                      <span class="badge runtime">{{ run.runtime }}</span>
                      <span class="badge">{{ run.mode }}</span>
                      @if (run.degraded) { <span class="badge danger">degraded</span> }
                      @if (run.stale) { <span class="badge stale">stale</span> }
                    </div>
                    <p class="run-id">Run {{ run.run_id }} · Task {{ run.task_id || 'nicht gebunden' }}</p>
                  </div>
                  <div class="outcome" [class.unverified]="run.outcome_claim === 'unverified'">
                    <span>Bewerteter Status</span>
                    <strong>{{ outcomeLabel(run) }}</strong>
                  </div>
                </header>

                @if (run.stale) {
                  <div class="inline-warning" data-testid="runtime-stale-warning">
                    Stale Read Model: letzte Hub-Evidence {{ formatTimestamp(run.updated_at) }}. Steuerung ist gesperrt.
                  </div>
                }
                @if (run.outcome_claim === 'unverified') {
                  <div class="inline-warning danger">
                    Ein Runtime-Erfolg wurde gemeldet, aber ohne verifizierte Evidence nicht als Erfolg bestätigt.
                  </div>
                }

                <div class="metrics">
                  <div><span>Kosten</span><strong>{{ formatCost(run.cost_micros) }}</strong></div>
                  <div><span>Latenz</span><strong>{{ formatLatency(run.latency_ms) }}</strong></div>
                  <div><span>Recovery</span><strong>{{ run.recovery.status }}</strong><small>{{ recoveryDetail(run) }}</small></div>
                  <div><span>Sequence</span><strong>{{ run.source_sequence }}</strong></div>
                </div>

                <div class="detail-grid">
                  <section>
                    <h3>Capabilities</h3>
                    <ul class="compact-list">
                      @for (capability of run.capabilities; track capability.name) {
                        <li>
                          <span>{{ capability.name }}</span>
                          <span class="badge" [class.danger]="capability.status !== 'supported'">{{ capability.status }}</span>
                        </li>
                      } @empty { <li class="muted">Keine Capability-Evidence</li> }
                    </ul>
                  </section>

                  <section>
                    <h3>Fallbacks</h3>
                    <ul class="compact-list">
                      @for (fallback of run.fallbacks; track $index) {
                        <li class="stacked">
                          <strong>{{ fallback.source_runtime }} → {{ fallback.target_runtime }}</strong>
                          <span>{{ fallback.reason_code }} · {{ fallback.semantic_class }} · {{ fallback.approved ? 'freigegeben' : 'nicht freigegeben' }}</span>
                        </li>
                      } @empty { <li class="muted">Kein Fallback beobachtet</li> }
                    </ul>
                  </section>

                  <section>
                    <h3>Gates</h3>
                    <ul class="compact-list">
                      @for (gate of run.gates; track gate.gate_id) {
                        <li class="stacked">
                          <strong>{{ gate.label }}</strong>
                          <span class="badge" [class.danger]="gate.status === 'open'">{{ gate.status }}</span>
                          <small>{{ gate.approval_id || 'keine Approval-Bindung' }}</small>
                        </li>
                      } @empty { <li class="muted">Keine Gates</li> }
                    </ul>
                  </section>

                  <section>
                    <h3>Evidence</h3>
                    <ul class="compact-list">
                      @for (evidence of run.evidence; track evidence.evidence_id) {
                        <li class="stacked">
                          <strong>{{ evidence.kind }} · {{ evidence.evidence_id }}</strong>
                          <span class="badge" [class.danger]="evidence.verification_status !== 'verified'">{{ evidence.verification_status }}</span>
                          @if (evidence.summary) { <small>{{ evidence.summary }}</small> }
                        </li>
                      } @empty { <li class="muted">Keine Evidence vorhanden</li> }
                    </ul>
                  </section>
                </div>

                @if (run.parity_gaps.length || run.semantic_deviations.length) {
                  <section class="gap-panel" data-testid="runtime-parity-gaps">
                    <h3>Native-Parität und semantische Abweichungen</h3>
                    @for (gap of combinedGaps(run); track gap.code) {
                      <div class="gap-row">
                        <span class="badge danger">{{ gap.severity }}</span>
                        <strong>{{ gap.code }}</strong>
                        <span>{{ gap.summary || gap.category }}</span>
                      </div>
                    }
                  </section>
                }

                <footer class="command-bar">
                  <div>
                    <strong>Hub-Steuerung</strong>
                    <p>{{ commandHint(run) }}</p>
                  </div>
                  <div class="command-actions">
                    <button type="button" class="secondary" (click)="toggleLiveStream(run)" [disabled]="!run.workflow_id">
                      {{ liveRunId === run.run_id ? 'Live-Stream schließen' : 'Live-Ereignisse' }}
                    </button>
                    <button type="button" class="secondary" (click)="sendCommand(run, 'pause_run')" [disabled]="!canCommand(run, 'pause_run') || commandPending(run)">Pausieren</button>
                    <button type="button" class="secondary" (click)="sendCommand(run, 'resume_run')" [disabled]="!canCommand(run, 'resume_run') || commandPending(run)">Fortsetzen</button>
                    <button type="button" (click)="sendCommand(run, 'retry_run_or_task')" [disabled]="!canCommand(run, 'retry_run_or_task') || commandPending(run)">Recovery starten</button>
                    <button type="button" class="danger-button" (click)="sendCommand(run, 'cancel_run')" [disabled]="!canCommand(run, 'cancel_run') || commandPending(run)">Abbrechen</button>
                  </div>
                  @if (commandMessages[run.run_id]) {
                    <p class="command-message" role="status">{{ commandMessages[run.run_id] }}</p>
                  }
                </footer>
                @if (liveRunId === run.run_id) {
                  <section class="live-events" data-testid="runtime-live-events" aria-live="polite">
                    <header><strong>Authentisierter Hub-Stream</strong><span>{{ liveCursor || 'warte auf Cursor' }}</span></header>
                    @if (liveError) { <p class="inline-warning danger">{{ liveError }}</p> }
                    <ol>
                      @for (event of liveEvents; track event.event_id) {
                        <li><code>{{ event.event_type }}</code><span>{{ event.step_id || event.run_id || 'Workflow' }}</span></li>
                      } @empty { <li class="muted">Noch keine Ereignisse empfangen.</li> }
                    </ol>
                  </section>
                }
              </article>
            }
          </div>
        }
      }
    </section>
  `,
  styles: [`
    :host { display: block; }
    .runtime-operations { max-width: 1500px; margin: 0 auto; padding: 22px; display: grid; gap: 18px; }
    .page-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 20px; }
    h1, h2, h3, p { margin: 0; }
    h1 { font-size: clamp(1.65rem, 3vw, 2.35rem); }
    h2 { font-size: 1.1rem; }
    h3 { font-size: .85rem; letter-spacing: .03em; text-transform: uppercase; }
    .eyebrow { color: var(--accent); font-size: .75rem; font-weight: 800; letter-spacing: .12em; text-transform: uppercase; }
    .intro { color: var(--muted); max-width: 850px; margin-top: 7px; line-height: 1.5; }
    .capability-panel { display: grid; gap: 12px; padding: 14px; border: 1px solid var(--border); border-radius: 12px; background: var(--card-bg); }
    .capability-panel > header, .capability-title { display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; }
    .capability-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
    .capability-grid article { min-width: 0; display: grid; gap: 7px; padding: 11px; border: 1px solid var(--border); border-radius: 9px; background: color-mix(in srgb, var(--fg) 3%, transparent); }
    .capability-grid article.degraded { border-color: #d97706; }
    .capability-grid small, .capability-grid p, .capability-grid li { color: var(--muted); font-size: .72rem; overflow-wrap: anywhere; }
    .capability-grid ul { margin: 0; padding-left: 18px; }
    .capability-tags { display: flex; gap: 4px; flex-wrap: wrap; }
    .capability-tags span { padding: 2px 5px; border-radius: 5px; background: color-mix(in srgb, var(--accent) 9%, transparent); font-size: .65rem; }
    button { cursor: pointer; }
    button:disabled { cursor: not-allowed; opacity: .45; }
    .filters { display: grid; grid-template-columns: minmax(190px, 1.5fr) repeat(4, minmax(130px, 1fr)) auto; gap: 10px; align-items: end; padding: 14px; border: 1px solid var(--border); border-radius: 12px; background: var(--card-bg); }
    label { display: grid; gap: 5px; color: var(--muted); font-size: .74rem; font-weight: 700; }
    input, select { width: 100%; min-height: 36px; border: 1px solid var(--border); border-radius: 7px; background: var(--bg); color: var(--fg); padding: 7px 9px; }
    .filter-actions, .command-actions { display: flex; gap: 7px; flex-wrap: wrap; }
    .summary-grid { display: grid; grid-template-columns: repeat(8, minmax(100px, 1fr)); gap: 9px; }
    .summary-grid article { display: grid; gap: 4px; border: 1px solid var(--border); border-radius: 10px; padding: 11px; background: var(--card-bg); }
    .summary-grid article.warn { border-color: #d97706; background: color-mix(in srgb, #d97706 8%, var(--card-bg)); }
    .summary-grid span, .metrics span, .outcome span { color: var(--muted); font-size: .72rem; text-transform: uppercase; letter-spacing: .04em; }
    .summary-grid strong { font-size: 1.2rem; }
    .state-panel { min-height: 130px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; border: 1px dashed var(--border); border-radius: 12px; color: var(--muted); text-align: center; padding: 20px; }
    .state-panel.danger { border-color: #dc2626; color: var(--fg); background: color-mix(in srgb, #dc2626 7%, var(--card-bg)); }
    .spinner { width: 22px; height: 22px; border: 3px solid var(--border); border-top-color: var(--accent); border-radius: 50%; animation: spin .8s linear infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }
    .run-list { display: grid; gap: 14px; }
    .run-card { display: grid; gap: 14px; border: 1px solid var(--border); border-radius: 14px; padding: 16px; background: var(--card-bg); box-shadow: 0 8px 24px rgba(0,0,0,.06); }
    .run-card.degraded { border-left: 5px solid #d97706; }
    .run-card.stale { border-style: dashed; }
    .run-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 14px; }
    .run-title-row { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
    .run-id { margin-top: 5px; color: var(--muted); font-size: .76rem; }
    .badge { display: inline-flex; width: fit-content; padding: 2px 7px; border-radius: 999px; background: color-mix(in srgb, var(--fg) 8%, transparent); font-size: .69rem; font-weight: 750; }
    .badge.runtime { color: var(--accent); background: color-mix(in srgb, var(--accent) 12%, transparent); }
    .badge.danger { color: #b45309; background: color-mix(in srgb, #f59e0b 16%, transparent); }
    .badge.stale { color: #7c3aed; background: color-mix(in srgb, #7c3aed 13%, transparent); }
    .outcome { min-width: 190px; display: grid; gap: 4px; text-align: right; }
    .outcome.unverified strong { color: #b45309; }
    .inline-warning { border-radius: 8px; padding: 9px 11px; background: color-mix(in srgb, #7c3aed 10%, var(--card-bg)); color: var(--fg); font-size: .82rem; }
    .inline-warning.danger { background: color-mix(in srgb, #dc2626 9%, var(--card-bg)); }
    .metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 9px; }
    .metrics > div { display: grid; gap: 3px; padding: 9px; border-radius: 8px; background: color-mix(in srgb, var(--fg) 4%, transparent); }
    .metrics small { color: var(--muted); }
    .detail-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 11px; }
    .detail-grid section { min-width: 0; border-top: 1px solid var(--border); padding-top: 10px; }
    .compact-list { list-style: none; padding: 0; margin: 8px 0 0; display: grid; gap: 6px; }
    .compact-list li { display: flex; justify-content: space-between; align-items: center; gap: 8px; min-width: 0; font-size: .8rem; }
    .compact-list li.stacked { align-items: flex-start; flex-direction: column; padding-bottom: 6px; border-bottom: 1px solid color-mix(in srgb, var(--border) 60%, transparent); }
    .compact-list small, .compact-list .muted { color: var(--muted); overflow-wrap: anywhere; }
    .gap-panel { display: grid; gap: 6px; border: 1px solid #d97706; border-radius: 9px; padding: 11px; background: color-mix(in srgb, #d97706 6%, var(--card-bg)); }
    .gap-row { display: grid; grid-template-columns: auto minmax(150px, auto) 1fr; gap: 8px; align-items: center; font-size: .8rem; }
    .command-bar { display: grid; grid-template-columns: 1fr auto; gap: 10px; align-items: center; border-top: 1px solid var(--border); padding-top: 12px; }
    .command-bar p { color: var(--muted); font-size: .78rem; margin-top: 3px; }
    .command-message { grid-column: 1 / -1; color: var(--accent) !important; }
    .live-events { display: grid; gap: 8px; border-top: 1px solid var(--border); padding-top: 11px; }
    .live-events header, .live-events li { display: flex; justify-content: space-between; gap: 12px; }
    .live-events header span, .live-events .muted { color: var(--muted); font-size: .78rem; }
    .live-events ol { display: grid; gap: 5px; margin: 0; padding-left: 22px; max-height: 220px; overflow: auto; }
    .live-events li { font-size: .8rem; }
    .danger-button { border-color: #dc2626; color: #dc2626; background: transparent; }
    @media (max-width: 1100px) {
      .filters { grid-template-columns: repeat(3, 1fr); }
      .summary-grid { grid-template-columns: repeat(4, 1fr); }
      .detail-grid { grid-template-columns: repeat(2, 1fr); }
    }
    @media (max-width: 700px) {
      .runtime-operations { padding: 12px; }
      .page-header, .run-header { flex-direction: column; }
      .filters, .summary-grid, .metrics, .detail-grid, .capability-grid { grid-template-columns: 1fr; }
      .outcome { text-align: left; }
      .command-bar { grid-template-columns: 1fr; }
      .gap-row { grid-template-columns: auto 1fr; }
      .gap-row span:last-child { grid-column: 1 / -1; }
    }
  `],
})
export class WorkflowRuntimeOperationsComponent implements OnInit, OnDestroy {
  private readonly api = inject(WorkflowRuntimeOperationsApiService);
  private readonly stream = inject(WorkflowRuntimeStreamService);
  private readonly system = inject(SystemFacade);
  private readonly cdr = inject(ChangeDetectorRef);
  private readonly subscriptions = new Subscription();
  private loadSubscription: Subscription | null = null;
  private capabilitySubscription: Subscription | null = null;
  private liveSubscription: Subscription | null = null;
  private hubUrl = '';

  loading = true;
  forbidden = false;
  errorCode = '';
  snapshot: RuntimeOperationsResponse | null = null;
  capabilityMatrix: RuntimeCapabilityMatrixProjection | null = null;
  capabilityError = '';
  pendingRunId = '';
  commandMessages: Record<string, string> = {};
  liveRunId = '';
  liveCursor = '';
  liveError = '';
  liveEvents: WorkflowRuntimeStreamFrame[] = [];
  filters: RuntimeOperationsFilters = this.emptyFilters();

  ngOnInit(): void {
    this.hubUrl = this.system.resolveHubAgent()?.url || '';
    if (!this.hubUrl) {
      this.loading = false;
      this.errorCode = 'hub_not_configured';
      return;
    }
    this.load();
  }

  ngOnDestroy(): void {
    this.loadSubscription?.unsubscribe();
    this.capabilitySubscription?.unsubscribe();
    this.liveSubscription?.unsubscribe();
    this.subscriptions.unsubscribe();
  }

  load(): void {
    if (!this.hubUrl) return;
    this.loadCapabilities();
    this.loadSubscription?.unsubscribe();
    this.loading = true;
    this.forbidden = false;
    this.errorCode = '';
    this.loadSubscription = this.api.list(this.hubUrl, this.filters).subscribe({
      next: (snapshot) => {
        this.snapshot = snapshot;
        this.loading = false;
        this.cdr.markForCheck();
      },
      error: (error) => {
        const details = this.errorDetails(error);
        this.loading = false;
        this.forbidden = details.status === 401 || details.status === 403;
        this.errorCode = details.reason;
        this.cdr.markForCheck();
      },
    });
  }

  resetFilters(): void {
    this.filters = this.emptyFilters();
    this.load();
  }

  runtimeOptions(): string[] {
    const values = new Set(['ananta-native', 'langgraph', 'temporal']);
    for (const runtime of this.capabilityMatrix?.runtimes || []) values.add(runtime.runtime_id);
    for (const run of this.snapshot?.runs || []) values.add(run.runtime);
    return Array.from(values).filter(Boolean).sort();
  }

  modeOptions(): string[] {
    const values = new Set(['compiled', 'durable', 'manual', 'dry_run']);
    for (const run of this.snapshot?.runs || []) values.add(run.mode);
    return Array.from(values).filter(Boolean).sort();
  }

  outcomeLabel(run: WorkflowRuntimeOperationRun): string {
    if (run.outcome_claim === 'unverified') return 'Unbestätigt · Evidence fehlt';
    const labels: Record<string, string> = {
      completed: 'Bestätigt abgeschlossen',
      succeeded: 'Bestätigt abgeschlossen',
      success: 'Bestätigt abgeschlossen',
      running: 'Laufend',
      failed: 'Fehlgeschlagen',
      cancelled: 'Abgebrochen',
      pending: 'Ausstehend',
    };
    return labels[run.outcome_claim] || run.outcome_claim;
  }

  formatCost(micros: number): string {
    return `${(Number(micros || 0) / 1_000_000).toFixed(4)} CU`;
  }

  formatLatency(milliseconds: number): string {
    return `${Number(milliseconds || 0).toFixed(1)} ms`;
  }

  formatTimestamp(seconds: number): string {
    if (!seconds) return 'unbekannt';
    return new Date(seconds * 1000).toLocaleString('de-DE');
  }

  recoveryDetail(run: WorkflowRuntimeOperationRun): string {
    const recovery = run.recovery;
    const parts = [recovery.strategy, recovery.reason_code, recovery.attempts ? `${recovery.attempts} Versuch(e)` : '']
      .filter(Boolean);
    return parts.join(' · ') || 'keine Recovery aktiv';
  }

  combinedGaps(run: WorkflowRuntimeOperationRun) {
    return [...run.parity_gaps, ...run.semantic_deviations];
  }

  toggleLiveStream(run: WorkflowRuntimeOperationRun): void {
    if (this.liveRunId === run.run_id) {
      this.closeLiveStream();
      return;
    }
    this.closeLiveStream();
    if (!run.workflow_id || !this.hubUrl) return;
    this.liveRunId = run.run_id;
    this.liveError = '';
    this.liveEvents = [];
    this.liveCursor = '';
    this.liveSubscription = this.stream.connect(this.hubUrl, run.workflow_id).subscribe({
      next: (event) => {
        this.liveCursor = event.cursor;
        if (event.event_type !== 'workflow.stream.heartbeat') {
          this.liveEvents = [...this.liveEvents, event].slice(-50);
        }
        this.cdr.markForCheck();
      },
      error: (error) => {
        this.liveError = this.errorDetails(error).reason;
        this.cdr.markForCheck();
      },
    });
  }

  private closeLiveStream(): void {
    this.liveSubscription?.unsubscribe();
    this.liveSubscription = null;
    this.liveRunId = '';
    this.liveCursor = '';
    this.liveError = '';
    this.liveEvents = [];
    this.cdr.markForCheck();
  }

  canCommand(run: WorkflowRuntimeOperationRun, commandType: RuntimeCommandType): boolean {
    return Boolean(this.commandContext(run, commandType));
  }

  commandPending(run: WorkflowRuntimeOperationRun): boolean {
    return this.pendingRunId === run.run_id;
  }

  commandHint(run: WorkflowRuntimeOperationRun): string {
    if (run.stale) return 'Gesperrt: das Hub-Read-Model ist stale.';
    if (!run.task_id) return 'Gesperrt: keine Hub-Task-Bindung.';
    if (!run.evidence.some((item) => item.verification_status === 'verified')) {
      return 'Gesperrt: verifizierte Evidence fehlt.';
    }
    if (!run.gates.some((gate) => gate.status === 'approved' && gate.approval_id)) {
      return 'Gesperrt: laufgebundene Approval fehlt.';
    }
    return 'Commands laufen ausschließlich über den Hub und werden auditiert.';
  }

  sendCommand(run: WorkflowRuntimeOperationRun, commandType: RuntimeCommandType): void {
    const context = this.commandContext(run, commandType);
    if (!context || this.pendingRunId) return;
    this.pendingRunId = run.run_id;
    this.commandMessages[run.run_id] = '';
    const idempotencyKey = globalThis.crypto?.randomUUID?.()
      || `runtime-ops-${run.run_id}-${commandType}-${Date.now()}`;
    const subscription = this.api.command(
      this.hubUrl,
      run.run_id,
      {
        type: commandType,
        approval_id: context.gate.approval_id || '',
        evidence_refs: context.evidenceRefs,
      },
      idempotencyKey,
    ).subscribe({
      next: (response) => {
        this.pendingRunId = '';
        this.commandMessages[run.run_id] = `Hub-Command ${response.command.command_id} · ${response.command.status}`;
        this.cdr.markForCheck();
        this.load();
      },
      error: (error) => {
        this.pendingRunId = '';
        this.commandMessages[run.run_id] = `Command blockiert: ${this.errorDetails(error).reason}`;
        this.cdr.markForCheck();
      },
    });
    this.subscriptions.add(subscription);
  }

  private commandContext(
    run: WorkflowRuntimeOperationRun,
    commandType: RuntimeCommandType,
  ): { gate: RuntimeGateView; evidenceRefs: string[] } | null {
    if (run.stale || !run.task_id) return null;
    const verified = new Set(
      run.evidence
        .filter((item) => item.verification_status === 'verified')
        .map((item) => item.evidence_id),
    );
    if (!verified.size) return null;
    const now = Date.now() / 1000;
    const gate = run.gates.find((item) => (
      item.status === 'approved'
      && Boolean(item.approval_id)
      && (!item.expires_at || item.expires_at > now)
      && (!item.allowed_commands.length || item.allowed_commands.includes(commandType))
      && item.required_evidence_refs.every((reference) => verified.has(reference))
    ));
    if (!gate) return null;
    const evidenceRefs = gate.required_evidence_refs.length
      ? gate.required_evidence_refs
      : [Array.from(verified)[0]];
    return { gate, evidenceRefs };
  }

  private emptyFilters(): RuntimeOperationsFilters {
    return { runtime: '', mode: '', status: '', health: '', q: '' };
  }

  private loadCapabilities(): void {
    this.capabilitySubscription?.unsubscribe();
    this.capabilityError = '';
    this.capabilitySubscription = this.api.capabilities(this.hubUrl).subscribe({
      next: (projection) => {
        this.capabilityMatrix = projection;
        this.cdr.markForCheck();
      },
      error: (error) => {
        this.capabilityMatrix = null;
        this.capabilityError = this.errorDetails(error).reason;
        this.cdr.markForCheck();
      },
    });
  }

  private errorDetails(error: unknown): { status: number; reason: string } {
    const raw = (error || {}) as {
      status?: number;
      message?: string;
      error?: {
        reason_code?: string;
        message?: string;
        data?: { reason_code?: string };
      };
    };
    return {
      status: Number(raw.status || 0),
      reason: String(
        raw.error?.reason_code
        || raw.error?.data?.reason_code
        || raw.error?.message
        || raw.message
        || 'workflow_runtime_operations_unavailable',
      ),
    };
  }
}
