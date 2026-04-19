import { Component, OnInit, inject } from '@angular/core';
import { Router, RouterLink } from '@angular/router';

import { ControlPlaneFacade } from '../features/control-plane/control-plane.facade';
import { GoalListEntry } from '../models/dashboard.models';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { DecisionExplanationComponent, MetricCardComponent } from '../shared/ui/display';
import { ActionCardComponent, PageIntroComponent, SectionCardComponent } from '../shared/ui/layout';
import { EmptyStateComponent, ErrorStateComponent, LoadingStateComponent } from '../shared/ui/state';

@Component({
  standalone: true,
  selector: 'app-personal-workspace',
  imports: [
    RouterLink,
    ActionCardComponent,
    DecisionExplanationComponent,
    EmptyStateComponent,
    ErrorStateComponent,
    LoadingStateComponent,
    MetricCardComponent,
    PageIntroComponent,
    SectionCardComponent,
  ],
  template: `
    <app-page-intro
      title="Mein Arbeitsbereich"
      subtitle="Plane Ziele, pruefe Ergebnisse und starte die naechste sinnvolle Aktion ohne technische Umwege."
    >
      <div intro-actions class="row gap-sm">
        <button class="primary" type="button" (click)="goPlan()">Ziel planen</button>
        <button class="secondary" type="button" [routerLink]="['/artifacts']">Ergebnisse ansehen</button>
      </div>
    </app-page-intro>

    <div class="start-actions mb-md">
      <app-action-card title="Planen" description="Aus einem Ziel konkrete Aufgaben machen." [routerLink]="['/dashboard']"></app-action-card>
      <app-action-card title="Diagnostizieren" description="Fehlerbild beschreiben und Pruefpfad starten." [routerLink]="['/dashboard']"></app-action-card>
      <app-action-card title="Reviewen" description="Aenderungen, Risiken und Tests bewerten." [routerLink]="['/dashboard']"></app-action-card>
      <app-action-card title="Ergebnisse" description="Resultate, Dateien und Zusammenfassungen oeffnen." [routerLink]="['/artifacts']"></app-action-card>
    </div>

    <div class="grid cols-3 mb-md">
      <app-metric-card label="Meine Ziele" [value]="goals.length" hint="Zuletzt bekannte Ziele."></app-metric-card>
      <app-metric-card label="Offene Arbeit" [value]="openGoalCount()" hint="Noch nicht abgeschlossen."></app-metric-card>
      <app-metric-card label="Vorlagen" value="3" hint="Schnelle Einstiege fuer typische Arbeit." tone="success"></app-metric-card>
    </div>

    <app-decision-explanation class="block mb-md" kind="verification"></app-decision-explanation>

    @if (loading) {
      <app-loading-state label="Arbeitsbereich wird geladen" [count]="1" [lineCount]="2" lineClass="skeleton block"></app-loading-state>
    } @else if (error) {
      <app-error-state title="Arbeitsbereich konnte nicht geladen werden" [message]="error" retryLabel="Erneut versuchen" (retry)="loadGoals()"></app-error-state>
    } @else {
      <app-section-card title="Zuletzt bearbeitet" subtitle="Oeffne ein Ziel oder starte mit einer neuen Aufgabe.">
        <button section-actions class="secondary btn-small" type="button" (click)="goPlan()">Neues Ziel</button>
        @if (goals.length) {
          <div class="grid gap-sm mt-md">
            @for (goal of goals.slice(0, 6); track goal.id) {
              <button class="card card-light text-left personal-list-item" type="button" (click)="openGoal(goal.id)">
                <strong>{{ goal.summary || goal.goal || goal.id }}</strong>
                <span class="muted font-sm">{{ friendlyStatus(goal.status) }}</span>
              </button>
            }
          </div>
        } @else {
          <app-empty-state
            title="Noch kein Ziel sichtbar"
            description="Starte mit Planen, Diagnose oder Review. Ananta erzeugt daraus nachvollziehbare Arbeitsschritte."
            primaryLabel="Ziel planen"
            secondaryLabel="Vorlagen ansehen"
            (primary)="goPlan()"
            (secondary)="goTemplates()"
          ></app-empty-state>
        }
      </app-section-card>
    }
  `,
})
export class PersonalWorkspaceComponent implements OnInit {
  private dir = inject(AgentDirectoryService);
  private hubApi = inject(ControlPlaneFacade);
  private router = inject(Router);

  goals: GoalListEntry[] = [];
  loading = false;
  error = '';

  ngOnInit(): void {
    this.loadGoals();
  }

  loadGoals(): void {
    const hub = this.dir.list().find(agent => agent.role === 'hub');
    if (!hub?.url) {
      this.error = 'Kein Hub konfiguriert. Oeffne die Agentenverwaltung oder starte Ananta lokal.';
      return;
    }
    this.loading = true;
    this.error = '';
    this.hubApi.listGoals(hub.url).subscribe({
      next: goals => {
        this.goals = Array.isArray(goals) ? goals : [];
        this.loading = false;
      },
      error: err => {
        this.error = err?.error?.message || err?.message || 'Goals konnten nicht geladen werden.';
        this.loading = false;
      },
    });
  }

  openGoal(goalId: string): void {
    this.router.navigate(['/goal', goalId]);
  }

  goPlan(): void {
    this.router.navigate(['/dashboard'], { fragment: 'quick-goal' });
  }

  goTemplates(): void {
    this.router.navigate(['/templates']);
  }

  openGoalCount(): number {
    return this.goals.filter(goal => !['completed', 'failed', 'cancelled'].includes(String(goal?.status || '').toLowerCase())).length;
  }

  friendlyStatus(status: string | undefined): string {
    const normalized = String(status || '').toLowerCase();
    if (normalized === 'completed') return 'abgeschlossen';
    if (normalized === 'failed') return 'fehlgeschlagen';
    if (normalized === 'in_progress') return 'in Arbeit';
    return status || 'offen';
  }
}
