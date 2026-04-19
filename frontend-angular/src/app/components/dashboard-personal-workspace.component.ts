import { Component, EventEmitter, Input, Output } from '@angular/core';

import { GoalListEntry } from '../models/dashboard.models';
import { MetricCardComponent } from '../shared/ui/display';
import { SectionCardComponent } from '../shared/ui/layout';
import { DemoPreviewExample } from './dashboard-demo-preview.component';

@Component({
  standalone: true,
  selector: 'app-dashboard-personal-workspace',
  imports: [MetricCardComponent, SectionCardComponent],
  template: `
    <app-section-card
      class="block mb-md"
      eyebrow="Mein Arbeitsbereich"
      title="Willkommen zurueck"
      subtitle="Hier findest du deine letzten Ziele, naechsten Aufgaben und passende Startvorlagen."
    >
      <button section-actions class="secondary btn-small" type="button" (click)="newGoal.emit()">Neues Ziel</button>
      <div class="grid cols-3 mt-md">
        <app-metric-card label="Laufende Goals" [value]="activeGoalCount" hint="Noch nicht abgeschlossene Ziele."></app-metric-card>
        <app-metric-card label="Naechste Aufgaben" [value]="nextTaskCount" hint="Offen, blockiert oder in Arbeit."></app-metric-card>
        <app-metric-card label="Erster Fortschritt" [value]="starterDone + '/' + starterTotal" [hint]="starterLabel" tone="success"></app-metric-card>
      </div>
      <div class="grid cols-2 mt-md">
        <div>
          <h4 class="no-margin">Zuletzt bearbeitet</h4>
          @if (recentGoals.length) {
            <div class="grid gap-sm mt-sm">
              @for (goal of recentGoals; track goal.id) {
                <button class="card card-light text-left personal-list-item" type="button" (click)="openGoal.emit(goal.id)">
                  <strong>{{ goal.summary || goal.goal || goal.id }}</strong>
                  <span class="muted font-sm">{{ goal.status || 'unbekannt' }}</span>
                </button>
              }
            </div>
          } @else {
            <div class="empty-state compact mt-sm">
              <strong>Noch kein eigenes Ziel sichtbar.</strong>
              <p class="muted no-margin mt-sm">Starte oben mit einem Ziel oder oeffne eine Vorlage.</p>
            </div>
          }
        </div>
        <div>
          <h4 class="no-margin">Vorlagen fuer den Start</h4>
          <div class="grid gap-sm mt-sm">
            @for (preset of presets; track preset.id) {
              <button class="card card-light text-left personal-list-item" type="button" (click)="applyPreset.emit(preset)">
                <strong>{{ preset.title }}</strong>
                <span class="muted font-sm">{{ preset.outcome }}</span>
              </button>
            }
          </div>
        </div>
      </div>
    </app-section-card>
  `,
})
export class DashboardPersonalWorkspaceComponent {
  @Input() activeGoalCount = 0;
  @Input() nextTaskCount = 0;
  @Input() starterDone = 0;
  @Input() starterTotal = 0;
  @Input() starterLabel = '';
  @Input() recentGoals: GoalListEntry[] = [];
  @Input() presets: DemoPreviewExample[] = [];

  @Output() newGoal = new EventEmitter<void>();
  @Output() openGoal = new EventEmitter<string>();
  @Output() applyPreset = new EventEmitter<DemoPreviewExample>();
}
