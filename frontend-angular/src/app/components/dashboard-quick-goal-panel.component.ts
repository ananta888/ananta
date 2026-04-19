import { Component, EventEmitter, Input, Output } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { NextStepAction } from '../shared/ui/display';
import { FormFieldComponent, PresetOption, PresetPickerComponent } from '../shared/ui/forms';
import { ExplanationNoticeComponent, NextStepsComponent, SafetyNoticeComponent } from '../shared/ui/display';

export interface QuickGoalResult {
  tasks_created: number;
  task_ids: string[];
  goal_id?: string;
}

@Component({
  standalone: true,
  selector: 'app-dashboard-quick-goal-panel',
  imports: [
    FormsModule,
    RouterLink,
    ExplanationNoticeComponent,
    FormFieldComponent,
    NextStepsComponent,
    PresetPickerComponent,
    SafetyNoticeComponent,
  ],
  template: `
    <h3 class="no-margin">Ziel planen</h3>
    <p class="muted font-sm mt-sm">Starte einfach mit einem Ziel. Gefuehrte Modi bleiben fuer strukturierte Faelle verfuegbar.</p>
    @if (showHint) {
      <app-explanation-notice class="block mt-sm inline-help" message='Ein gutes Ziel beschreibt Ergebnis und Grenze, zum Beispiel: "Analysiere nur das Frontend und schlage drei naechste Schritte vor."'>
        <button class="secondary btn-small" type="button" (click)="dismissHint.emit()">Ausblenden</button>
      </app-explanation-notice>
    }

    <app-preset-picker
      class="block mt-sm"
      [presets]="presets"
      ariaLabel="Goal-Vorlagen"
      (selectPreset)="selectPreset.emit($event.id)"
    ></app-preset-picker>

    <div class="row gap-sm mt-sm flex-end">
      <div class="flex-1">
        <app-form-field label="Quick Goal" hint="Ein Satz reicht fuer den ersten planbaren Hub-Auftrag.">
          <input
            [ngModel]="text"
            (ngModelChange)="textChange.emit($event)"
            placeholder="z.B. Analysiere dieses Repository und schlage die naechsten Schritte vor"
            class="w-full"
            aria-label="Quick Goal Beschreibung eingeben"
          />
        </app-form-field>
      </div>
      <button type="button" (click)="submit.emit()" [disabled]="busy || !text.trim()" aria-label="Goal planen und Tasks generieren">
        @if (busy) {
          Generiere...
        } @else {
          Goal planen
        }
      </button>
      <button class="secondary" [routerLink]="['/auto-planner']" aria-label="Zur Auto-Planner Konfiguration navigieren">Mehr Optionen</button>
    </div>
    @if (result) {
      <app-safety-notice class="block mt-sm" title="Goal wurde geplant" [message]="result.tasks_created + ' Tasks erstellt.'" tone="success"></app-safety-notice>
      <div class="card-success mt-sm">
        <div class="row space-between">
          <span><strong>{{ result.tasks_created }}</strong> Tasks erstellt</span>
          <div class="row gap-sm">
            @if (result.goal_id) {
              <button class="secondary btn-small" type="button" (click)="openGoal.emit(result.goal_id)">Zum Goal Detail</button>
            }
            <button class="secondary btn-small" type="button" (click)="openBoard.emit()">Zum Board</button>
          </div>
        </div>
        @if (result.task_ids?.length) {
          <div class="muted status-text-sm">
            Task IDs: {{ result.task_ids.slice(0, 3).join(', ') }}{{ result.task_ids.length > 3 ? '...' : '' }}
          </div>
        }
      </div>
      <app-next-steps class="block mt-sm" [steps]="nextSteps" (selectStep)="selectNextStep.emit($event)"></app-next-steps>
    }
  `,
})
export class DashboardQuickGoalPanelComponent {
  @Input() text = '';
  @Input() busy = false;
  @Input() result: QuickGoalResult | null = null;
  @Input() presets: PresetOption[] = [];
  @Input() nextSteps: NextStepAction[] = [];
  @Input() showHint = false;

  @Output() textChange = new EventEmitter<string>();
  @Output() dismissHint = new EventEmitter<void>();
  @Output() selectPreset = new EventEmitter<string>();
  @Output() submit = new EventEmitter<void>();
  @Output() openGoal = new EventEmitter<string>();
  @Output() openBoard = new EventEmitter<void>();
  @Output() selectNextStep = new EventEmitter<NextStepAction>();
}
