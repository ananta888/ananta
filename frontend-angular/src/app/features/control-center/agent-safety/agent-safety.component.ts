import { ChangeDetectionStrategy, ChangeDetectorRef, Component, OnDestroy, OnInit, inject } from '@angular/core';
import { Subscription, finalize } from 'rxjs';

import { ControlCenterStateFacade } from '../services/control-center-state.facade';
import { AgentSafetyApiService } from './agent-safety-api.service';
import { AgentSafetyOverview } from './agent-safety.models';

@Component({
  standalone: true,
  selector: 'app-agent-safety',
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
    .status,.card{display:block;width:100%;box-sizing:border-box;border:1px solid #334155;border-radius:10px;padding:12px;margin:10px 0;background:transparent;color:inherit;text-align:left}
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
      next: (value) => { this.overview = value; this.error = ''; },
      error: () => { this.error = 'Agent-Safety-Status nicht verfügbar'; },
    });
  }

  short(value: string): string { return String(value || '').slice(0, 12); }

  percent(value: number): string { return `${Math.round(Number(value || 0) * 100)} %`; }
}
