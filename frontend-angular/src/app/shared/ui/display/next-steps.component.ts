import { Component, EventEmitter, Input, Output } from '@angular/core';
import { RouterLink } from '@angular/router';

export interface NextStepAction {
  id: string;
  label: string;
  description?: string;
  routerLink?: string | unknown[];
  href?: string;
  disabled?: boolean;
}

@Component({
  standalone: true,
  selector: 'app-next-steps',
  imports: [RouterLink],
  template: `
    <section class="card card-light shared-next-steps" [attr.aria-label]="ariaLabel || title">
      <div>
        @if (title) {
          <h4 class="no-margin">{{ title }}</h4>
        }
        @if (description) {
          <p class="muted mt-sm no-margin">{{ description }}</p>
        }
      </div>
      <div class="shared-next-step-list">
        @for (step of steps; track step.id) {
          @if (step.routerLink) {
            <a class="shared-next-step" [routerLink]="step.routerLink" [class.disabled]="step.disabled">
              <strong>{{ step.label }}</strong>
              @if (step.description) {
                <span>{{ step.description }}</span>
              }
            </a>
          } @else if (step.href) {
            <a class="shared-next-step" [href]="step.href" [class.disabled]="step.disabled">
              <strong>{{ step.label }}</strong>
              @if (step.description) {
                <span>{{ step.description }}</span>
              }
            </a>
          } @else {
            <button class="shared-next-step" type="button" [disabled]="step.disabled" (click)="selectStep.emit(step)">
              <strong>{{ step.label }}</strong>
              @if (step.description) {
                <span>{{ step.description }}</span>
              }
            </button>
          }
        }
      </div>
    </section>
  `,
})
export class NextStepsComponent {
  @Input() title = 'Naechste Schritte';
  @Input() description = '';
  @Input() ariaLabel = '';
  @Input() steps: NextStepAction[] = [];
  @Output() selectStep = new EventEmitter<NextStepAction>();
}
