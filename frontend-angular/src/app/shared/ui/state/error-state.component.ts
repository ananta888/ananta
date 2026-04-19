import { Component, EventEmitter, Input, Output } from '@angular/core';
import { RouterLink } from '@angular/router';

@Component({
  standalone: true,
  selector: 'app-error-state',
  imports: [RouterLink],
  template: `
    <section class="state-banner error shared-error-state" role="alert" [attr.aria-label]="ariaLabel || title">
      <div class="shared-error-content">
        <strong>{{ title }}</strong>
        @if (message) {
          <p class="muted no-margin mt-sm">{{ message }}</p>
        }
        @if (details) {
          <details class="mt-sm">
            <summary>Details anzeigen</summary>
            <pre class="shared-error-details">{{ details }}</pre>
          </details>
        }
      </div>
      @if (retryLabel || secondaryLabel) {
        <div class="row gap-sm shared-state-actions">
          @if (retryLabel) {
            <button class="secondary btn-small" type="button" (click)="retry.emit()">{{ retryLabel }}</button>
          }
          @if (secondaryLabel) {
            @if (secondaryRouterLink) {
              <button class="secondary btn-small" [routerLink]="secondaryRouterLink">{{ secondaryLabel }}</button>
            } @else {
              <button class="secondary btn-small" type="button" (click)="secondary.emit()">{{ secondaryLabel }}</button>
            }
          }
        </div>
      }
    </section>
  `,
})
export class ErrorStateComponent {
  @Input() title = 'Etwas ist schiefgelaufen';
  @Input() message = '';
  @Input() details = '';
  @Input() ariaLabel = '';
  @Input() retryLabel = 'Erneut versuchen';
  @Input() secondaryLabel = '';
  @Input() secondaryRouterLink: string | unknown[] | null = null;

  @Output() retry = new EventEmitter<void>();
  @Output() secondary = new EventEmitter<void>();
}
