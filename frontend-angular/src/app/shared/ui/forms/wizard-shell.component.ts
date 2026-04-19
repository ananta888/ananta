import { Component, EventEmitter, Input, Output } from '@angular/core';

export interface WizardStep {
  id: string;
  title: string;
  helper?: string;
}

@Component({
  standalone: true,
  selector: 'app-wizard-shell',
  template: `
    <section class="card card-light shared-wizard-shell" [attr.aria-label]="ariaLabel || title">
      <div class="row space-between align-start">
        <div>
          @if (title) {
            <strong>{{ title }}</strong>
          }
          @if (activeStep()?.helper) {
            <p class="muted font-sm no-margin mt-5">{{ activeStep()?.helper }}</p>
          }
        </div>
        <div class="shared-wizard-toolbar">
          <ng-content select="[wizard-actions]"></ng-content>
        </div>
      </div>

      <div class="guided-stepper mt-md" aria-label="Wizard-Schritte">
        @for (step of steps; track step.id; let i = $index) {
          <button
            type="button"
            class="guided-step"
            [class.active]="i === activeIndex"
            [class.done]="i < activeIndex"
            (click)="stepSelect.emit(i)"
            [attr.aria-current]="i === activeIndex ? 'step' : null"
          >
            <span>{{ i + 1 }}</span>
            {{ step.title }}
          </button>
        }
      </div>

      <div class="mt-md">
        <ng-content></ng-content>
      </div>

      <div class="row mt-md space-between">
        <button class="secondary" type="button" (click)="previous.emit()" [disabled]="activeIndex === 0 || busy">{{ previousLabel }}</button>
        @if (!isLastStep()) {
          <button type="button" (click)="next.emit()" [disabled]="!canContinue || busy">{{ nextLabel }}</button>
        } @else {
          <button type="button" (click)="submit.emit()" [disabled]="!canContinue || busy">{{ busy ? busyLabel : submitLabel }}</button>
        }
      </div>
    </section>
  `,
})
export class WizardShellComponent {
  @Input() steps: WizardStep[] = [];
  @Input() activeIndex = 0;
  @Input() title = '';
  @Input() ariaLabel = '';
  @Input() canContinue = true;
  @Input() busy = false;
  @Input() previousLabel = 'Zurueck';
  @Input() nextLabel = 'Weiter';
  @Input() submitLabel = 'Absenden';
  @Input() busyLabel = 'Laedt...';
  @Output() stepSelect = new EventEmitter<number>();
  @Output() previous = new EventEmitter<void>();
  @Output() next = new EventEmitter<void>();
  @Output() submit = new EventEmitter<void>();

  activeStep(): WizardStep | null {
    return this.steps[this.activeIndex] || null;
  }

  isLastStep(): boolean {
    return this.activeIndex >= this.steps.length - 1;
  }
}
