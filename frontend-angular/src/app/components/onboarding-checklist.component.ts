import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';

@Component({
  standalone: true,
  selector: 'app-onboarding-checklist',
  imports: [CommonModule],
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
}
