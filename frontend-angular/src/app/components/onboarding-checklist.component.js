var __decorate = (this && this.__decorate) || function (decorators, target, key, desc) {
    var c = arguments.length, r = c < 3 ? target : desc === null ? desc = Object.getOwnPropertyDescriptor(target, key) : desc, d;
    if (typeof Reflect === "object" && typeof Reflect.decorate === "function") r = Reflect.decorate(decorators, target, key, desc);
    else for (var i = decorators.length - 1; i >= 0; i--) if (d = decorators[i]) r = (c < 3 ? d(r) : c > 3 ? d(target, key, r) : d(target, key)) || r;
    return c > 3 && r && Object.defineProperty(target, key, r), r;
};
import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
let OnboardingChecklistComponent = class OnboardingChecklistComponent {
    constructor() {
        this.items = [
            { key: 'hub', label: 'Hub configured', done: false },
            { key: 'agents', label: 'Worker agents added', done: false },
            { key: 'llm', label: 'LLM provider configured', done: false },
            { key: 'teams', label: 'Team and roles configured', done: false },
            { key: 'templates', label: 'Templates reviewed', done: false },
        ];
        this.items = this.items.map((i) => ({ ...i, done: localStorage.getItem(`ananta.onboarding.${i.key}`) === '1' }));
    }
    toggle(key, event) {
        const checked = !!event.target?.checked;
        this.items = this.items.map((i) => (i.key === key ? { ...i, done: checked } : i));
        localStorage.setItem(`ananta.onboarding.${key}`, checked ? '1' : '0');
    }
    progress() {
        const done = this.items.filter((i) => i.done).length;
        return Math.round((done / this.items.length) * 100);
    }
};
OnboardingChecklistComponent = __decorate([
    Component({
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
], OnboardingChecklistComponent);
export { OnboardingChecklistComponent };
//# sourceMappingURL=onboarding-checklist.component.js.map