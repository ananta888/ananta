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

export interface QuickGoalExpectation {
  title: string;
  goodInput: string;
  expectedResult: string;
  nextAction: string;
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
    <p class="muted font-sm mt-sm">Starte mit einem Ergebnis, das der Hub in pruefbare Aufgaben uebersetzen kann.</p>
    @if (showHint) {
      <app-explanation-notice class="block mt-sm inline-help" title="Gutes Ziel" message='Beschreibe Ergebnis, Grenze und gewuenschte Pruefung, zum Beispiel: "Analysiere nur das Frontend und schlage drei naechste Schritte vor."'>
        <button class="secondary btn-small" type="button" (click)="dismissHint.emit()">Ausblenden</button>
      </app-explanation-notice>
    }

    <app-preset-picker
      class="block mt-sm"
      [presets]="presets"
      ariaLabel="Goal-Vorlagen"
      (selectPreset)="selectPreset.emit($event.id)"
    ></app-preset-picker>

    @if (expectation) {
      <section class="quick-goal-expectation mt-sm" aria-label="Erwartetes Ergebnis">
        <div>
          <span class="muted font-sm">Passender Input</span>
          <strong>{{ expectation.goodInput }}</strong>
        </div>
        <div>
          <span class="muted font-sm">Erwartetes Ergebnis</span>
          <strong>{{ expectation.expectedResult }}</strong>
        </div>
        <div>
          <span class="muted font-sm">Danach sinnvoll</span>
          <strong>{{ expectation.nextAction }}</strong>
        </div>
      </section>
    }

    <div class="row gap-sm mt-sm flex-end">
      <div class="flex-1">
        <app-form-field label="Zielbeschreibung" hint="Ein Satz reicht, wenn Ergebnis und Grenze klar sind.">
          <input
            [ngModel]="text"
            (ngModelChange)="textChange.emit($event)"
            placeholder="z.B. Analysiere nur das Frontend und schlage drei naechste Schritte vor"
            class="w-full"
            aria-label="Zielbeschreibung eingeben"
          />
        </app-form-field>
      </div>
      <button type="button" (click)="submit.emit()" [disabled]="busy || !text.trim()" aria-label="Goal planen und Tasks generieren">
        @if (busy) {
          Plane...
        } @else {
          Goal planen
        }
      </button>
      <button class="secondary" [routerLink]="['/auto-planner']" aria-label="Zum gefuehrten Assistenten navigieren">Assistent</button>
    </div>
    @if (!busy && !result && !error && nextSteps.length) {
      <app-next-steps
        class="block mt-sm"
        title="Offizieller UI-Weg"
        description="Erst planen, dann Aufgaben verfolgen, danach Ergebnisse pruefen."
        [steps]="nextSteps"
        (selectStep)="selectNextStep.emit($event)"
      ></app-next-steps>
    }
    @if (result) {
      <app-safety-notice class="block mt-sm" title="Plan steht bereit" [message]="result.tasks_created + ' Aufgaben wurden angelegt und koennen jetzt verfolgt werden.'" tone="success"></app-safety-notice>
      <div class="card-success mt-sm">
        <div class="row space-between">
          <span><strong>{{ result.tasks_created }}</strong> Aufgaben fuer dieses Ziel</span>
          <div class="row gap-sm">
            @if (result.goal_id) {
              <button class="secondary btn-small" type="button" (click)="openGoal.emit(result.goal_id)">Ziel pruefen</button>
            }
            <button class="secondary btn-small" type="button" (click)="openBoard.emit()">Aufgaben ansehen</button>
          </div>
        </div>
        @if (result.task_ids?.length) {
          <details class="muted status-text-sm mt-sm">
            <summary>Interne Referenzen</summary>
            {{ result.task_ids.slice(0, 3).join(', ') }}{{ result.task_ids.length > 3 ? '...' : '' }}
          </details>
        }
      </div>
      <app-next-steps class="block mt-sm" [steps]="nextSteps" (selectStep)="selectNextStep.emit($event)"></app-next-steps>
    }
    @if (!busy && !result && error) {
      <app-safety-notice class="block mt-sm" title="Planung fehlgeschlagen" [message]="error" tone="danger"></app-safety-notice>
      <app-next-steps class="block mt-sm" [steps]="nextSteps" (selectStep)="selectNextStep.emit($event)"></app-next-steps>
    }
  `,
})
export class DashboardQuickGoalPanelComponent {
  @Input() text = '';
  @Input() busy = false;
  @Input() result: QuickGoalResult | null = null;
  @Input() error = '';
  @Input() presets: PresetOption[] = [];
  @Input() nextSteps: NextStepAction[] = [];
  @Input() showHint = false;
  @Input() expectation: QuickGoalExpectation | null = null;

  @Output() textChange = new EventEmitter<string>();
  @Output() dismissHint = new EventEmitter<void>();
  @Output() selectPreset = new EventEmitter<string>();
  @Output() submit = new EventEmitter<void>();
  @Output() openGoal = new EventEmitter<string>();
  @Output() openBoard = new EventEmitter<void>();
  @Output() selectNextStep = new EventEmitter<NextStepAction>();
}
