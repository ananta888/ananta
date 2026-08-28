import { ChangeDetectionStrategy, ChangeDetectorRef, Component, OnDestroy, OnInit, inject } from '@angular/core';
import { finalize, Subscription } from 'rxjs';

import { ControlCenterStateFacade } from '../services/control-center-state.facade';
import { ScrumImprovementApiService } from './scrum-improvement-api.service';
import { EffectView, ScrumImprovementOverview } from './scrum-improvement.models';

@Component({
  standalone: true,
  selector: 'app-scrum-improvement',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section data-testid="scrum-improvement-dashboard">
      <header class="header">
        <div>
          <p class="eyebrow">Hub Control Plane</p>
          <h2>Scrum Continuous Improvement</h2>
          <p>Sprint-Steuerung, sprintübergreifende Architektur und Retrospektiven bleiben getrennte Regelkreise.</p>
        </div>
        <button type="button" (click)="load()" [disabled]="loading || !scopeId">Aktualisieren</button>
      </header>
      @if (!scopeId) { <p>Bitte ein Projekt auswählen.</p> }
      @if (error) { <p class="danger" role="alert">{{ error }}</p> }
      @if (overview) {
        <div class="summary">
          <span>{{ overview.counts.sprints }} Sprints</span>
          <span>{{ overview.counts.active_architecture_baselines }} aktive Architekturrevision</span>
          <span>{{ overview.counts.accepted_commitments }} aktive Verbesserungen</span>
          <span>{{ overview.counts.rolled_back_commitments }} automatisch zurückgerollt</span>
        </div>
        <div class="loops">
          <section>
            <h3>In-Sprint Inspect & Adapt</h3>
            @for (sprint of overview.sprints; track sprint.sprint_id) {
              <article>
                <strong>#{{ sprint.sequence }} · {{ sprint.sprint_goal }}</strong>
                <span>{{ sprint.lifecycle_state }} · Architektur {{ sprint.architecture_handoff.architecture_revision_id }}</span>
                <small>{{ sprint.scope_changes.length }} Scope-Änderungen · {{ sprint.improvement_commitment_ids.length }} Commitments</small>
              </article>
            }
          </section>
          <section>
            <h3>Architecture Feedback</h3>
            @for (baseline of overview.architecture_baselines; track baseline.revision_id) {
              <article>
                <strong>{{ baseline.revision_id }}</strong>
                <span>{{ baseline.lifecycle_state }}</span>
                <small>Parent {{ baseline.parent_revision_id || '–' }} · {{ short(baseline.guardrail_digest) }}</small>
              </article>
            }
            @for (effect of overview.architecture_effects; track effect.evaluation_id) {
              <p [class]="tone(effect)">Wirkung {{ effect.revision_id }}: {{ effect.outcome }}</p>
            }
          </section>
          <section>
            <h3>Retrospective & Commitments</h3>
            @for (commitment of overview.improvement_commitments; track commitment.commitment_id) {
              <article>
                <strong>{{ commitment.commitment_id }}</strong>
                <span>{{ commitment.status }} · {{ commitment.owner_role }}</span>
                <small>{{ commitment.metric_names.join(', ') }}</small>
              </article>
            }
            @for (effect of overview.improvement_effects; track effect.evaluation_id) {
              <p [class]="tone(effect)">Wirkung {{ effect.commitment_id }}: {{ effect.outcome }}</p>
            }
          </section>
        </div>
      }
    </section>
  `,
  styles: [`
    .header,.summary{display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap}
    .summary,article{border:1px solid #334155;border-radius:10px;padding:12px;margin:10px 0}
    .loops{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.loops>section{min-width:0}
    article span,article small{display:block;margin-top:5px}.eyebrow{color:#60a5fa}.danger,.regressed{color:#fca5a5}
    .improved{color:#86efac}.neutral,.inconclusive,small{color:#94a3b8}
    @media(max-width:1000px){.loops{grid-template-columns:1fr}}
  `],
})
export class ScrumImprovementComponent implements OnInit, OnDestroy {
  private readonly api = inject(ScrumImprovementApiService);
  private readonly state = inject(ControlCenterStateFacade);
  private readonly cdr = inject(ChangeDetectorRef);
  private projectSubscription?: Subscription;
  scopeId = '';
  overview: ScrumImprovementOverview | null = null;
  loading = false;
  error = '';

  ngOnInit(): void {
    this.projectSubscription = this.state.selectedProjectId$.subscribe((scopeId) => {
      this.scopeId = scopeId;
      this.overview = null;
      if (scopeId) this.load();
      this.cdr.markForCheck();
    });
  }

  ngOnDestroy(): void { this.projectSubscription?.unsubscribe(); }

  load(): void {
    const hubUrl = this.state.hubBaseUrl();
    if (!hubUrl || !this.scopeId) { this.error = 'Hub oder Projekt nicht verfügbar'; return; }
    this.loading = true;
    this.api.overview(hubUrl, this.scopeId).pipe(
      finalize(() => { this.loading = false; this.cdr.markForCheck(); }),
    ).subscribe({
      next: (value) => { this.overview = value; this.error = ''; },
      error: () => { this.error = 'Continuous-Improvement-Status nicht verfügbar'; },
    });
  }

  short(value: string): string { return String(value || '').slice(0, 12); }
  tone(effect: EffectView): string { return effect.outcome; }
}
