import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { recommendBlueprint } from '../shared/blueprint-recommendation';

@Component({
  standalone: true,
  selector: 'app-onboarding-checklist',
  imports: [CommonModule, FormsModule],
  template: `
    <div class="card">
      <h3>Erste Schritte</h3>
      <div class="muted" style="font-size: 12px; margin-bottom: 8px;">Markiere, was fuer deinen ersten lokalen Lauf bereits erledigt ist.</div>
      @for (item of items; track item.key) {
        <label class="row" style="justify-content: space-between; width: 100%;">
          <span>{{ item.label }}</span>
          <input type="checkbox" [checked]="item.done" (change)="toggle(item.key, $event)">
        </label>
      }
      <div class="muted" style="font-size: 12px; margin-top: 8px;">Fortschritt: {{ progress() }}%</div>

      <div class="card card-light" style="margin-top: 12px;">
        <h4 style="margin: 0 0 6px;">Blueprint-Empfehlung fuer den Erststart</h4>
        <div class="muted" style="font-size: 12px; margin-bottom: 8px;">
          Kurze Guided-Auswahl fuer First-Run: Zieltyp, Striktheit, Domaene und Arbeitsstil. Ergebnis ist ein empfohlener Standard-Blueprint.
        </div>
        <div class="grid cols-2" style="gap: 8px;">
          <label>Zieltyp
            <select [(ngModel)]="guided.goalType">
              @for (option of goalTypeOptions; track option.value) {
                <option [value]="option.value">{{ option.label }}</option>
              }
            </select>
          </label>
          <label>Striktheit
            <select [(ngModel)]="guided.strictness">
              @for (option of strictnessOptions; track option.value) {
                <option [value]="option.value">{{ option.label }}</option>
              }
            </select>
          </label>
          <label>Domain
            <select [(ngModel)]="guided.domain">
              @for (option of domainOptions; track option.value) {
                <option [value]="option.value">{{ option.label }}</option>
              }
            </select>
          </label>
          <label>Arbeitsstil
            <select [(ngModel)]="guided.executionStyle">
              @for (option of executionStyleOptions; track option.value) {
                <option [value]="option.value">{{ option.label }}</option>
              }
            </select>
          </label>
        </div>
        <div class="muted" style="font-size: 12px; margin-top: 8px;">
          Empfehlung: <strong>{{ firstRunRecommendation().blueprintName }}</strong>
        </div>
        <div class="muted" style="font-size: 12px; margin-top: 4px;">
          Warum: {{ firstRunRecommendation().reasons.join(' ') }}
        </div>
      </div>
    </div>
  `,
})
export class OnboardingChecklistComponent {
  items = [
    { key: 'hub', label: 'Hub ist erreichbar', done: false },
    { key: 'agents', label: 'Mindestens ein Worker ist verbunden', done: false },
    { key: 'llm', label: 'LLM-Anbieter ist eingestellt', done: false },
    { key: 'teams', label: 'Team und Rollen sind vorbereitet', done: false },
    { key: 'templates', label: 'Vorlagen wurden geprueft', done: false },
  ];

  guided = {
    goalType: 'new_feature',
    strictness: 'balanced',
    domain: 'software',
    executionStyle: 'iterative',
  };
  readonly goalTypeOptions = [
    { value: 'new_feature', label: 'Neues Feature / Weiterentwicklung' },
    { value: 'bugfix', label: 'Bugfix / Incident' },
    { value: 'research', label: 'Research / Analyse' },
    { value: 'security_review', label: 'Security / Compliance Review' },
    { value: 'release_prep', label: 'Release-Vorbereitung' },
  ];
  readonly strictnessOptions = [
    { value: 'safe', label: 'Vorsichtig' },
    { value: 'balanced', label: 'Ausgewogen' },
    { value: 'strict', label: 'Strikt' },
  ];
  readonly domainOptions = [
    { value: 'software', label: 'Software' },
    { value: 'security', label: 'Security' },
    { value: 'release', label: 'Release' },
    { value: 'general', label: 'Allgemein' },
  ];
  readonly executionStyleOptions = [
    { value: 'iterative', label: 'Iterativ (Sprint/Loop)' },
    { value: 'flow', label: 'Flow/Kanban' },
    { value: 'opencode', label: 'OpenCode/Execution-Kaskade' },
    { value: 'evolution', label: 'Research -> Evolution' },
  ];

  constructor() {
    this.items = this.items.map((i) => ({ ...i, done: localStorage.getItem(`ananta.onboarding.${i.key}`) === '1' }));
  }

  toggle(key: string, event: Event) {
    const checked = !!(event.target as HTMLInputElement)?.checked;
    this.items = this.items.map((i) => (i.key === key ? { ...i, done: checked } : i));
    localStorage.setItem(`ananta.onboarding.${key}`, checked ? '1' : '0');
  }

  progress() {
    const done = this.items.filter((i) => i.done).length;
    return Math.round((done / this.items.length) * 100);
  }

  firstRunRecommendation() {
    return recommendBlueprint({
      goalType: this.guided.goalType,
      strictness: this.guided.strictness,
      domain: this.guided.domain,
      executionStyle: this.guided.executionStyle,
    });
  }
}
