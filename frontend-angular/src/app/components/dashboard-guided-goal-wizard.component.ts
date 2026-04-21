import { Component, EventEmitter, Input, OnChanges, Output, SimpleChanges } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ExplanationNoticeComponent } from '../shared/ui/display';
import { FormFieldComponent, ModeCardOption, ModeCardPickerComponent, WizardShellComponent } from '../shared/ui/forms';

export interface GoalModeField {
  name: string;
  label: string;
  type: string;
  options?: string[];
  placeholder?: string;
  default?: unknown;
  required?: boolean;
}

export interface GoalModeDefinition extends ModeCardOption {
  fields?: GoalModeField[];
}

export interface GuidedGoalSubmit {
  mode: GoalModeDefinition;
  modeData: Record<string, unknown>;
}

interface GoalWizardStep {
  id: 'goal' | 'context' | 'execution' | 'safety' | 'review';
  title: string;
  helper: string;
}

@Component({
  standalone: true,
  selector: 'app-dashboard-guided-goal-wizard',
  imports: [FormsModule, ExplanationNoticeComponent, FormFieldComponent, ModeCardPickerComponent, WizardShellComponent],
  template: `
    <h3 class="no-margin">Gefuehrter Ziel-Assistent</h3>
    <p class="muted font-sm mt-sm">Der Assistent fragt nur die Angaben ab, die fuer Planung, Zuweisung und Pruefung noetig sind.</p>

    @if (!selectedGoalMode) {
      <app-mode-card-picker
        class="block mt-sm"
        [options]="goalModes"
        [columns]="4"
        ariaLabel="Goal-Modus auswaehlen"
        (selectOption)="setGoalMode($event)"
      ></app-mode-card-picker>
    } @else {
      <app-wizard-shell
        class="block mt-sm guided-goal-card"
        [title]="selectedGoalMode.title"
        [steps]="goalWizardSteps"
        [activeIndex]="goalWizardStepIndex"
        [canContinue]="canContinueGoalWizard()"
        [busy]="busy"
        submitLabel="Goal planen"
        busyLabel="Plane..."
        ariaLabel="Gefuehrte Zielerstellung"
        (stepSelect)="goToGoalWizardStep($event)"
        (previous)="previousGoalWizardStep()"
        (next)="nextGoalWizardStep()"
        (submit)="submitGuidedGoal()"
      >
        <button wizard-actions class="secondary btn-small" type="button" (click)="setGoalMode(null)">Zurueck</button>
          @if (activeGoalWizardStep().id === 'goal') {
            <div class="grid gap-sm">
              @for (field of visibleGoalFields(); track field.name) {
                <app-form-field [label]="field.label" [hint]="fieldHelper(field.name)" [required]="field.required !== false">
                  @if (field.type === 'textarea') {
                    <textarea [(ngModel)]="goalModeData[field.name]" class="w-full" rows="3" style="min-height: 88px;" [placeholder]="field.placeholder || 'Beschreibe, was erreicht werden soll.'"></textarea>
                  } @else if (field.type === 'select') {
                    <select [(ngModel)]="goalModeData[field.name]" class="w-full">
                      @for (opt of field.options; track opt) {
                        <option [value]="opt">{{ opt }}</option>
                      }
                    </select>
                  } @else {
                    <input [(ngModel)]="goalModeData[field.name]" [type]="field.type" [placeholder]="field.placeholder || ''" class="w-full" />
                  }
                </app-form-field>
              }
            </div>
          } @else if (activeGoalWizardStep().id === 'context') {
            <app-form-field label="Kontext und Eingabedaten" hint="Mehr Kontext reduziert Rueckfragen und hilft, Aufgaben passend zuzuweisen.">
              <textarea [(ngModel)]="goalModeData['context']" class="w-full" rows="5" placeholder="Links, Dateien, Fehlermeldungen, Repo-Bereich oder wichtige Einschraenkungen"></textarea>
            </app-form-field>
          } @else if (activeGoalWizardStep().id === 'execution') {
            <div class="grid cols-3 gap-sm">
              @for (option of executionDepthOptions; track option.value) {
                <button type="button" class="card card-light wizard-choice text-left" [class.active]="goalModeData['execution_depth'] === option.value" (click)="goalModeData['execution_depth'] = option.value">
                  <strong>{{ option.label }}</strong>
                  <span>{{ option.description }}</span>
                </button>
              }
            </div>
          } @else if (activeGoalWizardStep().id === 'safety') {
            <div class="grid cols-3 gap-sm">
              @for (option of safetyLevelOptions; track option.value) {
                <button type="button" class="card card-light wizard-choice text-left" [class.active]="goalModeData['safety_level'] === option.value" (click)="goalModeData['safety_level'] = option.value">
                  <strong>{{ option.label }}</strong>
                  <span>{{ option.description }}</span>
                </button>
              }
            </div>
          } @else {
            <app-explanation-notice title="Bereit zum Planen" message="Ananta erstellt daraus planbare Aufgaben. Ausfuehrung, Pruefung und Freigaben bleiben sichtbar."></app-explanation-notice>
            <div class="grid cols-2 gap-sm mt-sm">
              <div class="card card-light">
                <div class="muted font-sm">Ausfuehrung</div>
                <strong>{{ selectedExecutionDepthLabel() }}</strong>
              </div>
              <div class="card card-light">
                <div class="muted font-sm">Sicherheit</div>
                <strong>{{ selectedSafetyLevelLabel() }}</strong>
              </div>
            </div>
          }
      </app-wizard-shell>
    }
  `,
})
export class DashboardGuidedGoalWizardComponent implements OnChanges {
  @Input() goalModes: GoalModeDefinition[] = [];
  @Input() busy = false;
  @Input() resetKey = 0;

  @Output() submitGoal = new EventEmitter<GuidedGoalSubmit>();

  selectedGoalMode: GoalModeDefinition | null = null;
  goalModeData: Record<string, unknown> = {};
  goalWizardStepIndex = 0;
  readonly goalWizardSteps: GoalWizardStep[] = [
    { id: 'goal', title: 'Ziel', helper: 'Beschreibe, was am Ende anders oder besser sein soll.' },
    { id: 'context', title: 'Kontext', helper: 'Ergaenze Daten, Grenzen oder Fundstellen, damit weniger Rueckfragen entstehen.' },
    { id: 'execution', title: 'Tiefe', helper: 'Waehle, wie gruendlich der Hub planen und Tasks erzeugen soll.' },
    { id: 'safety', title: 'Sicherheit', helper: 'Lege fest, wie vorsichtig Ananta mit Freigaben und Pruefung umgehen soll.' },
    { id: 'review', title: 'Pruefen', helper: 'Kontrolliere die Angaben, bevor der Hub Tasks erstellt.' },
  ];
  readonly executionDepthOptions = [
    { value: 'quick', label: 'Schnell', description: 'Kleiner Plan mit wenigen Tasks fuer einfache Ziele.' },
    { value: 'standard', label: 'Standard', description: 'Ausgewogener Plan mit Kontext, Umsetzung und Pruefung.' },
    { value: 'deep', label: 'Gruendlich', description: 'Mehr Analyse, klarere Risiken und staerkere Nachweise.' },
  ];
  readonly safetyLevelOptions = [
    { value: 'safe', label: 'Vorsichtig', description: 'Mehr Review und keine riskanten automatischen Schritte.' },
    { value: 'balanced', label: 'Ausgewogen', description: 'Normale Freigaben und sichtbare Pruefpunkte.' },
    { value: 'fast', label: 'Schneller', description: 'Weniger Reibung fuer harmlose lokale Aufgaben.' },
  ];

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['resetKey'] && !changes['resetKey'].firstChange) {
      this.reset();
    }
  }

  setGoalMode(mode: GoalModeDefinition | null): void {
    this.selectedGoalMode = mode;
    this.goalModeData = {};
    this.goalWizardStepIndex = 0;
    if (mode) {
      mode.fields?.forEach((field: GoalModeField) => {
        if (field.default !== undefined) this.goalModeData[field.name] = field.default;
      });
      this.goalModeData['execution_depth'] = this.goalModeData['execution_depth'] || 'standard';
      this.goalModeData['safety_level'] = this.goalModeData['safety_level'] || 'balanced';
    }
  }

  activeGoalWizardStep(): GoalWizardStep {
    return this.goalWizardSteps[this.goalWizardStepIndex] || this.goalWizardSteps[0];
  }

  goToGoalWizardStep(index: number): void {
    if (index < 0 || index >= this.goalWizardSteps.length) return;
    this.goalWizardStepIndex = index;
  }

  nextGoalWizardStep(): void {
    if (!this.canContinueGoalWizard()) return;
    this.goalWizardStepIndex = Math.min(this.goalWizardStepIndex + 1, this.goalWizardSteps.length - 1);
  }

  previousGoalWizardStep(): void {
    this.goalWizardStepIndex = Math.max(this.goalWizardStepIndex - 1, 0);
  }

  canContinueGoalWizard(): boolean {
    const step = this.activeGoalWizardStep().id;
    if (step === 'goal') return this.requiredGoalFields().every(field => String(this.goalModeData[field.name] || '').trim().length > 0);
    if (step === 'execution') return !!this.goalModeData['execution_depth'];
    if (step === 'safety') return !!this.goalModeData['safety_level'];
    return true;
  }

  requiredGoalFields(): GoalModeField[] {
    return this.visibleGoalFields().filter(field => field.required !== false);
  }

  visibleGoalFields(): GoalModeField[] {
    return (this.selectedGoalMode?.fields || []).filter(field => field.type !== 'hidden');
  }

  fieldHelper(name: string): string {
    const normalized = String(name || '').toLowerCase();
    if (normalized.includes('goal') || normalized.includes('ziel')) return 'Ein klares Ziel hilft dem Hub, daraus pruefbare Tasks zu bilden.';
    if (normalized.includes('context') || normalized.includes('kontext')) return 'Kontext verhindert falsche Annahmen und hilft bei der passenden Zuweisung.';
    if (normalized.includes('team')) return 'Optional: Teams koennen spaeter auch im Board oder in Expertenbereichen gesetzt werden.';
    return 'Diese Angabe strukturiert den Plan und macht das Ergebnis besser pruefbar.';
  }

  selectedExecutionDepthLabel(): string {
    const selected = this.executionDepthOptions.find(option => option.value === this.goalModeData['execution_depth']);
    return selected?.label || 'Standard';
  }

  selectedSafetyLevelLabel(): string {
    const selected = this.safetyLevelOptions.find(option => option.value === this.goalModeData['safety_level']);
    return selected?.label || 'Ausgewogen';
  }

  submitGuidedGoal(): void {
    if (!this.selectedGoalMode) return;
    this.submitGoal.emit({ mode: this.selectedGoalMode, modeData: { ...this.goalModeData } });
  }

  private reset(): void {
    this.selectedGoalMode = null;
    this.goalModeData = {};
    this.goalWizardStepIndex = 0;
  }
}
