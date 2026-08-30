import { ChangeDetectionStrategy, ChangeDetectorRef, Component, OnDestroy, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Subscription, finalize } from 'rxjs';

import { ControlCenterStateFacade } from '../services/control-center-state.facade';
import { AgentSafetyApiService } from './agent-safety-api.service';
import { AgentSafetyOverview, AgentSafetyPolicyCommand } from './agent-safety.models';

@Component({
  standalone: true,
  selector: 'app-agent-safety',
  imports: [FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section data-testid="agent-safety-dashboard">
      <header>
        <div>
          <p class="eyebrow">Hub Control Plane</p>
          <h2>Agent Safety</h2>
          <p>Run-gebundene Boundary Events, externe Abschaltung, Incident-Artefakte und Replay-Gates.</p>
        </div>
        <button type="button" (click)="load()" [disabled]="loading || !projectId">Aktualisieren</button>
      </header>
      @if (!projectId) { <p>Bitte ein Projekt auswählen.</p> }
      @if (error) { <p role="alert" class="danger">{{ error }}</p> }
      @if (overview) {
        <div class="status" [class.danger]="!overview.containment_available">
          Technische Containment-Adapter:
          {{ overview.containment_available ? 'vollständig verdrahtet' : 'nicht verfügbar – Ausführung bleibt fail-closed' }}
        </div>
        <form class="policy" (ngSubmit)="savePolicy()" aria-label="Agent-Safety-Policy">
          <h3>Automatisch vorautorisierte Hub-Policy</h3>
          <label>Policy <input name="policyId" [(ngModel)]="policy.policy_id" required></label>
          <label>Revision <input name="revision" type="number" min="1" [(ngModel)]="policy.revision"></label>
          <label>Modus <select name="mode" [(ngModel)]="policy.mode">
            <option value="enforce">Enforce</option><option value="observe_only">Observe</option>
            <option value="adversarial_eval">Red Team</option><option value="disabled">Disabled</option>
          </select></label>
          <label><input name="prevention" type="checkbox" [(ngModel)]="policy.preventive_policy_enabled"> Prävention</label>
          <label><input name="training" type="checkbox" [(ngModel)]="policy.preventive_training_enabled"> Training-Outbox</label>
          <label><input name="sentinel" type="checkbox" [(ngModel)]="policy.sentinel_enabled"> Sentinel</label>
          <label><input name="freeze" type="checkbox" [(ngModel)]="policy.incident_freeze_enabled"> Incident Freeze</label>
          <label><input name="redTeam" type="checkbox" [(ngModel)]="policy.adversarial_evaluation_enabled"> Red-Team-Modus</label>
          <label>Lokale Ziele <input name="scope" [(ngModel)]="adversarialScope" placeholder="local:fixture"></label>
          <label>Stop-Scope <select name="stopScope" [(ngModel)]="policy.global_stop_scope">
            <option value="agent">Agent</option><option value="sandbox">Sandbox</option>
            <option value="run">Run</option><option value="group">Gruppe</option>
          </select></label>
          <label>Max. Agenten <input name="parallel" type="number" min="1" max="100" [(ngModel)]="policy.max_parallel_agents"></label>
          <span>Telemetrie und externer Kill-Switch bleiben zwingend aktiv.</span>
          <button type="submit" [disabled]="loading">Policy automatisch prüfen und speichern</button>
        </form>
        <div class="metrics" aria-label="Safety-Metriken">
          <span>Incidents <b>{{ overview.metrics.incident_count }}</b></span>
          <span>Offene Findings <b>{{ overview.metrics.open_critical_findings }}</b></span>
          <span>Externe Beobachtungen <b>{{ overview.metrics.external_observations }}</b></span>
          <span>Selbstmeldungen <b>{{ overview.metrics.self_reports }}</b></span>
          <span>Containment-Fehler <b>{{ overview.metrics.containment_receipt_failures }}</b></span>
          <span>Replay-Abdeckung <b>{{ percent(overview.metrics.incident_replay_coverage) }}</b></span>
        </div>
        <div class="grid">
          <section>
            <h3>Runs</h3>
            @for (run of overview.runs; track run.run_id) {
              <button type="button" class="card" (click)="load(run.run_id)">
                <strong>{{ run.run_id }}</strong>
                <span>{{ run.mode }} · {{ run.state }}</span>
                <small>{{ run.agents.length }} Agent(en) · Policy {{ run.policy_id }}:{{ run.policy_revision }}</small>
                <b [class.danger]="!run.execution_allowed">
                  {{ run.execution_allowed ? 'Ausführung aktiv' : 'Ausführung gesperrt' }}
                </b>
              </button>
            }
          </section>
          <section>
            <h3>Incidents</h3>
            @for (incident of overview.incidents; track incident.bundle_id) {
              <article class="card">
                <strong>{{ incident.bundle_id }}</strong>
                <span>{{ incident.run_id }} · {{ incident.event_count }} Events</span>
                <small>{{ short(incident.bundle_digest) }}</small>
              </article>
            }
          </section>
          <section>
            <h3>Boundary & Trigger Events</h3>
            @for (event of overview.events; track event.event_id) {
              <article class="card">
                <strong [class.danger]="event.severity === 'critical'">{{ event.event_type }}</strong>
                <span>{{ event.severity }} · {{ event.source }}</span>
                <small>{{ event.observed_at }} · {{ short(event.event_digest) }}</small>
              </article>
            }
          </section>
        </div>
      }
    </section>
  `,
  styles: [`
    header{display:flex;align-items:center;justify-content:space-between;gap:1rem;flex-wrap:wrap}
    .grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:1rem}.grid>section{min-width:0}
    .status,.card,.policy{display:block;width:100%;box-sizing:border-box;border:1px solid #334155;border-radius:10px;padding:12px;margin:10px 0;background:transparent;color:inherit;text-align:left}
    .policy{display:flex;gap:.75rem;flex-wrap:wrap;align-items:end}.policy h3{width:100%;margin:.25rem 0}.policy label{display:grid;gap:.25rem}.policy span{width:100%;color:#94a3b8}
    .metrics{display:flex;gap:.75rem;flex-wrap:wrap}.metrics span{border:1px solid #334155;border-radius:8px;padding:.5rem}.metrics b{margin-left:.25rem}
    .card span,.card small,.card b{display:block;margin-top:5px}.eyebrow{color:#60a5fa}.danger{color:#fca5a5}
    small{color:#94a3b8}@media(max-width:1000px){.grid{grid-template-columns:1fr}}
  `],
})
export class AgentSafetyComponent implements OnInit, OnDestroy {
  private readonly api = inject(AgentSafetyApiService);
  private readonly state = inject(ControlCenterStateFacade);
  private readonly cdr = inject(ChangeDetectorRef);
  private projectSubscription?: Subscription;
  projectId = '';
  overview: AgentSafetyOverview | null = null;
  loading = false;
  error = '';
  adversarialScope = 'local:fixture';
  policy: AgentSafetyPolicyCommand = {
    policy_id: 'agent-safety-default', revision: 1, mode: 'enforce',
    preventive_policy_enabled: true, preventive_training_enabled: false, sentinel_enabled: true,
    telemetry_enabled: true, external_kill_switch_enabled: true, incident_freeze_enabled: true,
    adversarial_evaluation_enabled: false, adversarial_scope: [], global_stop_scope: 'run',
    max_parallel_agents: 1, automatic_authorization: true,
  };

  ngOnInit(): void {
    this.projectSubscription = this.state.selectedProjectId$.subscribe((projectId) => {
      this.projectId = projectId;
      this.overview = null;
      if (projectId) this.load();
      this.cdr.markForCheck();
    });
  }

  ngOnDestroy(): void { this.projectSubscription?.unsubscribe(); }

  load(runId?: string): void {
    const hubUrl = this.state.hubBaseUrl();
    if (!hubUrl || !this.projectId) { this.error = 'Hub oder Projekt nicht verfügbar'; return; }
    this.loading = true;
    this.api.overview(hubUrl, this.projectId, runId).pipe(
      finalize(() => { this.loading = false; this.cdr.markForCheck(); }),
    ).subscribe({
      next: (value) => { this.overview = value; this.syncPolicy(value); this.error = ''; },
      error: () => { this.error = 'Agent-Safety-Status nicht verfügbar'; },
    });
  }

  savePolicy(): void {
    const hubUrl = this.state.hubBaseUrl();
    if (!hubUrl) { this.error = 'Hub nicht verfügbar'; return; }
    this.loading = true;
    const command = {
      ...this.policy,
      adversarial_scope: this.adversarialScope.split(',').map((value) => value.trim()).filter(Boolean),
    };
    this.api.configurePolicy(hubUrl, command).pipe(
      finalize(() => { this.loading = false; this.cdr.markForCheck(); }),
    ).subscribe({
      next: () => this.load(),
      error: () => { this.error = 'Policy wurde von der automatischen Hub-Policy abgelehnt'; },
    });
  }

  private syncPolicy(value: AgentSafetyOverview): void {
    const current = value.policies?.at(-1);
    if (!current) return;
    this.policy = {
      policy_id: current.policy_id, revision: current.policy_revision + 1, mode: current.mode,
      preventive_policy_enabled: current.preventive_policy_enabled,
      preventive_training_enabled: current.preventive_training_enabled, sentinel_enabled: current.sentinel_enabled !== false,
      telemetry_enabled: true, external_kill_switch_enabled: true,
      incident_freeze_enabled: current.incident_freeze_enabled,
      adversarial_evaluation_enabled: current.adversarial_evaluation_enabled,
      adversarial_scope: [...current.adversarial_scope], global_stop_scope: current.global_stop_scope,
      max_parallel_agents: current.max_parallel_agents, automatic_authorization: true,
    };
    this.adversarialScope = current.adversarial_scope.join(', ');
  }

  short(value: string): string { return String(value || '').slice(0, 12); }

  percent(value: number): string { return `${Math.round(Number(value || 0) * 100)} %`; }
}
