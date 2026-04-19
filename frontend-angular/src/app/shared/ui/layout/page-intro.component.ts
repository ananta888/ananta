import { Component, Input } from '@angular/core';

@Component({
  standalone: true,
  selector: 'app-page-intro',
  template: `
    <section class="shared-page-intro" [attr.aria-label]="ariaLabel || title">
      <div class="shared-page-intro-copy">
        @if (eyebrow) {
          <div class="muted font-sm mb-xs">{{ eyebrow }}</div>
        }
        <h2>{{ title }}</h2>
        @if (subtitle) {
          <p class="muted">{{ subtitle }}</p>
        }
      </div>
      <div class="shared-page-intro-actions">
        <ng-content select="[intro-actions]"></ng-content>
      </div>
    </section>
  `,
})
export class PageIntroComponent {
  @Input() title = '';
  @Input() subtitle = '';
  @Input() eyebrow = '';
  @Input() ariaLabel = '';
}
