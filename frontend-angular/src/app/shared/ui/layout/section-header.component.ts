import { Component, Input } from '@angular/core';

@Component({
  standalone: true,
  selector: 'app-section-header',
  template: `
    <div class="row space-between align-start shared-section-header" [attr.aria-label]="ariaLabel || null">
      <div class="shared-section-title">
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
  `,
})
export class SectionHeaderComponent {
  @Input() title = '';
  @Input() subtitle = '';
  @Input() eyebrow = '';
  @Input() ariaLabel = '';
}
