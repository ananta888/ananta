import { Component, EventEmitter, Input, Output } from '@angular/core';
import { RouterLink } from '@angular/router';

@Component({
  standalone: true,
  selector: 'app-empty-state',
  imports: [RouterLink],
  template: `
    <section class="empty-state shared-empty-state" [class.compact]="compact" [attr.aria-label]="ariaLabel || title">
      @if (icon) {
        <div class="shared-state-icon" aria-hidden="true">{{ icon }}</div>
      }
      <h3>{{ title }}</h3>
      @if (description) {
        <p class="muted">{{ description }}</p>
      }
      @if (primaryLabel || secondaryLabel) {
        <div class="row gap-sm flex-center shared-state-actions">
          @if (primaryLabel) {
            @if (primaryRouterLink) {
              <button class="primary" [routerLink]="primaryRouterLink">{{ primaryLabel }}</button>
            } @else {
              <button class="primary" type="button" (click)="primary.emit()">{{ primaryLabel }}</button>
            }
          }
          @if (secondaryLabel) {
            @if (secondaryRouterLink) {
              <button class="secondary" [routerLink]="secondaryRouterLink">{{ secondaryLabel }}</button>
            } @else {
              <button class="secondary" type="button" (click)="secondary.emit()">{{ secondaryLabel }}</button>
            }
          }
        </div>
      }
    </section>
  `,
})
export class EmptyStateComponent {
  @Input() title = 'Noch nichts vorhanden';
  @Input() description = '';
  @Input() icon = '';
  @Input() compact = false;
  @Input() ariaLabel = '';
  @Input() primaryLabel = '';
  @Input() secondaryLabel = '';
  @Input() primaryRouterLink: string | unknown[] | null = null;
  @Input() secondaryRouterLink: string | unknown[] | null = null;

  @Output() primary = new EventEmitter<void>();
  @Output() secondary = new EventEmitter<void>();
}
