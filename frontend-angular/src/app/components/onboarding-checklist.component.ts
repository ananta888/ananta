import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';

@Component({
  standalone: true,
  selector: 'app-onboarding-checklist',
  imports: [CommonModule],
  template: `
    <div class="card">
      <h3>Onboarding Checklist</h3>
      <div class="muted" style="font-size: 12px; margin-bottom: 8px;">Guided setup for first-time operators.</div>
      @for (item of items; track item.key) {
        <label class="row" style="justify-content: space-between; width: 100%;">
          <span>{{ item.label }}</span>
          <input type="checkbox" [checked]="item.done" (change)="toggle(item.key, $event)">
        </label>
      }
      <div class="muted" style="font-size: 12px; margin-top: 8px;">Progress: {{ progress() }}%</div>
    </div>
  `,
})
export class OnboardingChecklistComponent {
  items = [
    { key: 'hub', label: 'Hub configured', done: false },
    { key: 'agents', label: 'Worker agents added', done: false },
    { key: 'llm', label: 'LLM provider configured', done: false },
    { key: 'teams', label: 'Team and roles configured', done: false },
    { key: 'templates', label: 'Templates reviewed', done: false },
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
