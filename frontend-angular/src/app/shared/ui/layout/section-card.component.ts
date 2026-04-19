import { Component, Input } from '@angular/core';

@Component({
  standalone: true,
  selector: 'app-section-card',
  template: `
    <section class="card shared-section-card" [class.card-primary]="variant === 'primary'" [attr.aria-label]="ariaLabel || title">
      @if (title || subtitle) {
        <div class="row space-between align-start shared-section-header">
          <div>
            @if (eyebrow) {
              <div class="muted font-sm mb-xs">{{ eyebrow }}</div>
            }
            @if (title) {
              <h3 class="no-margin">{{ title }}</h3>
            }
            @if (subtitle) {
              <p class="muted mt-sm no-margin">{{ subtitle }}</p>
            }
          </div>
          <div class="shared-section-actions">
            <ng-content select="[section-actions]"></ng-content>
          </div>
        </div>
      }
      <div [class.mt-md]="title || subtitle">
        <ng-content></ng-content>
      </div>
    </section>
  `,
})
export class SectionCardComponent {
  @Input() title = '';
  @Input() subtitle = '';
  @Input() eyebrow = '';
  @Input() ariaLabel = '';
  @Input() variant: 'default' | 'primary' = 'default';
}
